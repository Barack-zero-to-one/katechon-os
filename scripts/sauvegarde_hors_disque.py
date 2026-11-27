"""
Sauvegarde hors disque de la base Katechon, vérifiée par une vraie restauration.

Lancé par le Planificateur de tâches Windows, hors du processus du bot : il
tourne même si le bot est arrêté, et son échec ne touche pas le bot.

1. Compte les lignes des tables financières de la base source.
2. pg_dump -Fc : instantané MVCC cohérent, sans bloquer lectures ni écritures ;
   --lock-wait-timeout abandonne au lieu d'attendre derrière une migration.
3. restic backup vers le dépôt (B2 en production) : chiffré en AES-256,
   dédupliqué, avec l'empreinte SHA-256 du dump en étiquette.
4. restic forget (7 jours, 4 semaines, 6 mois) puis restic check.
5. Exercice de restauration : restic dump du dernier instantané dans un dossier
   temporaire, comparaison SHA-256, cluster PostgreSQL jetable (initdb +
   pg_ctl sur un port libre, jamais le cluster de production), pg_restore,
   tables critiques, nombres de lignes et invariants financiers.
6. Ping BACKUP_HEALTHCHECK_URL si tout a tenu, <URL>/fail sinon. Le dossier
   temporaire et le cluster jetable sont toujours supprimés.

Variables : PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASS, PG_BIN, RESTIC_BIN
(défaut « restic »), RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE, B2_ACCOUNT_ID,
B2_ACCOUNT_KEY, BACKUP_HEALTHCHECK_URL, BACKUP_TRAVAIL (dossier des fichiers
temporaires, défaut : dossier temporaire du système).

Code de sortie : 0 si la sauvegarde est restaurée et vérifiée, 1 sinon.
"""
import hashlib
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import psycopg2
import requests
from psycopg2 import sql

log = logging.getLogger("Sauvegarde")

NOM_DUMP = "barack_corp.dump"
ETIQUETTE = "katechon"
TABLES_CRITIQUES = ["membres", "tontines", "adhesions", "admins_groupe", "cotisations_manuelles",
                    "screenshots_hash", "bouffages_manuels", "dettes_badf", "liste_passage",
                    "sanctions", "reprises_donnees", "transactions", "dettes_ira",
                    "cautions_garantie", "audit_log"]
TABLES_COMPTEES = ["membres", "tontines", "transactions", "cotisations_manuelles",
                   "bouffages_manuels", "dettes_ira", "cautions_garantie", "dettes_badf"]
# (table, colonne) : aucun montant négatif ne doit exister.
MONTANTS = [("transactions", "montant_brut"), ("transactions", "montant_net"),
            ("cotisations_manuelles", "montant_declare"), ("bouffages_manuels", "montant_brut"),
            ("bouffages_manuels", "montant_net"), ("dettes_ira", "montant"),
            ("cautions_garantie", "montant")]
_ENV_SYSTEME = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA", "USERPROFILE",
                "HOME", "PATH", "PATHEXT", "COMSPEC")
DELAI_DUMP = 900
DELAI_RESTIC = 1800


class EchecSauvegarde(Exception):
    pass


def _bin(nom):
    dossier = os.getenv("PG_BIN") or ""
    exe = nom + (".exe" if os.name == "nt" else "")
    chemin = os.path.join(dossier, exe) if dossier else exe
    return chemin if not dossier or os.path.exists(chemin) else exe


def _env_systeme():
    return {k: os.environ[k] for k in _ENV_SYSTEME if k in os.environ}


def _env_pg():
    """Environnement réduit pour les outils PostgreSQL : jamais les secrets du bot."""
    env = _env_systeme()
    env["PGPASSWORD"] = os.getenv("PG_PASS", "")
    return env


def _env_restic():
    env = _env_systeme()
    for k in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "B2_ACCOUNT_ID", "B2_ACCOUNT_KEY",
              "RESTIC_CACHE_DIR"):
        if os.getenv(k):
            env[k] = os.environ[k]
    return env


