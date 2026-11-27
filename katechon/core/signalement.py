"""
Remontée des erreurs : ce que @healed et les blocs « non bloquants » avalaient
en silence part désormais vers deux destinations.

1. Une issue GitHub par empreinte d'erreur (type + fichier + ligne). Une même
   erreur n'ouvre jamais deux issues : les occurrences suivantes deviennent
   des commentaires « +N occurrence(s) », au plus un toutes les
   INTERVALLE_EMPREINTE secondes. Le texte publié est masqué (secrets,
   numéros, montants) : le dépôt est public. Un seul thread publie, la file
   est bornée, un disjoncteur coupe les envois après DISJONCTEUR_SEUIL échecs
   de l'API, et un plafond limite les envois par heure. Rien de tout cela ne
   ralentit ni ne fait échouer l'appelant.

2. Pour une opération financière, une dead letter queue. L'entrée est écrite
   tout de suite dans un fichier local (la base est souvent la cause de
   l'erreur), puis rejouée dans la table dead_letter_queue au cycle de santé
   suivant, sans doublon grâce à sa clé unique.

Activé par KATECHON_GITHUB_ISSUES=1 (avec GITHUB_TOKEN et GITHUB_REPO) et
KATECHON_DLQ=1, lus à chaque appel : sans eux, rien ne change.
"""
import collections
import hashlib
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg2.extras
import requests

from katechon.core.db import get_conn, release_conn

__all__ = ["masquer", "masquer_secrets", "signaler_erreur", "rejouer_dead_letters",
           "attendre_envois", "etat", "reinitialiser"]

log = logging.getLogger("BarackCorp")

FILE_MAX = 100
DISJONCTEUR_SEUIL = 5
DISJONCTEUR_DUREE = 900
INTERVALLE_EMPREINTE = 60
MAX_PAR_HEURE = 30
TIMEOUT_GITHUB = 10
DLQ_FICHIER = os.path.join("logs", "dead_letter.jsonl")

_SECRETS_ENV = ("GREENAPI_TOKEN", "GREENAPI_INSTANCE_ID", "GREENAPI_WEBHOOK_SECRET", "PG_PASS",
                "NGROK_TOKEN", "FLASK_SECRET_KEY", "DASHBOARD_TOKEN", "HEALTH_TOKEN", "GITHUB_TOKEN",
                "HEALTHCHECK_PING_URL")
_CLES_SECRETES = re.compile(r"(?i)token|secret|password|passwd|pwd|api[_-]?key|jeton")

_RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_IGNORES = {os.path.normcase(os.path.join(_RACINE, "katechon", "core", n))
            for n in ("health.py", "signalement.py")}

_horloge = time.monotonic

_verrou = threading.Lock()
_vide = threading.Condition(_verrou)
_dlq_verrou = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="SignalementGitHub")
_par_empreinte = {}
_issues = {}
_envois = collections.deque()
_en_attente = 0
_abandonnes = 0
_echecs = 0
_ouvert_jusqu = 0.0


# ── Masquage ────────────────────────────────────────────────────────────────

