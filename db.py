"""
Accès PostgreSQL du bot : pool de connexions, disjoncteur, helpers de requête
et verrou d'ordre par tontine.

Extrait de barack_corp_v9_18.py. Le pool est un état de CE module : le bot
ne garde aucune copie de _db_pool et passe par configurer(), init_pool(),
fermer_pool(), reinitialiser_pool() et pool_actif(). reinitialiser_pool()
ferme l'ancien pool avant d'en ouvrir un neuf ; init_pool(), comme avant
l'extraction, en ouvre un sans fermer celui qui existe. Aucune dépendance
au bot.
"""
import logging
import threading
import time as time_module
from typing import Optional

import psycopg2
from psycopg2 import pool as pg_pool

__all__ = [
    "configurer", "init_pool", "fermer_pool", "reinitialiser_pool", "pool_actif",
    "get_conn", "q", "release_conn", "fetchone", "fetchall", "verrou_ordre_tontine",
]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée
_config: dict = {}

_db_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_db_pool_lock = threading.Lock()   # protège init_pool() et la réinitialisation dans get_conn()

def configurer(host, port, dbname, user, password) -> None:
    """Paramètres de connexion, fournis une fois par le bot (seul lecteur des PG_*)."""
    _config.update(host=host, port=port, dbname=dbname, user=user, password=password)


def _creer_pool():
    if not _config:
        raise RuntimeError("db.configurer() n'a pas été appelé : paramètres PostgreSQL absents")
    pool = pg_pool.ThreadedConnectionPool(
        minconn=10,
        maxconn=80,
        host=_config["host"], port=_config["port"], dbname=_config["dbname"],
        user=_config["user"], password=_config["password"],
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
        application_name="TontineBotPro_v9.18",
        options="-c timezone=Africa/Douala",
    )
    log.info(f"✅ Pool PostgreSQL initialisé (10–80 connexions) | "
             f"{_config['host']}:{_config['port']}/{_config['dbname']}")
    return pool


def init_pool():
    """Initialise le pool de connexions au démarrage."""
    global _db_pool
    _db_pool = _creer_pool()


def fermer_pool(attente_verrou: float = 5.0) -> None:
    """Ferme toutes les connexions du pool (arrêt du bot).

    Le pool fermé reste en place, comme avant l'extraction : un appel tardif
    à get_conn() échoue (PoolError) au lieu d'ouvrir en douce un pool neuf que
    plus rien ne fermerait. L'arrêt n'attend le verrou qu'un temps borné, pour
    ne pas rester bloqué derrière une reconstruction en cours."""
    pris = _db_pool_lock.acquire(timeout=attente_verrou)
    try:
        if _db_pool is not None:
            try:
                _db_pool.closeall()
            except Exception:
                pass
    finally:
        if pris:
            _db_pool_lock.release()


def reinitialiser_pool() -> None:
    """Ferme le pool courant puis en ouvre un neuf, sous le verrou du pool.
    Seul chemin de remplacement : get_conn et l'auto-réparation du bot."""
    global _db_pool
    with _db_pool_lock:
        if _db_pool is not None:
            try:
                _db_pool.closeall()
            except Exception:
                pass
        _db_pool = None
        init_pool()


def pool_actif() -> bool:
    return _db_pool is not None and not _db_pool.closed


# ── Circuit breaker DB — protection contre saturation ────────────────────
_db_failures      = 0
_db_last_failure  = 0
_DB_MAX_FAILURES  = 10
_DB_RESET_AFTER   = 60  # secondes
_db_circuit_lock  = threading.Lock()   # protège le check-then-act atomique

def _db_circuit_open() -> bool:
    """True si le circuit est ouvert (DB en panne) → on évite de marteler."""
    global _db_failures, _db_last_failure
    with _db_circuit_lock:
        if _db_failures < _DB_MAX_FAILURES:
            return False
        if time_module.time() - _db_last_failure > _DB_RESET_AFTER:
            _db_failures = 0
            return False
        return True


def _db_record_failure():
    global _db_failures, _db_last_failure
    with _db_circuit_lock:
        _db_failures += 1
        _db_last_failure = time_module.time()


def _db_record_success():
    global _db_failures
    with _db_circuit_lock:
        if _db_failures > 0:
            _db_failures = max(0, _db_failures - 1)


def get_conn(retries: int = 3):
    """
    Récupère une connexion depuis le pool.
    Auto-reconnexion en cas de connexion morte ou pool saturé.
    Toujours appeler release_conn() pour la rendre au pool.
    """
    global _db_pool
    with _db_pool_lock:
        if _db_pool is None:
            init_pool()

    if _db_circuit_open():
        log.warning("⚠️ Circuit breaker DB ouvert — attente 60s")
        time_module.sleep(2)

    last_err = None
    for attempt in range(retries):
        try:
            conn = _db_pool.getconn()
            # Connexion morte ? (keepalives gèrent la détection réseau)
            if getattr(conn, "closed", False):
                try:
                    _db_pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = _db_pool.getconn()
                # Fix 6 : vérifier aussi la connexion de remplacement
                if getattr(conn, "closed", False):
                    raise pg_pool.PoolError("Connexion de remplacement également fermée")
            conn.autocommit = False
            _db_record_success()
            return conn
        except (psycopg2.OperationalError, psycopg2.InterfaceError, pg_pool.PoolError) as e:
            last_err = e
            _db_record_failure()
            log.warning(f"⚠️ DB connexion échec {attempt+1}/{retries} : {str(e)[:100]}")
            if attempt < retries - 1:
                time_module.sleep(2 ** attempt)  # 1s, 2s, 4s
                # Réinitialiser le pool si trop d'échecs — protégé par lock
                if _db_failures >= _DB_MAX_FAILURES // 2:
                    reinitialiser_pool()
    raise last_err or RuntimeError("Impossible d'obtenir une connexion DB")


def q(conn, sql: str, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or ())
    return cur


def release_conn(conn):
    """Rend la connexion au pool. Rollback auto si transaction non commitée."""
    global _db_pool
    if conn and _db_pool:
        try:
            conn.rollback()
        except Exception:
            try:
                _db_pool.putconn(conn, close=True)
                return
            except Exception:
                return
        try:
            _db_pool.putconn(conn)
        except Exception:
            pass


def fetchone(conn, sql: str, params=None) -> Optional[dict]:
    cur  = q(conn, sql, params)
    row  = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def fetchall(conn, sql: str, params=None) -> list:
    cur  = q(conn, sql, params)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


# ── FORTERESSE concurrence : verrou d'ordre par tontine ────────────────────
_ORDRE_LOCK_NS = 42   # namespace advisory lock dédié aux mutations d'ordre

def verrou_ordre_tontine(conn, tontine_id: int):
    """
    Mutex STRICT par tontine sur les mutations d'ordre de liste_passage
    (pg_advisory_xact_lock, relâché automatiquement au COMMIT/ROLLBACK).

    Sérialise les writers d'une MÊME tontine (reclassement retard, MODIF_ORDRE,
    duplication de cycle, saisie de liste) sans jamais bloquer les autres tontines
    — 100 groupes à 16h05 restent pleinement parallèles.

    À appeler comme PREMIER statement de toute transaction qui modifie `ordre`.
    Couplé à la contrainte UNIQUE(tontine,cycle,ordre) DEFERRABLE : le lock
    garantit l'absence de writer concurrent, la contrainte deferrable tolère les
    doublons transitoires d'une permutation jusqu'au commit. Défense en profondeur.
    """
    q(conn, "SELECT pg_advisory_xact_lock(%s, %s)", (_ORDRE_LOCK_NS, int(tontine_id)))