def _executer(cmd, env, delai, **kw):
    r = subprocess.run(cmd, env=env, timeout=delai, capture_output=True, **kw)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
        raise EchecSauvegarde(f"{os.path.basename(cmd[0])} {cmd[1] if len(cmd) > 1 else ''} "
                              f"a échoué ({r.returncode}) : {err.strip()[-400:]}")
    return r


def _sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def _source():
    return dict(host=os.getenv("PG_HOST", "localhost"), port=os.getenv("PG_PORT", "5432"),
                dbname=os.getenv("PG_DB", "barack_corp"), user=os.getenv("PG_USER", "postgres"),
                password=os.getenv("PG_PASS", ""))


def _compter(dsn):
    conn = psycopg2.connect(connect_timeout=10, **dsn)
    try:
        with conn.cursor() as c:
            comptes = {}
            for t in TABLES_COMPTEES:
                c.execute("SELECT to_regclass(%s)", (f"public.{t}",))
                if c.fetchone()[0]:
                    c.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(t)))
                    comptes[t] = c.fetchone()[0]
        return comptes
    finally:
        conn.close()


def _verifier(dsn, comptes):
    """Tables critiques, lignes au moins aussi nombreuses qu'avant le dump,
    invariants financiers. Lève EchecSauvegarde au premier manquement."""
    conn = psycopg2.connect(connect_timeout=10, **dsn)
    try:
        with conn.cursor() as c:
            for t in TABLES_CRITIQUES:
                c.execute("SELECT to_regclass(%s)", (f"public.{t}",))
                if not c.fetchone()[0]:
                    raise EchecSauvegarde(f"table absente après restauration : {t}")
            for t, avant in comptes.items():
                c.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(t)))
                apres = c.fetchone()[0]
                if apres < avant:
                    raise EchecSauvegarde(f"{t} : {apres} lignes restaurées, {avant} avant le dump")
            for t, col in MONTANTS:
                c.execute(sql.SQL("SELECT COUNT(*) FROM {} WHERE {} < 0")
                          .format(sql.Identifier(t), sql.Identifier(col)))
                n = c.fetchone()[0]
                if n:
                    raise EchecSauvegarde(f"invariant violé : {n} montant(s) négatif(s) dans {t}.{col}")
            c.execute("""SELECT COUNT(*) FROM dettes_ira d
                         WHERE d.bouffage_id IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM bouffages_manuels b WHERE b.id = d.bouffage_id)""")
            n = c.fetchone()[0]
            if n:
                raise EchecSauvegarde(f"invariant violé : {n} dette(s) IRA liée(s) à un bouffage inexistant")
    finally:
        conn.close()


