"""
État en mémoire du bot : sessions de conversation (membre, admin, KYC,
configuration de groupe, owner) et leur verrou, limiteur de débit,
exécuteurs bornés des messages et des téléchargements, purge des sessions
expirées, sauvegarde JSON et restauration au démarrage.

Extrait de barack_corp_v9_18.py sans changement de comportement. Le bot et
les modules de menus partagent ces mêmes dictionnaires ; ce module ne
dépend que de socle.py et des modules purs, jamais du bot.
"""
import logging
import concurrent.futures
import json
import os
import threading
import time as time_module
from pathlib import Path

from katechon.security.rate_limiter import RateLimiter
from socle import SESSION_TIMEOUT

__all__ = ["WAITRESS_THREADS", "RATE_LIMIT_MAX", "RATE_LIMIT_FENETRE", "RATE_LIMIT_GLOBAL_MAX",
    "_sessions_membre", "_sessions_admin", "_sessions_kyc", "_sessions_config", "_rate_limiter",
    "_sessions_lock", "_msg_executor", "_download_executor", "_groupes_intro_envoyes",
    "session_valide", "rate_limit_ok", "_sessions_path", "_sessions_bak_lock", "_sessions_owner",
    "_purger_sessions_expirees", "_sauvegarder_sessions", "_restaurer_sessions"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée
audit = logging.getLogger("Audit")      # même journal de sécurité que le bot


WAITRESS_THREADS = 70   # aligné sur maxconn=80 (-10 réservés APScheduler)


# ── Limiteur de débit (anti-flood par numéro + plafond global) ──────────────
RATE_LIMIT_MAX          = 10
RATE_LIMIT_FENETRE      = 60
# Plafond GLOBAL (tous émetteurs) : garde-fou anti-burst multi-numéros. Doit rester
# au-dessus du pic légitime (ouverture simultanée de nombreuses tontines) — réglable.
RATE_LIMIT_GLOBAL_MAX   = int(os.getenv("RATE_LIMIT_GLOBAL_MAX", "3000"))


_sessions_membre: dict = {}   # {wa: {"etape": str, "data": dict, "ts": float}}
_sessions_admin:  dict = {}   # {wa: {"etape": str, "tontine_id": int, "data": dict, "ts": float}}
_sessions_kyc:    dict = {}   # {wa: {"etape": str, "data": dict, "ts": float}}
_sessions_config: dict = {}   # {wa: {"etape": str, "group_id": str, "group_name": str, "data": dict, "ts": float}}
_rate_limiter = RateLimiter(max_per_key=RATE_LIMIT_MAX, max_global=RATE_LIMIT_GLOBAL_MAX,
                            window=RATE_LIMIT_FENETRE)
_sessions_lock    = threading.RLock()   # protège toutes les mutations _sessions_*

# ── Exécuteur borné pour le traitement des messages webhook ───────────────────
# non-daemon → le process attend la fin des threads en cours avant de quitter
# max_workers aligné sur WAITRESS_THREADS pour ne pas dépasser maxconn DB
_msg_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=WAITRESS_THREADS,
    thread_name_prefix="MsgWorker",
)
_download_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=10,
    thread_name_prefix="DownloadWorker",
)

# ── Groupes ayant déjà reçu l'intro — évite le spam ──────────────────────────
_groupes_intro_envoyes: set = set()


def session_valide(sessions: dict, wa: str) -> bool:
    with _sessions_lock:
        s = sessions.get(wa)
        if not s:
            return False
        if time_module.time() - s.get("ts", 0) > SESSION_TIMEOUT:
            sessions.pop(wa, None)
            return False
        s["ts"] = time_module.time()
        return True


def rate_limit_ok(identifiant: str) -> bool:
    """
    Autorise le message ? Double limite (per-numéro + plafond global), mémoire
    bornée et thread-safe (cf. rate_limiter.RateLimiter). Le plafond global
    neutralise le burst multi-numéros que l'ancien limiteur per-clé laissait passer.
    """
    ok = _rate_limiter.autorise(identifiant)
    if not ok:
        audit.warning(f"RATE LIMIT : {identifiant}")
    return ok


# H6 — Backup sessions
_sessions_path = Path("logs/sessions_backup.json")

_sessions_bak_lock = threading.Lock()


_sessions_owner: dict = {}   # wa → {etape, data}


def _purger_sessions_expirees():
    now = time_module.time()
    for store in (_sessions_kyc, _sessions_membre, _sessions_admin, _sessions_owner):
        for k in [k for k, v in list(store.items())
                  if now - v.get("ts", 0) > SESSION_TIMEOUT]:
            store.pop(k, None)
    for k in [k for k, v in list(_sessions_config.items())
              if now - v.get("ts", 0) > SESSION_TIMEOUT]:
        sess = _sessions_config.pop(k, None)
        if sess:
            _groupes_intro_envoyes.discard(sess.get("group_id", ""))


def _sauvegarder_sessions():
    """Snapshot des sessions actives vers fichier JSON (toutes les 60s + atexit)."""
    now = time_module.time()
    snapshot = {
        "kyc": {k: v for k, v in _sessions_kyc.items() if now - v.get("ts", 0) < SESSION_TIMEOUT},
        "admin": {k: v for k, v in _sessions_admin.items() if now - v.get("ts", 0) < SESSION_TIMEOUT},
        "membre": {k: v for k, v in _sessions_membre.items() if now - v.get("ts", 0) < SESSION_TIMEOUT},
    }
    try:
        with _sessions_bak_lock:
            with open(_sessions_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f)
    except Exception as e:
        log.warning(f"⚠️ Sauvegarde sessions échouée : {e}")


def _restaurer_sessions():
    """Restaure les sessions non-expirées depuis le backup JSON au démarrage."""
    if not _sessions_path.exists():
        return
    try:
        with open(_sessions_path, encoding="utf-8") as f:
            data = json.load(f)
        now = time_module.time()
        for k, v in data.get("kyc", {}).items():
            if now - v.get("ts", 0) < SESSION_TIMEOUT:
                _sessions_kyc[k] = v
        for k, v in data.get("admin", {}).items():
            if now - v.get("ts", 0) < SESSION_TIMEOUT:
                _sessions_admin[k] = v
        for k, v in data.get("membre", {}).items():
            if now - v.get("ts", 0) < SESSION_TIMEOUT:
                _sessions_membre[k] = v
        log.info(f"Sessions restaurées : {len(_sessions_kyc)} KYC, {len(_sessions_admin)} admin, {len(_sessions_membre)} membre")
    except Exception as e:
        log.warning(f"⚠️ Restauration sessions échouée (non bloquant) : {e}")