def masquer_secrets(texte) -> str:
    """Retire les secrets (valeurs connues, jetons, mots de passe) sans toucher
    aux identifiants ni aux montants : pour la DLQ, qui reste dans notre base."""
    t = str(texte)
    for nom in _SECRETS_ENV:
        v = os.getenv(nom) or ""
        if len(v) >= 4:
            t = t.replace(v, "[secret]")
    t = re.sub(r"(waInstance)\d+(/[A-Za-z]+/)[A-Za-z0-9]+", r"\1[masqué]\2[masqué]", t)
    t = re.sub(r"waInstance\d+", "waInstance[masqué]", t)
    t = re.sub(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", "[secret]", t)
    t = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+", "Bearer [secret]", t)
    t = re.sub(r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key)=([^\s&,;]+)", r"\1=[secret]", t)
    t = re.sub(r"postgres(?:ql)?://\S+", "postgresql://[masqué]", t)
    return t


def masquer(texte) -> str:
    """Masquage complet pour le dépôt public : secrets, e-mails, montants, numéros."""
    t = masquer_secrets(texte)
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", t)
    t = re.sub(r"\d(?:[\d  .,]*\d)?\s?(?:FCFA|XAF|CFA)\b", "[montant]", t)
    t = re.sub(r"\+?\d{8,15}", "[numéro]", t)
    return t


def _masquer_contexte(v):
    if isinstance(v, dict):
        return {str(k): ("[secret]" if _CLES_SECRETES.search(str(k)) and v[k] is not None
                         else _masquer_contexte(v[k])) for k in v}
    if isinstance(v, (list, tuple)):
        return [_masquer_contexte(x) for x in v]
    if isinstance(v, str):
        return masquer_secrets(v)
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return masquer_secrets(repr(v))


# ── Empreinte ───────────────────────────────────────────────────────────────

def _origine(exc):
    """Fichier et ligne où l'erreur est née : la frame la plus profonde du
    dépôt (hors health.py et signalement.py), sinon la plus profonde tout court."""
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    if not frames:
        return None, None
    choisie = frames[-1]
    for f in reversed(frames):
        chemin = os.path.normcase(os.path.abspath(f.filename))
        if chemin.startswith(os.path.normcase(_RACINE) + os.sep) and chemin not in _IGNORES:
            choisie = f
            break
    chemin = os.path.abspath(choisie.filename)
    if os.path.normcase(chemin).startswith(os.path.normcase(_RACINE) + os.sep):
        fichier = os.path.relpath(chemin, _RACINE).replace(os.sep, "/")
    else:
        fichier = os.path.basename(choisie.filename)
    return fichier, choisie.lineno


def _empreinte(exc, fonction):
    fichier, ligne = _origine(exc)
    base = f"{type(exc).__name__}|{fichier}|{ligne}" if fichier else f"{type(exc).__name__}|{fonction}"
    return hashlib.sha256(base.encode()).hexdigest(), fichier, ligne


# ── Point d'entrée ──────────────────────────────────────────────────────────

def signaler_erreur(exc, fonction, financier=False, contexte=None):
    """Signale une erreur avalée. Ne lève jamais et ne bloque jamais."""
    if financier and os.getenv("KATECHON_DLQ") == "1":
        try:
            _ecrire_dlq(exc, fonction, contexte)
        except Exception as e:
            log.error(f"❌ DLQ : écriture impossible ({type(e).__name__})")
    try:
        _vers_github(exc, fonction)
    except Exception as e:
        log.debug(f"Signalement GitHub ignoré : {type(e).__name__}")


# ── GitHub ──────────────────────────────────────────────────────────────────

def _config_github():
    if os.getenv("KATECHON_GITHUB_ISSUES") != "1":
        return None
    jeton, depot = os.getenv("GITHUB_TOKEN") or "", os.getenv("GITHUB_REPO") or ""
    if not jeton or "/" not in depot:
        return None
    base = (os.getenv("GITHUB_API_BASE") or "https://api.github.com").rstrip("/")
    return base, depot, jeton


def _vers_github(exc, fonction):
    global _en_attente, _abandonnes
    config = _config_github()
    if config is None:
        return
    fp, fichier, ligne = _empreinte(exc, fonction)
    # Construit avant toute mise à jour de l'état : s'il lève, rien n'est compté.
    rapport = {
        "titre": masquer(f"[auto] {type(exc).__name__}: {fichier or '?'}:{ligne or 0} dans {fonction}")[:200],
        "message": masquer(str(exc))[:500],
        "type": type(exc).__name__, "fichier": fichier, "ligne": ligne,
        "fonction": masquer(fonction), "empreinte": fp,
    }
    maintenant = _horloge()
    with _verrou:
        if maintenant < _ouvert_jusqu:
            return
        e = _par_empreinte.setdefault(fp, {"dernier": None, "compte": 0})
        e["compte"] += 1
        if e["dernier"] is not None and maintenant - e["dernier"] < INTERVALLE_EMPREINTE:
            return
        while _envois and maintenant - _envois[0] > 3600:
            _envois.popleft()
        if len(_envois) >= MAX_PAR_HEURE:
            return
        if _en_attente >= FILE_MAX:
            _abandonnes += 1
            return
        compte, e["compte"], e["dernier"] = e["compte"], 0, maintenant
        _envois.append(maintenant)
        _en_attente += 1
    try:
        _executor.submit(_publier, config, rapport, compte)
    except RuntimeError:   # exécuteur arrêté (fin du processus)
        _termine()


def _termine():
    global _en_attente
    with _verrou:
        _en_attente -= 1
        if _en_attente <= 0:
            _vide.notify_all()


def _publier(config, rapport, compte):
    global _echecs, _ouvert_jusqu
    base, depot, jeton = config
    entetes = {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    etiquette = f"fp-{rapport['empreinte'][:12]}"
    try:
        if _horloge() < _ouvert_jusqu:
            return
        numero = _issues.get(rapport["empreinte"])
        if numero is None:
            r = requests.get(f"{base}/repos/{depot}/issues", headers=entetes, timeout=TIMEOUT_GITHUB,
                             params={"state": "open", "labels": etiquette, "per_page": 1})
            r.raise_for_status()
            trouvees = r.json()
            numero = trouvees[0]["number"] if trouvees else None
        if numero is None:
            corps = (f"**Fonction** : `{rapport['fonction']}`\n"
                     f"**Erreur** : `{rapport['type']}` à `{rapport['fichier']}:{rapport['ligne']}`\n"
                     f"**Empreinte** : `{rapport['empreinte'][:12]}`\n\n"
                     f"```\n{rapport['message']}\n```\n\n"
                     f"_Signalé automatiquement par Katechon. Texte masqué : ni secret, ni numéro, ni montant._")
            r = requests.post(f"{base}/repos/{depot}/issues", headers=entetes, timeout=TIMEOUT_GITHUB,
                              json={"title": rapport["titre"], "body": corps,
                                    "labels": ["erreur-auto", etiquette]})
            r.raise_for_status()
            numero = r.json()["number"]
        else:
            texte = f"+{compte} occurrence" + ("s" if compte > 1 else "")
            r = requests.post(f"{base}/repos/{depot}/issues/{numero}/comments", headers=entetes,
                              timeout=TIMEOUT_GITHUB, json={"body": texte})
            r.raise_for_status()
        with _verrou:
            _issues[rapport["empreinte"]] = numero
            _echecs = 0
    except Exception as e:
        with _verrou:
            _echecs += 1
            if _echecs >= DISJONCTEUR_SEUIL:
                _ouvert_jusqu = _horloge() + DISJONCTEUR_DUREE
                _echecs = 0
        log.debug(f"Issue GitHub non publiée : {type(e).__name__}")
    finally:
        _termine()


def attendre_envois(timeout) -> bool:
    """Attend que la file de publication soit vide. True si elle l'est."""
    with _vide:
        return _vide.wait_for(lambda: _en_attente <= 0, timeout)


def etat() -> dict:
    with _verrou:
        return {"en_attente": _en_attente, "abandonnes": _abandonnes,
                "disjoncteur_ouvert": _horloge() < _ouvert_jusqu}


def reinitialiser():
    """Oublie l'état local (comme un redémarrage). Les envois en vol continuent."""
    global _abandonnes, _echecs, _ouvert_jusqu
    with _verrou:
        _par_empreinte.clear()
        _issues.clear()
        _envois.clear()
        _abandonnes = _echecs = 0
        _ouvert_jusqu = 0.0


# ── Dead letter queue ───────────────────────────────────────────────────────

def _ecrire_dlq(exc, fonction, contexte):
    fp, _, _ = _empreinte(exc, fonction)
    entree = {
        "cle": uuid.uuid4().hex,
        "cree_le": datetime.now(timezone.utc).isoformat(),
        "fonction": fonction,
        "empreinte": fp,
        "contexte": _masquer_contexte(contexte or {}),
        "erreur": masquer_secrets(f"{type(exc).__name__}: {exc}")[:2000],
    }
    ligne = json.dumps(entree, ensure_ascii=False, default=str) + "\n"
    dossier = os.path.dirname(DLQ_FICHIER)
    if dossier:
        os.makedirs(dossier, exist_ok=True)
    with _dlq_verrou, open(DLQ_FICHIER, "a", encoding="utf-8") as f:
        f.write(ligne)
        f.flush()
        os.fsync(f.fileno())
    log.error(f"💀 DLQ : {fonction} — {entree['erreur'][:120]}")


def rejouer_dead_letters() -> int:
    """Déplace les entrées du fichier DLQ vers la table. Retourne le nombre de
    lignes insérées. Le fichier n'est vidé qu'après le commit ; une entrée déjà
    en base (clé connue) n'est jamais insérée deux fois. Ne lève jamais."""
    with _dlq_verrou:
        try:
            if not os.path.exists(DLQ_FICHIER) or os.path.getsize(DLQ_FICHIER) == 0:
                return 0
            with open(DLQ_FICHIER, encoding="utf-8") as f:
                lignes = [l for l in f.read().splitlines() if l.strip()]
            entrees = []
            for l in lignes:
                try:
                    entrees.append(json.loads(l))
                except ValueError:
                    log.error("❌ DLQ : ligne illisible conservée dans le fichier")
                    return 0
            conn = get_conn(retries=1)
        except Exception as e:
            log.warning(f"⚠️ DLQ : rejeu reporté ({type(e).__name__})")
            return 0
        try:
            inseres = 0
            with conn.cursor() as c:
                for e in entrees:
                    c.execute("""INSERT INTO dead_letter_queue
                                 (cle, cree_le, fonction, empreinte, contexte, erreur)
                                 VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (cle) DO NOTHING""",
                              (e["cle"], e["cree_le"], e["fonction"], e.get("empreinte"),
                               psycopg2.extras.Json(e.get("contexte") or {}), e.get("erreur")))
                    inseres += c.rowcount
            conn.commit()
        except Exception as e:
            log.warning(f"⚠️ DLQ : rejeu en échec ({type(e).__name__})")
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            release_conn(conn)
        try:
            open(DLQ_FICHIER, "w", encoding="utf-8").close()
        except OSError as e:
            log.warning(f"⚠️ DLQ : fichier non vidé ({type(e).__name__}), les clés évitent les doublons")
        if inseres:
            log.warning(f"💀 DLQ : {inseres} entrée(s) à traiter dans dead_letter_queue")
        return inseres