def _port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _exercice(travail, empreinte, comptes):
    """Restaure le dernier instantané dans un cluster jetable et le vérifie."""
    restic = os.getenv("RESTIC_BIN") or "restic"
    restaure = os.path.join(travail, "restaure.dump")
    with open(restaure, "wb") as f:
        r = subprocess.run([restic, "dump", "--tag", ETIQUETTE, "latest", "/" + NOM_DUMP],
                           env=_env_restic(), timeout=DELAI_RESTIC, stdout=f, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise EchecSauvegarde(f"restic dump a échoué : {r.stderr.decode('utf-8', 'replace').strip()[-400:]}")
    if _sha256(restaure) != empreinte:
        raise EchecSauvegarde("le dump restauré ne correspond pas à l'empreinte SHA-256 d'origine")

    donnees, port, role = os.path.join(travail, "pgdata"), _port_libre(), "katechon_exercice"
    env = _env_systeme()
    _executer([_bin("initdb"), "-D", donnees, "-U", role, "-A", "trust", "-E", "UTF8"], env, 300)
    demarre = False
    try:
        # Pas de capture : postgres hériterait des tubes et subprocess attendrait
        # leur fermeture, c'est-à-dire l'arrêt du serveur. Son journal va dans pg.log.
        demarre = True   # avant le lancement : un échec à mi-chemin est quand même arrêté
        r = subprocess.run([_bin("pg_ctl"), "-D", donnees, "-l", os.path.join(travail, "pg.log"), "-w",
                            "-t", "120", "-o", f"-p {port} -c listen_addresses=127.0.0.1", "start"],
                           env=env, timeout=180, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            raise EchecSauvegarde(f"pg_ctl start a échoué ({r.returncode}), voir pg.log")
        dsn = dict(host="127.0.0.1", port=str(port), user=role, password="")
        conn = psycopg2.connect(dbname="postgres", connect_timeout=10, **dsn)
        conn.autocommit = True
        with conn.cursor() as c:
            c.execute("CREATE DATABASE exercice")
        conn.close()
        _executer([_bin("pg_restore"), "--exit-on-error", "--no-owner", "--no-privileges",
                   "-h", "127.0.0.1", "-p", str(port), "-U", role, "-d", "exercice", restaure],
                  env, DELAI_DUMP)
        _verifier(dict(dbname="exercice", **dsn), comptes)
    finally:
        if demarre and os.path.exists(os.path.join(donnees, "postmaster.pid")):
            r = subprocess.run([_bin("pg_ctl"), "-D", donnees, "-m", "fast", "-w", "-t", "60", "stop"],
                               env=env, timeout=90, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode != 0:
                log.error(f"Arrêt du cluster d'exercice en échec ({r.returncode})")


def _ping(succes):
    url = (os.getenv("BACKUP_HEALTHCHECK_URL") or "").strip()
    if not url:
        return
    try:
        requests.get(url if succes else url.rstrip("/") + "/fail", timeout=10)
    except Exception as e:
        log.warning(f"Ping de supervision non envoyé : {type(e).__name__}")


def sauvegarder():
    if not os.getenv("RESTIC_REPOSITORY") or not os.getenv("RESTIC_PASSWORD_FILE"):
        raise EchecSauvegarde("RESTIC_REPOSITORY et RESTIC_PASSWORD_FILE sont obligatoires")
    restic = os.getenv("RESTIC_BIN") or "restic"
    racine = os.getenv("BACKUP_TRAVAIL") or None
    travail = tempfile.mkdtemp(prefix="katechon-sauvegarde-", dir=racine)
    try:
        source = _source()
        comptes = _compter(source)
        dump = os.path.join(travail, NOM_DUMP)
        _executer([_bin("pg_dump"), "-Fc", "--lock-wait-timeout=30000", "-h", source["host"],
                   "-p", str(source["port"]), "-U", source["user"], "-d", source["dbname"], "-f", dump],
                  _env_pg(), DELAI_DUMP)
        empreinte = _sha256(dump)
        log.info(f"Dump : {os.path.getsize(dump) // 1024} Ko, sha256 {empreinte[:12]}")
        with open(dump, "rb") as f:
            _executer([restic, "backup", "--stdin", "--stdin-filename", NOM_DUMP, "--host", ETIQUETTE,
                       "--tag", ETIQUETTE, "--tag", f"sha256={empreinte}"],
                      _env_restic(), DELAI_RESTIC, stdin=f)
        os.remove(dump)
        _executer([restic, "forget", "--tag", ETIQUETTE, "--keep-daily", "7", "--keep-weekly", "4",
                   "--keep-monthly", "6", "--prune"], _env_restic(), DELAI_RESTIC)
        _executer([restic, "check", "--read-data-subset=10%"], _env_restic(), DELAI_RESTIC)
        _exercice(travail, empreinte, comptes)
    finally:
        for _ in range(5):   # Windows libère parfois les fichiers du cluster avec retard
            shutil.rmtree(travail, ignore_errors=True)
            if not os.path.exists(travail):
                break
            time.sleep(1)


def main():
    if not log.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    debut = time.monotonic()
    try:
        sauvegarder()
    except Exception as e:
        log.error(f"❌ Sauvegarde en échec : {e}")
        _ping(False)
        return 1
    log.info(f"✅ Sauvegarde restaurée et vérifiée en {time.monotonic() - debut:.0f} s")
    _ping(True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
