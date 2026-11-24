"""
╔══════════════════════════════════════════════════════════════════════════╗
║   TONTINEBOT PRO — VERSION 9.17 — BADF Ltd — Cameroun 🇨🇲               ║
║   "Utiliser la technologie pour servir le prochain avec intégrité"      ║
║                                                                          ║
║   Stack : Python 3.11 · Flask · PostgreSQL · Green API WhatsApp         ║
║                                                                          ║
║   CORRECTIFS v9.2 :                                                      ║
║   ✅ Changement de numéro (CHGNUM 500 FCFA) — handler complet           ║
║   ✅ Admins groupe enregistrés automatiquement en base                   ║
║   ✅ Bot rejoint groupe → se présente + enregistre admins                ║
║   ✅ creer_tontine() / inscrire_dans_tontine() — config auto DM admin   ║
║   ✅ jours_avance respecté dans suspension 72h                           ║
║   ✅ SELECT FOR UPDATE PostgreSQL sur cashout (verrou natif DB)          ║
║   ✅ Codes USSD MTN/Orange configurables par tontine                     ║
║   ✅ Webhook promotion admin WhatsApp → enregistrement auto              ║
║                                                                          ║
║   FONCTIONNALITÉS COMPLÈTES :                                            ║
║   ✅ Inscription gratuite — prénom uniquement, compte actif immédiat     ║
║   ✅ Commission 2% + IRA 150 FCFA retard                                ║
║   ✅ Rappels horaires 12h→18h + matin 8h (codes USSD MTN + Orange)      ║
║   ✅ Suspension automatique 72h | Réactivation 1 000 FCFA               ║
║   ✅ Paiement avancé plusieurs périodes                                  ║
║   ✅ Cashout retry exponentiel 5 tentatives + check-back proactif        ║
║   ✅ Déduction dette IRA sur bouffage                                    ║
║   ✅ Caution anti-fuyard (bloquée/libérée/saisie)                       ║
║   ✅ Menu membre + admin 12 options conversationnel                      ║
║   ✅ Cas difficiles : pause/échelonnement/cession/exonération/exclusion  ║
║   ✅ Rapport owner 21h | Backup pg_dump 2h rotation 7                   ║
║   ✅ Messages CEMAC/ANIF | Audit log complet                             ║
║                                                                          ║
║   FICHIERS :                                                             ║
║   • barack_corp_v9_2.py  ← ce fichier (bot Python)                      ║
║   • wppconnect_server.js ← serveur WhatsApp Node.js (obligatoire)       ║
║   • DEMARRAGE_WINDOWS.bat ← watchdog démarrage automatique              ║
║                                                                          ║
║   DÉMARRAGE : double-cliquer DEMARRAGE_WINDOWS.bat                       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import atexit
import sys
import concurrent.futures
import hashlib
import hmac
import base64
import os
import re
import json
import shutil
import subprocess
import functools
import logging
import threading
import time as time_module
from pathlib import Path
from collections import defaultdict
from datetime import datetime, time, timedelta, date as _date
from typing import Optional

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers import SchedulerNotRunningError

import ira_engine  # Moteur IRA v2 (barème pur, testé hors DB)
import webhook_security as ws        # Durcissement webhook : anti-rejeu + token + SSRF (module pur)
import screenshot_integrity as si    # Anti-recyclage renforcé : référence + perceptual hash (module pur)
from rate_limiter import RateLimiter  # Limiteur per-clé + global, mémoire bornée (module pur)
import wa_dispatch                   # File d'envoi WhatsApp : un expéditeur, priorités, écart par destinataire (module pur)
from ocr import (                    # Lecture des reçus : OpenCV, PDF SwitchN, Tesseract (module pur)
    _pretraiter_screenshot_whatsapp, _lire_pdf_switchn, lire_screenshot_mobile_money,
)
from messages import (               # Textes du bot et liste de passage (module pur)
    BOT_NOM, MSG_DISSUASION, msg_dissuasion, msg_intro_groupe, MSG_INTRO_GROUPE,
    msg_dm_admin_bienvenue, parser_liste_passage, msg_kyc_groupe, MSG_BIENVENUE_DM,
    MENU_MEMBRE_TXT, MENU_ADMIN_TXT, MENU_CAS_DIFFICILES,
)

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION GÉNÉRALE
# ══════════════════════════════════════════════════════════════════════════

PORT             = 5000
WAITRESS_THREADS = 70   # aligné sur maxconn=80 (-10 réservés APScheduler)

# ── Frais BADF Ltd ────────────────────────────────────────────────────────
FRAIS_FMP        = 0.00    # Gratuit au lancement — modèle 2% activable (une ligne)
HEURE_LIMITE_DEF = time(18, 0)

# ── IRA v2 — Katechon comportemental (barème indexé sur la mise) ───────────
# Modèle : falaise 15% dès la 6e min + 2%/jour linéaire, plafond 50%, plancher 200,
# arrondi bas 100. Rampe horaire optionnelle (IRA_HOURLY_PCT>0). Voir ira_engine.py.
IRA_CLIFF_PCT    = 0.15    # Socle Schelling : % de la mise dès la 6e minute de retard
IRA_DAILY_PCT    = 0.02    # Accrual journalier linéaire
IRA_HOURLY_PCT   = 0.00    # Rampe intra-day (0 = falaise plate ; 0.01 = urgence horaire)
IRA_CAP_PCT      = 0.50    # Plafond anti-usure (optics ANIF/COBAC)
IRA_FLOOR        = 200     # Plancher aligné grille 100 (protège les petits groupes)
IRA_GRACE_MIN    = 5       # Tolérance réseau MTN/Orange (minutes)
MONTANT_IRA      = 150     # DÉPRÉCIÉ — conservé le temps de migrer les anciens messages
FRAIS_REACTIV    = 1_000   # Réactivation après suspension
FRAIS_CHGNUM     = 500     # Changement de numéro Mobile Money

# ── Numéro collecteur BADF — reçoit FMP + adhésions (jamais l'IRA, qui
#    revient aux membres ponctuels du groupe) ──────────────────────────────
NUMERO_BADF_MTN    = os.getenv("NUMERO_BADF_MTN",    "+237683603113")  # MTN MoMo
NUMERO_BADF_ORANGE = os.getenv("NUMERO_BADF_ORANGE", "+237692100606")  # Orange Money

# ── Anti-fraude général ────────────────────────────────────────────────────
MAX_TENTATIVES_FRAUDE   = 3
RATE_LIMIT_MAX          = 10
RATE_LIMIT_FENETRE      = 60
# Plafond GLOBAL (tous émetteurs) : garde-fou anti-burst multi-numéros. Doit rester
# au-dessus du pic légitime (ouverture simultanée de nombreuses tontines) — réglable.
RATE_LIMIT_GLOBAL_MAX   = int(os.getenv("RATE_LIMIT_GLOBAL_MAX", "3000"))
DELAI_SUSPENSION_HEURES = 72
DELAI_ALERTE_FUGUE      = 3
DELAI_BLOCAGE_FUGUE     = 7
BACKUP_ROTATION         = 7
HEURE_BACKUP           = 2

# ── PostgreSQL ────────────────────────────────────────────────────────────
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB   = os.getenv("PG_DB",   "barack_corp")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASS = os.getenv("PG_PASS", "")
PG_BIN  = os.getenv("PG_BIN",  r"C:\Program Files\PostgreSQL\18\bin")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")

# ── WhatsApp — Green API (compte perso existant, scan QR) ─────────────────
from whatsapp import (               # Envoi Green API : outbox, expéditeur, wa_* (module whatsapp.py)
    GREENAPI_INSTANCE_ID, GREENAPI_TOKEN, GREENAPI_BASE, OWNER_WA,
    _outbox_lock, _outbox_path, _outbox_enqueue, _outbox_drain, _wa_request, _chat_id,
    _wa_send_direct, WA_ECART_PAR_CHAT, WA_DEBIT_GLOBAL, _expediteur, _wa_send_groupe_direct,
    _wa_send, _wa_send_groupe, _wa_send_group_chatid, wa_kick_membre, wa_quitter_groupe,
    wa_prive, wa_owner, wa_admins_tontine, wa_groupe, wa_admin,
)
GREENAPI_WEBHOOK_SECRET = os.getenv("GREENAPI_WEBHOOK_SECRET", "")  # Token secret webhook (256 bits)
from health import (                 # @healed, état de santé, auto-réparation (module health.py)
    healed, _health_state, _health_lock, _self_heal_db, _self_heal_check,
    _self_heal_outbox, _check_greenapi,
)

# Anti-rejeu webhook : le token statique dans l'URL n'empêche pas de rejouer une
# requête capturée. On déduplique les idMessage sur une fenêtre glissante, borné.
WEBHOOK_REPLAY_TTL = int(os.getenv("WEBHOOK_REPLAY_TTL", "900"))  # secondes
_replay_guard = ws.ReplayGuard(ttl=WEBHOOK_REPLAY_TTL, max_size=50_000)

# Plafond de taille d'un média téléchargé (anti-DoS mémoire côté SSRF).
MEDIA_MAX_BYTES = int(os.getenv("MEDIA_MAX_BYTES", str(15 * 1024 * 1024)))  # 15 Mo

# ── Validation credentials Green API au démarrage ─────────────────────────
if not GREENAPI_WEBHOOK_SECRET:
    logging.getLogger(__name__).error(
        "❌ GREENAPI_WEBHOOK_SECRET manquant — webhook sera refusé en 503")
if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
    logging.getLogger(__name__).error(
        "❌ GREENAPI_INSTANCE_ID / GREENAPI_TOKEN manquants — envoi impossible")

# ── WhatsApp ──────────────────────────────────────────────────────────────
GROUPE_ADMIN = "Admin Barack Corp"

# ── Configuration multi-pays (i18n + payments rails) ─────────────────────
# Chaque tontine peut avoir son country_code → on lit la config ici.
# Pour ajouter un pays : ajouter une entrée + traduire les messages clés.
COUNTRY_CONFIG = {
    "CM": {
        "name":           "Cameroon",
        "flag":           "🇨🇲",
        "phone_prefix":   "+237",
        "phone_local":    "237",
        "currency":       "FCFA",
        "currency_full":  "Franc CFA",
        "timezone":       "Africa/Douala",
        "language":       "fr",
        "mobile_money":   ["MTN MoMo", "Orange Money"],
        "regulator":      "COBAC",
        "regulator_ref":  "Règlement COBAC R-2019/01",
        "aml_agency":     "ANIF",
        "penal_ref":      "Articles 318 à 323 du Code Pénal Camerounais",
        "tontine_name":   "tontine",
        "tontine_admin":  "président de tontine",
        "decimal_sep":    ",",
        "thousand_sep":   " ",
    },
    "SN": {
        "name":           "Senegal",
        "flag":           "🇸🇳",
        "phone_prefix":   "+221",
        "phone_local":    "221",
        "currency":       "FCFA",
        "timezone":       "Africa/Dakar",
        "language":       "fr",
        "mobile_money":   ["Wave", "Orange Money", "Free Money"],
        "regulator":      "BCEAO",
        "tontine_name":   "tontine",
    },
    "CI": {
        "name":           "Côte d'Ivoire",
        "flag":           "🇨🇮",
        "phone_prefix":   "+225",
        "currency":       "FCFA",
        "timezone":       "Africa/Abidjan",
        "language":       "fr",
        "mobile_money":   ["Wave", "MTN MoMo", "Orange Money", "Moov"],
        "regulator":      "BCEAO",
        "tontine_name":   "tontine",
    },
}

DEFAULT_COUNTRY = "CM"  # Cameroun par défaut


# ── Webhook public (ngrok ou domaine) ────────────────────────────────────
NGROK_DOMAIN  = os.getenv("NGROK_DOMAIN", "lennox-unbiographical-jasmin.ngrok-free.dev")
NGROK_TOKEN   = os.getenv("NGROK_TOKEN",  "")
PUBLIC_URL    = ""

SESSION_TIMEOUT = 300


def enregistrer_liste_passage(tontine_id: int, liste: list, wa_admin: str) -> tuple:
    """
    Enregistre la liste parsée dans liste_passage.
    Gère les places multiples : si un nickname apparaît N fois dans la liste,
    le membre a N places → il cotise N × montant_base par période.
    Retourne (nb_ok, nb_non_lies).
    """
    conn  = get_conn()
    verrou_ordre_tontine(conn, tontine_id)  # FORTERESSE : sérialise l'ordre de cette tontine
    cycle = fetchone(conn,
        "SELECT cycle_actuel FROM tontines WHERE id=%s", (tontine_id,))["cycle_actuel"]

    # Supprimer l'ancienne liste de ce cycle
    q(conn, """DELETE FROM liste_passage
               WHERE tontine_id=%s AND cycle=%s AND statut='En_attente'""",
      (tontine_id, cycle))

    nb_ok       = 0
    nb_non_lies = 0

    # Compter les occurrences de chaque nickname → nombre de places
    from collections import Counter
    occurrences = Counter(
        item["nickname"].upper().strip() for item in liste
    )

    # Mapper nickname → membre_id (cache pour éviter les doublons de requêtes)
    nickname_cache = {}

    for item in liste:
        nick_key = item["nickname"].upper().strip()

        if nick_key not in nickname_cache:
            membre = fetchone(conn, """
                SELECT m.id FROM membres m
                JOIN adhesions a ON a.membre_id = m.id
                WHERE a.tontine_id=%s AND a.statut='Actif'
                  AND UPPER(m.nom_complet) LIKE UPPER(%s)
                LIMIT 1
            """, (tontine_id, f"%{item['nickname']}%"))
            nickname_cache[nick_key] = membre["id"] if membre else None

        membre_id = nickname_cache[nick_key]
        if not membre_id:
            nb_non_lies += 1

        # Enregistrer la ligne de passage — upsert MANUEL (ON CONFLICT retiré :
        # incompatible avec la contrainte DEFERRABLE ; sérialisé par le verrou).
        cur_up = q(conn, """
            UPDATE liste_passage SET
                nickname      = %s,
                date_bouffage = %s,
                membre_id     = %s,
                soumis_par    = %s
            WHERE tontine_id=%s AND cycle=%s AND ordre=%s
        """, (item["nickname"], item["date_bouffage"], membre_id, wa_admin,
              tontine_id, cycle, item["ordre"]))
        if cur_up.rowcount == 0:
            q(conn, """
                INSERT INTO liste_passage
                    (tontine_id, membre_id, nickname, date_bouffage, cycle, ordre, soumis_par)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (tontine_id, membre_id,
                  item["nickname"], item["date_bouffage"],
                  cycle, item["ordre"], wa_admin))
        nb_ok += 1

    # Mettre à jour nombre_places dans adhesions selon les occurrences réelles
    places_par_membre = {}
    for nick_key, nb in occurrences.items():
        mid = nickname_cache.get(nick_key)
        if mid:
            if mid not in places_par_membre:
                places_par_membre[mid] = 0
            places_par_membre[mid] += nb

    for membre_id, nb_places in places_par_membre.items():
        q(conn, """UPDATE adhesions SET nombre_places=%s
                   WHERE membre_id=%s AND tontine_id=%s""",
          (nb_places, membre_id, tontine_id))

    conn.commit()
    release_conn(conn)

    # Construire résumé des places multiples pour log
    multi = {k: v for k, v in occurrences.items() if v > 1}
    if multi:
        log.info(f"Places multiples détectées : {multi}")
        log_audit("PLACES_MULTIPLES",
                  f"Tontine {tontine_id} — {multi}", wa_admin)

    log_audit("LISTE_PASSAGE",
              f"Tontine {tontine_id} — {nb_ok} entrées, {nb_non_lies} non liées",
              wa_admin)
    return nb_ok, nb_non_lies


# ══════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("barack_corp.log",    encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log   = logging.getLogger("BarackCorp")
audit = logging.getLogger("Audit")
audit.addHandler(logging.FileHandler("audit_securite.log", encoding="utf-8"))
audit.setLevel(logging.WARNING)
from audit import log_audit, _masquer_wa   # Journal d'audit (module audit.py)

# ══════════════════════════════════════════════════════════════════════════
# ÉTAT EN MÉMOIRE
# ══════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES — POSTGRESQL + POOL DE CONNEXIONS
# ══════════════════════════════════════════════════════════════════════════
#
# Le pool (ThreadedConnectionPool 10 à 80 connexions), le disjoncteur et les
# helpers de requête vivent dans db.py ; le bot lui passe ses PG_* une fois.
# Chaque thread prend une connexion et la rend aussitôt avec release_conn().
# ══════════════════════════════════════════════════════════════════════════

from db import (                     # Accès PostgreSQL : pool, disjoncteur, helpers (module db.py)
    configurer as _configurer_db, init_pool, fermer_pool, reinitialiser_pool, pool_actif,
    get_conn, q, release_conn, fetchone, fetchall, verrou_ordre_tontine,
)

_configurer_db(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS)


from schema import init_db   # Schéma PostgreSQL : tables, migrations, index (module schema.py)


# ══════════════════════════════════════════════════════════════════════════
# FONCTIONS MÉTIER — CRÉATION TONTINE / INSCRIPTION MEMBRES
# ══════════════════════════════════════════════════════════════════════════


def inscrire_dans_tontine(membre_id: int, tontine_id: int,
                          nb_places: int = 1) -> None:
    """
    Inscrit un membre dans une tontine et l'ajoute à la liste de passage.
    Vérifie : capacité max, doublon.
    """
    conn    = get_conn()
    membre  = fetchone(conn,
        "SELECT nom_complet FROM membres WHERE id=%s", (membre_id,))
    if not membre:
        release_conn(conn)
        raise ValueError(f"Membre {membre_id} introuvable.")

    tontine = fetchone(conn,
        "SELECT capacite_max, cycle_actuel, nom FROM tontines WHERE id=%s", (tontine_id,))
    if not tontine:
        release_conn(conn)
        raise ValueError(f"Tontine {tontine_id} introuvable.")

    nb_actuel = fetchone(conn,
        "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
        (tontine_id,))["n"]
    if nb_actuel + nb_places > tontine["capacite_max"]:
        release_conn(conn)
        raise ValueError(
            f"Tontine pleine : {nb_actuel}/{tontine['capacite_max']} membres.")

    # Adhésion
    q(conn, """
        INSERT INTO adhesions (membre_id, tontine_id, nombre_places)
        VALUES (%s,%s,%s)
        ON CONFLICT (membre_id, tontine_id)
        DO UPDATE SET nombre_places=EXCLUDED.nombre_places, statut='Actif'
    """, (membre_id, tontine_id, nb_places))

    # Ajouter à la liste de passage
    cycle     = tontine["cycle_actuel"]
    ordre_max = fetchone(conn,
        "SELECT COALESCE(MAX(ordre),0) m FROM liste_passage WHERE tontine_id=%s AND cycle=%s",
        (tontine_id, cycle))["m"]
    for i in range(nb_places):
        ordre_max += 1
        q(conn, """
            INSERT INTO liste_passage (tontine_id, membre_id, cycle, ordre)
            VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
        """, (tontine_id, membre_id, cycle, ordre_max))

    conn.commit()
    release_conn(conn)
    log.info(f"✅ Membre {membre_id} → tontine {tontine_id} ({nb_places} place(s))")
    log_audit("INSCRIPTION_TONTINE",
              f"Membre {membre_id} → {tontine['nom']} ({nb_places}p)")


def enregistrer_admins_groupe(tontine_id: int, admins: list) -> None:
    """
    Enregistre les admins WhatsApp d'un groupe en base.
    admins = [{"whatsapp": "+237...", "nom": "Jean"}, ...]
    Appelé automatiquement quand le bot rejoint un groupe.
    """
    if not admins:
        return
    conn = get_conn()
    for a in admins:
        wa  = normaliser_numero(a.get("whatsapp", ""))
        nom = a.get("nom", "")
        if not wa:
            continue
        q(conn, """
            INSERT INTO admins_groupe (tontine_id, whatsapp, nom)
            VALUES (%s,%s,%s)
            ON CONFLICT (tontine_id, whatsapp) DO UPDATE SET nom=%s
        """, (tontine_id, wa, nom, nom))
    conn.commit()
    release_conn(conn)
    log.info(f"Admins enregistrés tontine {tontine_id}: {[a.get('whatsapp') for a in admins]}")
    log_audit("ADMINS_ENREGISTRES",
              f"Tontine {tontine_id} — {len(admins)} admin(s)")


# ══════════════════════════════════════════════════════════════════════════
# AUDIT
# ══════════════════════════════════════════════════════════════════════════

def detecter_url_publique() -> str:
    """
    Détecte l'URL publique ngrok via l'API locale (port 4040).
    Si NGROK_DOMAIN est configuré → utilise directement le domaine statique.
    Appelé au démarrage. Met à jour PUBLIC_URL global.
    """
    global PUBLIC_URL

    # Domaine statique configuré → URL connue d'avance, pas besoin de détecter
    if NGROK_DOMAIN:
        PUBLIC_URL = f"https://{NGROK_DOMAIN}"
        log.info(f"🌐 URL publique (statique) : {PUBLIC_URL}")
        return PUBLIC_URL

    # Tentative de détection via l'API ngrok locale
    for tentative in range(10):
        try:
            resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=3)
            tunnels = resp.json().get("tunnels", [])
            for t in tunnels:
                if t.get("proto") == "https":
                    PUBLIC_URL = t["public_url"]
                    log.info(f"🌐 URL publique (dynamique) : {PUBLIC_URL}")
                    return PUBLIC_URL
        except Exception:
            pass
        time_module.sleep(2)

    log.warning("⚠️ URL ngrok non détectée après 10 tentatives.")
    return ""


def tester_connexion_postgresql() -> bool:
    """
    Vérifie que PostgreSQL est accessible et que la base existe.
    Retourne True si OK, False sinon.
    Appelé au démarrage avant init_db().
    """
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DB,
            user=PG_USER, password=PG_PASS,
            connect_timeout=5
        )
        conn.close()
        log.info(f"✅ PostgreSQL OK — Base '{PG_DB}' accessible.")
        return True
    except psycopg2.OperationalError as e:
        erreur = str(e).strip()
        if f'database "{PG_DB}" does not exist' in erreur:
            log.warning(f"⚠️ La base '{PG_DB}' n'existe pas. Tentative de création...")
            return creer_base_postgresql()
        else:
            log.error(f"❌ PostgreSQL inaccessible : {erreur}")
            log.error(f"   Vérifiez : PG_HOST={PG_HOST}, PG_PORT={PG_PORT}, "
                      f"PG_USER={PG_USER}, PG_PASS=***")
            return False


def creer_base_postgresql() -> bool:
    """
    Crée la base de données barack_corp si elle n'existe pas.
    Se connecte à la base 'postgres' (toujours présente) pour créer.
    """
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname="postgres",
            user=PG_USER, password=PG_PASS,
            connect_timeout=5
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE {PG_DB} ENCODING 'UTF8'")
        conn.close()
        log.info(f"✅ Base '{PG_DB}' créée avec succès.")
        return True
    except Exception as e:
        log.error(f"❌ Impossible de créer la base '{PG_DB}' : {e}")
        log.error(f"   Créez-la manuellement : psql -U postgres -c "
                  f"\"CREATE DATABASE {PG_DB};\"")
        return False


# ══════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════

def normaliser_numero(numero: str) -> str:
    """
    Normalise un numéro WhatsApp au format +237XXXXXXXXX.
    Gère tous les formats envoyés par Meta :
      - Format Meta : 237690123456 → +237690123456
      - Device suffix: 237690123456:12@s.whatsapp.net → +237690123456
      - Préfixe 00   : 00237690123456               → +237690123456
      - Court 9 ch.  : 690123456                    → +237690123456
      - Déjà bon     : +237690123456                → +237690123456
    """
    if not numero:
        return numero
    n = numero.strip()
    # Extraire avant @ (au cas où JID complet)
    if "@" in n:
        n = n.split("@")[0]
    # Extraire avant : (device suffix éventuel)
    if ":" in n:
        n = n.split(":")[0]
    # Supprimer tout sauf chiffres et +
    n = re.sub(r"[^\d+]", "", n)
    # Préfixe 00 → +
    if n.startswith("00"):
        n = "+" + n[2:]
    # Ajouter + si absent
    if not n.startswith("+"):
        n = "+" + n
    # Numéro court camerounais sans indicatif (6/7/8/9XXXXXXXX)
    if re.match(r"^\+[6-9]\d{8}$", n):
        n = "+237" + n[1:]
    return n


def valider_numero_cameroun(numero: str) -> bool:
    return bool(re.match(r"^\+237[26789]\d{8}$", numero))


# ══════════════════════════════════════════════════════════════════════════
# COUCHE D'INTERNATIONALISATION — Multi-pays / Multi-devise
# ══════════════════════════════════════════════════════════════════════════

# Cache pays en mémoire (évite de hit la DB à chaque message)


def get_pays(code: str = "CM") -> dict:
    """Retourne la config pays depuis COUNTRY_CONFIG (plus de table DB)."""
    cfg = COUNTRY_CONFIG.get(code, COUNTRY_CONFIG[DEFAULT_COUNTRY])
    return {
        "code":         code,
        "nom":          cfg.get("name",         "Cameroun"),
        "devise":       cfg.get("currency",      "FCFA"),
        "indicatif":    cfg.get("phone_prefix",  "+237"),
        "longueur_num": 9,
        "langue":       cfg.get("language",      "fr"),
        "timezone":     cfg.get("timezone",      "Africa/Douala"),
        "momo_ops":     [],
        "regulateur":   cfg.get("regulator",     "COBAC"),
    }


def format_montant(montant: int, pays_code: str = "CM") -> str:
    """
    Formate un montant avec la devise du pays.
    format_montant(5000, "CM")  → "5 000 FCFA"
    format_montant(5000, "SN")  → "5 000 FCFA"
    format_montant(5000, "CI")  → "5 000 FCFA"
    """
    devise = get_pays(pays_code).get("devise", "FCFA")
    return f"{montant:,} {devise}".replace(",", " ")


def calculer_ira_mise(montant_mise: int, dt_paiement: datetime,
                      dt_echeance: datetime) -> dict:
    """
    IRA v2 indexée sur la mise (falaise 15% + 2%/jour, plafond 50%, plancher 200).
    Barème pur délégué à ira_engine — calcul côté serveur, incontournable.

    Args:
        montant_mise : montant de la cotisation attendue (la mise), FCFA.
        dt_paiement  : datetime du paiement effectif (aware WAT).
        dt_echeance  : échéance DATÉE de la période due (heure limite du jour de
                       collecte, AVANT la grâce). Calculée par
                       ira_engine.echeance_periode — jamais reconstruite ici sur
                       le jour de soumission (c'était la racine des findings #1/#2).
    """
    return ira_engine.calculer_ira(
        montant_mise, dt_paiement, dt_echeance,
        cliff=IRA_CLIFF_PCT, daily=IRA_DAILY_PCT, hourly=IRA_HOURLY_PCT,
        cap=IRA_CAP_PCT, floor=IRA_FLOOR, grace_min=IRA_GRACE_MIN,
    )


def get_membre_by_wa(wa: str) -> Optional[dict]:
    wa   = normaliser_numero(wa)
    conn = get_conn()
    m    = fetchone(conn, "SELECT * FROM membres WHERE whatsapp=%s", (wa,))
    release_conn(conn)
    return m


def get_tontines_admin(wa: str) -> list:
    n    = normaliser_numero(wa)
    conn = get_conn()
    rows = fetchall(conn,
        "SELECT tontine_id FROM admins_groupe WHERE whatsapp=%s", (n,))
    release_conn(conn)
    return [r["tontine_id"] for r in rows]


def est_owner(wa: str) -> bool:
    return normaliser_numero(wa) == normaliser_numero(OWNER_WA)


def est_admin(wa: str) -> bool:
    return bool(get_tontines_admin(wa)) or est_owner(wa)


def incrementer_tentatives_fraude(membre_id: int, raison: str):
    conn = get_conn()
    try:
        q(conn,
          "UPDATE membres SET tentatives_fraude=tentatives_fraude+1 WHERE id=%s",
          (membre_id,))
        row = fetchone(conn,
            "SELECT tentatives_fraude, nom_complet, whatsapp FROM membres WHERE id=%s",
            (membre_id,))
        conn.commit()
        log_audit("TENTATIVE_FRAUDE",
                  f"{row['nom_complet']} — {raison} — #{row['tentatives_fraude']}",
                  row["whatsapp"])
        if row["tentatives_fraude"] >= MAX_TENTATIVES_FRAUDE:
            q(conn,
              "UPDATE membres SET statut_global='Banni', blackliste=1 WHERE id=%s",
              (membre_id,))
            _update_score_confiance(conn, membre_id, set_val=0, raison="Bannissement automatique — fraude")
            q(conn, """INSERT INTO sanctions (membre_id, type_sanction, notes)
                       VALUES (%s,'Blocage_permanent',%s)""",
              (membre_id, f"Auto-bannissement après {row['tentatives_fraude']} tentatives"))
            conn.commit()
            log_audit("BANNISSEMENT_AUTO", f"{row['nom_complet']} banni", row["whatsapp"])
            wa_admin(
                f"🚨 *MEMBRE BANNI — BADF Ltd*\n"
                f"👤 {row['nom_complet']} | {row['whatsapp']}\n"
                f"Raison : {row['tentatives_fraude']} tentatives de fraude\n"
                f"Dernière : {raison}"
            )
            wa_prive(row["whatsapp"], msg_dissuasion(row["whatsapp"]))
        _alerter_burst_fraude()
    finally:
        release_conn(conn)


def _alerter_burst_fraude():
    """DE — Alerte owner si ≥5 tentatives fraude sur n'importe quel membre en 1h (attaque coordonnée)."""
    try:
        conn = get_conn()
        row = fetchone(conn, """
            SELECT COUNT(*) as nb FROM audit_log
            WHERE type_event = 'TENTATIVE_FRAUDE'
            AND date_heure > NOW() - INTERVAL '1 hour'
        """)
        release_conn(conn)
        if row and row["nb"] >= 5:
            wa_owner(
                f"🚨 *ALERTE COORDONNÉE — BADF Ltd*\n"
                f"{row['nb']} tentatives de fraude détectées en 1h.\n"
                f"Vérifier audit_log immédiatement."
            )
            log_audit("ALERTE_BURST_FRAUDE", f"{row['nb']} tentatives en 1h")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# PAIEMENT MANUEL — Screenshot + Confirmation Admin
# ══════════════════════════════════════════════════════════════════════════


def hash_screenshot(image_bytes: bytes) -> str:
    """SHA-256 du contenu brut du screenshot — anti-recyclage."""
    return hashlib.sha256(image_bytes).hexdigest()


def screenshot_deja_utilise(conn, image_hash: str) -> bool:
    """Retourne True si ce screenshot a déjà été soumis."""
    row = fetchone(conn,
        "SELECT id FROM screenshots_hash WHERE hash=%s", (image_hash,))
    return row is not None


def enregistrer_screenshot(conn, image_hash: str,
                            membre_id: int, tontine_id: int, dhash_hex: str = ""):
    """Enregistre le hash brut + le perceptual hash (dHash) pour l'anti-recyclage.

    Écrit dans la transaction de l'appelant (enregistrer_cotisation_manuelle,
    avant son commit). Un échec est isolé dans un SAVEPOINT : il est logué sans
    annuler la cotisation en cours.
    """
    try:
        q(conn, "SAVEPOINT anti_recyclage_hash")
        q(conn,
          """INSERT INTO screenshots_hash (hash, membre_id, tontine_id, dhash)
             VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
          (image_hash, membre_id, tontine_id, dhash_hex or None))
        q(conn, "RELEASE SAVEPOINT anti_recyclage_hash")
    except Exception as e:
        # ON CONFLICT DO NOTHING absorbe déjà le cas bénin (hash déjà vu) sans
        # jamais lever — donc toute exception ici est anormale (DB down, FK
        # invalide, etc.) ET signifie que le hash n'a PAS été enregistré :
        # l'anti-recyclage est silencieusement désactivé pour ce screenshot.
        # Avaler l'erreur sans trace rendait ça indétectable — on logue.
        log.error(f"❌ enregistrer_screenshot échec (anti-recyclage NON enregistré) "
                  f"membre={membre_id} tontine={tontine_id}: {e}")
        try:
            q(conn, "ROLLBACK TO SAVEPOINT anti_recyclage_hash")
        except Exception:
            pass


def reference_deja_utilisee(conn, tontine_id: int, ref_norm: str) -> bool:
    """
    True si cette référence de transaction a déjà servi dans la tontine.
    Couche anti-recyclage FORTE : la référence est unique par paiement réel, donc
    insensible au re-encodage/1px qui défait le byte-hash (finding #3).
    """
    if not ref_norm:
        return False
    row = fetchone(conn,
        "SELECT id FROM references_transaction WHERE tontine_id=%s AND reference_norm=%s",
        (tontine_id, ref_norm))
    return row is not None


def enregistrer_reference(conn, tontine_id: int, ref_norm: str, membre_id: int):
    """Mémorise une référence de transaction (dédup ON CONFLICT via UNIQUE),
    dans la transaction de l'appelant ; un échec est isolé par SAVEPOINT."""
    if not ref_norm:
        return
    try:
        q(conn, "SAVEPOINT anti_recyclage_ref")
        q(conn,
          """INSERT INTO references_transaction (tontine_id, reference_norm, membre_id)
             VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
          (tontine_id, ref_norm, membre_id))
        q(conn, "RELEASE SAVEPOINT anti_recyclage_ref")
    except Exception as e:
        log.error(f"❌ enregistrer_reference échec (anti-recyclage NON enregistré) "
                  f"membre={membre_id} tontine={tontine_id}: {e}")
        try:
            q(conn, "ROLLBACK TO SAVEPOINT anti_recyclage_ref")
        except Exception:
            pass


def screenshot_near_duplicate(conn, membre_id: int, dh_int) -> bool:
    """
    Signal SOUPLE : True si un screenshot perceptuellement identique (dHash à
    <= SEUIL bits) existe déjà pour ce membre. Sert d'ALERTE admin, jamais de
    blocage dur — deux reçus MoMo distincts partagent le même template et sont
    donc naturellement proches en dHash (faux positif inacceptable en blocage).
    """
    if dh_int is None:
        return False
    rows = fetchall(conn,
        """SELECT dhash FROM screenshots_hash
           WHERE membre_id=%s AND dhash IS NOT NULL AND dhash <> ''
           ORDER BY created_at DESC LIMIT 200""",
        (membre_id,))
    for r in rows:
        autre = si.hex_vers_int(r["dhash"])
        if si.est_near_duplicate(dh_int, autre):
            return True
    return False


class MontantAberrantError(ValueError):
    """Levée quand le montant saisi est trop éloigné du montant attendu."""
    pass


def enregistrer_cotisation_manuelle(conn, membre_id: int, tontine_id: int,
                                     montant: int, screenshot_hash: str,
                                     admin_wa: str,
                                     force: bool = False,
                                     dhash_hex: str = "",
                                     ref_norm: str = "") -> int:
    """
    Enregistre une cotisation manuelle en attente de confirmation admin.
    Valide la cohérence du montant avant insertion (PATCH 2 v9.18).

    Args:
        force: Si True, accepte le montant même si écart 15-50%
               (admin confirme explicitement via commande FORCE).

    Raises:
        MontantAberrantError: Si écart > 50% du montant attendu, OU
                              si écart 15-50% sans force=True.
        ValueError: Si tontine introuvable ou montant <= 0.
        psycopg2.errors.UniqueViolation: Si screenshot_hash déjà utilisé
                                          (filet DB du PATCH 3).

    Returns:
        ID de la cotisation créée.
    """
    # ── Validations basiques ──────────────────────────────────────────
    if montant <= 0:
        raise ValueError(f"Montant invalide: {montant} (doit être > 0)")

    # ── Récupérer le montant attendu de la tontine ────────────────────
    tontine = fetchone(conn,
        "SELECT id, nom, montant_place FROM tontines WHERE id=%s",
        (tontine_id,))

    if not tontine:
        raise ValueError(f"Tontine #{tontine_id} introuvable")

    # Un membre peut détenir plusieurs places → l'attendu doit être mis à l'échelle,
    # sinon tout membre multi-places déclenche un écart artificiel (jusqu'à 100%)
    # et se fait rejeter à tort par MontantAberrantError.
    adhesion_places = fetchone(conn,
        "SELECT nombre_places FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
        (membre_id, tontine_id))
    nb_places = (adhesion_places["nombre_places"] if adhesion_places else 1) or 1
    montant_attendu = tontine["montant_place"] * nb_places

    # ── Calculer l'écart relatif ──────────────────────────────────────
    if montant_attendu > 0:
        ecart_pct = abs(montant - montant_attendu) / montant_attendu * 100
    else:
        ecart_pct = 0

    # ── Règles de validation ──────────────────────────────────────────
    if ecart_pct > 50:
        log_audit("COTISATION_REJETEE_ABERRANTE",
                  f"Membre#{membre_id} | Tontine#{tontine_id} ({tontine['nom']}) | "
                  f"Saisie:{montant:,} | Attendu:{montant_attendu:,} | "
                  f"Ecart:{ecart_pct:.1f}% | Admin:{admin_wa}")
        raise MontantAberrantError(
            f"❌ *Montant aberrant détecté.*\n\n"
            f"Saisie : *{montant:,} FCFA*\n"
            f"Attendu pour {tontine['nom']} : *{montant_attendu:,} FCFA*\n"
            f"Écart : {ecart_pct:.1f}%\n\n"
            f"_Vérifier la saisie. Si vraiment correct, contacter BADF._"
        )

    elif ecart_pct > 15 and not force:
        log_audit("COTISATION_ECART_IMPORTANT",
                  f"Membre#{membre_id} | Saisie:{montant:,} | "
                  f"Attendu:{montant_attendu:,} | Ecart:{ecart_pct:.1f}%")
        raise MontantAberrantError(
            f"⚠️ *Écart important détecté*\n\n"
            f"Saisie : *{montant:,} FCFA*\n"
            f"Attendu : *{montant_attendu:,} FCFA*\n"
            f"Écart : {ecart_pct:.1f}%\n\n"
            f"_Pour confirmer quand même, taper :_\n"
            f"*FORCE {membre_id} {montant}*"
        )

    elif ecart_pct > 5:
        log_audit("COTISATION_ECART_LEGER",
                  f"Membre#{membre_id} | Saisie:{montant:,} | "
                  f"Attendu:{montant_attendu:,} | Ecart:{ecart_pct:.1f}%")

    # ── Insertion (catch UniqueViolation du filet DB PATCH 3) ────────
    fmp = int(montant * FRAIS_FMP)
    try:
        cur = q(conn, """
            INSERT INTO cotisations_manuelles
                (membre_id, tontine_id, montant_declare, fmp_du, screenshot_hash)
            VALUES (%s,%s,%s,%s,%s) RETURNING id
        """, (membre_id, tontine_id, montant, fmp, screenshot_hash))
        cotis_id = cur.fetchone()[0]

        # Créer la dette FMP pour l'admin → à reverser à BADF. Aucune dette
        # quand la FMP vaut 0 (lancement sans frais) : rien n'est dû.
        if fmp > 0:
            q(conn, """
                INSERT INTO dettes_badf (admin_wa, tontine_id, type_dette, montant, ref_cotis)
                VALUES (%s,%s,'FMP',%s,%s)
            """, (admin_wa, tontine_id, fmp, cotis_id))

        # Anti-recyclage dans la MÊME transaction que la cotisation : écrits
        # avant le commit, sinon release_conn les annule (rollback).
        if screenshot_hash:
            enregistrer_screenshot(conn, screenshot_hash, membre_id, tontine_id, dhash_hex)
        enregistrer_reference(conn, tontine_id, ref_norm, membre_id)

        conn.commit()
        return cotis_id

    except psycopg2.errors.UniqueViolation as e:
        conn.rollback()
        log_audit("DOUBLON_COTISATION_BLOQUE_DB",
                  f"Membre#{membre_id} | Tentative doublon: {str(e)[:100]}")
        raise ValueError(
            "❌ Cette cotisation existe déjà (doublon détecté par la base)."
        )


def _incrementer_slip_cycle(conn, membre_id: int, tontine_id: int, cycle: int) -> int:
    """
    Enregistre un slip (retard hors grâce) pour ce membre/cycle et retourne le
    nombre de slips AVANT celui-ci (0 => ce slip est le mulligan).

    Upsert atomique : la ligne ponctualite_cycle pilote l'éligibilité bonus et la
    priorité de rotation du cycle suivant (voir ira_engine.eligibilite_apres_slip).
    """
    row = fetchone(conn, """
        INSERT INTO ponctualite_cycle (membre_id, tontine_id, cycle, slips, eligible)
        VALUES (%s, %s, %s, 1, TRUE)
        ON CONFLICT (membre_id, tontine_id, cycle)
        DO UPDATE SET slips      = ponctualite_cycle.slips + 1,
                      eligible   = (ponctualite_cycle.slips + 1) <= 1,
                      updated_at = NOW()
        RETURNING slips
    """, (membre_id, tontine_id, cycle))
    slips_apres = row["slips"] if row else 1
    return slips_apres - 1


def _reclasser_en_dernier(conn, membre_id: int, tontine_id: int) -> dict:
    """
    Paiement après heure_limite → déplace le membre en dernière position
    dans liste_passage (cycle actif, statut En_attente ou Notifie).
    Retourne {"ok": True, "position_avant": N, "position_apres": M}
    ou {"ok": False} si déjà dernier / déjà bouffé.
    """
    # FORTERESSE : sérialise toute mutation d'ordre de cette tontine (tenu au commit)
    verrou_ordre_tontine(conn, tontine_id)

    passage = fetchone(conn, """
        SELECT id, ordre, cycle FROM liste_passage
        WHERE membre_id=%s AND tontine_id=%s
          AND statut IN ('En_attente','Notifie')
        ORDER BY cycle DESC LIMIT 1
    """, (membre_id, tontine_id))

    if not passage:
        return {"ok": False}

    cycle        = passage["cycle"]
    ordre_actuel = passage["ordre"]

    max_row = fetchone(conn,
        "SELECT MAX(ordre) AS max_ordre FROM liste_passage WHERE tontine_id=%s AND cycle=%s",
        (tontine_id, cycle))
    max_ordre = (max_row["max_ordre"] or ordre_actuel) if max_row else ordre_actuel

    if ordre_actuel >= max_ordre:
        return {"ok": False}  # déjà dernier

    # Sortir d'abord le membre vers une position sentinelle (-1) pour libérer son slot :
    # sans ça, le décalage `ordre-1` collisionne sur UNIQUE(tontine,cycle,ordre) tant que
    # le membre occupe encore ordre_actuel (doublon transitoire = UniqueViolation).
    q(conn, "UPDATE liste_passage SET ordre=-1 WHERE id=%s", (passage["id"],))

    # Décaler les membres entre l'ancienne position+1 et la dernière de -1 (slot libéré)
    q(conn, """
        UPDATE liste_passage SET ordre = ordre - 1
        WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND ordre <= %s
    """, (tontine_id, cycle, ordre_actuel, max_ordre))

    # Mettre le membre en dernière position (slot max_ordre désormais vacant)
    q(conn, "UPDATE liste_passage SET ordre=%s WHERE id=%s", (max_ordre, passage["id"]))
    return {"ok": True, "position_avant": ordre_actuel, "position_apres": max_ordre}


def confirmer_cotisation(conn, cotis_id: int, admin_wa: str) -> dict:
    """
    Admin confirme une cotisation manuelle.
    Met à jour le registre et notifie le membre.

    PATCH 1 v9.18 : SELECT FOR UPDATE pour verrou pessimiste PostgreSQL.
    Empêche la race condition si l'admin clique plusieurs fois rapidement.
    """
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # ── VERROU PESSIMISTE : SELECT FOR UPDATE ─────────────────────
        # La ligne est verrouillée pour les autres transactions jusqu'au COMMIT.
        # Si un autre admin essaye SELECT FOR UPDATE sur la même ligne,
        # il attend que cette transaction finisse.
        cur.execute(
            "SELECT * FROM cotisations_manuelles WHERE id=%s FOR UPDATE",
            (cotis_id,)
        )
        cotis = cur.fetchone()

        if not cotis:
            conn.rollback()
            return {"ok": False, "msg": "Cotisation introuvable."}

        if cotis["statut"] != "En_attente":
            # Idempotence : si déjà confirmée, on retourne un message clair
            conn.rollback()
            return {"ok": False, "msg": f"Déjà traitée ({cotis['statut']})."}

        # ── UPDATE (le verrou est toujours actif jusqu'au commit) ─────
        cur.execute("""
            UPDATE cotisations_manuelles
            SET statut='Confirme', confirme_par=%s, date_confirmation=NOW()
            WHERE id=%s
        """, (admin_wa, cotis_id))

        # ── INSERT dans transactions ──────────────────────────────────
        cur.execute("""
            INSERT INTO transactions
                (membre_id, tontine_id, montant_brut, frais_fmp,
                 montant_net, type_transaction, statut)
            VALUES (%s,%s,%s,%s,%s,'Cotisation','Confirmee')
        """, (cotis["membre_id"], cotis["tontine_id"],
              cotis["montant_declare"], cotis["fmp_du"],
              cotis["montant_declare"] - cotis["fmp_du"]))

        conn.commit()  # Le verrou est libéré ici

    except Exception as e:
        try:    conn.rollback()
        except: pass
        log.error(f"❌ confirmer_cotisation #{cotis_id} échec: {e}")
        return {"ok": False, "msg": f"Erreur technique: {str(e)[:80]}"}
    finally:
        cur.close()

    # ── Notifier le membre (hors transaction) ─────────────────────────
    membre = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s",
        (cotis["membre_id"],))
    tontine = fetchone(conn,
        "SELECT id, nom, heure_limite, heure_ouverture, montant_place, cycle_actuel, "
        "       type_tontine, jour_semaine, jour_mois "
        "FROM tontines WHERE id=%s", (cotis["tontine_id"],))

    if membre and tontine:
        wa_prive(membre["whatsapp"],
            f"✅ *COTISATION CONFIRMÉE — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Bonjour *{membre['nom_complet']}*,\n\n"
            f"Votre cotisation de *{cotis['montant_declare']:,} FCFA* "
            f"a été confirmée et enregistrée.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    # ── Reclassement : retard mesuré contre l'ÉCHÉANCE DATÉE de la période due ──
    #    (pas l'heure limite du jour de soumission — cf. ira_engine.echeance_periode).
    #    Gate ET calcul IRA partagent la MÊME échéance → impossible de diverger.
    try:
        from datetime import timezone, timedelta as _td
        WAT   = timezone(_td(hours=1))            # tz de collecte (Africa/Douala, +1)
        h_lim = (tontine or {}).get("heure_limite") or "18:00"
        h_ouv = (tontine or {}).get("heure_ouverture") or "05:00"
        try:
            _lh, _lm = map(int, h_lim.split(":")); t_limite = time(_lh, _lm)
        except Exception:
            t_limite = HEURE_LIMITE_DEF
        try:
            _oh, _om = map(int, h_ouv.split(":")); t_ouverture = time(_oh, _om)
        except Exception:
            t_ouverture = time(5, 0)

        soumis_dt    = cotis["date_soumission"].astimezone(WAT)
        soumis_local = soumis_dt.time()  # conservé pour l'affichage groupe/audit
        dt_echeance  = ira_engine.echeance_periode(
            soumis_dt,
            lambda d: _est_jour_collecte(tontine, d),
            t_ouverture, t_limite,
        )
        grace_fin = dt_echeance + _td(minutes=IRA_GRACE_MIN)

        if soumis_dt > grace_fin:
            # 1. IRA v2 indexée sur la mise — collecte À LA SOURCE (à ce paiement).
            #    La mise réelle inclut nombre_places : un membre à 3 places retient
            #    3× plus d'argent en retard, la pénalité doit suivre.
            _places = fetchone(conn,
                "SELECT nombre_places FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
                (cotis["membre_id"], cotis["tontine_id"]))
            mise    = tontine["montant_place"] * ((_places["nombre_places"] if _places else 1) or 1)
            cycle   = tontine["cycle_actuel"]
            calc_ira  = calculer_ira_mise(mise, soumis_dt, dt_echeance)
            montant_ira = calc_ira["ira"]
            motif_ira = (
                f"Retard cotisation — payé {soumis_dt.strftime('%d/%m %H:%M')} "
                f"> échéance {dt_echeance.strftime('%d/%m %H:%M')} "
                f"| J+{calc_ira['jours_retard']}"
                + (" | plafond 50%" if calc_ira["plafonnee"] else "")
            )
            q(conn, """INSERT INTO dettes_ira (membre_id, tontine_id, montant, motif)
                       VALUES (%s, %s, %s, %s)""",
              (cotis["membre_id"], cotis["tontine_id"], montant_ira, motif_ira))
            conn.commit()

            # 1bis. Tracking mulligan/éligibilité (1 slip réseau toléré/cycle).
            slips_avant = _incrementer_slip_cycle(conn, cotis["membre_id"],
                                                  cotis["tontine_id"], cycle)
            elig = ira_engine.eligibilite_apres_slip(slips_avant)
            conn.commit()

            # 2. Reclassement en dernière position — SEULEMENT à l'éjection (2e slip).
            #    Le mulligan (1er slip) préserve la position ; le cliff IRA suffit.
            reclasse = {"ok": False}
            if elig["ejecte"]:
                reclasse = _reclasser_en_dernier(conn, cotis["membre_id"], cotis["tontine_id"])
                if reclasse["ok"]:
                    conn.commit()

            # 3. Notification membre (IRA indexée + statut mulligan/éjection)
            if membre and tontine:
                pct_cliff = int(IRA_CLIFF_PCT * 100)
                msg_retard = (
                    f"⚠️ *COTISATION EN RETARD — {tontine['nom']}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Votre cotisation a été enregistrée ✅, mais elle est "
                    f"arrivée après l'heure limite (*{h_lim}*).\n\n"
                    f"📌 *Conséquences :*\n"
                    f"• Pénalité IRA : *{montant_ira:,} FCFA* ajoutée à votre dette "
                    f"({pct_cliff}% de la mise"
                    + (f" +{int(IRA_DAILY_PCT*100)}%/jour" if calc_ira["jours_retard"] else "")
                    + (", *plafond atteint*" if calc_ira["plafonnee"] else "") + ")\n"
                )
                if elig["mulligan_utilise"]:
                    msg_retard += (
                        f"• 🎟️ *Retard réseau toléré* (1/cycle) — votre bonus de "
                        f"ponctualité et votre position sont *préservés*. "
                        f"Un 2e retard ce cycle vous éjecte.\n"
                    )
                elif elig["ejecte"]:
                    msg_retard += (
                        f"• ❌ *2e retard du cycle* — éjecté du bonus de ponctualité.\n"
                    )
                if reclasse["ok"]:
                    msg_retard += (
                        f"• Reclassement : *position {reclasse['position_avant']}*"
                        f" → *position {reclasse['position_apres']}*\n"
                    )
                msg_retard += (
                    f"\n💡 Payez avant *{h_lim}* pour éviter ces pénalités.\n\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )
                wa_prive(membre["whatsapp"], msg_retard)

            # 4. Envoyer la liste de bouffage mise à jour au groupe
            tontine_full = fetchone(conn,
                "SELECT id, nom, whatsapp_groupe, cycle_actuel FROM tontines WHERE id=%s",
                (cotis["tontine_id"],))
            if tontine_full and tontine_full.get("whatsapp_groupe") and reclasse["ok"]:
                passages = fetchall(conn, """
                    SELECT lp.ordre, lp.statut, lp.nickname, lp.date_bouffage,
                           m.nom_complet
                    FROM liste_passage lp
                    LEFT JOIN membres m ON m.id = lp.membre_id
                    WHERE lp.tontine_id=%s AND lp.cycle=%s
                    ORDER BY lp.ordre
                """, (tontine_full["id"], tontine_full["cycle_actuel"]))
                lines = [
                    f"🔄 *LISTE DE BOUFFAGE MISE À JOUR — {tontine_full['nom']}*\n"
                    f"_(Reclassement retard — {soumis_local.strftime('%H:%M')} > {h_lim})_\n"
                ]
                for p in passages:
                    s   = {"Paye": "✅", "En_attente": "⏳", "Notifie": "🔔",
                           "Intercepte": "🚫", "Cede": "🔄"}.get(p["statut"], "❓")
                    nom = p["nom_complet"] or p["nickname"] or "???"
                    dt  = f"  📅 {p['date_bouffage']}" if p["date_bouffage"] else ""
                    lines.append(f"{str(p['ordre']).zfill(2)}- {s} {nom}{dt}")
                wa_groupe(tontine_full["whatsapp_groupe"], "\n".join(lines))

            pos_log = (f"position {reclasse['position_avant']} → position {reclasse['position_apres']}"
                       if reclasse["ok"] else "position préservée (mulligan)")
            log_audit("RETARD_COTISATION",
                      f"Membre#{cotis['membre_id']} | {pos_log} | IRA {montant_ira} FCFA "
                      f"(J+{calc_ira['jours_retard']}{', cap' if calc_ira['plafonnee'] else ''}) | "
                      f"slip#{elig['slips_apres']} {'ÉJECTÉ' if elig['ejecte'] else 'toléré'} | "
                      f"Tontine#{cotis['tontine_id']} | Soumis:{soumis_local.strftime('%H:%M')} > {h_lim}+5min")
    except Exception as _re:
        log.warning(f"⚠️ Reclassement retard non bloquant : {_re}")

    _verifier_credit_comm(conn, cotis["tontine_id"])
    log_audit("COTISATION_CONFIRMEE",
              f"Cotis#{cotis_id} | Membre#{cotis['membre_id']} | "
              f"Tontine#{cotis['tontine_id']} | Admin:{admin_wa}")
    return {"ok": True, "msg": "✅ Cotisation confirmée."}


def rejeter_cotisation(conn, cotis_id: int, admin_wa: str,
                        raison: str = "") -> dict:
    """Admin rejette une cotisation (screenshot incorrect ou non reçu).

    SELECT FOR UPDATE + vérif statut='En_attente' : même verrou pessimiste et
    idempotence que confirmer_cotisation. Sans ça, un admin qui rejette une
    cotisation déjà confirmée (double-tap, session admin périmée) écrase le
    statut sans toucher la ligne `transactions` déjà créée par la confirmation
    → contradiction permanente dans le grand livre.
    """
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM cotisations_manuelles WHERE id=%s FOR UPDATE",
            (cotis_id,))
        cotis = cur.fetchone()
        if not cotis:
            conn.rollback()
            return {"ok": False, "msg": "Cotisation introuvable."}

        if cotis["statut"] != "En_attente":
            # Idempotence : si déjà confirmée/rejetée, on retourne un message clair
            conn.rollback()
            return {"ok": False, "msg": f"Déjà traitée ({cotis['statut']})."}

        cur.execute("""UPDATE cotisations_manuelles
                   SET statut='Rejete', confirme_par=%s, date_confirmation=NOW()
                   WHERE id=%s""", (admin_wa, cotis_id))

        # Annuler la dette FMP correspondante
        cur.execute("""UPDATE dettes_badf SET statut='Payee'
                   WHERE ref_cotis=%s AND statut='Due'""", (cotis_id,))

        conn.commit()
    except Exception as e:
        try:    conn.rollback()
        except: pass
        log.error(f"❌ rejeter_cotisation #{cotis_id} échec: {e}")
        return {"ok": False, "msg": f"Erreur technique: {str(e)[:80]}"}
    finally:
        cur.close()

    membre = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s",
        (cotis["membre_id"],))
    if membre:
        wa_prive(membre["whatsapp"],
            f"❌ *COTISATION REJETÉE*\n\n"
            f"Bonjour *{membre['nom_complet']}*,\n\n"
            f"Votre soumission a été rejetée par l'admin.\n"
            f"{f'Raison : {raison}' if raison else ''}\n\n"
            f"Veuillez vérifier que vous avez :\n"
            f"• Payé le bon montant\n"
            f"• Payé sur le bon numéro\n"
            f"• Envoyé le bon screenshot\n\n"
            f"Puis soumettez à nouveau dans le groupe.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
    return {"ok": True, "msg": "❌ Cotisation rejetée."}


def declencher_bouffage_manuel(conn, membre_id: int, tontine_id: int,
                                passage_id: int, montant_brut: int,
                                caution: int,
                                deductions_detail: str = "",
                                caution_reelle: int = None,
                                dettes_ira: list = None) -> int:
    """
    Déclenche le flux de bouffage manuel.
    DM au bénéficiaire pour son numéro MM.
    DM à l'admin avec le montant à virer.
    Retourne l'ID du bouffage créé.

    `caution` = total des déductions (caution + IRA + cotisations manquantes),
    utilisé pour le calcul du montant net. `caution_reelle` = la VRAIE caution
    (celle qui sera restituée en fin de cycle) — si absente ou différente de
    `caution`, le reste (`caution - caution_reelle`) est une pénalité/dette
    NON remboursable et ne doit jamais être présenté comme telle au membre.

    `dettes_ira` = les lignes dettes_ira (id, montant) déduites de ce versement.
    Elles sont liées au versement dans la même transaction : la confirmation ne
    soldera qu'elles, jamais une dette apparue entre-temps.
    """
    if caution_reelle is None:
        caution_reelle = caution  # rétrocompatibilité : rien à distinguer
    caution_non_remboursable = max(0, caution - caution_reelle)
    montant_net = montant_brut - caution
    expiration  = datetime.now() + timedelta(hours=2)
    dettes_ira  = dettes_ira or []
    montant_ira = sum(d["montant"] for d in dettes_ira)

    cur = q(conn, """
        INSERT INTO bouffages_manuels
            (membre_id, tontine_id, passage_id, montant_brut,
             caution, montant_net, expiration, montant_ira)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (membre_id, tontine_id, passage_id,
          montant_brut, caution, montant_net, expiration, montant_ira))
    bouffage_id = cur.fetchone()[0]
    if dettes_ira:
        q(conn, "UPDATE dettes_ira SET bouffage_id=%s WHERE id = ANY(%s) AND statut='Due'",
          (bouffage_id, [d["id"] for d in dettes_ira]))
    conn.commit()

    membre  = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s", (membre_id,))
    tontine = fetchone(conn,
        "SELECT nom FROM tontines WHERE id=%s", (tontine_id,))

    if not membre or not tontine:
        return bouffage_id

    # DM au bénéficiaire — décompte honnête : seule caution_reelle est présentée
    # comme "retenue et restituable". Le reste (IRA, cotisations manquantes) est
    # une dette réglée, jamais un dépôt remboursable — ne jamais dire l'inverse.
    if caution_non_remboursable > 0:
        decompte = (
            f"  Cagnotte brute       : {montant_brut:>10,} FCFA\n"
            f"  Caution (restituable): {caution_reelle:>10,} FCFA\n"
            f"  Pénalités/dettes     : {caution_non_remboursable:>10,} FCFA (non remboursable)\n"
            f"  ─────────────────────────────────\n"
            f"  *MONTANT NET         : {montant_net:>10,} FCFA*\n\n"
            f"*II. DÉTAIL*\n\n"
            + (f"  Caution de *{caution_reelle:,} FCFA* : retenue par l'admin, "
               f"restituée en fin de cycle si vous continuez à cotiser normalement.\n"
               if caution_reelle > 0 else "")
            + f"  Pénalités/dettes de *{caution_non_remboursable:,} FCFA* : réglées sur "
              f"cette cagnotte, ne sont *pas* restituées (IRA de retard et/ou "
              f"cotisations manquantes).\n\n"
        )
    else:
        decompte = (
            f"  Cagnotte brute    : {montant_brut:>10,} FCFA\n"
            f"  Caution retenue   : {caution:>10,} FCFA\n"
            f"  ─────────────────────────────────\n"
            f"  *MONTANT NET      : {montant_net:>10,} FCFA*\n\n"
            f"*II. CAUTION*\n\n"
            f"  La caution de *{caution:,} FCFA* est retenue par l'admin.\n"
            f"  Elle vous sera restituée à la fin du cycle si vous\n"
            f"  continuez à cotiser normalement.\n\n"
        )
    wa_prive(membre["whatsapp"],
        f"🎉 *AVIS DE DÉBLOCAGE DE CAGNOTTE*\n"
        f"*{tontine['nom']}* — BADF Ltd\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*I. DÉCOMPTE FINANCIER*\n\n"
        f"{decompte}"
        f"*III. INSTRUCTIONS*\n\n"
        f"  Envoyez votre numéro Mobile Money, précédé de l'opérateur :\n\n"
        f"    *MTN +237690XXXXXX*\n"
        f"  ou\n"
        f"    *ORANGE +237699XXXXXX*\n\n"
        f"  ⏱ Vous avez *2 heures* pour répondre.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Réf. COBAC R-2019/01 — Barack & AI Development Facilities Ltd_"
    )

    # Notifier les admins
    detail = deductions_detail if deductions_detail else (
        f"  Cagnotte brute : *{montant_brut:,} FCFA*\n"
        f"  Caution        : *-{caution:,} FCFA*\n"
        f"  ─────────────────────────\n"
        f"  *NET À VIRER   : {montant_net:,} FCFA*"
    )
    wa_admins_tontine(tontine_id,
        f"🔔 *BOUFFAGE EN COURS — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Bénéficiaire : *{membre['nom_complet']}*\n\n"
        f"📊 *DÉCOMPTE FINANCIER :*\n"
        f"{detail}\n\n"
        f"En attente du numéro Mobile Money...\n"
        f"⏱ Délai : 2 heures\n\n"
        f"_TontineBot Pro — BADF Ltd_"
    )

    log_audit("BOUFFAGE_DECLENCHE",
              f"Membre#{membre_id} | Tontine#{tontine_id} | Net:{montant_net:,} FCFA")
    return bouffage_id


def traiter_reponse_numero_bouffage(wa: str, texte: str, membre: dict):
    """
    Intercepte la réponse d'un bénéficiaire de bouffage manuel à qui on a
    demandé son numéro Mobile Money (déclenché par declencher_bouffage_manuel).

    Volontairement SANS session en mémoire : le bénéficiaire a 2h pour
    répondre, largement au-delà de SESSION_TIMEOUT (300s) — l'état est lu
    directement dans bouffages_manuels.statut='En_attente'.

    Retourne None si rien à faire (laisser le dispatcher normal continuer),
    ou une chaîne de réponse sinon.
    """
    conn = get_conn()
    try:
        bouffage = fetchone(conn, """
            SELECT bm.*, t.nom AS tontine_nom
            FROM bouffages_manuels bm JOIN tontines t ON t.id = bm.tontine_id
            WHERE bm.membre_id=%s AND bm.statut='En_attente'
            ORDER BY bm.id DESC LIMIT 1
        """, (membre["id"],))
        if not bouffage:
            return None

        match = re.search(r"(\+?237[26789]\d{8}|\b[26789]\d{8}\b)", texte)
        if not match:
            # Ne ressemble pas à une tentative de numéro MM → laisser passer
            # (ex: le membre navigue son menu normalement).
            return None

        tu = texte.upper()
        operateur = None
        if "MTN" in tu:
            operateur = "cm.mtn"
        elif "ORANGE" in tu:
            operateur = "cm.orange"

        n      = match.group(1)
        numero = normaliser_numero(n if "237" in n else "237" + n)
        if not operateur or not valider_numero_cameroun(numero):
            return (
                "❌ Format non reconnu.\nPrécisez l'opérateur, exemples :\n"
                "• *MTN +237690123456*\n• *ORANGE +237699123456*"
            )

        import psycopg2.extras
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("SELECT * FROM bouffages_manuels WHERE id=%s FOR UPDATE", (bouffage["id"],))
            b2 = cur.fetchone()
            if not b2 or b2["statut"] != "En_attente":
                conn.rollback()
                return "ℹ️ Ce bouffage a déjà été traité."
            cur.execute("""UPDATE bouffages_manuels
                           SET statut='Vire', numero_mm=%s, operateur_cashout=%s
                           WHERE id=%s""", (numero, operateur, bouffage["id"]))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log.error(f"traiter_reponse_numero_bouffage #{bouffage['id']} : {e}")
            return "❌ Erreur technique. Réessayez ou contactez votre admin."
        finally:
            cur.close()

        log_audit("BOUFFAGE_NUMERO_RECU",
                  f"Bouffage#{bouffage['id']} | Membre#{membre['id']} | {numero} ({operateur})", wa)

        wa_admins_tontine(bouffage["tontine_id"],
            f"💸 *NUMÉRO REÇU — {bouffage['tontine_nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Bénéficiaire : *{membre['nom_complet']}*\n"
            f"Montant net  : *{bouffage['montant_net']:,} FCFA*\n"
            f"Numéro MM    : *{numero}* ({'MTN' if operateur == 'cm.mtn' else 'Orange'})\n\n"
            f"Effectuez le virement, puis tapez :\n"
            f"*admin {bouffage['tontine_nom']}* puis *BOUFFAGE_VIRE {bouffage['id']}*\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        return (
            f"✅ Numéro enregistré : *{numero}*\n\n"
            f"Votre admin va effectuer le virement sous peu. "
            f"Vous recevrez une confirmation dès réception."
        )
    finally:
        release_conn(conn)


def _solder_versement(cur, bouffage: dict) -> int:
    """Règle un versement dont l'argent quitte la cagnotte : solde les dettes
    IRA qu'il a déduites et écrit la transaction Bouffage. Seul point d'écriture
    pour les deux chemins de versement (confirmation BOUFFAGE_VIRE et cashout
    forcé). `cur` est dans la transaction de l'appelant, ligne bouffage verrouillée.

    Les dettes soldées sont celles liées au versement au déclenchement
    (bouffage_id), jamais une dette apparue depuis ; frais_ira = montant_ira,
    ce qui a réellement été déduit. Un versement créé avant ce lien
    (montant_ira NULL) garde l'ancienne règle. Retourne frais_ira.
    """
    if bouffage.get("montant_ira") is None:
        cur.execute("""
            SELECT id, montant FROM dettes_ira
            WHERE membre_id=%s AND tontine_id=%s AND statut='Due'
            FOR UPDATE
        """, (bouffage["membre_id"], bouffage["tontine_id"]))
        dettes = cur.fetchall()
        total_ira = sum(d["montant"] for d in dettes)
    else:
        cur.execute("""
            SELECT id, montant FROM dettes_ira
            WHERE bouffage_id=%s AND statut='Due'
            FOR UPDATE
        """, (bouffage["id"],))
        dettes = cur.fetchall()
        total_ira = bouffage["montant_ira"]
    if dettes:
        cur.execute("""
            UPDATE dettes_ira SET statut='Prelevee', prelevee_le=NOW()
            WHERE id = ANY(%s)
        """, ([d["id"] for d in dettes],))

    cur.execute("""
        INSERT INTO transactions
            (membre_id, tontine_id, montant_brut, frais_ira, montant_net,
             type_transaction, statut)
        VALUES (%s,%s,%s,%s,%s,'Bouffage','Confirmee')
    """, (bouffage["membre_id"], bouffage["tontine_id"],
          bouffage["montant_brut"], total_ira, bouffage["montant_net"]))
    return total_ira


def confirmer_bouffage_vire(conn, bouffage_id: int, admin_wa: str) -> dict:
    """Admin confirme avoir viré le montant au bénéficiaire.

    SELECT FOR UPDATE + vérif statut='Vire' : verrou pessimiste et
    idempotence, même schéma que confirmer_cotisation (PATCH 1 v9.18) —
    empêche un double-tap ou un retry webhook de créer deux transactions.
    """
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM bouffages_manuels WHERE id=%s FOR UPDATE", (bouffage_id,))
        bouffage = cur.fetchone()
        if not bouffage:
            conn.rollback()
            return {"ok": False, "msg": "Bouffage introuvable."}
        if bouffage["statut"] != "Vire":
            conn.rollback()
            return {"ok": False, "msg": f"❌ Déjà traité ou pas encore prêt (statut : {bouffage['statut']})."}

        cur.execute("""UPDATE bouffages_manuels
                       SET statut='Confirme', confirme_par=%s, date_confirmation=NOW()
                       WHERE id=%s""", (admin_wa, bouffage_id))

        # Marquer le passage comme payé
        if bouffage["passage_id"]:
            cur.execute("""UPDATE liste_passage
                           SET statut='Paye', date_paiement=NOW(),
                               numero_cashout=%s, operateur_cashout=%s
                           WHERE id=%s""",
                        (bouffage["numero_mm"], bouffage.get("operateur_cashout"),
                         bouffage["passage_id"]))

        _solder_versement(cur, bouffage)
        conn.commit()
    except Exception as e:
        try: conn.rollback()
        except: pass
        log.error(f"❌ confirmer_bouffage_vire #{bouffage_id} échec: {e}")
        return {"ok": False, "msg": f"Erreur technique: {str(e)[:80]}"}
    finally:
        cur.close()

    # Notifier le bénéficiaire
    membre = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s",
        (bouffage["membre_id"],))
    tontine = fetchone(conn,
        "SELECT nom FROM tontines WHERE id=%s", (bouffage["tontine_id"],))

    if membre and tontine:
        wa_prive(membre["whatsapp"],
            f"✅ *VIREMENT EFFECTUÉ — {tontine['nom']}*\n\n"
            f"Bonjour *{membre['nom_complet']}*,\n\n"
            f"Votre bouffage de *{bouffage['montant_net']:,} FCFA* "
            f"a été viré sur votre numéro Mobile Money.\n\n"
            f"Vérifiez la réception sur votre téléphone.\n\n"
            f"Merci de continuer à cotiser normalement.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    log_audit("BOUFFAGE_CONFIRME",
              f"Bouffage#{bouffage_id} | Admin:{admin_wa} | "
              f"{bouffage['montant_net']:,} FCFA viré")

    # ── Détection fin de cycle ────────────────────────────────────────────
    _verifier_fin_cycle(conn, bouffage["tontine_id"])

    return {"ok": True, "msg": "✅ Bouffage confirmé et enregistré."}


def _rapport_ponctualite_cycle(conn, tontine_id: int, cycle: int) -> dict:
    """
    Construit le rapport de ponctualité d'un cycle + l'ordre PROPOSÉ pour le suivant.

    Bot = moteur de calcul + mémoire impartiale ; l'admin reste juge final. Ce rapport
    est de la situational awareness (qui parfait / mulligan / fugitif) + carburant Trust
    Graph, et sert de PROPOSITION que l'admin validera via NOUVEAU_CYCLE.

    Absence de ligne ponctualite_cycle = 0 slip = parfait.

    Returns: {"rapport_txt", "propose_txt", "stats": {parfaits, mulligan, ejectes, total}}
    """
    membres = fetchall(conn, """
        SELECT lp.membre_id, lp.ordre,
               COALESCE(m.nom_complet, lp.nickname, '???') AS nom,
               COALESCE(pc.slips, 0) AS slips
        FROM liste_passage lp
        LEFT JOIN membres m           ON m.id = lp.membre_id
        LEFT JOIN ponctualite_cycle pc ON pc.membre_id = lp.membre_id
                                      AND pc.tontine_id = lp.tontine_id
                                      AND pc.cycle = %s
        WHERE lp.tontine_id=%s AND lp.cycle=%s
        ORDER BY lp.ordre
    """, (cycle, tontine_id, cycle))

    # Un placeholder non lié (membre_id NULL) n'a pas de ponctualité mesurable :
    # il ne doit PAS compter comme "parfait" (sinon promu à tort en tête).
    lies      = [m for m in membres if m["membre_id"] is not None]
    parfaits  = [m for m in lies if m["slips"] == 0]
    mulligan  = [m for m in lies if m["slips"] == 1]
    ejectes   = [m for m in lies if m["slips"] >= 2]
    non_lies  = [m for m in membres if m["membre_id"] is None]

    def _bloc(titre, emoji, lst, suffixe=""):
        if not lst:
            return ""
        lignes = "\n".join(f"   {emoji} {x['nom']}{suffixe}" for x in lst)
        return f"*{titre}* ({len(lst)})\n{lignes}\n"

    rapport_txt = (
        _bloc("Parfaits — 0 retard", "🏅", parfaits)
        + _bloc("Mulligan consommé — 1 retard toléré", "🎟️", mulligan)
        + _bloc("Éjectés — 2+ retards", "❌", ejectes)
        + _bloc("Non liés (nickname seul)", "🔗", non_lies)
    ) or "   (aucune donnée de ponctualité ce cycle)\n"

    # Ordre proposé pour le prochain cycle (parfaits en tête ; non-liés en dernier tier).
    classement = ira_engine.ordre_priorite_next_cycle([
        {"membre_id": m["membre_id"], "nom": m["nom"],
         "slips": (m["slips"] if m["membre_id"] is not None else 99),
         "ordre_actuel": m["ordre"]}
        for m in membres
    ])
    propose_txt = "\n".join(
        f"   {m['ordre_nouveau']:>2}. {m['nom']}"
        + ("  🏅" if m["tier"] == 0 else "  🎟️" if m["tier"] == 1 else "  ❌")
        for m in classement
    ) or "   (liste vide)"

    return {
        "rapport_txt": rapport_txt,
        "propose_txt": propose_txt,
        "stats": {
            "parfaits": len(parfaits),
            "mulligan": len(mulligan),
            "ejectes": len(ejectes),
            "total": len(membres),
        },
    }


def _verifier_fin_cycle(conn, tontine_id: int):
    """
    Appelée après chaque bouffage confirmé.
    Vérifie si tous les passages du cycle actuel sont payés.
    Si oui → annonce fin de cycle + demande à l'admin si on repart.
    """
    tontine = fetchone(conn,
        "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
    if not tontine:
        return

    # Compter passages restants dans ce cycle
    restants = fetchone(conn, """
        SELECT COUNT(*) n FROM liste_passage
        WHERE tontine_id=%s AND cycle=%s AND statut NOT IN ('Paye','Intercepte','Cede')
    """, (tontine_id, tontine["cycle_actuel"]))["n"]

    if restants > 0:
        return  # Cycle pas encore terminé

    # Total du cycle
    nb_membres = fetchone(conn,
        "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
        (tontine_id,))["n"]
    nb_cycles  = tontine["cycle_actuel"]

    log_audit("FIN_CYCLE",
              f"Tontine:{tontine['nom']} | Cycle:{nb_cycles} terminé")

    # ── PHASE 2 — Dispatch Bonus de Ponctualité (compte marchand MoMo/OM) ──
    # INACTIF tant que pas d'API de mouvement de fonds (roadmap Q1 2027).
    # La récompense Phase 1 = priorité de rotation (appliquée dans demarrer_nouveau_cycle).
    # À l'arrivée du compte marchand, décommenter : le pool = Σ IRA COLLECTÉES
    # (statut='Prelevee', pas 'Due'), netté des frais MoMo/OM, réparti entre les
    # membres éligibles (0-1 slip). Invariant dur : Redistribué ≤ Collecté.
    # Voir mémoire projet : project_merchant_account_rubicon.
    #
    #   pool = fetchone(conn, "SELECT COALESCE(SUM(montant),0) t FROM dettes_ira "
    #                         "WHERE tontine_id=%s AND statut='Prelevee'",
    #                         (tontine_id,))["t"]
    #   n_parfaits = fetchone(conn, "SELECT COUNT(*) n FROM ponctualite_cycle "
    #                               "WHERE tontine_id=%s AND cycle=%s AND eligible",
    #                               (tontine_id, nb_cycles))["n"]
    #   dispatch = ira_engine.bonus_par_parfait(pool, n_parfaits,
    #                                           frais_dispatch=_frais_momo(pool, n_parfaits))
    #   # reliquat_rollover → pot du cycle suivant (dynamique jackpot)

    # Annonce dans le groupe
    if tontine.get("whatsapp_groupe"):
        wa_groupe(tontine["whatsapp_groupe"],
            f"🎉 *CYCLE {nb_cycles} TERMINÉ — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Félicitations à tous les *{nb_membres} membres* !\n\n"
            f"Le cycle {nb_cycles} est officiellement clôturé.\n"
            f"Tous les bouffages ont été effectués avec succès.\n\n"
            f"Votre administrateur vous informera prochainement "
            f"de la suite — nouveau cycle ou clôture définitive.\n\n"
            f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
        )

    # Rapport de ponctualité + ordre PROPOSÉ (bot propose, admin dispose).
    try:
        rap = _rapport_ponctualite_cycle(conn, tontine_id, nb_cycles)
    except Exception as _re:
        log.warning(f"⚠️ Rapport ponctualité non bloquant : {_re}")
        rap = None

    # DM aux admins — rapport + proposition + demande de décision
    admins = fetchall(conn,
        "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s", (tontine_id,))
    for adm in admins:
        msg = (
            f"🎉 *CYCLE {nb_cycles} TERMINÉ — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Tous les membres ont bouffé. Le cycle {nb_cycles} est clôturé.\n\n"
        )
        if rap:
            s = rap["stats"]
            msg += (
                f"📊 *PONCTUALITÉ DU CYCLE*\n"
                f"{rap['rapport_txt']}\n"
                f"🔄 *ORDRE PROPOSÉ — prochain cycle*\n"
                f"_(parfaits promus en tête — proposition, vous restez juge)_\n"
                f"{rap['propose_txt']}\n\n"
                f"💡 Le classement s'applique si vous lancez *NOUVEAU_CYCLE*. "
                f"Vous pouvez l'ajuster ensuite (option *modifier la liste*) — "
                f"toute modification est tracée.\n\n"
            )
        msg += (
            f"Que souhaitez-vous faire ?\n\n"
            f"  1. Tapez *admin {tontine['nom']}*\n"
            f"  2. Puis tapez *NOUVEAU_CYCLE* pour repartir avec les mêmes membres\n"
            f"     ou *CLOTURER* pour clôturer définitivement cette tontine\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        wa_prive(adm["whatsapp"], msg)

    # Notifier le owner
    wa_owner(
        f"✅ *FIN DE CYCLE — {tontine['nom']}*\n"
        f"Cycle {nb_cycles} | {nb_membres} membres | "
        f"En attente de décision admin."
    )

    # Score confiance × nombre d'or pour membres ayant honoré tout le cycle
    conn2 = get_conn()
    try:
        membres_cycle = fetchall(conn2, """
            SELECT DISTINCT m.id FROM membres m
            JOIN adhesions a ON a.membre_id = m.id
            WHERE a.tontine_id=%s AND a.statut='Actif'
        """, (tontine_id,))
        for mb in membres_cycle:
            q(conn2, """UPDATE membres
                       SET score_confiance = LEAST(100, ROUND(score_confiance * 1.618))
                       WHERE id=%s""", (mb["id"],))
        conn2.commit()
        log.info(f"✅ Score confiance × 1.618 appliqué à {len(membres_cycle)} membres — {tontine['nom']}")
    except Exception as e:
        log.error(f"Score confiance golden ratio : {e}")
    finally:
        release_conn(conn2)


def demarrer_nouveau_cycle(tontine_id: int, admin_wa: str) -> str:
    """
    Lance un nouveau cycle pour la tontine.
    - Incrémente cycle_actuel
    - Remet tous les passages en statut En_attente
    - Remet les adhesions en Actif
    - Libère les cautions restantes
    - Annonce dans le groupe
    """
    conn = get_conn()
    try:
        verrou_ordre_tontine(conn, tontine_id)  # FORTERESSE : sérialise l'ordre de cette tontine
        tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine:
            release_conn(conn)
            return "❌ Tontine introuvable."

        # Garde-fou : refuser si le cycle actuel n'est pas terminé — sinon
        # on libère les cautions de membres qui n'ont pas encore bouffé.
        restants = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s AND statut NOT IN ('Paye','Intercepte','Cede')
        """, (tontine_id, tontine["cycle_actuel"]))["n"]
        if restants > 0:
            release_conn(conn)
            return (
                f"❌ Le cycle {tontine['cycle_actuel']} n'est pas encore terminé "
                f"({restants} passage(s) restant(s)). Impossible de lancer un nouveau cycle."
            )

        ancien_cycle = tontine["cycle_actuel"]
        nouveau_cycle = ancien_cycle + 1

        # Incrémenter le cycle
        q(conn, "UPDATE tontines SET cycle_actuel=%s WHERE id=%s",
          (nouveau_cycle, tontine_id))

        # Remettre les passages en attente pour le nouveau cycle
        # (on garde la même liste, juste on remet les statuts)
        q(conn, """UPDATE liste_passage
                   SET statut='En_attente', date_paiement=NULL,
                       numero_cashout=NULL, operateur_cashout=NULL
                   WHERE tontine_id=%s AND cycle=%s""",
          (tontine_id, ancien_cycle))

        # Dupliquer la liste pour le nouveau cycle — REWARD : priorité de rotation.
        # Les membres parfaits (0 slip) sont promus en tête, mulligan (1 slip) ensuite,
        # éjectés (2+) en fin. Absence de ligne ponctualite = jamais en retard = parfait.
        passages = fetchall(conn, """
            SELECT lp.membre_id, lp.nickname, lp.ordre, lp.soumis_par,
                   COALESCE(pc.slips, 0) AS slips
            FROM liste_passage lp
            LEFT JOIN ponctualite_cycle pc
                   ON pc.membre_id = lp.membre_id
                  AND pc.tontine_id = lp.tontine_id
                  AND pc.cycle = %s
            WHERE lp.tontine_id=%s AND lp.cycle=%s
            ORDER BY lp.ordre
        """, (ancien_cycle, tontine_id, ancien_cycle))

        classement = ira_engine.ordre_priorite_next_cycle([
            {"membre_id": p["membre_id"], "nickname": p["nickname"],
             "soumis_par": p["soumis_par"],
             # Placeholder non lié (membre_id NULL) : jamais "parfait" — il n'a pas de
             # ligne ponctualite, donc slips=0 le hisserait à tort en tête. On le
             # classe en dernier tier (slips élevé) pour ne pas polluer la récompense.
             "slips": (p["slips"] if p["membre_id"] is not None else 99),
             "ordre_actuel": p["ordre"]}
            for p in passages
        ])
        nb_promus = sum(1 for m in classement if m["tier"] == 0)

        for m in classement:
            # Ordres 1..N distincts par construction + nouveau cycle vierge sous
            # verrou → INSERT direct (ON CONFLICT retiré : incompatible DEFERRABLE).
            q(conn, """
                INSERT INTO liste_passage
                    (tontine_id, membre_id, nickname, cycle, ordre, soumis_par)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (tontine_id, m["membre_id"], m["nickname"],
                  nouveau_cycle, m["ordre_nouveau"], m["soumis_par"]))

        # Remettre adhesions en Actif
        q(conn, """UPDATE adhesions SET statut='Actif', jours_avance=0
                   WHERE tontine_id=%s AND statut IN ('Suspendu','Pause')""",
          (tontine_id,))

        # Libérer les cautions bloquées
        cautions = fetchall(conn, """
            SELECT cg.id, cg.montant, m.whatsapp, m.nom_complet
            FROM cautions_garantie cg
            JOIN membres m ON m.id = cg.membre_id
            WHERE cg.tontine_id=%s AND cg.statut='Bloquee'
        """, (tontine_id,))

        for c in cautions:
            q(conn, "UPDATE cautions_garantie SET statut='Liberee', date_liberation=NOW() WHERE id=%s",
              (c["id"],))
            wa_prive(c["whatsapp"],
                f"🔓 *CAUTION LIBÉRÉE — {tontine['nom']}*\n\n"
                f"Fin du cycle {ancien_cycle}.\n"
                f"Votre caution de *{c['montant']:,} FCFA* est libérée.\n"
                f"Votre admin va vous la reverser.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )

        conn.commit()

        nb_membres = len(passages)
        log_audit("NOUVEAU_CYCLE",
                  f"Tontine:{tontine['nom']} | Cycle {ancien_cycle}→{nouveau_cycle} | Admin:{admin_wa}")

        # Traçabilité : le tri algorithmique de ponctualité est le DÉFAUT appliqué.
        # Toute déviation ultérieure de l'admin est loggée séparément (MODIF_ORDRE).
        nb_mulligan = sum(1 for m in classement if m["tier"] == 1)
        nb_ejectes  = sum(1 for m in classement if m["tier"] == 2)
        ordre_applique = " | ".join(
            f"{m['ordre_nouveau']}.{m.get('nickname') or ('#'+str(m['membre_id']) if m['membre_id'] else '?')}"
            for m in classement)
        log_audit("REORDER_PONCTUALITE",
                  f"Tontine:{tontine['nom']} | Cycle {nouveau_cycle} | "
                  f"parfaits:{nb_promus} mulligan:{nb_mulligan} éjectés:{nb_ejectes} | "
                  f"ordre appliqué (défaut algo) : {ordre_applique}", admin_wa)

        # Annonce dans le groupe
        if tontine.get("whatsapp_groupe"):
            wa_groupe(tontine["whatsapp_groupe"],
                f"🚀 *CYCLE {nouveau_cycle} LANCÉ — {tontine['nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Le cycle {nouveau_cycle} démarre maintenant !\n\n"
                f"👥 *{nb_membres} membres* repartent pour un nouveau cycle.\n"
                f"🏅 *{nb_promus} membre(s) parfait(s)* (zéro retard) promus en tête "
                f"de la liste de bouffage — la ponctualité paie.\n\n"
                f"Les cotisations reprennent normalement.\n"
                f"Envoyez vos screenshots dans ce groupe après chaque virement.\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )

        release_conn(conn)
        return (
            f"✅ *Cycle {nouveau_cycle} lancé — {tontine['nom']}*\n\n"
            f"▪ {nb_membres} membres dans le cycle\n"
            f"▪ {len(cautions)} caution(s) libérée(s)\n"
            f"▪ Groupe notifié\n\n"
            f"Les cotisations reprennent immédiatement."
        )

    except Exception as e:
        conn.rollback()
        release_conn(conn)
        log.error(f"demarrer_nouveau_cycle : {e}")
        return f"❌ Erreur : {str(e)[:80]}"


def cloturer_tontine(tontine_id: int, admin_wa: str) -> str:
    """Clôture définitive d'une tontine."""
    conn = get_conn()
    try:
        tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine:
            release_conn(conn)
            return "❌ Tontine introuvable."

        q(conn, "UPDATE tontines SET statut='Terminee' WHERE id=%s", (tontine_id,))
        conn.commit()

        if tontine.get("whatsapp_groupe"):
            wa_groupe(tontine["whatsapp_groupe"],
                f"🏁 *TONTINE CLÔTURÉE — {tontine['nom']}*\n\n"
                f"Cette tontine est officiellement terminée.\n"
                f"Merci à tous les membres pour leur sérieux et leur confiance.\n\n"
                f"TontineBot Pro se retire de ce groupe.\n"
                f"À bientôt sur le réseau BADF Ltd 🤝\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )
            import time as _t; _t.sleep(3)
            wa_quitter_groupe(tontine["whatsapp_groupe"])

        log_audit("TONTINE_CLOTUREE",
                  f"Tontine:{tontine['nom']} | Admin:{admin_wa}")
        release_conn(conn)
        return f"✅ Tontine *{tontine['nom']}* clôturée définitivement."

    except Exception as e:
        conn.rollback()
        release_conn(conn)
        return f"❌ Erreur : {str(e)[:80]}"


def rapport_dettes_badf_admin(admin_wa: str) -> str:
    """Rapport des dettes BADF dues par un admin (envoyé chaque soir à 20h)."""
    conn = get_conn()
    dettes = fetchall(conn, """
        SELECT type_dette, SUM(montant) as total
        FROM dettes_badf
        WHERE admin_wa=%s AND statut='Due'
        GROUP BY type_dette
    """, (admin_wa,))
    total = sum(d["total"] for d in dettes) if dettes else 0
    release_conn(conn)

    if total == 0:
        return ""

    lignes = []
    for d in dettes:
        lignes.append(f"  {d['type_dette']:<15} : {d['total']:>8,} FCFA")

    return (
        f"💰 *REVERSEMENT BADF DU JOUR*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lignes) +
        f"\n  ─────────────────────────────\n"
        f"  *TOTAL À REVERSER : {total:,} FCFA*\n\n"
        f"📱 Virement vers :\n"
        f"  MTN    : *{NUMERO_BADF_MTN}*\n"
        f"  Orange : *{NUMERO_BADF_ORANGE}*\n\n"
        f"Envoyez *PAIEMENT_BADF <code>* au bot après le virement.\n\n"
        f"_Barack & AI Development Facilities Ltd_"
    )


def enregistrer_paiement_badf(admin_wa: str, code_transaction: str) -> str:
    """Admin envoie son code de virement vers BADF — marque les dettes comme payées."""
    conn = get_conn()
    try:
        dettes = fetchall(conn, """
            SELECT id FROM dettes_badf
            WHERE admin_wa=%s AND statut='Due'
        """, (admin_wa,))

        if not dettes:
            return "Aucune dette en cours pour votre compte."

        ids = [d["id"] for d in dettes]
        q(conn, """UPDATE dettes_badf
                   SET statut='Payee', date_paiement=NOW(), code_paiement=%s
                   WHERE id = ANY(%s) AND statut='Due'""",
          (code_transaction, ids))

        # Rétablir les tontines suspendues pour dette
        tontines_suspendues = fetchall(conn, """
            SELECT id, nom, whatsapp_groupe
            FROM tontines
            WHERE statut='Suspendue'
              AND id IN (SELECT tontine_id FROM admins_groupe WHERE whatsapp=%s)
        """, (normaliser_numero(admin_wa),))

        groupes_retablis = []
        for t in tontines_suspendues:
            q(conn, "UPDATE tontines SET statut='Active' WHERE id=%s", (t["id"],))
            groupes_retablis.append(t["nom"])
            log_audit("TONTINE_RETABLIE",
                      f"Tontine:{t['nom']} rétablie après paiement dette BADF | Admin:{admin_wa}")

        conn.commit()

        log_audit("PAIEMENT_BADF_RECU",
                  f"Admin:{admin_wa} | Code:{code_transaction} | {len(ids)} dettes soldées")

        retabli_txt = ""
        if groupes_retablis:
            retabli_txt = (
                f"\n\n✅ *Service rétabli sur vos tontines :*\n"
                + "\n".join(f"  • {g}" for g in groupes_retablis)
                + f"\n\n_TontineBot Pro a été réintégré. "
                  f"Rajoutez le bot dans les groupes concernés._"
            )
            wa_owner(
                f"✅ *DETTE BADF SOLDÉE*\n"
                f"Admin : {admin_wa}\n"
                f"Code  : {code_transaction}\n"
                f"Dettes soldées : {len(ids)}\n"
                f"Tontines rétablies : {', '.join(groupes_retablis)}"
            )

        return (
            f"✅ *{len(ids)}* dettes soldées. Code : `{code_transaction}`"
            f"{retabli_txt}"
        )
    except Exception as e:
        try: conn.rollback()
        except: pass
        log.error(f"❌ enregistrer_paiement_badf Admin:{admin_wa} : {e}")
        return f"❌ Erreur technique : {str(e)[:80]}"
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# WHATSAPP — envoi dans whatsapp.py ; restent ici les sessions et deux
# fonctions liées au bot (journal d'audit, constantes IRA)
# ══════════════════════════════════════════════════════════════════════════

# H6 — Backup sessions
_sessions_path = Path("logs/sessions_backup.json")

_sessions_bak_lock = threading.Lock()

def kick_membre_si_bot_admin(membre_id: int, tontine_id: int, raison: str = "") -> bool:
    """
    Exclut un membre de tous les groupes de sa tontine si le bot y est admin.
    Appelé automatiquement après un bannissement.
    Retourne True si au moins un kick a réussi.
    """
    conn    = get_conn()
    tontine = fetchone(conn,
        "SELECT whatsapp_groupe, bot_est_admin, nom FROM tontines WHERE id=%s",
        (tontine_id,))
    membre  = fetchone(conn,
        "SELECT whatsapp, nom_complet FROM membres WHERE id=%s", (membre_id,))
    release_conn(conn)

    if not tontine or not membre:
        return False
    if not tontine.get("bot_est_admin"):
        log.info(f"Bot non admin dans tontine {tontine_id} — kick ignoré")
        return False
    if not tontine.get("whatsapp_groupe"):
        return False

    wa_num = membre["whatsapp"]
    # Format Meta (sans le +) : +237XXXXXXXXX → 237XXXXXXXXX@s.whatsapp.net
    jid = wa_num.lstrip("+") + "@s.whatsapp.net"

    ok = wa_kick_membre(tontine["whatsapp_groupe"], jid)
    if ok:
        log_audit("KICK_GROUPE",
                  f"{membre['nom_complet']} exclu du groupe {tontine['nom']}. {raison}",
                  wa_num)
    return ok

def wa_mentionner_retardataires(nom_groupe: str, retardataires: list,
                                 tontine: dict, heure: str):
    """Rappel aux non-cotisants — envoi DM individuel."""
    for r in retardataires:
        msg = (
            f"⏰ *RAPPEL COTISATION — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Bonjour *{r['nom_complet']}*,\n\n"
            f"Votre cotisation de *{tontine['montant_place']:,} FCFA* "
            f"n'a pas encore été enregistrée.\n\n"
            f"📸 Effectuez votre virement vers le numéro de votre admin,\n"
            f"   puis envoyez le *screenshot de confirmation* dans le groupe.\n\n"
            f"⏱ Heure limite : *{tontine['heure_limite']}*\n"
            f"💸 Passé ce délai : IRA *{int(IRA_CLIFF_PCT*100)}% de la mise* dès le retard "
            f"+{int(IRA_DAILY_PCT*100)}%/jour (plafond {int(IRA_CAP_PCT*100)}%)"
        )
        wa_prive(r["whatsapp"], msg, wa_dispatch.PRIORITE_DIFFUSION)


# ══════════════════════════════════════════════════════════════════════════
# ONBOARDING — COLLECTE NOM
# ══════════════════════════════════════════════════════════════════════════


def _demarrer_collecte_nom(wa: str):
    """Collecte uniquement le prenom/surnom. Non bloquant — membre deja Actif."""
    with _sessions_lock:
        _sessions_kyc[wa] = {"etape": 0, "data": {}, "ts": time_module.time()}
    wa_prive(wa,
        "👋 Bienvenue sur *TontineBot Pro* — BADF Ltd\n\n"
        "Une seule question pour personnaliser votre espace :\n\n"
        "Quel est votre *prénom ou surnom* ?"
    )


def traiter_kyc(wa: str, texte: str, est_media: bool = False) -> bool:
    """Collecte le prénom/surnom. Étape unique d'onboarding."""
    wa = normaliser_numero(wa)
    if wa not in _sessions_kyc or not session_valide(_sessions_kyc, wa):
        return False

    sess  = _sessions_kyc[wa]
    etape = sess["etape"]

    # Commande annulation
    if texte.strip().upper() == "STOP":
        _sessions_kyc.pop(wa, None)
        wa_prive(wa,
            "⏸️ KYC interrompu.\n"
            "Tapez *menu* quand vous serez prêt à reprendre.")
        return True

    # ══ ÉTAPE 0 : PRÉNOM / SURNOM (seule étape) ═════════════════════════
    if etape == 0:
        nom = texte.strip()
        nom = re.sub(r"[^A-Za-zÀ-ÿ\s\-'\.]", "", nom).strip()
        if len(nom) < 2:
            wa_prive(wa,
                "❌ Prénom invalide.\n"
                "Entrez votre *prénom ou surnom* (lettres uniquement).\n"
                "Exemple : *Nicole* ou *Jean-Pierre*")
            return True
        sess["data"]["kyc_nom"] = nom
        _finaliser_nom(wa, nom)

    return True


def _finaliser_nom(wa: str, nom: str):
    """Enregistre le nom et active le membre. Fin du flux d'accueil sans KYC."""
    wa_norm = normaliser_numero(wa)
    conn = get_conn()
    try:
        q(conn, """UPDATE membres
                   SET nom_complet=%s, kyc_nom=%s, statut_global='Actif'
                   WHERE whatsapp=%s""", (nom, nom, wa_norm))
        conn.commit()
    finally:
        release_conn(conn)
    with _sessions_lock:
        _sessions_kyc.pop(wa_norm, None)
    log_audit("NOM_COLLECTE", f"{nom} | onboarding sans KYC", wa_norm)
    wa_prive(wa_norm,
        f"✅ Bonjour *{nom}* !\n\n"
        f"{MSG_BIENVENUE_DM}\n\n"
        "Tapez *menu* pour voir vos options."
    )


# ══════════════════════════════════════════════════════════════════════════
# MENU MEMBRE WHATSAPP
# ══════════════════════════════════════════════════════════════════════════


def traiter_menu_membre(wa: str, texte: str, est_media: bool = False) -> str:
    wa     = normaliser_numero(wa)
    texte  = texte.strip()
    membre = get_membre_by_wa(wa)

    # ── KYC en cours ─────────────────────────────────────────────────────
    if wa in _sessions_kyc and session_valide(_sessions_kyc, wa):
        traiter_kyc(wa, texte, est_media)
        return ""

    # ── Commandes naturelles (rang/caution en langage naturel) ────────────
    texte_lower = texte.lower()
    if any(kw in texte_lower for kw in
           ("mon rang", "numéro combien", "je suis combien", "ma position",
            "c'est quand mon tour", "quand mon tour")):
        return _repondre_rang(wa, membre)
    if any(kw in texte_lower for kw in
           ("ma caution", "caution", "garantie", "argent bloqué", "argent bloque")):
        return _repondre_caution(wa, membre)

    # ── Entrée menu ──────────────────────────────────────────────────────
    if texte_lower in ("menu", "bonjour", "aide", "hello", "hi", "salut", "help", "start"):
        _sessions_membre[wa] = {"etape": "menu", "data": {}, "ts": time_module.time()}
        if not membre:
            membre_row = None
            conn_new = get_conn()
            try:
                kyc_hash_new = hashlib.sha256(f"NEW{wa}".encode()).hexdigest()
                cur_new = q(conn_new, """
                    INSERT INTO membres
                        (nom_complet, kyc_hash, whatsapp, adhesion_payee, statut_global, kyc_etape)
                    VALUES (%s,%s,%s,1,'Actif',0)
                    ON CONFLICT (whatsapp) DO NOTHING
                """, (f"Membre_{wa[-4:]}", kyc_hash_new, wa))
                conn_new.commit()
                try:
                    membre_row = fetchone(conn_new,
                        "SELECT nom_complet FROM membres WHERE whatsapp=%s", (wa,))
                except Exception:
                    pass
            finally:
                release_conn(conn_new)
            nom_existant = membre_row["nom_complet"] if membre_row else None
            if nom_existant and not nom_existant.startswith("Membre_"):
                wa_prive(wa,
                    f"👋 Bienvenue *{nom_existant}* !\n\n"
                    f"Votre compte a été configuré par votre admin.\n"
                    f"Tapez *menu* pour voir vos options.\n\n"
                    f"_TontineBot Pro — BADF Ltd_")
                return ""
            # Couvre inserted=True ET inserted=False (conflit sur Membre_XXXX existant)
            _demarrer_collecte_nom(wa)
            return ""
        if membre["statut_global"] == "En_attente_kyc":
            conn_mig = get_conn()
            try:
                q(conn_mig, "UPDATE membres SET statut_global='Actif' WHERE whatsapp=%s", (wa,))
                conn_mig.commit()
            finally:
                release_conn(conn_mig)
            _demarrer_collecte_nom(wa)
            return ""
        if membre["statut_global"] == "Banni":
            return msg_dissuasion(wa)
        return MENU_MEMBRE_TXT

    # ── Réponse à une demande de numéro Mobile Money (bouffage manuel) ────
    # Doit s'exécuter AVANT le gate session_valide() ci-dessous : sans
    # session active (cas normal ici, 2h > SESSION_TIMEOUT=300s), ce gate
    # renvoie "" et avale silencieusement la réponse du membre.
    # Le guard etape_courante évite de percuter un flux explicite en cours
    # (CHGNUM, paiement avancé, signalement...) qui attend aussi du texte libre.
    etape_courante = (_sessions_membre.get(wa) or {}).get("etape")
    if membre and etape_courante in (None, "menu"):
        rep_bouffage = traiter_reponse_numero_bouffage(wa, texte, membre)
        if rep_bouffage is not None:
            return rep_bouffage

    if not session_valide(_sessions_membre, wa):
        if texte in ("1","2","3","4","5","6","7","8","0"):
            _sessions_membre[wa] = {"etape": "menu", "data": {}, "ts": time_module.time()}
        else:
            return ""

    if not membre:
        return ""
    if membre["statut_global"] == "Banni":
        return msg_dissuasion(wa)

    sess = _sessions_membre.get(wa, {"etape": "menu"})

    # ── 1 : Solde & statut ───────────────────────────────────────────────
    if texte == "1" and sess["etape"] == "menu":
        conn    = get_conn()
        caution = fetchone(conn,
            "SELECT COALESCE(SUM(montant),0) AS t FROM cautions_garantie "
            "WHERE membre_id=%s AND statut='Bloquee'", (membre["id"],))["t"]
        dettes  = fetchone(conn,
            "SELECT COALESCE(SUM(montant),0) AS t FROM dettes_ira "
            "WHERE membre_id=%s AND statut='Due'", (membre["id"],))["t"]
        release_conn(conn)
        st_icon = {"Actif": "✅", "Suspendu_global": "🔴", "Banni": "🚫"}.get(
            membre["statut_global"], "❓")
        stars = "⭐" * max(1, membre["score_confiance"] // 20)
        return (
            f"📊 *MON PROFIL BARACK CORP*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {membre['nom_complet']}\n"
            f"📱 {wa}\n"
            f"Statut : {st_icon} {membre['statut_global']}\n"
            f"Score : {stars} ({membre['score_confiance']}/100)\n\n"
            f"💰 Dette : *{membre['solde_dette']:,} FCFA*\n"
            f"🔒 Caution bloquée : *{caution:,} FCFA*\n"
            f"📋 Dette IRA : *{dettes:,} FCFA*\n"
            f"🏆 Bouffages reçus : {membre['nb_bouffages']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Tapez *menu* pour revenir_"
        )

    # ── 2 : Mes tontines ─────────────────────────────────────────────────
    elif texte == "2" and sess["etape"] == "menu":
        conn    = get_conn()
        adhs    = fetchall(conn, """
            SELECT t.nom, t.montant_place, t.type_tontine,
                   a.statut, a.nombre_places, a.jours_avance
            FROM adhesions a JOIN tontines t ON t.id=a.tontine_id
            WHERE a.membre_id=%s
        """, (membre["id"],))
        release_conn(conn)
        if not adhs:
            return "❌ Vous n'êtes inscrit dans aucune tontine.\n_Tapez *menu*_"
        lines = ["📋 *MES TONTINES*\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for a in adhs:
            icon = "✅" if a["statut"] == "Actif" else ("⏸️" if a["statut"] == "Pause" else "⚠️")
            avance = f" | {a['jours_avance']} période(s) d'avance" if a["jours_avance"] > 0 else ""
            lines.append(
                f"{icon} *{a['nom']}*\n"
                f"   {a['montant_place']:,} FCFA/{a['type_tontine']} · "
                f"{a['nombre_places']} place(s){avance}"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━\n_Tapez *menu*_")
        return "\n".join(lines)

    # ── 3 : Rang / Prochain passage ──────────────────────────────────────
    elif texte == "3" and sess["etape"] == "menu":
        return _repondre_rang(wa, membre)

    # ── 4 : Ma caution ───────────────────────────────────────────────────
    elif texte == "4" and sess["etape"] == "menu":
        return _repondre_caution(wa, membre)

    # ── 5 : Historique ───────────────────────────────────────────────────
    elif texte == "5" and sess["etape"] == "menu":
        conn = get_conn()
        txs  = fetchall(conn, """
            SELECT t.type_transaction, t.montant_brut, t.frais_fmp, t.frais_ira,
                   t.montant_net, t.statut, t.date_heure,
                   t.periodes_payees, ton.nom AS tontine
            FROM transactions t
            LEFT JOIN tontines ton ON ton.id=t.tontine_id
            WHERE t.membre_id=%s ORDER BY t.date_heure DESC LIMIT 15
        """, (membre["id"],))
        release_conn(conn)
        if not txs:
            return "📭 Aucune transaction.\n_Tapez *menu*_"
        lines = ["💳 *MES 15 DERNIÈRES TRANSACTIONS*\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for t in txs:
            icon = "✅" if t["statut"] == "Confirmee" else "⏳"
            date = t["date_heure"].strftime("%d/%m %H:%M") if t["date_heure"] else "?"
            av   = f" (+{t['periodes_payees']-1} avance)" if t["periodes_payees"] > 1 else ""
            lines.append(
                f"{icon} {date} | *{t['type_transaction']}{av}*\n"
                f"   {t['montant_brut']:,} FCFA"
                f"{' | IRA:-' + str(t['frais_ira']) if t['frais_ira'] else ''}"
                f" | Net:{t['montant_net']:,}\n"
                f"   {t['tontine'] or 'N/A'}"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━\n_Tapez *menu*_")
        return "\n".join(lines)

    # ── 6 : Payer une cotisation ─────────────────────────────────────────
    elif texte == "6" and sess["etape"] == "menu":
        return _init_paiement_cotisation(wa, membre, sess, nb_periodes=1)

    # ── 7 : Paiement avancé ──────────────────────────────────────────────
    elif texte == "7" and sess["etape"] == "menu":
        _sessions_membre[wa]["etape"] = "choix_avance_nb"
        return (
            "⏩ *PAIEMENT AVANCÉ*\n"
            "Payez plusieurs périodes à l'avance pour :\n"
            "✅ Éviter les pénalités IRA\n"
            "✅ Améliorer votre score de confiance\n"
            "✅ Sécuriser votre place\n\n"
            "Combien de périodes voulez-vous payer ?\n"
            "(Tapez un chiffre entre 2 et 10)\n\n"
            "Tapez *0* pour annuler."
        )

    elif sess.get("etape") == "choix_avance_nb":
        if texte == "0":
            _sessions_membre[wa]["etape"] = "menu"
            return "❌ Annulé. Tapez *menu*."
        try:
            nb = int(texte)
            if nb < 2 or nb > 10:
                return "❌ Entrez un nombre entre 2 et 10."
        except ValueError:
            return "❌ Nombre invalide."
        _sessions_membre[wa]["data"]["nb_periodes"] = nb
        _sessions_membre[wa]["etape"] = "choix_avance_tontine"
        return _init_paiement_cotisation(wa, membre, sess, nb_periodes=nb, retour=True)

    elif sess.get("etape") in ("choix_tontine_paiement", "choix_avance_tontine"):
        nb_periodes = sess["data"].get("nb_periodes", 1)
        tontines    = sess["data"].get("tontines", [])
        if texte == "0":
            _sessions_membre.pop(wa, None)
            return "❌ Annulé."
        try:
            idx = int(texte) - 1
            t   = tontines[idx]
        except (ValueError, IndexError):
            return "❌ Choix invalide."
        montant_total = t["montant_place"] * nb_periodes
        ref = f"COT-{membre['id']}-{t['id']}-{nb_periodes}-{int(time_module.time())}"
        # Flux manuel — rediriger vers screenshot
        conn_t = get_conn()
        adm_t = fetchone(conn_t, "SELECT numero_collecte FROM admins_groupe WHERE tontine_id=%s AND numero_collecte IS NOT NULL LIMIT 1", (t["id"],))
        release_conn(conn_t)
        num_col = adm_t["numero_collecte"] if adm_t else "— demandez à votre admin"
        wa_prive(wa,
            f"💰 *COTISATION — {t['nom']}*\n\n"
            f"Montant : *{montant_total:,} FCFA*\n\n"
            f"📱 Virez vers : *{num_col}*\n"
            f"📸 Puis envoyez le *screenshot* dans le groupe WhatsApp.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        res = {"success": True, "url": ""}
        _sessions_membre.pop(wa, None)
        if res["success"]:
            avance_txt = f" ({nb_periodes} périodes)" if nb_periodes > 1 else ""
            return (
                f"💳 *PAIEMENT COTISATION{avance_txt}*\n"
                f"Tontine : *{t['nom']}*\n"
                f"Montant : *{montant_total:,} FCFA*\n\n"
                f"📱 Virez *{t['montant_place']:,} FCFA* vers le numéro de collecte de votre admin.\n"
                f"📸 Puis envoyez le screenshot dans le groupe WhatsApp.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
        return f"❌ Erreur paiement. Contactez un admin."

    # ── 8 : Signaler un problème ─────────────────────────────────────────
    elif texte == "8" and sess["etape"] == "menu":
        _sessions_membre[wa]["etape"] = "signalement"
        return "📣 *SIGNALEMENT*\nDécrivez votre problème en détail :"

    elif sess.get("etape") == "signalement":
        wa_admin(
            f"📣 *SIGNALEMENT MEMBRE*\n"
            f"👤 {membre['nom_complet']} ({wa})\n"
            f"📝 {texte}"
        )
        log_audit("SIGNALEMENT", texte, wa)
        _sessions_membre.pop(wa, None)
        return "✅ Transmis aux admins. Réponse sous 24h.\n_Tapez *menu*_"

    # ── 9 : Changer numéro Mobile Money ──────────────────────────────────
    elif texte == "9" and sess["etape"] == "menu":
        _sessions_membre[wa]["etape"] = "chgnum_nouveau"
        return (
            "📱 *CHANGEMENT DE NUMÉRO*\n"
            f"Frais : *{FRAIS_CHGNUM:,} FCFA* (une seule fois)\n\n"
            "⚠️ *Attention :* votre ancien numéro sera désactivé.\n\n"
            "Entrez votre *nouveau numéro* Mobile Money :\n"
            "Format : +237XXXXXXXXX\n\n"
            "Tapez *0* pour annuler."
        )

    elif sess.get("etape") == "chgnum_nouveau":
        if texte == "0":
            _sessions_membre[wa]["etape"] = "menu"
            return "❌ Annulé.\n_Tapez *menu*_"
        nouveau = normaliser_numero(texte)
        if not valider_numero_cameroun(nouveau):
            return "❌ Numéro camerounais invalide.\nFormat : *+237690123456*"
        if nouveau == wa:
            return "❌ C'est déjà votre numéro actuel."
        # Vérifier doublon
        conn    = get_conn()
        doublon = fetchone(conn, "SELECT id FROM membres WHERE whatsapp=%s", (nouveau,))
        # Anti-bypass blacklist : membre banni ne peut pas changer de numéro
        mbr_actuel = fetchone(conn, "SELECT blackliste, statut_global FROM membres WHERE whatsapp=%s", (wa,))
        release_conn(conn)
        if mbr_actuel and (mbr_actuel.get("blackliste") or mbr_actuel.get("statut_global") == "Banni"):
            return "❌ Compte banni — changement de numéro impossible."
        if doublon:
            return "❌ Ce numéro est déjà utilisé par un autre membre."
        sess["data"]["chgnum_nouveau"] = nouveau
        _sessions_membre[wa]["etape"] = "chgnum_confirm"
        return (
            f"📱 *CONFIRMATION*\n\n"
            f"Ancien numéro : {wa}\n"
            f"Nouveau numéro : *{nouveau}*\n\n"
            f"Frais : *{FRAIS_CHGNUM:,} FCFA*\n\n"
            f"Tapez *OUI* pour confirmer et générer le lien de paiement.\n"
            f"Tapez *0* pour annuler."
        )

    elif sess.get("etape") == "chgnum_confirm":
        if texte == "0":
            _sessions_membre.pop(wa, None)
            return "❌ Annulé.\n_Tapez *menu*_"
        if texte.upper() != "OUI":
            return "❌ Tapez *OUI* pour confirmer ou *0* pour annuler."
        nouveau = sess["data"]["chgnum_nouveau"]
        import base64
        nouveau_b64 = base64.b64encode(nouveau.encode()).decode("ascii")
        ref = f"CHGNUM-{membre['id']}-{nouveau_b64}-{int(time_module.time())}"
        _sessions_membre.pop(wa, None)
        # Paiement manuel v9.17
        return (
            f"💳 *PAIEMENT CHANGEMENT DE NUMÉRO*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Montant : *{FRAIS_CHGNUM:,} FCFA*\n\n"
            f"📱 Virez *{FRAIS_CHGNUM:,} FCFA* vers :\n"
            f"   MTN : *{NUMERO_BADF_MTN}*\n"
            f"   Orange : *{NUMERO_BADF_ORANGE}*\n\n"
            f"📸 Envoyez le screenshot en DM à ce bot.\n"
            f"   Réf obligatoire dans le message : `{ref}`\n\n"
            f"Dès confirmation, votre compte sera basculé vers *{nouveau}*.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    # ── 0 : Aide ─────────────────────────────────────────────────────────
    elif texte == "0" and sess["etape"] == "menu":
        return (
            "ℹ️ *AIDE — BARACK CORP*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📱 MTN : *✱126✱MONTANT✱CODE#* (MTN)\n"
            "📱 Orange : *#150✱MONTANT✱CODE#* (Orange)\n\n"
            "Commandes rapides :\n"
            "• *menu* → Menu principal\n"
            "• *mon rang* → Votre position\n"
            "• *ma caution* → Caution bloquée\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    return ""


def _repondre_rang(wa: str, membre: Optional[dict]) -> str:
    """Réponse langage naturel pour le rang/passage."""
    if not membre:
        return "❌ Vous n'êtes pas encore inscrit. Tapez *menu*."
    conn     = get_conn()
    passages = fetchall(conn, """
        SELECT lp.ordre, lp.cycle, lp.statut, t.nom, t.montant_place,
               t.caution_pourcent, t.caution_active,
               (SELECT COALESCE(SUM(nombre_places),0) FROM adhesions WHERE tontine_id=t.id
                AND statut='Actif') AS nb_places,
               (SELECT COUNT(*) FROM liste_passage lp2
                WHERE lp2.tontine_id=lp.tontine_id AND lp2.cycle=lp.cycle
                  AND lp2.statut='Paye') AS deja_passes
        FROM liste_passage lp JOIN tontines t ON t.id=lp.tontine_id
        WHERE lp.membre_id=%s AND lp.statut='En_attente'
        ORDER BY lp.ordre
    """, (membre["id"],))
    release_conn(conn)
    if not passages:
        return (
            "ℹ️ *MON RANG*\n"
            "Vous n'avez pas de passage en attente.\n"
            "Si vous venez de vous inscrire, contactez un admin.\n"
            "_Tapez *menu*_"
        )
    lines = ["📅 *MON RANG — BARACK CORP*\n━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for p in passages:
        avant        = p["ordre"] - p["deja_passes"] - 1
        montant_tot  = p["montant_place"] * p["nb_places"]
        fmp          = int(montant_tot * FRAIS_FMP)
        caution_pct  = p["caution_pourcent"] if p["caution_active"] else 0
        caution_mont = int(montant_tot * caution_pct / 100)
        montant_net  = montant_tot - fmp - caution_mont
        lines.append(
            f"🏦 *{p['nom']}* — Cycle {p['cycle']}\n"
            f"   📌 Vous êtes *n°{p['ordre']}* dans la liste\n"
            f"   👥 Personnes avant vous : *{avant}*\n"
            f"   💰 Bouffage estimé : *{montant_net:,} FCFA* (net)\n"
            f"   🔒 Caution : {caution_mont:,} FCFA ({caution_pct}%) sera bloquée\n"
            f"   _(libérée quand vous continuez à cotiser)_"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━\n_Tapez *menu*_")
    return "\n".join(lines)


def _repondre_caution(wa: str, membre: Optional[dict]) -> str:
    """Réponse langage naturel pour la caution."""
    if not membre:
        return "❌ Vous n'êtes pas inscrit. Tapez *menu*."
    conn     = get_conn()
    cautions = fetchall(conn, """
        SELECT c.montant, c.pourcent, c.statut, c.date_bouffage, t.nom,
               (SELECT COUNT(*) FROM liste_passage lp
                WHERE lp.tontine_id=c.tontine_id
                  AND lp.ordre > (SELECT ordre FROM liste_passage lp2
                                  WHERE lp2.id=c.passage_id)
                  AND lp.statut='En_attente') AS restants
        FROM cautions_garantie c JOIN tontines t ON t.id=c.tontine_id
        WHERE c.membre_id=%s ORDER BY c.date_bouffage DESC
    """, (membre["id"],))
    release_conn(conn)
    total_bloque = sum(c["montant"] for c in cautions if c["statut"] == "Bloquee")
    if not cautions:
        return (
            "🔒 *MA CAUTION*\n"
            "Aucune caution enregistrée.\n"
            "La caution est prélevée au moment du bouffage.\n"
            "_Tapez *menu*_"
        )
    lines = [f"🔒 *MA CAUTION — BARACK CORP*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
             f"💰 Total bloqué : *{total_bloque:,} FCFA*\n"]
    for c in cautions:
        icon = {"Bloquee": "🔒", "Liberee": "✅", "Saisie": "🚫"}.get(c["statut"], "❓")
        date = c["date_bouffage"].strftime("%d/%m/%Y") if c["date_bouffage"] else "?"
        cond = (f"Libérée quand vous cotisez pour *{c['restants']}* membre(s) restants"
                if c["statut"] == "Bloquee" else "")
        lines.append(
            f"{icon} *{c['nom']}* — {c['statut']}\n"
            f"   {c['montant']:,} FCFA ({c['pourcent']}%) | Bouffage : {date}\n"
            + (f"   _{cond}_" if cond else "")
        )
    lines.append(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ La caution est saisie si vous arrêtez de cotiser après avoir bouffé.\n"
        "_Tapez *menu*_"
    )
    return "\n".join(lines)


def _init_paiement_cotisation(wa: str, membre: dict, sess: dict,
                               nb_periodes: int = 1, retour: bool = False) -> str:
    conn    = get_conn()
    adhs    = fetchall(conn, """
        SELECT t.id, t.nom, t.montant_place
        FROM adhesions a JOIN tontines t ON t.id=a.tontine_id
        WHERE a.membre_id=%s AND a.statut='Actif'
    """, (membre["id"],))
    release_conn(conn)
    if not adhs:
        return "❌ Aucune tontine active.\n_Tapez *menu*_"
    _sessions_membre[wa]["etape"] = "choix_avance_tontine" if retour else "choix_tontine_paiement"
    _sessions_membre[wa]["data"]["tontines"] = adhs
    _sessions_membre[wa]["data"]["nb_periodes"] = nb_periodes
    lines = [f"💳 *CHOISISSEZ VOTRE TONTINE*\n"]
    for i, t in enumerate(adhs, 1):
        total = t["montant_place"] * nb_periodes
        lines.append(f"{i}. {t['nom']} — {total:,} FCFA (x{nb_periodes})")
    lines.append("\nTapez le *numéro* ou *0* pour annuler.")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# MENU ADMIN — 15 OPTIONS CONVERSATIONNEL (chaque option avec son utilité, PATCH 4 v9.18)
# ══════════════════════════════════════════════════════════════════════════


def _passer_a_cotisation_suivante(sess: dict, tid: int, tnom: str, msg_resultat: str) -> str:
    """
    PATCH 5 v9.18 — Helper pour le flow OUI/NON.
    Après une décision sur une cotisation, passe à la suivante s'il en reste,
    sinon termine le flow et revient au menu admin.
    """
    restantes = sess.get("cotis_restantes", [])

    if not restantes:
        # Plus de cotisations à traiter
        sess["etape"] = "menu"
        sess.pop("cotis_en_cours",  None)
        sess.pop("cotis_restantes", None)
        return (
            f"{msg_resultat}\n\n"
            f"✅ *Toutes les cotisations traitées — {tnom}*\n\n"
            f"_Retour au menu admin._\n\n"
            + MENU_ADMIN_TXT
        )

    # Recharger la prochaine cotisation depuis la DB (statut peut avoir changé)
    next_id = restantes[0]
    conn = get_conn()
    c = fetchone(conn, """
        SELECT cm.id, m.nom_complet, cm.montant_declare, cm.fmp_du,
               cm.date_soumission, cm.statut, a.nombre_places
        FROM cotisations_manuelles cm
        JOIN membres m ON m.id = cm.membre_id
        JOIN adhesions a ON a.membre_id = cm.membre_id AND a.tontine_id = cm.tontine_id
        WHERE cm.id=%s
    """, (next_id,))
    release_conn(conn)

    # Si la cotisation a déjà été traitée entretemps (autre admin), on saute
    if not c or c["statut"] != "En_attente":
        sess["cotis_restantes"] = restantes[1:]
        return _passer_a_cotisation_suivante(sess, tid, tnom, msg_resultat)

    sess["cotis_en_cours"]   = c["id"]
    sess["cotis_restantes"]  = restantes[1:]

    dt = c["date_soumission"].strftime("%d/%m %H:%M") if c["date_soumission"] else "?"
    places_txt = f" ×{c['nombre_places']}" if c["nombre_places"] > 1 else ""

    return (
        f"{msg_resultat}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *COTISATION SUIVANTE — {tnom}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔖 *#{c['id']}* — {c['nom_complet']}{places_txt}\n"
        f"   💰 {c['montant_declare']:,} FCFA\n"
        f"   📅 Soumise : {dt}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"❓ *Le transfert a-t-il bien été reçu ?*\n\n"
        f"   ✅ *OUI* → confirmer\n"
        f"   ❌ *NON [raison]* → rejeter\n"
        f"   ⏭️  *PASSER* → suivante\n"
        f"   ↩️  *0* → retour menu"
    )


def traiter_menu_admin(wa: str, texte: str) -> str:
    wa    = normaliser_numero(wa)
    texte = texte.strip()

    tontines_admin = get_tontines_admin(wa)
    if not tontines_admin:
        return ""

    # ── Commande PAIEMENT_BADF — solder les dettes BADF (tontine-agnostique) ──
    # Placée AVANT le blocage >72h ci-dessous : un admin bloqué doit pouvoir
    # se débloquer lui-même en soldant sa dette. Placée AVANT session_valide()
    # (plus bas) : la dette est liée au wa admin, pas à une tontine
    # sélectionnée via "admin [nom]".
    if texte.upper().startswith("PAIEMENT_BADF "):
        parts = texte.strip().split(None, 1)
        code  = parts[1].strip() if len(parts) > 1 else ""
        if not code:
            return "❌ Format : *PAIEMENT_BADF <code_transaction>*"
        return enregistrer_paiement_badf(wa, code)

    # ── Blocage si dette BADF >72h impayée (jamais pour le owner) ────────
    if not est_owner(normaliser_numero(wa)):
        conn_chk = get_conn()
        dette_bloquante = fetchone(conn_chk, """
            SELECT SUM(montant) AS total,
                   EXTRACT(EPOCH FROM (NOW() - MIN(date_creation)))/3600 AS heures
            FROM dettes_badf
            WHERE admin_wa=%s AND statut='Due'
            HAVING MIN(date_creation) < NOW() - INTERVAL '72 hours'
               AND SUM(montant) > 0
        """, (normaliser_numero(wa),))
        release_conn(conn_chk)

        if dette_bloquante and dette_bloquante.get("total"):
            total  = int(dette_bloquante["total"])
            heures = int(dette_bloquante["heures"] or 0)
            jours  = heures // 24
            return (
                f"🔴 *ACCÈS SUSPENDU — BADF Ltd*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Votre accès au menu admin est bloqué en raison d'une dette BADF "
                f"impayée depuis *{jours} jours*.\n\n"
                f"Montant dû : *{total:,} FCFA*\n\n"
                f"Régularisez immédiatement :\n"
                f"  MTN    : *{NUMERO_BADF_MTN}*\n"
                f"  Orange : *{NUMERO_BADF_ORANGE}*\n\n"
                f"Envoyez *PAIEMENT_BADF <code>* au bot pour débloquer votre accès.\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )

    # ── Commande prédictive : "risque [nom]" → score de risque fugue ────
    if texte.lower().startswith("risque "):
        nom_membre = texte[7:].strip()
        if nom_membre:
            return commande_admin_score_risque(wa, nom_membre)
        return "Usage : *risque [nom du membre]*\nExemple : *risque Marie*"

    # ── Commande escalade humaine : "cas [nom] [description]" ────────────
    # Quand un événement social imprévu survient (mort, départ, échange de
    # tour, panne téléphone, changement collectif), l'admin peut escalader
    # aux autres admins de la tontine pour décision collective.
    # Le membre concerné est mis en "Pause" — sanctions auto suspendues 30j.
    if texte.lower().startswith("cas_resolu"):
        return commande_admin_cas_resolu(wa, texte[len("cas_resolu"):].strip())
    if texte.lower().startswith("cas "):
        return commande_admin_cas_difficile(wa, texte[4:].strip())

    # ── Sélection tontine ─────────────────────────────────────────────────
    if texte.lower().startswith("admin"):
        parts = texte.split(None, 1)
        conn  = get_conn()
        if len(parts) < 2 and len(tontines_admin) == 1:
            t = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontines_admin[0],))
            release_conn(conn)
            _sessions_admin[wa] = {
                "etape": "menu", "tontine_id": t["id"],
                "tontine_nom": t["nom"], "data": {}, "ts": time_module.time()
            }
            return f"✅ *{t['nom']}* sélectionnée.\n\n" + MENU_ADMIN_TXT
        elif len(parts) >= 2:
            nom_rech = parts[1].upper()
            t = fetchone(conn,
                "SELECT * FROM tontines WHERE UPPER(nom) LIKE %s AND id=ANY(%s)",
                (f"%{nom_rech}%", tontines_admin))
            release_conn(conn)
            if not t:
                return f"❌ Tontine '{parts[1]}' non trouvée."
            _sessions_admin[wa] = {
                "etape": "menu", "tontine_id": t["id"],
                "tontine_nom": t["nom"], "data": {}, "ts": time_module.time()
            }
            return f"✅ *{t['nom']}*\n\n" + MENU_ADMIN_TXT
        else:
            adhs_t = fetchall(conn,
                "SELECT id, nom FROM tontines WHERE id=ANY(%s)", (tontines_admin,))
            release_conn(conn)
            lines = ["🔐 *CHOISISSEZ VOTRE TONTINE :*\n"]
            for t in adhs_t:
                lines.append(f"• Tapez : *admin {t['nom']}*")
            return "\n".join(lines)

    if not session_valide(_sessions_admin, wa):
        return ""

    sess        = _sessions_admin[wa]
    tid         = sess.get("tontine_id")
    tnom        = sess.get("tontine_nom", "")

    # ══ SESSION ATTENTE LISTE — interceptée avant le menu principal ════════
    if sess.get("etape") == "attente_liste":
        # Vérifier si le message ressemble à une liste de passage
        lignes_parsees = parser_liste_passage(texte)

        if not lignes_parsees:
            # Pas une liste → peut-être une question ou commande
            if texte.strip().lower() in ("aide", "help", "menu", "0"):
                sess["etape"] = "menu"
                conn2 = get_conn()
                t2    = fetchone(conn2, "SELECT nom FROM tontines WHERE id=%s", (tid,))
                release_conn(conn2)
                if t2:
                    sess["tontine_nom"] = t2["nom"]
                return MENU_ADMIN_TXT
            return (
                "⏳ J'attends la liste de passage.\n\n"
                "Format attendu :\n"
                "*01- Prénom JJ/MM/AA*\n"
                "*02- Prénom JJ/MM/AA*\n"
                "...\n\n"
                "Tapez *0* pour accéder au menu admin sans envoyer de liste."
            )

        # Liste détectée → vérifier si une liste existe déjà pour ce cycle
        conn_chk = get_conn()
        cycle_chk = fetchone(conn_chk,
            "SELECT cycle_actuel FROM tontines WHERE id=%s", (tid,))["cycle_actuel"]
        liste_existante = fetchone(conn_chk, """
            SELECT COUNT(*) n, MAX(soumis_par) soumis_par
            FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s AND statut='En_attente'
        """, (tid, cycle_chk))
        release_conn(conn_chk)

        if liste_existante and liste_existante["n"] > 0:
            # Une liste existe déjà — demander confirmation avant d'écraser
            if sess["data"].get("confirme_remplacement") != "oui":
                sess["data"]["liste_en_attente"]     = texte
                sess["data"]["confirme_remplacement"] = "attente"
                par = liste_existante["soumis_par"] or "un autre admin"
                return (
                    f"⚠️ *UNE LISTE EXISTE DÉJÀ*\n\n"
                    f"Une liste de {liste_existante['n']} passage(s) a déjà été "
                    f"enregistrée par *{par}* pour ce cycle.\n\n"
                    f"Voulez-vous la *remplacer* par celle que vous venez d'envoyer ?\n\n"
                    f"Tapez *OUI* pour confirmer le remplacement.\n"
                    f"Tapez *NON* pour annuler."
                )

        # Confirmer remplacement si en attente
        if sess["data"].get("confirme_remplacement") == "attente":
            if texte.strip().upper() == "NON":
                sess["data"].pop("confirme_remplacement", None)
                sess["data"].pop("liste_en_attente", None)
                return "❌ Remplacement annulé. La liste existante est conservée."
            elif texte.strip().upper() == "OUI":
                # Reprendre la liste mise en attente
                texte = sess["data"].pop("liste_en_attente", texte)
                lignes_parsees = parser_liste_passage(texte)
                sess["data"].pop("confirme_remplacement", None)
            else:
                return "Tapez *OUI* pour remplacer ou *NON* pour annuler."
        nb_ok, nb_non_lies = enregistrer_liste_passage(tid, lignes_parsees, wa)

        # Construire le récapitulatif
        recap_lignes = [
            f"  {str(e['ordre']).zfill(2)}- {e['nickname']}  "
            f"{'📅 ' + e['date_bouffage'] if e['date_bouffage'] else '⚠️ date invalide'}"
            for e in lignes_parsees
        ]
        recap = "\n".join(recap_lignes)

        avertissement = ""
        if nb_non_lies > 0:
            avertissement = (
                f"\n\n⚠️ *{nb_non_lies} nom(s) non liés à un membre enregistré.*\n"
                f"Ces membres devront s'enrôler via *menu* en DM pour être liés.\n"
                f"Ils apparaîtront dans la liste mais ne recevront pas de DM "
                f"automatique tant qu'ils ne sont pas enrôlés."
            )

        sess["etape"] = "menu"
        conn3 = get_conn()
        t3    = fetchone(conn3, "SELECT nom FROM tontines WHERE id=%s", (tid,))
        autres_admins = fetchall(conn3,
            "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s AND whatsapp!=%s",
            (tid, wa))
        release_conn(conn3)
        if t3:
            sess["tontine_nom"] = t3["nom"]

        # Notifier les autres admins
        for adm in autres_admins:
            wa_prive(adm["whatsapp"],
                f"ℹ️ *LISTE MISE À JOUR — {t3['nom'] if t3 else tnom}*\n\n"
                f"L'administrateur *{wa}* vient d'enregistrer "
                f"une liste de *{nb_ok} passage(s)*.\n\n"
                f"Tapez *admin* puis *3* pour la consulter."
            )

        return (
            f"✅ *LISTE ENREGISTRÉE — {tnom or 'Tontine'}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*{nb_ok} passage(s) enregistré(s) :*\n\n"
            f"{recap}"
            f"{avertissement}\n\n"
            f"─────────────────────────────────────────\n"
            f"Menu admin disponible. Tapez un numéro :\n\n"
            + MENU_ADMIN_TXT
        )

    # ══ MENU PRINCIPAL ════════════════════════════════════════════════════

    # ── Option 1 : Rapport du jour ────────────────────────────────────────
    if texte == "1" and sess["etape"] == "menu":
        conn = get_conn()
        t    = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tid,))
        nb_a = fetchone(conn,
            "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'", (tid,))["n"]
        nb_p = fetchone(conn, """
            SELECT COUNT(DISTINCT membre_id) n FROM transactions
            WHERE tontine_id=%s AND type_transaction='Cotisation'
              AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
        """, (tid,))["n"]
        tot  = fetchone(conn, """
            SELECT COALESCE(SUM(montant_net),0) t FROM transactions
            WHERE tontine_id=%s AND type_transaction='Cotisation'
              AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
        """, (tid,))["t"]
        fug  = fetchone(conn,
            "SELECT COUNT(DISTINCT membre_id) n FROM alertes_fugue "
            "WHERE tontine_id=%s AND traite=0", (tid,))["n"]
        release_conn(conn)
        taux = int(nb_p / nb_a * 100) if nb_a else 0
        return (
            f"📊 *RAPPORT — {tnom}*\n"
            f"🗓️ {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Membres actifs : {nb_a}\n"
            f"✅ Cotisé : {nb_p} ({taux}%)\n"
            f"⏰ Retard : {nb_a-nb_p}\n"
            f"💰 Collecté : *{tot:,} FCFA*\n"
            f"🚨 Fugitifs : {fug}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    # ── Option 2 : Liste des membres ──────────────────────────────────────
    elif texte == "2" and sess["etape"] == "menu":
        conn    = get_conn()
        membres = fetchall(conn, """
            SELECT m.nom_complet, m.whatsapp, m.score_confiance,
                   a.statut, a.nombre_places
            FROM adhesions a JOIN membres m ON m.id=a.membre_id
            WHERE a.tontine_id=%s ORDER BY m.nom_complet
        """, (tid,))
        release_conn(conn)
        total   = len(membres)
        actifs  = sum(1 for m in membres if m["statut"] == "Actif")
        lines   = [f"👥 *MEMBRES — {tnom}*\nTotal:{total} | Actifs:{actifs}\n"]
        for m in membres[:25]:
            risk = "🔴" if m["score_confiance"] < 50 else ("🟡" if m["score_confiance"] < 75 else "🟢")
            st   = "✅" if m["statut"] == "Actif" else "⚠️"
            lines.append(f"{risk}{st} {m['nom_complet']} ({m['nombre_places']}p)")
        if total > 25:
            lines.append(f"... et {total-25} autres")
        return "\n".join(lines)

    # ── Option 3 : Ordre de bouffage ──────────────────────────────────────
    elif texte == "3" and sess["etape"] == "menu":
        conn    = get_conn()
        t       = fetchone(conn, "SELECT cycle_actuel FROM tontines WHERE id=%s", (tid,))
        passages = fetchall(conn, """
            SELECT lp.ordre, lp.statut, lp.nickname, lp.date_bouffage,
                   m.nom_complet, m.score_confiance
            FROM liste_passage lp
            LEFT JOIN membres m ON m.id = lp.membre_id
            WHERE lp.tontine_id=%s AND lp.cycle=%s
            ORDER BY lp.ordre
        """, (tid, t["cycle_actuel"]))
        release_conn(conn)
        lines = [f"📋 *ORDRE DE BOUFFAGE — {tnom}* | Cycle {t['cycle_actuel']}\n"]
        for p in passages:
            s    = {"Paye": "✅", "En_attente": "⏳", "Notifie": "🔔",
                    "Intercepte": "🚫", "Cede": "🔄"}.get(p["statut"], "❓")
            nom  = p["nom_complet"] or p["nickname"] or "???"
            risk = "🔴" if p["score_confiance"] and p["score_confiance"] < 50 else ""
            lien = "" if p["nom_complet"] else " _(non enrôlé)_"
            date = f"  📅 {p['date_bouffage']}" if p["date_bouffage"] else ""
            lines.append(
                f"{str(p['ordre']).zfill(2)}- {s}{risk} {nom}{lien}{date}"
            )
        return "\n".join(lines) if passages else f"⚠️ Aucune liste enregistrée — {tnom}\nUtilisez l'option 13 pour saisir la liste."

    # ── Option 4 : Modifier ordre / Forcer cashout ───────────────────────
    elif texte == "4" and sess["etape"] == "menu":
        # Vérifier s'il y a un bouffage en attente de confirmation admin
        conn_chk = get_conn()
        expire = fetchone(conn_chk, """
            SELECT bm.id, bm.montant_net AS montant,
                   m.nom_complet, m.whatsapp
            FROM bouffages_manuels bm
            JOIN membres m ON m.id = bm.membre_id
            WHERE bm.tontine_id=%s AND bm.statut='En_attente'
            ORDER BY bm.id DESC LIMIT 1
        """, (tid,))
        release_conn(conn_chk)

        if expire:
            sess["etape"] = "choix_opt4"
            sess["data"]["cashout_expire"] = dict(expire)
            return (
                f"⚙️ *OPTION 4 — {tnom}*\n\n"
                f"Que souhaitez-vous faire ?\n\n"
                f"*1* → Modifier l'ordre de passage d'un membre\n"
                f"*2* → Forcer le cashout de *{expire['nom_complet']}* "
                f"_(bouffage expiré sans réponse — {expire['montant']:,} FCFA)_\n\n"
                f"Tapez *0* pour annuler."
            )
        else:
            sess["etape"] = "modif_ordre_wa"
            return (
                "✏️ *MODIFIER L'ORDRE*\n\n"
                "Entrez le *numéro WhatsApp* du membre à déplacer :"
            )

    elif sess.get("etape") == "choix_opt4":
        if texte == "0":
            sess["etape"] = "menu"
            return "❌ Annulé."
        elif texte == "1":
            sess["etape"] = "modif_ordre_wa"
            return (
                "✏️ *MODIFIER L'ORDRE*\n\n"
                "Entrez le *numéro WhatsApp* du membre à déplacer :"
            )
        elif texte == "2":
            expire = sess["data"].get("cashout_expire", {})
            if not expire:
                sess["etape"] = "menu"
                return "❌ Aucun cashout expiré trouvé."
            sess["etape"] = "forcer_cashout_num"
            return (
                f"💸 *FORCER CASHOUT — {expire['nom_complet']}*\n\n"
                f"Montant : *{expire['montant']:,} FCFA*\n\n"
                f"Entrez le numéro Mobile Money du bénéficiaire :\n\n"
                f"  *MTN +237690XXXXXX*\nou\n  *ORANGE +237699XXXXXX*\n\n"
                f"Tapez *0* pour annuler."
            )
        else:
            return "Tapez *1*, *2* ou *0*."

    elif sess.get("etape") == "forcer_cashout_num":
        if texte.strip() == "0":
            sess["etape"] = "menu"
            return "❌ Annulé."
        tu = texte.upper()
        operateur = None
        if "MTN" in tu:
            operateur = "cm.mtn"
        elif "ORANGE" in tu:
            operateur = "cm.orange"
        # re est déjà importé globalement
        match = re.search(r"(\+?237[26789]\d{8}|\b[26789]\d{8}\b)", texte)
        numero = None
        if match:
            n = match.group(1)
            numero = normaliser_numero(n if "237" in n else "237" + n)
        if not operateur or not numero:
            return (
                "❌ Format non reconnu.\nExemples :\n"
                "• *MTN +237690123456*\n• *ORANGE +237699123456*"
            )

        expire = sess["data"].get("cashout_expire", {})
        conn   = get_conn()
        ac_rec = fetchone(conn,
            "SELECT * FROM bouffages_manuels WHERE id=%s", (expire["id"],))
        if not ac_rec:
            release_conn(conn)
            sess["etape"] = "menu"
            return "❌ Enregistrement introuvable."

        cashout_ref = f"FORCE-{ac_rec['membre_id']}-{ac_rec['tontine_id']}-{int(time_module.time())}"
        res = {"success": True, "reference": cashout_ref}
        if res["success"]:
            # Même règlement que BOUFFAGE_VIRE : verrou + statut, dettes IRA
            # déduites soldées et transaction écrite dans la même transaction.
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                cur.execute("SELECT * FROM bouffages_manuels WHERE id=%s FOR UPDATE",
                            (ac_rec["id"],))
                ac_rec = cur.fetchone()
                if not ac_rec or ac_rec["statut"] != "En_attente":
                    conn.rollback()
                    release_conn(conn)
                    sess["etape"] = "menu"
                    return "ℹ️ Ce bouffage a déjà été traité."
                cur.execute("""UPDATE bouffages_manuels
                               SET statut='Confirme', numero_mm=%s, operateur_cashout=%s,
                                   confirme_par=%s, date_confirmation=NOW()
                               WHERE id=%s""", (numero, operateur, wa, ac_rec["id"]))
                cur.execute("""UPDATE liste_passage
                               SET statut='Paye', numero_cashout=%s, operateur_cashout=%s,
                                   montant_bouffage=%s, date_paiement=NOW()
                               WHERE id=%s""",
                            (numero, operateur, ac_rec["montant_net"], ac_rec["passage_id"]))
                cur.execute("UPDATE membres SET nb_bouffages=nb_bouffages+1, dernier_bouffage=NOW() WHERE id=%s",
                            (ac_rec["membre_id"],))
                _solder_versement(cur, ac_rec)
                conn.commit()
            except Exception as e:
                conn.rollback()
                release_conn(conn)
                log.error(f"❌ cashout forcé #{ac_rec['id'] if ac_rec else '?'} échec: {e}")
                sess["etape"] = "menu"
                return "❌ Erreur technique. Le cashout n'a pas été enregistré."
            finally:
                cur.close()
            # ── Détection fin de cycle — manquait sur ce chemin "forcé" ─────
            # (confirmer_bouffage_vire l'appelle déjà pour le chemin normal ;
            # sans ça, un cycle clôturé via cashout forcé ne déclenchait jamais
            # l'annonce fin-de-cycle / NOUVEAU_CYCLE / CLOTURER).
            _verifier_fin_cycle(conn, ac_rec["tontine_id"])
            release_conn(conn)
            log_audit("CASHOUT_FORCE",
                      f"{expire['nom_complet']} | {ac_rec['montant_net']:,} FCFA → {numero}", wa)
            # Notifier le bénéficiaire
            wa_prive(expire["whatsapp"],
                f"✅ *VIREMENT EFFECTUÉ — {tnom}*\n\n"
                f"*{ac_rec['montant_net']:,} FCFA* ont été envoyés sur *{numero}*\n"
                f"Réf : `{cashout_ref}`\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )
            sess["etape"] = "menu"
            return (
                f"✅ *Cashout forcé effectué*\n\n"
                f"{expire['nom_complet']} → *{ac_rec['montant_net']:,} FCFA* → {numero}\n"
                f"Réf : `{cashout_ref}`"
            )
        else:
            release_conn(conn)
            sess["etape"] = "menu"
            return (
                f"❌ *Échec du virement*\n\n"
                f"Erreur : {res.get('message','inconnue')}\n\n"
                f"Vérifiez le numéro et réessayez via l'option *4*."
            )

    elif sess.get("etape") == "modif_ordre_wa":
        if texte == "0":
            sess["etape"] = "menu"
            return "❌ Annulé."
        num  = normaliser_numero(texte)
        if num == wa:
            return "❌ Vous ne pouvez pas modifier votre propre rang."
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num,))
        if not m:
            release_conn(conn)
            return "❌ Numéro non trouvé."
        t       = fetchone(conn, "SELECT cycle_actuel FROM tontines WHERE id=%s", (tid,))
        passage = fetchone(conn, """
            SELECT id, ordre FROM liste_passage
            WHERE tontine_id=%s AND membre_id=%s AND cycle=%s AND statut='En_attente'
        """, (tid, m["id"], t["cycle_actuel"]))
        release_conn(conn)
        if not passage:
            return f"❌ {m['nom_complet']} n'a pas de passage en attente."
        sess["data"].update({"modif_mbr_id": m["id"], "modif_mbr_nom": m["nom_complet"],
                             "modif_pass_id": passage["id"], "modif_ordre_actuel": passage["ordre"]})
        sess["etape"] = "modif_ordre_nouv"
        return (
            f"👤 *{m['nom_complet']}* — Ordre actuel : n°{passage['ordre']}\n\n"
            f"Entrez le *nouvel ordre* (numéro de position) :"
        )

    elif sess.get("etape") == "modif_ordre_nouv":
        try:
            nouv_ordre = int(texte)
        except ValueError:
            return "❌ Numéro invalide."
        d           = sess["data"]
        ancien      = d["modif_ordre_actuel"]
        pass_id     = d["modif_pass_id"]
        conn        = get_conn()
        verrou_ordre_tontine(conn, tid)   # FORTERESSE : sérialise l'ordre de cette tontine
        t           = fetchone(conn, "SELECT cycle_actuel FROM tontines WHERE id=%s", (tid,))
        # Sortir le membre vers une sentinelle (-1) AVANT le décalage : sinon le shift
        # collisionne sur UNIQUE(tontine,cycle,ordre) tant que le membre occupe `ancien`.
        q(conn, "UPDATE liste_passage SET ordre=-1 WHERE id=%s", (pass_id,))
        # Décaler les autres membres (slot `ancien` désormais libre)
        if nouv_ordre < ancien:
            q(conn, """UPDATE liste_passage SET ordre=ordre+1
                       WHERE tontine_id=%s AND cycle=%s
                         AND ordre >= %s AND ordre < %s AND statut='En_attente'""",
              (tid, t["cycle_actuel"], nouv_ordre, ancien))
        else:
            q(conn, """UPDATE liste_passage SET ordre=ordre-1
                       WHERE tontine_id=%s AND cycle=%s
                         AND ordre > %s AND ordre <= %s AND statut='En_attente'""",
              (tid, t["cycle_actuel"], ancien, nouv_ordre))
        q(conn, "UPDATE liste_passage SET ordre=%s WHERE id=%s", (nouv_ordre, pass_id))
        conn.commit()
        release_conn(conn)
        log_audit("MODIF_ORDRE", f"{d['modif_mbr_nom']} : {ancien}→{nouv_ordre}", wa)
        sess["etape"] = "menu"
        return (
            f"✅ Ordre modifié : *{d['modif_mbr_nom']}* "
            f"déplacé de n°{ancien} → n°{nouv_ordre}."
        )

    # ── Option 5 : Suspendre / Réactiver ──────────────────────────────────
    elif texte == "5" and sess["etape"] == "menu":
        sess["etape"] = "susp_choix"
        return (
            "🔴 *SUSPENDRE / RÉACTIVER*\n"
            "A. Suspendre un membre\n"
            "B. Réactiver un membre\n"
            "0. Retour"
        )

    elif sess.get("etape") == "susp_choix":
        if texte.upper() == "A":
            sess["etape"] = "susp_wa"
            return "Entrez le *numéro WhatsApp* du membre à suspendre :"
        elif texte.upper() == "B":
            sess["etape"] = "react_wa"
            return "Entrez le *numéro WhatsApp* du membre à réactiver :"
        elif texte == "0":
            sess["etape"] = "menu"
            return "↩️ Retour menu."
        return "❌ Tapez A, B ou 0."

    elif sess.get("etape") == "susp_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num,))
        release_conn(conn)
        if not m:
            return "❌ Numéro non trouvé."
        sess["data"].update({"susp_id": m["id"], "susp_nom": m["nom_complet"], "susp_wa": num})
        sess["etape"] = "susp_raison"
        return f"👤 *{m['nom_complet']}*\nEntrez la raison de la suspension :"

    elif sess.get("etape") == "susp_raison":
        d      = sess["data"]
        raison = texte
        conn   = get_conn()
        q(conn, "UPDATE membres SET statut_global='Suspendu_global' WHERE id=%s", (d["susp_id"],))
        _update_score_confiance(conn, d["susp_id"], delta=-20, raison="Suspension globale par admin")
        q(conn, "UPDATE adhesions SET statut='Suspendu' WHERE membre_id=%s AND tontine_id=%s", (d["susp_id"], tid))
        q(conn, "INSERT INTO sanctions (membre_id, tontine_id, type_sanction, notes) VALUES (%s,%s,'Suspension_72h',%s)",
          (d["susp_id"], tid, raison))
        conn.commit()
        release_conn(conn)
        log_audit("SUSPENSION", f"{d['susp_nom']} — {raison}", d["susp_wa"])
        wa_prive(d["susp_wa"],
            f"🔴 *COMPTE SUSPENDU — BARACK CORP*\n"
            f"Raison : {raison}\n"
            f"Régularisez votre situation (réactivation : {FRAIS_REACTIV:,} FCFA).\n"
            f"Payez le code *REACTIV* pour réactiver votre compte."
        )
        sess["etape"] = "menu"
        return f"✅ *{d['susp_nom']}* suspendu. Raison : {raison}"

    elif sess.get("etape") == "react_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet, statut_global FROM membres WHERE whatsapp=%s", (num,))
        release_conn(conn)
        if not m:
            return "❌ Numéro non trouvé."
        if m["statut_global"] not in ("Suspendu_global",):
            sess["etape"] = "menu"
            return f"ℹ️ {m['nom_complet']} n'est pas suspendu (statut: {m['statut_global']})."
        conn = get_conn()
        q(conn, "UPDATE membres SET statut_global='Actif' WHERE id=%s", (m["id"],))
        q(conn, "UPDATE adhesions SET statut='Actif' WHERE membre_id=%s AND tontine_id=%s", (m["id"], tid))
        conn.commit()
        release_conn(conn)
        wa_prive(num, "✅ *Compte réactivé — Barack Corp*\nBienvenue de retour !")
        sess["etape"] = "menu"
        return f"✅ *{m['nom_complet']}* réactivé."

    # ── Option 6 : Historique complet membre ──────────────────────────────
    elif texte == "6" and sess["etape"] == "menu":
        sess["etape"] = "histo_wa"
        return "🔍 *HISTORIQUE MEMBRE*\nEntrez le numéro WhatsApp du membre :"

    elif sess.get("etape") == "histo_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT * FROM membres WHERE whatsapp=%s", (num,))
        if not m:
            release_conn(conn)
            return "❌ Numéro non trouvé."
        txs     = fetchall(conn, """
            SELECT type_transaction, montant_brut, frais_fmp, frais_ira,
                   montant_net, statut, date_heure, periodes_payees
            FROM transactions WHERE membre_id=%s ORDER BY date_heure DESC LIMIT 20
        """, (m["id"],))
        adhs    = fetchall(conn, """
            SELECT t.nom, a.statut, a.nombre_places, a.jours_avance
            FROM adhesions a JOIN tontines t ON t.id=a.tontine_id
            WHERE a.membre_id=%s
        """, (m["id"],))
        caution = fetchone(conn,
            "SELECT COALESCE(SUM(montant),0) t FROM cautions_garantie "
            "WHERE membre_id=%s AND statut='Bloquee'", (m["id"],))["t"]
        sanctions = fetchall(conn,
            "SELECT type_sanction, date_sanction, notes FROM sanctions "
            "WHERE membre_id=%s ORDER BY date_sanction DESC LIMIT 5", (m["id"],))
        release_conn(conn)
        lines = [
            f"🔍 *HISTORIQUE — {m['nom_complet']}*",
            f"📱 {num} | Score: {m['score_confiance']}/100",
            f"Statut: {m['statut_global']}",
            f"Bouffages: {m['nb_bouffages']} | Caution bloquée: {caution:,} FCFA\n",
            "*Tontines :*"
        ]
        for a in adhs:
            lines.append(f"  • {a['nom']} — {a['statut']} ({a['nombre_places']}p, +{a['jours_avance']}j avance)")
        lines.append("\n*20 dernières transactions :*")
        for t in txs:
            date = t["date_heure"].strftime("%d/%m %H:%M") if t["date_heure"] else "?"
            av   = f"x{t['periodes_payees']}" if t["periodes_payees"] > 1 else ""
            lines.append(f"  {'✅' if t['statut']=='Confirmee' else '❌'} {date} "
                         f"{t['type_transaction']}{av} {t['montant_brut']:,}F → net:{t['montant_net']:,}F")
        if sanctions:
            lines.append("\n*Sanctions :*")
            for s in sanctions:
                date = s["date_sanction"].strftime("%d/%m/%Y") if s["date_sanction"] else "?"
                lines.append(f"  ⚠️ {date} {s['type_sanction']} — {s['notes'] or ''}")
        sess["etape"] = "menu"
        return "\n".join(lines)

    # ── Option 7 : Rappel groupe ───────────────────────────────────────────
    elif texte == "7" and sess["etape"] == "menu":
        conn    = get_conn()
        t       = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tid,))
        retards = _get_retardataires(conn, tid)
        release_conn(conn)
        if t and t.get("whatsapp_groupe"):
            wa_mentionner_retardataires(
                t["whatsapp_groupe"], retards, t,
                datetime.now().strftime("%Hh%M")
            )
            return f"✅ Rappel envoyé dans *{tnom}*. ({len(retards)} retardataires mentionnés)"
        return "❌ Groupe WhatsApp non configuré."

    # ── Option 8 : Fugitifs ────────────────────────────────────────────────
    elif texte == "8" and sess["etape"] == "menu":
        conn    = get_conn()
        fug     = fetchall(conn, """
            SELECT m.nom_complet, m.whatsapp, m.score_confiance,
                   af.jours_retard, af.montant_du, af.type_alerte
            FROM alertes_fugue af JOIN membres m ON m.id=af.membre_id
            WHERE af.tontine_id=%s AND af.traite=0
            ORDER BY af.jours_retard DESC
        """, (tid,))
        release_conn(conn)
        if not fug:
            return f"✅ Aucun fugitif post-bouffage — *{tnom}*."
        lines = [f"🚨 *FUGITIFS — {tnom}*\n"]
        for f in fug:
            lines.append(
                f"⚠️ *{f['nom_complet']}* | {f['whatsapp']}\n"
                f"   Retard: {f['jours_retard']}j | Doit: {f['montant_du']:,}F\n"
                f"   Alerte: {f['type_alerte']} | Score: {f['score_confiance']}"
            )
        lines.append("\n_Tapez *10* pour saisir une caution._")
        return "\n".join(lines)

    # ── Option 9 : Cas difficiles ─────────────────────────────────────────
    elif texte == "9" and sess["etape"] == "menu":
        sess["etape"] = "cas_difficiles"
        return MENU_CAS_DIFFICILES

    elif sess.get("etape") == "cas_difficiles":
        t = texte.upper()
        if t == "0":
            sess["etape"] = "menu"
            return "↩️ " + MENU_ADMIN_TXT
        elif t == "A":
            sess["etape"] = "pause_wa"
            return "⏸️ *PAUSE*\nEntrez le numéro WhatsApp du membre :"
        elif t == "B":
            sess["etape"] = "echel_wa"
            return "📅 *ÉCHELONNEMENT*\nEntrez le numéro WhatsApp du membre :"
        elif t == "C":
            sess["etape"] = "cession_wa"
            return "🔄 *CESSION DE PLACE*\nEntrez le numéro WhatsApp du cédant :"
        elif t == "D":
            sess["etape"] = "exoner_wa"
            return "✳️ *EXONÉRATION*\nEntrez le numéro WhatsApp du membre :"
        elif t == "E":
            sess["etape"] = "exclus_wa"
            return "🚫 *EXCLUSION*\nEntrez le numéro WhatsApp du membre :"
        return "❌ Tapez A, B, C, D, E ou 0."

    # ── Pause ──────────────────────────────────────────────────────────────
    elif sess.get("etape") == "pause_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num,))
        release_conn(conn)
        if not m:
            return "❌ Numéro non trouvé."
        sess["data"].update({"cas_mbr_id": m["id"], "cas_mbr_nom": m["nom_complet"], "cas_wa": num})
        sess["etape"] = "pause_date"
        return f"👤 *{m['nom_complet']}*\nEntrez la *date de reprise* (format: JJ/MM/AAAA) :"

    elif sess.get("etape") == "pause_date":
        if not re.match(r"^\d{2}/\d{2}/\d{4}$", texte):
            return "❌ Format incorrect. Ex: 15/06/2026"
        d    = sess["data"]
        conn = get_conn()
        q(conn, "UPDATE adhesions SET statut='Pause' WHERE membre_id=%s AND tontine_id=%s", (d["cas_mbr_id"], tid))
        q(conn, """INSERT INTO cas_difficiles (membre_id, tontine_id, type_cas, details, date_reprise, admin_id)
                   VALUES (%s,%s,'Pause',%s,%s,%s)""",
          (d["cas_mbr_id"], tid, f"Pause jusqu'au {texte}", texte, wa))
        conn.commit()
        release_conn(conn)
        wa_prive(d["cas_wa"],
            f"⏸️ *PAUSE ACCORDÉE — {tnom}*\n"
            f"Votre cotisation est suspendue jusqu'au *{texte}*.\n"
            f"Reprise automatique à cette date.")
        sess["etape"] = "menu"
        return f"✅ Pause accordée à *{d['cas_mbr_nom']}* jusqu'au {texte}."

    # ── Échelonnement ──────────────────────────────────────────────────────
    elif sess.get("etape") == "echel_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num,))
        release_conn(conn)
        if not m:
            return "❌ Numéro non trouvé."
        sess["data"].update({"cas_mbr_id": m["id"], "cas_mbr_nom": m["nom_complet"], "cas_wa": num})
        sess["etape"] = "echel_config"
        return (
            f"👤 *{m['nom_complet']}*\n\n"
            f"Entrez : *[nombre de tranches] [montant par tranche]*\n"
            f"Exemple : *3 5000* (3 tranches de 5 000 FCFA)"
        )

    elif sess.get("etape") == "echel_config":
        try:
            parts = texte.split()
            nb    = int(parts[0])
            mont  = int(parts[1])
        except (ValueError, IndexError):
            return "❌ Format invalide. Ex : *3 5000*"
        d    = sess["data"]
        conn = get_conn()
        q(conn, """INSERT INTO cas_difficiles
                   (membre_id, tontine_id, type_cas, nb_tranches, montant_tranche, admin_id)
                   VALUES (%s,%s,'Echelonnement',%s,%s,%s)""",
          (d["cas_mbr_id"], tid, nb, mont, wa))
        conn.commit()
        release_conn(conn)
        wa_prive(d["cas_wa"],
            f"📅 *ÉCHELONNEMENT ACCORDÉ — {tnom}*\n"
            f"{nb} tranches de *{mont:,} FCFA*\n"
            f"Payez chaque tranche via le code habituel.")
        sess["etape"] = "menu"
        return f"✅ Échelonnement : *{d['cas_mbr_nom']}* — {nb}×{mont:,} FCFA."

    # ── Cession de place ───────────────────────────────────────────────────
    elif sess.get("etape") == "cession_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num,))
        release_conn(conn)
        if not m:
            return "❌ Numéro non trouvé."
        sess["data"].update({"cas_mbr_id": m["id"], "cas_mbr_nom": m["nom_complet"]})
        sess["etape"] = "cession_cessionnaire"
        return f"👤 Cédant : *{m['nom_complet']}*\nEntrez le numéro du *bénéficiaire* (cessionnaire) :"

    elif sess.get("etape") == "cession_cessionnaire":
        num2 = normaliser_numero(texte)
        conn = get_conn()
        m2   = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num2,))
        if not m2:
            release_conn(conn)
            return "❌ Cessionnaire non trouvé."
        d    = sess["data"]
        t_db = fetchone(conn, "SELECT cycle_actuel FROM tontines WHERE id=%s", (tid,))
        q(conn, """UPDATE liste_passage SET membre_id=%s
                   WHERE tontine_id=%s AND membre_id=%s AND cycle=%s AND statut='En_attente'""",
          (m2["id"], tid, d["cas_mbr_id"], t_db["cycle_actuel"]))
        q(conn, "UPDATE adhesions SET statut='Quitte' WHERE membre_id=%s AND tontine_id=%s",
          (d["cas_mbr_id"], tid))
        q(conn, """INSERT INTO cas_difficiles
                   (membre_id, tontine_id, type_cas, cessionnaire_id, admin_id)
                   VALUES (%s,%s,'Cession',%s,%s)""",
          (d["cas_mbr_id"], tid, m2["id"], wa))
        conn.commit()
        release_conn(conn)
        wa_prive(num2,
            f"🔄 *CESSION DE PLACE — {tnom}*\n"
            f"La place de *{d['cas_mbr_nom']}* vous a été cédée.\n"
            f"Vous figurez maintenant dans l'ordre de bouffage.")
        sess["etape"] = "menu"
        return (
            f"✅ Cession effectuée :\n"
            f"*{d['cas_mbr_nom']}* → *{m2['nom_complet']}*"
        )

    # ── Exonération ────────────────────────────────────────────────────────
    elif sess.get("etape") == "exoner_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num,))
        release_conn(conn)
        if not m:
            return "❌ Numéro non trouvé."
        sess["data"].update({"cas_mbr_id": m["id"], "cas_mbr_nom": m["nom_complet"], "cas_wa": num})
        sess["etape"] = "exoner_type"
        return (
            f"👤 *{m['nom_complet']}*\n\n"
            f"Que voulez-vous exonérer ?\n"
            f"A. Pénalité IRA\n"
            f"B. Pénalité retard\n"
            f"C. Les deux"
        )

    elif sess.get("etape") == "exoner_type":
        d    = sess["data"]
        t    = texte.upper()
        conn = get_conn()
        if t in ("A", "C"):
            q(conn, "UPDATE dettes_ira SET statut='Prelevee', prelevee_le=NOW() WHERE membre_id=%s AND statut='Due'",
              (d["cas_mbr_id"],))
        if t in ("B", "C"):
            q(conn, "UPDATE membres SET solde_dette=0 WHERE id=%s", (d["cas_mbr_id"],))
        q(conn, """INSERT INTO cas_difficiles
                   (membre_id, tontine_id, type_cas, details, admin_id)
                   VALUES (%s,%s,'Exoneration',%s,%s)""",
          (d["cas_mbr_id"], tid, f"Type: {t}", wa))
        conn.commit()
        release_conn(conn)
        wa_prive(d["cas_wa"],
            f"✳️ *EXONÉRATION ACCORDÉE — {tnom}*\n"
            f"Vos pénalités ont été effacées par l'administration.")
        sess["etape"] = "menu"
        return f"✅ Exonération accordée à *{d['cas_mbr_nom']}*."

    # ── Exclusion ──────────────────────────────────────────────────────────
    elif sess.get("etape") == "exclus_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn, "SELECT id, nom_complet FROM membres WHERE whatsapp=%s", (num,))
        release_conn(conn)
        if not m:
            return "❌ Numéro non trouvé."
        sess["data"].update({"cas_mbr_id": m["id"], "cas_mbr_nom": m["nom_complet"], "cas_wa": num})
        sess["etape"] = "exclus_raison"
        return (
            f"🚫 *EXCLUSION DÉFINITIVE*\n"
            f"Membre : *{m['nom_complet']}*\n\n"
            f"⚠️ Cette action est irréversible.\n"
            f"Entrez la raison de l'exclusion :"
        )

    elif sess.get("etape") == "exclus_raison":
        d      = sess["data"]
        raison = texte
        conn   = get_conn()
        q(conn, "UPDATE membres SET statut_global='Banni', blackliste=1 WHERE id=%s",
          (d["cas_mbr_id"],))
        _update_score_confiance(conn, d["cas_mbr_id"], set_val=0, raison="Bannissement par admin")
        q(conn, "UPDATE adhesions SET statut='Quitte' WHERE membre_id=%s AND tontine_id=%s",
          (d["cas_mbr_id"], tid))
        q(conn, "INSERT INTO sanctions (membre_id, tontine_id, type_sanction, notes) VALUES (%s,%s,'Exclusion',%s)",
          (d["cas_mbr_id"], tid, raison))
        q(conn, "INSERT INTO cas_difficiles (membre_id, tontine_id, type_cas, details, admin_id) VALUES (%s,%s,'Exclusion',%s,%s)",
          (d["cas_mbr_id"], tid, raison, wa))
        conn.commit()
        release_conn(conn)
        log_audit("EXCLUSION", f"{d['cas_mbr_nom']} — {raison}", d["cas_wa"])
        wa_prive(d["cas_wa"],
            f"🚫 *EXCLUSION — BARACK CORP*\n"
            f"Vous avez été exclu définitivement de la tontine *{tnom}*.\n"
            f"Raison : {raison}\n\n" + msg_dissuasion(wa)
        )
        # Kick automatique du groupe si bot est admin
        kick_ok = kick_membre_si_bot_admin(d["cas_mbr_id"], tid, f"Exclusion : {raison}")
        kick_txt = " Retiré du groupe." if kick_ok else ""
        sess["etape"] = "menu"
        return f"✅ *{d['cas_mbr_nom']}* exclu définitivement. Raison : {raison}{kick_txt}"

    # ── Option 10 : Saisir caution fugitif ────────────────────────────────
    elif texte == "10" and sess["etape"] == "menu":
        sess["etape"] = "saisie_caution_wa"
        return (
            "🔒 *SAISIE CAUTION FUGITIF*\n\n"
            "Entrez le numéro WhatsApp du fugitif :"
        )

    elif sess.get("etape") == "saisie_caution_wa":
        if texte.strip() == "0":
            sess["etape"] = "menu"
            return "❌ Annulé."
        num  = normaliser_numero(texte)
        conn = get_conn()
        m    = fetchone(conn,
            "SELECT id, nom_complet, whatsapp FROM membres WHERE whatsapp=%s", (num,))
        if not m:
            release_conn(conn)
            return "❌ Numéro non trouvé en base."
        caution = fetchone(conn,
            "SELECT id, montant FROM cautions_garantie "
            "WHERE membre_id=%s AND tontine_id=%s AND statut='Bloquee'",
            (m["id"], tid))
        if not caution:
            release_conn(conn)
            return f"❌ Aucune caution bloquée pour *{m['nom_complet']}* dans *{tnom}*."

        # Saisir caution + compenser le groupe
        res = saisir_caution_et_compenser_groupe(
            conn,
            membre_id  = m["id"],
            tontine_id = tid,
            raison     = "Saisie manuelle par admin — fugitif"
        )
        montant_saisi = res.get("caution", 0)
        compense      = res.get("compense", 0)
        reliquat      = res.get("reliquat", 0)

        # Bannir le membre
        q(conn, """UPDATE membres
                   SET statut_global='Banni', blackliste=1
                   WHERE id=%s""", (m["id"],))
        _update_score_confiance(conn, m["id"], set_val=0, raison="Bannissement — fugue post-bouffage")
        q(conn, """INSERT INTO sanctions
                   (membre_id, tontine_id, type_sanction, montant_penalite, notes)
                   VALUES (%s,%s,'Interception_bouffage',%s,'Saisie caution fugitif — réserve BADF')""",
          (m["id"], tid, caution["montant"]))
        q(conn, """UPDATE alertes_fugue SET traite=1
                   WHERE membre_id=%s AND tontine_id=%s""", (m["id"], tid))
        conn.commit()
        release_conn(conn)
        # Kick automatique du groupe si bot est admin
        kick_ok  = kick_membre_si_bot_admin(m["id"], tid, "Fugitif — caution saisie")
        kick_txt = "\n🚪 Membre retiré du groupe." if kick_ok else ""

        sess["etape"] = "menu"
        return (
            f"✅ *CAUTION SAISIE — {tnom}*\n\n"
            f"Fugitif        : *{m['nom_complet']}*\n"
            f"Caution saisie : *{montant_saisi:,} FCFA*\n\n"
            f"Utilisation :\n"
            f"  ▪ Compensé au groupe : *{compense:,} FCFA*\n"
            f"  ▪ Reversé à BADF     : *{reliquat:,} FCFA*\n\n"
            f"*{m['nom_complet']}* a été banni définitivement du système."
            f"{kick_txt}"
        )

    # ── Option 11 : Ajouter membre manuel ────────────────────────────────
    elif texte == "11" and sess["etape"] == "menu":
        sess["etape"] = "ajout_mbr_nom"
        return "➕ *AJOUTER MEMBRE*\nEntrez le *nom complet* du nouveau membre :"

    elif sess.get("etape") == "ajout_mbr_nom":
        sess["data"]["ajout_nom"] = texte.upper().strip()
        sess["etape"] = "ajout_mbr_wa"
        return "Entrez le *numéro WhatsApp* (+237...) :"

    elif sess.get("etape") == "ajout_mbr_wa":
        num = normaliser_numero(texte)
        if not valider_numero_cameroun(num):
            return "❌ Numéro camerounais invalide (+237...)."
        d    = sess["data"]
        nom  = d["ajout_nom"]
        conn = get_conn()
        existant = fetchone(conn, "SELECT id FROM membres WHERE whatsapp=%s", (num,))
        if existant:
            # Membre existe déjà → proposer de l'inscrire dans cette tontine
            mid = existant["id"]
            release_conn(conn)
            try:
                inscrire_dans_tontine(mid, tid)
                log_audit("INSCRIPTION_MANUELLE", f"Membre {mid} → tontine {tid}", wa)
                wa_prive(num,
                    f"👋 Bienvenue dans *{tnom}* !\n"
                    f"Vous avez été ajouté à cette tontine par un admin.")
                sess["etape"] = "menu"
                return f"✅ Membre existant (ID:{mid}) inscrit dans *{tnom}*."
            except Exception as e:
                sess["etape"] = "menu"
                return f"❌ {e}"
        kyc_hash = hashlib.sha256(f"{nom}{num}MANUEL".encode()).hexdigest()
        cur = q(conn, """INSERT INTO membres (nom_complet, kyc_hash, whatsapp, adhesion_payee, statut_global)
                         VALUES (%s,%s,%s,1,'En_attente_kyc') RETURNING id""",
                (nom, kyc_hash, num))
        mid = cur.fetchone()[0]
        conn.commit()
        release_conn(conn)
        inscrire_dans_tontine(mid, tid)
        log_audit("AJOUT_MANUEL", f"{nom} par admin {wa}", num)
        wa_prive(num,
            f"👋 Bienvenue *{nom}* dans Barack Corp !\n"
            f"Un admin vous a ajouté à la tontine *{tnom}*.\n"
            f"Complétez votre KYC en tapant *menu*."
        )
        sess["etape"] = "menu"
        return f"✅ *{nom}* ({num}) ajouté et inscrit dans *{tnom}*. ID:{mid}"

    # ── Option 13 : Saisir l'ordre initial de bouffage ───────────────────
    elif texte == "13" and sess["etape"] == "menu":
        conn    = get_conn()
        membres = fetchall(conn, """
            SELECT m.id, m.nom_complet, lp.ordre
            FROM adhesions a
            JOIN membres m ON m.id = a.membre_id
            LEFT JOIN liste_passage lp
                ON lp.membre_id = m.id AND lp.tontine_id = a.tontine_id
                AND lp.cycle = (SELECT cycle_actuel FROM tontines WHERE id = a.tontine_id)
                AND lp.statut = 'En_attente'
            WHERE a.tontine_id=%s AND a.statut='Actif'
            ORDER BY lp.ordre NULLS LAST, m.nom_complet
        """, (tid,))
        release_conn(conn)
        if not membres:
            return "❌ Aucun membre actif dans cette tontine."
        liste = "\n".join(
            f"  {i+1}. {m['nom_complet']}"
            for i, m in enumerate(membres)
        )
        sess["etape"]          = "ordre_init_saisie"
        sess["data"]["ordre_membres"] = membres
        return (
            f"📋 *ORDRE INITIAL DE BOUFFAGE — {tnom}*\n\n"
            f"Membres actuels ({len(membres)}) :\n{liste}\n\n"
            f"─────────────────────────────────────\n"
            f"Entrez l'ordre souhaité en séparant les numéros par des virgules.\n\n"
            f"Exemple (passer membre 3 en premier) :\n"
            f"*3,1,2,4,5,...*\n\n"
            f"Tapez *0* pour annuler."
        )

    elif sess.get("etape") == "ordre_init_saisie":
        if texte.strip() == "0":
            sess["etape"] = "menu"
            return "❌ Annulé.\n_Tapez un numéro de menu._"
        membres = sess["data"].get("ordre_membres", [])
        try:
            positions = [int(x.strip()) for x in texte.split(",")]
            if sorted(positions) != list(range(1, len(membres) + 1)):
                return (
                    f"❌ Ordre invalide.\n"
                    f"Vous devez utiliser chaque numéro de 1 à {len(membres)} exactement une fois.\n"
                    f"Exemple : *1,2,3,...,{len(membres)}*"
                )
        except ValueError:
            return "❌ Format invalide. Entrez des numéros séparés par des virgules."
        conn = get_conn()
        cycle = fetchone(conn,
            "SELECT cycle_actuel FROM tontines WHERE id=%s", (tid,))["cycle_actuel"]
        # Réinitialiser la liste de passage et recréer dans le bon ordre
        q(conn, """DELETE FROM liste_passage
                   WHERE tontine_id=%s AND cycle=%s AND statut='En_attente'""",
          (tid, cycle))
        for nouveau_ordre, position in enumerate(positions, start=1):
            membre = membres[position - 1]
            # Créer autant de lignes que de places pour ce membre
            nb_places = fetchone(conn,
                "SELECT nombre_places FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
                (membre["id"], tid))
            places = nb_places["nombre_places"] if nb_places else 1
            for p in range(places):
                q(conn, """INSERT INTO liste_passage
                           (tontine_id, membre_id, cycle, ordre)
                           VALUES (%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                  (tid, membre["id"], cycle, nouveau_ordre + p))
        conn.commit()
        release_conn(conn)
        log_audit("ORDRE_INITIAL", f"Tontine {tnom} reordonné par {wa}")
        # Construire le récap
        recap = "\n".join(
            f"  {nouveau_ordre}. {membres[pos-1]['nom_complet']}"
            for nouveau_ordre, pos in enumerate(positions, start=1)
        )
        sess["etape"] = "menu"
        return (
            f"✅ *ORDRE DE BOUFFAGE ENREGISTRÉ — {tnom}*\n\n"
            f"{recap}\n\n"
            f"_Cet ordre prend effet immédiatement._"
        )

    # ── Option 14 : Configurer les heures ────────────────────────────────
    # L'admin contrôle toutes les heures de sa tontine :
    #   heure_ouverture  → début des cotisations (message dans le groupe)
    #   heure_limite     → fin des cotisations (après = pénalité IRA)
    #   heure_rappel     → rappel des non-cotisants dans le groupe
    #   heure_bouffage   → DM au bénéficiaire pour cashout
    elif texte == "14" and sess["etape"] == "menu":
        conn    = get_conn()
        t       = fetchone(conn,
            "SELECT heure_ouverture, heure_limite, heure_rappel, heure_bouffage "
            "FROM tontines WHERE id=%s", (tid,))
        release_conn(conn)
        sess["etape"] = "config_heures"
        return (
            f"🕐 *CONFIGURATION DES HEURES — {tnom}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Heures actuelles :\n"
            f"  1️⃣  Ouverture cotisations : *{t['heure_ouverture'] or '05:00'}*\n"
            f"  2️⃣  Limite cotisations    : *{t['heure_limite']    or '18:00'}*\n"
            f"  3️⃣  Rappel non-cotisants  : *{t['heure_rappel']    or '14:00'}*\n"
            f"  4️⃣  Heure de bouffage     : *{t['heure_bouffage']  or '17:00'}*\n\n"
            f"─────────────────────────────────────────\n"
            f"Tapez le *numéro* de l'heure à modifier.\n"
            f"Tapez *0* pour annuler."
        )

    elif sess.get("etape") == "config_heures":
        if texte.strip() == "0":
            sess["etape"] = "menu"
            return "❌ Annulé.\n_Tapez un numéro de menu._"
        choix_heure = {
            "1": ("heure_ouverture", "Ouverture cotisations",
                  "Les membres seront informés que les dépôts sont ouverts."),
            "2": ("heure_limite",    "Limite cotisations",
                  "Après cette heure : IRA = {}% de la mise + {}%/jour (plafond {}%).".format(
                      int(IRA_CLIFF_PCT*100), int(IRA_DAILY_PCT*100), int(IRA_CAP_PCT*100))),
            "3": ("heure_rappel",    "Rappel non-cotisants",
                  "Liste des non-cotisants publiée dans le groupe."),
            "4": ("heure_bouffage",  "Bouffage",
                  "Le bénéficiaire recevra son DM à cette heure."),
        }
        if texte.strip() not in choix_heure:
            return "❌ Tapez *1*, *2*, *3* ou *4* pour choisir l'heure à modifier, ou *0* pour annuler."
        col, label, explication = choix_heure[texte.strip()]
        sess["data"]["heure_col"]   = col
        sess["data"]["heure_label"] = label
        sess["etape"] = "saisie_heure"
        return (
            f"✏️ *{label.upper()} — {tnom}*\n\n"
            f"_{explication}_\n\n"
            f"Entrez la nouvelle heure au format *HH:MM*\n"
            f"Exemples : *05:00* · *12:30* · *18:00*\n\n"
            f"Tapez *0* pour annuler."
        )

    elif sess.get("etape") == "saisie_heure":
        if texte.strip() == "0":
            sess["etape"] = "config_heures"
            # Réafficher le menu des heures
            conn = get_conn()
            t    = fetchone(conn,
                "SELECT heure_ouverture, heure_limite, heure_rappel, heure_bouffage "
                "FROM tontines WHERE id=%s", (tid,))
            release_conn(conn)
            return (
                f"🕐 *CONFIGURATION DES HEURES — {tnom}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"  1️⃣  Ouverture cotisations : *{t['heure_ouverture'] or '05:00'}*\n"
                f"  2️⃣  Limite cotisations    : *{t['heure_limite']    or '18:00'}*\n"
                f"  3️⃣  Rappel non-cotisants  : *{t['heure_rappel']    or '14:00'}*\n"
                f"  4️⃣  Heure de bouffage     : *{t['heure_bouffage']  or '17:00'}*\n\n"
                f"Tapez le numéro à modifier ou *0* pour annuler."
            )

        if not re.match(r"^\d{1,2}:\d{2}$", texte.strip()):
            return "❌ Format invalide. Utilisez *HH:MM* (ex: *14:00*)"
        heure_str = texte.strip()
        try:
            h, m = map(int, heure_str.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return "❌ Heure invalide. Heure entre 00 et 23, minutes entre 00 et 59."
        except ValueError:
            return "❌ Format invalide. Utilisez *HH:MM*"

        col   = sess["data"]["heure_col"]
        label = sess["data"]["heure_label"]

        # Ouverture/Rappel/Bouffage sont déclenchés par des jobs cron HORAIRES
        # (notifier_prochain_bouffage, rappel_ouverture, rappel_non_cotisants)
        # qui comparent l'heure configurée à datetime.now().strftime("%H:00") —
        # une minute ≠ 00 ne matchera JAMAIS et le déclenchement ne se produira
        # silencieusement jamais. Seule heure_limite tolère des minutes libres
        # (comparée en continu, pas via un cron horaire).
        _COLONNES_HEURE_PLEINE = {"heure_ouverture", "heure_rappel", "heure_bouffage"}
        if col in _COLONNES_HEURE_PLEINE and m != 0:
            return (
                f"❌ *{label}* se déclenche par un job qui tourne une fois par "
                f"heure — les minutes doivent être *:00* (ex: *{h:02d}:00*).\n\n"
                f"Entrez une autre heure ou tapez *0* pour annuler."
            )

        # Validation logique des heures entre elles
        conn  = get_conn()
        t_act = fetchone(conn,
            "SELECT heure_ouverture, heure_limite, heure_rappel, heure_bouffage "
            "FROM tontines WHERE id=%s", (tid,))

        def hm(s): return tuple(map(int, (s or "00:00").split(":")))

        heures = {
            "heure_ouverture": hm(t_act["heure_ouverture"]),
            "heure_limite":    hm(t_act["heure_limite"]),
            "heure_rappel":    hm(t_act["heure_rappel"]),
            "heure_bouffage":  hm(t_act["heure_bouffage"]),
        }
        heures[col] = hm(heure_str)

        erreur_logique = None
        if heures["heure_ouverture"] >= heures["heure_limite"]:
            erreur_logique = "L'ouverture doit être *avant* la limite de cotisation."
        elif heures["heure_rappel"] <= heures["heure_ouverture"]:
            erreur_logique = "Le rappel doit être *après* l'ouverture."
        elif heures["heure_rappel"] >= heures["heure_limite"]:
            erreur_logique = "Le rappel doit être *avant* la limite de cotisation."
        elif heures["heure_bouffage"] <= heures["heure_limite"]:
            erreur_logique = "Le bouffage doit être *après* la limite de cotisation."

        if erreur_logique:
            release_conn(conn)
            return (
                f"❌ *Incohérence d'horaires*\n\n"
                f"{erreur_logique}\n\n"
                f"Ordre requis :\n"
                f"  Ouverture < Rappel < Limite < Bouffage\n\n"
                f"Entrez une autre heure ou tapez *0* pour annuler."
            )

        _COLONNES_HEURE_OK = {"heure_ouverture", "heure_limite", "heure_rappel", "heure_bouffage"}
        if col not in _COLONNES_HEURE_OK:
            log.error(f"🔴 Colonne invalide tentée dans UPDATE tontines : {col}")
            return "❌ Erreur interne."
        q(conn, f"UPDATE tontines SET {col}=%s WHERE id=%s", (heure_str, tid))
        conn.commit()

        # Récupérer toutes les heures mises à jour pour l'annonce
        t_new = fetchone(conn,
            "SELECT heure_ouverture, heure_limite, heure_rappel, heure_bouffage, "
            "whatsapp_groupe FROM tontines WHERE id=%s", (tid,))
        release_conn(conn)

        log_audit("CHGMT_HEURE",
                  f"Tontine {tnom} : {col} → {heure_str}", wa)

        # Annoncer dans le groupe
        if t_new and t_new.get("whatsapp_groupe"):
            wa_groupe(t_new["whatsapp_groupe"],
                f"📢 *{tnom} — MODIFICATION DES HORAIRES*\n\n"
                f"🕐 Ouverture cotisations : *{t_new['heure_ouverture']}*\n"
                f"🕐 Limite cotisations    : *{t_new['heure_limite']}*\n"
                f"🕐 Rappel non-cotisants  : *{t_new['heure_rappel']}*\n"
                f"🏆 Bouffage              : *{t_new['heure_bouffage']}*\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )

        sess["etape"] = "menu"
        return (
            f"✅ *{label}* mise à jour : *{heure_str}*\n\n"
            f"Récapitulatif — {tnom} :\n"
            f"  🕐 Ouverture : *{t_new['heure_ouverture']}*\n"
            f"  🕐 Limite    : *{t_new['heure_limite']}*\n"
            f"  🕐 Rappel    : *{t_new['heure_rappel']}*\n"
            f"  🏆 Bouffage  : *{t_new['heure_bouffage']}*\n\n"
            f"Le groupe a été notifié."
        )

    # ── Option 15 : Confirmer / Rejeter cotisations (PATCH 5 v9.18 — OUI/NON) ─
    elif texte == "15" and sess["etape"] == "menu":
        conn = get_conn()
        en_attente = fetchall(conn, """
            SELECT cm.id, m.nom_complet, cm.montant_declare, cm.fmp_du,
                   cm.date_soumission, a.nombre_places
            FROM cotisations_manuelles cm
            JOIN membres m ON m.id = cm.membre_id
            JOIN adhesions a ON a.membre_id = cm.membre_id AND a.tontine_id = cm.tontine_id
            WHERE cm.tontine_id=%s AND cm.statut='En_attente'
            ORDER BY cm.date_soumission ASC
        """, (tid,))
        release_conn(conn)

        if not en_attente:
            return (
                f"✅ *Aucune cotisation en attente — {tnom}*\n\n"
                f"Toutes les cotisations ont été traitées."
            )

        # On affiche la PREMIÈRE en attente, l'admin répond OUI/NON
        sess["etape"]            = "confirm_cotisation"
        sess["cotis_en_cours"]   = en_attente[0]["id"]
        sess["cotis_restantes"]  = [c["id"] for c in en_attente[1:]]

        c = en_attente[0]
        dt = c["date_soumission"].strftime("%d/%m %H:%M") if c["date_soumission"] else "?"
        places_txt = f" ×{c['nombre_places']}" if c["nombre_places"] > 1 else ""
        total = len(en_attente)

        _fmp = f"   💼 FMP : {c['fmp_du']:,} FCFA\n" if FRAIS_FMP > 0 else ""
        return (
            f"📋 *COTISATION 1/{total} — {tnom}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔖 *#{c['id']}* — {c['nom_complet']}{places_txt}\n"
            f"   💰 {c['montant_declare']:,} FCFA\n"
            f"{_fmp}"
            f"   📅 Soumise : {dt}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"❓ *Le transfert a-t-il bien été reçu ?*\n\n"
            f"   ✅ Tapez *OUI* pour confirmer\n"
            f"   ❌ Tapez *NON [raison]* pour rejeter\n"
            f"   ⏭️  Tapez *PASSER* pour voir la suivante\n"
            f"   ↩️  Tapez *0* pour retour menu"
        )

    elif sess.get("etape") == "confirm_cotisation":
        texte_up = texte.strip().upper()
        cotis_id = sess.get("cotis_en_cours")

        # Retour menu
        if texte_up == "0":
            sess["etape"] = "menu"
            sess.pop("cotis_en_cours",  None)
            sess.pop("cotis_restantes", None)
            return "↩️ " + MENU_ADMIN_TXT

        # Pas de cotisation en cours (cas anormal)
        if not cotis_id:
            sess["etape"] = "menu"
            return "❌ Aucune cotisation active. Retour menu.\n\n" + MENU_ADMIN_TXT

        # OUI → confirmer
        if texte_up == "OUI":
            conn = get_conn()
            try:
                res = confirmer_cotisation(conn, cotis_id, wa)
                release_conn(conn)
                if not res.get("ok"):
                    msg_resultat = f"❌ {res.get('msg', 'Erreur inconnue')}"
                else:
                    msg_resultat = f"✅ *Cotisation #{cotis_id} CONFIRMÉE*"
            except Exception as e:
                try:    conn.rollback()
                except: pass
                release_conn(conn)
                log.error(f"confirmer_cotisation #{cotis_id} erreur: {e}")
                msg_resultat = f"❌ Erreur confirmation #{cotis_id} : {str(e)[:80]}"

            return _passer_a_cotisation_suivante(sess, tid, tnom, msg_resultat)

        # NON [raison] → rejeter
        if texte_up.startswith("NON"):
            parts  = texte.strip().split(None, 1)
            raison = parts[1] if len(parts) > 1 else "Rejeté par l'admin"
            conn   = get_conn()
            try:
                res = rejeter_cotisation(conn, cotis_id, wa, raison)
                release_conn(conn)
                if not res.get("ok"):
                    msg_resultat = f"❌ {res.get('msg', 'Erreur inconnue')}"
                else:
                    msg_resultat = (
                        f"❌ *Cotisation #{cotis_id} REJETÉE*\n"
                        f"📝 Raison : _{raison}_"
                    )
            except Exception as e:
                try:    conn.rollback()
                except: pass
                release_conn(conn)
                log.error(f"rejeter_cotisation #{cotis_id} erreur: {e}")
                msg_resultat = f"❌ Erreur rejet #{cotis_id} : {str(e)[:80]}"

            return _passer_a_cotisation_suivante(sess, tid, tnom, msg_resultat)

        # PASSER → cotisation suivante sans décision
        if texte_up == "PASSER":
            return _passer_a_cotisation_suivante(
                sess, tid, tnom,
                f"⏭️  Cotisation #{cotis_id} reportée (toujours en attente)."
            )

        # Commande non reconnue
        return (
            "❓ *Commande non reconnue.*\n\n"
            "   ✅ *OUI* → confirmer\n"
            "   ❌ *NON [raison]* → rejeter\n"
            "   ⏭️  *PASSER* → suivante\n"
            "   ↩️  *0* → retour menu"
        )

    # ── Commande BOUFFAGE_COMPLET — cas grave, admin donne tout ─────────
    if texte.upper().startswith("BOUFFAGE_COMPLET ") and sess["etape"] == "menu":
        parts = texte.strip().split(None, 2)
        if len(parts) < 2 or not parts[1].isdigit():
            return "❌ Format : *BOUFFAGE_COMPLET 42 raison*"
        passage_id = int(parts[1])
        raison     = parts[2] if len(parts) > 2 else "Cas exceptionnel — décision admin"
        conn = get_conn()
        passage = fetchone(conn, """
            SELECT lp.*, m.nom_complet, m.whatsapp
            FROM liste_passage lp
            LEFT JOIN membres m ON m.id = lp.membre_id
            WHERE lp.id=%s AND lp.tontine_id=%s AND lp.bloque_suspect=1
        """, (passage_id, tid))
        if not passage:
            release_conn(conn)
            return f"❌ Passage suspect #{passage_id} introuvable."

        # Anti-fraude : un admin ne peut pas accorder son propre bouffage
        if passage.get("whatsapp") == wa:
            release_conn(conn)
            return "❌ Vous ne pouvez pas accorder un bouffage sur votre propre numéro."

        # Réinitialiser le blocage + déclencher bouffage complet
        q(conn, """UPDATE liste_passage
                   SET statut='En_attente', bloque_suspect=0, date_blocage=NULL
                   WHERE id=%s""", (passage_id,))
        conn.commit()
        release_conn(conn)

        log_audit("BOUFFAGE_COMPLET_ADMIN",
                  f"Passage#{passage_id} | {passage['nom_complet']} | "
                  f"Admin:{wa} | Raison:{raison}")
        wa_owner(
            f"✅ *BOUFFAGE COMPLET ACCORDÉ*\n"
            f"Admin   : {wa}\n"
            f"Membre  : {passage['nom_complet']}\n"
            f"Tontine : {tnom}\n"
            f"Raison  : {raison}"
        )
        return (
            f"✅ Bouffage complet accordé à *{passage['nom_complet']}*.\n"
            f"Raison : _{raison}_\n\n"
            f"Il recevra la cagnotte intégrale à la prochaine heure de bouffage.\n"
            f"Cette décision a été notifiée au owner BADF."
        )

    # ── Commande DEBLOQUER — débloquer un bouffage suspendu ──────────────
    if texte.upper().startswith("DEBLOQUER ") and sess["etape"] == "menu":
        parts = texte.strip().split()
        if len(parts) < 2 or not parts[1].isdigit():
            return "❌ Format : *DEBLOQUER 42* (ID du passage)"
        passage_id = int(parts[1])
        conn = get_conn()
        passage = fetchone(conn, """
            SELECT lp.*, m.nom_complet
            FROM liste_passage lp
            LEFT JOIN membres m ON m.id = lp.membre_id
            WHERE lp.id=%s AND lp.tontine_id=%s
        """, (passage_id, tid))
        if not passage:
            release_conn(conn)
            return f"❌ Passage #{passage_id} introuvable pour cette tontine."
        q(conn, "UPDATE liste_passage SET statut='En_attente' WHERE id=%s",
          (passage_id,))
        conn.commit()
        release_conn(conn)
        log_audit("BOUFFAGE_DEBLOQUE",
                  f"Passage#{passage_id} | {passage['nom_complet']} | Admin:{wa}")
        wa_owner(
            f"✅ *BOUFFAGE DÉBLOQUÉ*\n"
            f"Admin  : {wa}\n"
            f"Membre : {passage['nom_complet']}\n"
            f"Tontine: {tnom}"
        )
        return (
            f"✅ Bouffage de *{passage['nom_complet']}* débloqué.\n"
            f"Il sera déclenché à la prochaine heure de bouffage."
        )

    # ── Commande NOUVEAU_CYCLE — relancer un cycle après fin de cycle ────
    if texte.upper() == "NOUVEAU_CYCLE" and sess["etape"] == "menu":
        return demarrer_nouveau_cycle(tid, wa)

    # ── Commande CLOTURER — clôturer définitivement la tontine ───────────
    if texte.upper() == "CLOTURER" and sess["etape"] == "menu":
        return cloturer_tontine(tid, wa)

    # ── Commande BOUFFAGE_VIRE — admin confirme le virement après réception
    # du numéro Mobile Money du bénéficiaire (cf. traiter_reponse_numero_bouffage) ──
    if texte.upper().startswith("BOUFFAGE_VIRE ") and sess["etape"] == "menu":
        parts = texte.strip().split()
        if len(parts) < 2 or not parts[1].isdigit():
            return "❌ Format : *BOUFFAGE_VIRE 42* (ID du bouffage)"
        bouffage_id = int(parts[1])
        conn = get_conn()
        bouffage = fetchone(conn,
            "SELECT id FROM bouffages_manuels WHERE id=%s AND tontine_id=%s",
            (bouffage_id, tid))
        if not bouffage:
            release_conn(conn)
            return f"❌ Bouffage #{bouffage_id} introuvable pour cette tontine."
        resultat = confirmer_bouffage_vire(conn, bouffage_id, wa)
        release_conn(conn)
        return resultat["msg"]

    # ── Commande KICK — retirer un membre du groupe ───────────────────────
    # Admin tape : KICK +237XXXXXXXXX  ou  KICK +237XXXXXXXXX raison
    if texte.upper().startswith("KICK ") and sess["etape"] == "menu":
        parts    = texte.strip().split(None, 2)
        if len(parts) < 2:
            return "❌ Format : *KICK +237690123456* ou *KICK +237690123456 raison*"
        num      = normaliser_numero(parts[1])
        raison   = parts[2] if len(parts) > 2 else "Retiré par l'admin"
        conn     = get_conn()
        membre   = fetchone(conn,
            "SELECT id, nom_complet, whatsapp FROM membres WHERE whatsapp=%s", (num,))
        tontine  = fetchone(conn,
            "SELECT whatsapp_groupe, bot_est_admin, nom FROM tontines WHERE id=%s", (tid,))
        release_conn(conn)

        if not tontine or not tontine.get("whatsapp_groupe"):
            return "❌ Groupe WhatsApp non configuré pour cette tontine."
        if not tontine.get("bot_est_admin"):
            return (
                "❌ *Le bot n\'est pas admin de ce groupe.*\n\n"
                "Promouvez d\'abord TontineBot Pro au rang d\'administrateur "
                "dans les paramètres du groupe WhatsApp."
            )

        # Nom d'affichage — membre enregistré ou juste le numéro
        nom_affiche = membre["nom_complet"] if membre else num
        jid         = num.lstrip("+") + "@s.whatsapp.net"
        kick_ok     = wa_kick_membre(tontine["whatsapp_groupe"], jid)

        if kick_ok:
            log_audit("KICK_ADMIN",
                      f"{nom_affiche} retiré de {tontine['nom']} par {wa}. {raison}",
                      num)
            # DM au membre retiré seulement s'il est enregistré en base
            if membre:
                wa_prive(membre["whatsapp"],
                    f"🚪 *{tontine['nom']} — BADF Ltd*\n\n"
                    f"Vous avez été retiré de ce groupe par l\'administration.\n"
                    f"Motif : _{raison}_\n\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )
            note = ("\n_Son compte reste actif en base. "
                    "Utilisez option 5 pour suspendre ou option 9E pour exclure._"
                    if membre else "")
            return (
                f"✅ *{nom_affiche}* retiré du groupe.\n"
                f"Motif : _{raison}_{note}"
            )
        else:
            return (
                f"❌ Échec du kick pour *{nom_affiche}*.\n"
                f"Vérifiez que le bot est bien admin du groupe "
                f"et que ce numéro est bien dans le groupe."
            )

    # ── Option 0 : Aide admin ────────────────────────────────────────────
    elif texte == "0" and sess["etape"] == "menu":
        return (
            "ℹ️ *AIDE ADMIN — BARACK CORP*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• *admin [NOM]* → ouvrir menu admin\n"
            "• *KICK +237XXXXXXXXX* → retirer du groupe\n"
            "• *KICK +237XXXXXXXXX raison* → retirer avec motif\n"
            "• Menu expire après 5 min d'inactivité\n"
            "• Toutes actions sont auditées\n"
            "• Options 1-15 disponibles\n"
            "• Option 13 : owner uniquement\n"
            "• KICK nécessite que le bot soit admin du groupe\n"
            f"• Owner : {OWNER_WA}"
        )

    return ""


# ══════════════════════════════════════════════════════════════════════════
# TRAITEMENT COTISATION
# ══════════════════════════════════════════════════════════════════════════

def _get_retardataires(conn, tontine_id: int) -> list:
    """Membres actifs n'ayant pas cotisé aujourd'hui."""
    return fetchall(conn, """
        SELECT m.nom_complet, m.whatsapp, m.score_confiance
        FROM adhesions a JOIN membres m ON m.id=a.membre_id
        WHERE a.tontine_id=%s AND a.statut='Actif'
          AND m.id NOT IN (
              SELECT DISTINCT membre_id FROM transactions
              WHERE tontine_id=%s AND type_transaction='Cotisation'
                AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
          )
        ORDER BY m.score_confiance ASC
    """, (tontine_id, tontine_id))


def saisir_caution_et_compenser_groupe(conn, membre_id: int, tontine_id: int,
                                        raison: str = "Fugue post-bouffage") -> dict:
    """
    Saisit la caution d'un fugitif ET compense le groupe avec cet argent.

    Logique :
      1. Récupère la caution bloquée du membre
      2. Calcule combien de cotisations manquantes il doit encore
      3. Utilise la caution pour couvrir ces cotisations manquantes
      4. Le reliquat (si caution > dettes) est crédité à la réserve BADF
      5. Si caution < dettes → dette résiduelle enregistrée sur le membre

    Retourne un dict avec le détail de la compensation.
    """
    # VERROU PESSIMISTE : sans ça, deux déclencheurs concurrents (saisie manuelle
    # admin + job traiter_bouffages_suspects_expires) peuvent tous deux lire la
    # caution 'Bloquee' avant que l'un des deux ne commit son UPDATE → double
    # compensation, double crédit BADF. Même schéma que confirmer_cotisation.
    import psycopg2.extras
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM cautions_garantie WHERE membre_id=%s AND tontine_id=%s "
        "AND statut='Bloquee' FOR UPDATE",
        (membre_id, tontine_id))
    caution = cur.fetchone()
    if not caution:
        conn.rollback()
        cur.close()
        return {"ok": False, "msg": "Aucune caution bloquée pour ce membre."}

    tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
    membre  = fetchone(conn, "SELECT nom_complet, whatsapp FROM membres WHERE id=%s", (membre_id,))
    if not tontine or not membre:
        conn.rollback()
        cur.close()
        return {"ok": False, "msg": "Membre ou tontine introuvable."}

    # `cur` a fait son travail (acquérir + lire la ligne verrouillée) : le verrou
    # reste tenu par la TRANSACTION jusqu'au commit() plus bas, pas par le curseur
    # lui-même — on peut donc le fermer ici, le reste utilise q()/fetchone() sur conn.
    cur.close()

    # Cotisations manquantes depuis le bouffage
    mon_pass = fetchone(conn, """
        SELECT ordre FROM liste_passage
        WHERE membre_id=%s AND tontine_id=%s AND cycle=%s AND statut='Paye'
        ORDER BY date_paiement DESC LIMIT 1
    """, (membre_id, tontine_id, tontine["cycle_actuel"]))

    if mon_pass:
        passages_restants = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND statut='En_attente'
        """, (tontine_id, tontine["cycle_actuel"], mon_pass["ordre"]))["n"]
    else:
        passages_restants = 0

    cotis_deja_faites = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
          AND statut='Confirmee'
          AND date_heure > (SELECT date_bouffage FROM cautions_garantie WHERE id=%s)
    """, (membre_id, tontine_id, caution["id"]))["n"]

    cotis_manquantes  = max(0, passages_restants - cotis_deja_faites)
    montant_du        = cotis_manquantes * tontine["montant_place"]
    montant_caution   = caution["montant"]

    # Saisir la caution
    q(conn, "UPDATE cautions_garantie SET statut='Saisie', date_liberation=NOW() WHERE id=%s",
      (caution["id"],))

    if montant_du == 0:
        # Fugitif a déjà tout cotisé — caution va à BADF
        reliquat = montant_caution
        compense = 0
        note = "Toutes cotisations honorées — caution intégralement reversée à BADF"
    elif montant_caution >= montant_du:
        # Caution couvre toutes les cotisations manquantes
        compense = montant_du
        reliquat = montant_caution - montant_du
        note = f"Caution couvre {cotis_manquantes} cotisation(s) manquante(s)"
    else:
        # Caution partielle — ne couvre pas tout
        compense = montant_caution
        reliquat = 0
        dette_residuelle = montant_du - montant_caution
        # Enregistrer la dette résiduelle sur le membre
        q(conn, """INSERT INTO dettes_ira (membre_id, tontine_id, montant, motif)
                   VALUES (%s,%s,%s,%s)""",
          (membre_id, tontine_id, dette_residuelle,
           f"Dette résiduelle post-fugue — caution insuffisante"))
        note = (f"Caution partielle — couvre {montant_caution:,} FCFA sur "
                f"{montant_du:,} FCFA dus. Dette résiduelle : {dette_residuelle:,} FCFA")

    # Créditer le groupe si compensation
    if compense > 0:
        # Injecter dans les transactions du groupe comme compensation
        q(conn, """INSERT INTO transactions
                   (membre_id, tontine_id, montant_brut, montant_net,
                    type_transaction, statut, reference)
                   VALUES (%s,%s,%s,%s,'Remboursement','Confirmee',%s)""",
          (membre_id, tontine_id, compense, compense,
           f"Compensation caution fugitif — {membre['nom_complet']}"))

    # Reliquat → dettes BADF (crédite BADF)
    if reliquat > 0:
        admin = fetchone(conn,
            "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
            (tontine_id,))
        if admin:
            q(conn, """INSERT INTO dettes_badf
                       (admin_wa, tontine_id, type_dette, montant, ref_cotis)
                       VALUES (%s,%s,'FMP',%s,NULL)""",
              (admin["whatsapp"], tontine_id, reliquat))

    conn.commit()
    log_audit("CAUTION_SAISIE_COMPENSATION",
              f"{membre['nom_complet']} | Caution:{montant_caution:,} | "
              f"Compensé:{compense:,} | Reliquat BADF:{reliquat:,} | {note}",
              membre["whatsapp"])

    # Notifier les admins
    wa_admins_tontine(tontine_id,
        f"🔒 *CAUTION SAISIE — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Membre    : *{membre['nom_complet']}*\n"
        f"Caution   : *{montant_caution:,} FCFA*\n"
        f"Motif     : _{raison}_\n\n"
        f"Utilisation :\n"
        f"  ▪ Compensation groupe : *{compense:,} FCFA*\n"
        f"  ▪ Reversé à BADF      : *{reliquat:,} FCFA*\n\n"
        f"_{note}_\n\n"
        f"_TontineBot Pro — BADF Ltd_"
    )

    # Notifier le fugitif
    wa_prive(membre["whatsapp"],
        f"🔴 *CAUTION SAISIE — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Votre caution de *{montant_caution:,} FCFA* a été saisie.\n"
        f"Motif : _{raison}_\n\n"
        f"*Utilisation :*\n"
        f"  ▪ Cotisations manquantes couvertes : *{compense:,} FCFA*\n"
        f"  ▪ Reversé à BADF Ltd : *{reliquat:,} FCFA*\n\n"
        + (f"⚠️ Il reste *{montant_du - montant_caution:,} FCFA* non couverts "
           f"enregistrés comme dette à votre charge.\n\n"
           if montant_du > montant_caution else "") +
        f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
    )

    return {
        "ok":        True,
        "compense":  compense,
        "reliquat":  reliquat,
        "caution":   montant_caution,
        "note":      note,
        "nom":       membre["nom_complet"],
    }


@healed()
def verifier_et_liberer_cautions():
    """
    Lancé quotidiennement à 9h00.
    Pour chaque membre ayant une caution bloquée :
      - Si cotisations post-bouffage = passages restants → libère la caution
      - Notifie le membre + l'admin
    """
    conn     = get_conn()
    cautions = fetchall(conn, """
        SELECT cg.id, cg.membre_id, cg.tontine_id, cg.montant,
               m.nom_complet, m.whatsapp,
               t.nom AS tontine_nom, t.cycle_actuel
        FROM cautions_garantie cg
        JOIN membres m  ON m.id  = cg.membre_id
        JOIN tontines t ON t.id  = cg.tontine_id
        WHERE cg.statut = 'Bloquee'
    """)

    liberees = 0
    for c in cautions:
        mon_pass = fetchone(conn, """
            SELECT ordre FROM liste_passage
            WHERE membre_id=%s AND tontine_id=%s AND cycle=%s AND statut='Paye'
            ORDER BY date_paiement DESC LIMIT 1
        """, (c["membre_id"], c["tontine_id"], c["cycle_actuel"]))

        if not mon_pass:
            continue

        restants = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND statut='En_attente'
        """, (c["tontine_id"], c["cycle_actuel"], mon_pass["ordre"]))["n"]

        cotis_post = fetchone(conn, """
            SELECT COUNT(*) n FROM transactions
            WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
              AND statut='Confirmee'
              AND date_heure > (SELECT date_bouffage FROM cautions_garantie WHERE id=%s)
        """, (c["membre_id"], c["tontine_id"], c["id"]))["n"]

        if cotis_post >= restants and restants == 0:
            # Fin de cycle — libérer automatiquement
            q(conn, """UPDATE cautions_garantie
                       SET statut='Liberee', date_liberation=NOW()
                       WHERE id=%s""", (c["id"],))
            conn.commit()
            liberees += 1

            # Notifier le membre
            wa_prive(c["whatsapp"],
                f"🔓 *CAUTION LIBÉRÉE — {c['tontine_nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Félicitations *{c['nom_complet']}* !\n\n"
                f"Vous avez honoré toutes vos cotisations jusqu'à la fin du cycle.\n"
                f"Votre caution de *{c['montant']:,} FCFA* est libérée.\n\n"
                f"Votre admin va vous la reverser.\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )

            # Notifier l'admin
            wa_admins_tontine(c["tontine_id"],
                f"🔓 *CAUTION À LIBÉRER — {c['tontine_nom']}*\n\n"
                f"Le membre *{c['nom_complet']}* a honoré toutes ses cotisations.\n"
                f"Veuillez lui reverser sa caution : *{c['montant']:,} FCFA*\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )

            log_audit("CAUTION_LIBEREE_AUTO",
                      f"{c['nom_complet']} | {c['tontine_nom']} | {c['montant']:,} FCFA")

    release_conn(conn)
    if liberees > 0:
        log.info(f"✅ {liberees} caution(s) libérée(s) automatiquement")


def _verifier_liberation_caution(conn, membre_id: int, tontine_id: int):
    """Libère la caution si le membre a cotisé pour tous les membres restants."""
    caution = fetchone(conn,
        "SELECT id, montant FROM cautions_garantie "
        "WHERE membre_id=%s AND tontine_id=%s AND statut='Bloquee'",
        (membre_id, tontine_id))
    if not caution:
        return
    tontine   = fetchone(conn, "SELECT cycle_actuel FROM tontines WHERE id=%s", (tontine_id,))
    mon_pass  = fetchone(conn, """
        SELECT ordre FROM liste_passage
        WHERE membre_id=%s AND tontine_id=%s AND cycle=%s AND statut='Paye'
        ORDER BY date_paiement DESC LIMIT 1
    """, (membre_id, tontine_id, tontine["cycle_actuel"]))
    if not mon_pass:
        return
    restants = fetchone(conn, """
        SELECT COUNT(*) n FROM liste_passage
        WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND statut='En_attente'
    """, (tontine_id, tontine["cycle_actuel"], mon_pass["ordre"]))["n"]
    cotis_post = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
          AND statut='Confirmee'
          AND date_heure > (SELECT date_bouffage FROM cautions_garantie WHERE id=%s)
    """, (membre_id, tontine_id, caution["id"]))["n"]
    if cotis_post >= restants:
        q(conn, "UPDATE cautions_garantie SET statut='Liberee', date_liberation=NOW() WHERE id=%s",
          (caution["id"],))
        conn.commit()
        m = fetchone(conn, "SELECT whatsapp, nom_complet FROM membres WHERE id=%s", (membre_id,))
        if m:
            wa_prive(m["whatsapp"],
                f"🔓 *CAUTION LIBÉRÉE !*\n"
                f"Félicitations {m['nom_complet']} !\n"
                f"Votre caution de *{caution['montant']:,} FCFA* est libérée.\n"
                f"Merci de votre sérieux 🙏")


# ══════════════════════════════════════════════════════════════════════════
# FLASK — WEBHOOKS
# ══════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
# Pas de fallback hardcodé : ce fichier est sur un repo public GitHub, donc
# toute valeur par défaut ici serait un secret public. Si la variable est
# absente, secret_key vaut "" — Flask lève à la première utilisation de la
# session, et le check fail-closed dans __main__ empêche même de démarrer.
app.secret_key  = os.getenv("FLASK_SECRET_KEY", "")
_DASH_TOKEN     = os.getenv("DASHBOARD_TOKEN",  "")
_HEALTH_TOKEN   = os.getenv("HEALTH_TOKEN",     "")
_BOT_START_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot_start")
try:
    with open(_BOT_START_FILE) as _f:
        _BOT_START = datetime.fromisoformat(_f.read().strip())
except Exception:
    _BOT_START = datetime.now()
    try:
        with open(_BOT_START_FILE, "w") as _f:
            _f.write(_BOT_START.isoformat())
    except Exception:
        pass

@app.after_request
def _add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# ══════════════════════════════════════════════════════════════════════════
# MENU OWNER — Tableau de bord financier temps réel
# Mot-clé d'accès : "badf" ou "owner" en DM
# ══════════════════════════════════════════════════════════════════════════

_sessions_owner: dict = {}   # wa → {etape, data}

def traiter_menu_owner(wa: str, texte: str) -> str | None:
    """
    Menu exclusif au owner. Accessible via "badf" ou "owner" en DM.
    Toute tentative d'accès depuis un autre numéro déclenche une alerte.
    """
    t = texte.strip().lower()

    # ── Anti-usurpation ────────────────────────────────────────────────────
    # Si quelqu'un tape les mots-clés owner sans être le owner → alerte
    if t in ("badf", "owner", "dashboard") and not est_owner(wa):
        wa_owner(
            f"⚠️ *TENTATIVE D'USURPATION DÉTECTÉE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Numéro       : *{wa}*\n"
            f"Commande     : `{texte.strip()}`\n"
            f"Heure        : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
            f"Ce numéro a tenté d'accéder au tableau de bord owner.\n"
            f"Accès refusé. Aucune donnée n'a été transmise.\n\n"
            f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
        )
        log_audit("TENTATIVE_USURPATION",
                  f"Mot-clé owner utilisé par {wa}", wa)
        # Réponse neutre au suspect — ne pas révéler que c'est un mot-clé owner
        return None

    if not est_owner(wa):
        return None

    # ── Commande directe CREDIT_VERSE (hors menu) ──────────────────────────
    if texte.strip().upper().startswith("CREDIT_VERSE"):
        parts = texte.strip().split()
        if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) > 0:
            conn = get_conn()
            try:
                tid = int(parts[1])
                tontine_cv = fetchone(conn, "SELECT id, nom, credit_comm_statut FROM tontines WHERE id=%s", (tid,))
                if not tontine_cv:
                    return f"❌ Tontine ID {tid} introuvable."
                if tontine_cv["credit_comm_statut"] == "Verse":
                    return f"ℹ️ Crédit déjà marqué versé pour tontine *{tontine_cv['nom']}*."
                q(conn, "UPDATE tontines SET credit_comm_statut='Verse' WHERE id=%s", (tid,))
                conn.commit()
                log_audit("CREDIT_COMM_VERSE", f"Tontine {tontine_cv['nom']} (ID {tid})", wa)
                return f"✅ Crédit comm marqué comme versé pour *{tontine_cv['nom']}* (ID {tid})."
            except Exception as _e:
                return f"❌ Erreur : {_e}"
            finally:
                release_conn(conn)
        return "⚠️ Usage : CREDIT_VERSE <tontine_id>"

    t  = texte.strip().lower()
    sess = _sessions_owner.setdefault(wa, {"etape": "hors_menu", "data": {}})

    # ── Activation ─────────────────────────────────────────────────────────
    if t in ("badf", "owner", "dashboard", "0"):
        sess["etape"] = "menu"
        return _owner_menu_principal()

    if sess["etape"] == "hors_menu":
        return None   # Pas dans le menu owner → laisser passer au menu membre

    # ── Menu principal ──────────────────────────────────────────────────────
    if sess["etape"] == "menu":

        if t == "1":
            return _owner_rapport_jour()

        elif t == "2":
            return _owner_rapport_semaine()

        elif t == "3":
            return _owner_rapport_mois()

        elif t == "4":
            return _owner_tontines_actives()

        elif t == "5":
            return _owner_reserve_badf()

        elif t == "6":
            sess["etape"] = "historique_jours"
            return (
                "📅 *HISTORIQUE PAR PÉRIODE*\n\n"
                "Entrez le nombre de jours :\n"
                "_Ex : 7 pour les 7 derniers jours_\n\n"
                "Tapez *0* pour revenir."
            )

        elif t == "7":
            return _owner_membres_stats()

        elif t == "8":
            return _owner_fugitifs_stats()

        elif t == "9":
            sess["etape"] = "hors_menu"
            return "🔒 Tableau de bord fermé."

        else:
            return _owner_menu_principal()

    # ── Historique N jours ─────────────────────────────────────────────────
    elif sess["etape"] == "historique_jours":
        if t == "0":
            sess["etape"] = "menu"
            return _owner_menu_principal()
        try:
            nb_jours = int(texte.strip())
            if nb_jours < 1 or nb_jours > 365:
                return "❌ Entre 1 et 365 jours."
            sess["etape"] = "menu"
            return _owner_historique_n_jours(nb_jours)
        except ValueError:
            return "❌ Entrez un nombre de jours (ex: 30)."

    return None


# ── Sous-fonctions owner ────────────────────────────────────────────────────

def _owner_menu_principal() -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛️ *BADF Ltd — TABLEAU DE BORD*\n"
        f"       _{now}_\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*1* → 📊 Rapport du jour (temps réel)\n"
        f"*2* → 📈 Rapport 7 derniers jours\n"
        f"*3* → 📆 Rapport du mois en cours\n"
        f"*4* → 🏦 État de toutes les tontines\n"
        f"*5* → 🔐 Réserve de garantie BADF\n"
        f"*6* → 📅 Historique personnalisé (N jours)\n"
        f"*7* → 👥 Statistiques membres\n"
        f"*8* → 🚨 Rapport fugitifs & pertes\n"
        f"*9* → 🔒 Fermer\n\n"
        f"_Tapez le numéro de votre choix._"
    )


def _bloc_ira_membres(ira: int) -> str:
    """Pénalités IRA prélevées : elles reviennent aux membres ponctuels du
    groupe (dissuasion, pas revenu), donc hors de tout total BADF."""
    return (
        f"🤝 *PÉNALITÉS IRA — fonds des membres ponctuels*\n"
        f"  Prélevées (hors revenu BADF) : *{ira:,} FCFA*\n\n"
    )


def _owner_rapport_jour() -> str:
    conn = get_conn()
    try:
        # FMP du jour
        fmp = fetchone(conn, """
            SELECT COALESCE(SUM(frais_fmp), 0) v FROM transactions
            WHERE statut='Confirmee' AND date_heure::date = CURRENT_DATE
        """)["v"]

        # IRA du jour
        ira = fetchone(conn, """
            SELECT COALESCE(SUM(frais_ira), 0) v FROM transactions
            WHERE statut='Confirmee' AND date_heure::date = CURRENT_DATE
        """)["v"]

        # Cotisations du jour
        cot = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant_brut), 0) v FROM transactions
            WHERE type_transaction='Cotisation' AND statut='Confirmee'
              AND date_heure::date = CURRENT_DATE
        """)

        # Bouffages du jour
        bouffages = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant_bouffage), 0) v
            FROM liste_passage
            WHERE statut='Paye' AND date_paiement::date = CURRENT_DATE
        """)

        # Cautions saisies du jour
        cautions_saisies = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie
            WHERE statut='Saisie' AND date_liberation::date = CURRENT_DATE
        """)

        # Détail par tontine
        tontines = fetchall(conn, "SELECT * FROM tontines WHERE statut='Active'")
        detail = ""
        for t in tontines:
            t_fmp = fetchone(conn, """
                SELECT COALESCE(SUM(frais_fmp), 0) v FROM transactions
                WHERE tontine_id=%s AND statut='Confirmee'
                  AND date_heure::date = CURRENT_DATE
            """, (t["id"],))["v"]
            t_cot = fetchone(conn, """
                SELECT COUNT(DISTINCT membre_id) n FROM transactions
                WHERE tontine_id=%s AND type_transaction='Cotisation'
                  AND statut='Confirmee' AND date_heure::date = CURRENT_DATE
            """, (t["id"],))["n"]
            t_total = fetchone(conn,
                "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
                (t["id"],))["n"]
            detail += (
                f"\n  *{t['nom']}* — {t_cot}/{t_total} cotisants"
                f" | FMP: {t_fmp:,} FCFA"
            )

        total_badf = fmp + cautions_saisies["v"]

        return (
            f"📊 *RAPPORT DU JOUR — {datetime.now().strftime('%d/%m/%Y')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP (2%)     : *{fmp:,} FCFA*\n"
            f"  Cautions saisies        : *{cautions_saisies['v']:,} FCFA* ({cautions_saisies['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL BADF AUJOURD'HUI : {total_badf:,} FCFA*\n\n"
            f"{_bloc_ira_membres(ira)}"
            f"📈 *ACTIVITÉ*\n"
            f"  Cotisations reçues      : {cot['n']} ({cot['v']:,} FCFA)\n"
            f"  Bouffages exécutés      : {bouffages['n']} ({bouffages['v']:,} FCFA)\n\n"
            f"🏦 *DÉTAIL PAR TONTINE*{detail}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Tapez *0* pour le menu principal._"
        )
    finally:
        release_conn(conn)


def _owner_rapport_semaine() -> str:
    return _owner_historique_n_jours(7, titre="RAPPORT — 7 DERNIERS JOURS")


def _owner_rapport_mois() -> str:
    conn = get_conn()
    try:
        debut_mois = datetime.now().replace(day=1).strftime("%Y-%m-%d")
        fmp = fetchone(conn, """
            SELECT COALESCE(SUM(frais_fmp), 0) v FROM transactions
            WHERE statut='Confirmee' AND date_heure >= %s
        """, (debut_mois,))["v"]
        ira = fetchone(conn, """
            SELECT COALESCE(SUM(frais_ira), 0) v FROM transactions
            WHERE statut='Confirmee' AND date_heure >= %s
        """, (debut_mois,))["v"]
        cautions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie
            WHERE statut='Saisie' AND date_liberation >= %s
        """, (debut_mois,))
        bouffages = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE statut='Paye' AND date_paiement >= %s
        """, (debut_mois,))
        total = fmp + cautions["v"]
        mois_nom = datetime.now().strftime("%B %Y").upper()
        return (
            f"📆 *RAPPORT {mois_nom}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP          : *{fmp:,} FCFA*\n"
            f"  Cautions saisies        : *{cautions['v']:,} FCFA* ({cautions['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL MOIS : {total:,} FCFA*\n\n"
            f"{_bloc_ira_membres(ira)}"
            f"📈 *ACTIVITÉ*\n"
            f"  Bouffages exécutés      : {bouffages['n']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Tapez *0* pour le menu principal._"
        )
    finally:
        release_conn(conn)


def _owner_historique_n_jours(nb_jours: int, titre: str = None) -> str:
    conn = get_conn()
    try:
        titre = titre or f"RAPPORT — {nb_jours} DERNIERS JOURS"
        debut = (datetime.now() - timedelta(days=nb_jours)).strftime("%Y-%m-%d")

        fmp = fetchone(conn, """
            SELECT COALESCE(SUM(frais_fmp), 0) v FROM transactions
            WHERE statut='Confirmee' AND date_heure >= %s
        """, (debut,))["v"]
        ira = fetchone(conn, """
            SELECT COALESCE(SUM(frais_ira), 0) v FROM transactions
            WHERE statut='Confirmee' AND date_heure >= %s
        """, (debut,))["v"]
        cautions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie WHERE statut='Saisie' AND date_liberation >= %s
        """, (debut,))

        # Top 5 jours les plus rentables
        top_jours = fetchall(conn, """
            SELECT date_heure::date AS jour,
                   COALESCE(SUM(frais_fmp), 0) AS total
            FROM transactions
            WHERE statut='Confirmee' AND date_heure >= %s
            GROUP BY jour ORDER BY total DESC LIMIT 5
        """, (debut,))

        lignes_top = "\n".join(
            f"  {r['jour'].strftime('%d/%m/%Y')} → {r['total']:,} FCFA"
            for r in top_jours
        ) or "  Aucune donnée."

        total = fmp + cautions["v"]
        return (
            f"📈 *{titre}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP          : *{fmp:,} FCFA*\n"
            f"  Cautions saisies        : *{cautions['v']:,} FCFA* ({cautions['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL PÉRIODE : {total:,} FCFA*\n\n"
            f"{_bloc_ira_membres(ira)}"
            f"🏆 *TOP 5 JOURS*\n{lignes_top}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Tapez *0* pour le menu principal._"
        )
    finally:
        release_conn(conn)


def _owner_tontines_actives() -> str:
    conn = get_conn()
    try:
        tontines = fetchall(conn, "SELECT * FROM tontines ORDER BY statut, id")
        lignes = []
        total_membres = 0
        for t in tontines:
            nb = fetchone(conn,
                "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
                (t["id"],))["n"]
            nb_bouffes = fetchone(conn,
                "SELECT COUNT(*) n FROM liste_passage WHERE tontine_id=%s AND statut='Paye'",
                (t["id"],))["n"]
            fmp_total = fetchone(conn, """
                SELECT COALESCE(SUM(frais_fmp), 0) v FROM transactions
                WHERE tontine_id=%s AND statut='Confirmee'
            """, (t["id"],))["v"]
            icone = "✅" if t["statut"] == "Active" else "⏸️"
            lignes.append(
                f"\n{icone} *{t['nom']}*\n"
                f"   Membres    : {nb} | Bouffages : {nb_bouffes}\n"
                f"   Montant    : {t['montant_place']:,} FCFA/période\n"
                f"   FMP généré : {fmp_total:,} FCFA\n"
                f"   Statut     : {t['statut']}"
            )
            if t["statut"] == "Active":
                total_membres += nb

        return (
            f"🏦 *ÉTAT DES TONTINES — BADF Ltd*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "".join(lignes) +
            f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Total membres actifs : *{total_membres}*\n\n"
            f"_Tapez *0* pour le menu principal._"
        )
    finally:
        release_conn(conn)


def _owner_reserve_badf() -> str:
    """Remplacé par rapport dettes BADF v9.17."""
    return rapport_dettes_badf_admin(OWNER_WA) or (
        "💼 *DETTES BADF*\n\nAucune dette en attente aujourd'hui.\n\n_TontineBot Pro — BADF Ltd_"
    )


def _owner_membres_stats() -> str:
    conn = get_conn()
    try:
        total   = fetchone(conn, "SELECT COUNT(*) n FROM membres")["n"]
        actifs  = fetchone(conn, "SELECT COUNT(*) n FROM membres WHERE statut_global='Actif'")["n"]
        suspendus = fetchone(conn, "SELECT COUNT(*) n FROM membres WHERE statut_global='Suspendu'")["n"]
        bannis  = fetchone(conn, "SELECT COUNT(*) n FROM membres WHERE statut_global='Banni'")["n"]
        nouveaux_j = fetchone(conn, """
            SELECT COUNT(*) n FROM membres WHERE created_at::date = CURRENT_DATE
        """)["n"]
        nouveaux_m = fetchone(conn, """
            SELECT COUNT(*) n FROM membres
            WHERE created_at >= date_trunc('month', CURRENT_DATE)
        """)["n"]
        return (
            f"👥 *STATISTIQUES MEMBRES — BADF Ltd*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"  Total inscrits   : *{total}*\n"
            f"  Actifs           : {actifs}\n"
            f"  Suspendus        : {suspendus}\n"
            f"  Bannis           : {bannis}\n\n"
            f"  Nouveaux aujourd'hui : {nouveaux_j}\n"
            f"  Nouveaux ce mois     : {nouveaux_m}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Tapez *0* pour le menu principal._"
        )
    finally:
        release_conn(conn)


def _owner_fugitifs_stats() -> str:
    conn = get_conn()
    try:
        total_fugitifs = fetchone(conn, """
            SELECT COUNT(DISTINCT membre_id) n FROM alertes_fugue
        """)["n"]
        cautions_saisies = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie WHERE statut='Saisie'
        """)
        cautions_bloquees = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie WHERE statut='Bloquee'
        """)
        # Liste des 5 derniers fugitifs
        derniers = fetchall(conn, """
            SELECT m.nom_complet, m.whatsapp, t.nom AS tontine,
                   af.date_detection
            FROM alertes_fugue af
            JOIN membres m ON m.id = af.membre_id
            JOIN tontines t ON t.id = af.tontine_id
            ORDER BY af.date_detection DESC LIMIT 5
        """)
        lignes = []
        for f in derniers:
            date = f["date_detection"].strftime("%d/%m/%Y") if f["date_detection"] else "?"
            lignes.append(f"  {f['nom_complet']} — {f['tontine']} ({date})")

        return (
            f"🚨 *RAPPORT FUGITIFS — BADF Ltd*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"  Total fugitifs détectés : *{total_fugitifs}*\n\n"
            f"  Cautions saisies        : {cautions_saisies['n']} → *{cautions_saisies['v']:,} FCFA*\n"
            f"  Cautions encore bloquées: {cautions_bloquees['n']} → {cautions_bloquees['v']:,} FCFA\n\n"
            f"📋 *5 DERNIERS FUGITIFS*\n"
            + ("\n".join(lignes) or "  Aucun fugitif.") +
            f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Tapez *0* pour le menu principal._"
        )
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# ÉVÉNEMENTS GROUPE — bot ajouté / membre quitte
# ══════════════════════════════════════════════════════════════════════════

def _est_nom_valide(name: str) -> bool:
    """Vérifie qu'un nom d'affichage WhatsApp est un vrai prénom (pas un numéro, pas vide).
    re.search au lieu de re.match pour tolérer les emojis en préfixe (🔥Nicole → valide)."""
    if not name or len(name) < 2:
        return False
    if name.replace("+", "").isdigit():
        return False
    return bool(re.search(r"[A-Za-zÀ-ÿ]", name))


def _auto_inscrire_participants(conn, tontine_id: int, participants: list) -> int:
    """Auto-inscrit une liste de participants dans une tontine sans KYC ni frais.
    Accepte list[str] (phones) ou list[tuple[str,str]] (phone, pushname).
    Retourne le nombre de nouveaux membres créés."""
    inscrits = 0
    bot_wa   = normaliser_numero(OWNER_WA)

    # Phase 1 — Résolution des pushnames AVANT toute opération DB.
    # Les appels HTTP getContactInfo (timeout=8s) sont faits ici, conn reste idle.
    pushnames: dict = {}
    for p in participants:
        p_norm   = normaliser_numero(p[0] if isinstance(p, tuple) else p)
        pushname = (p[1] or "").strip() if isinstance(p, tuple) else ""
        if not p_norm or p_norm == bot_wa:
            continue
        if not pushname:
            pushname = _greenapi_get_pushname(p_norm)
        pushnames[p_norm] = pushname

    # Phase 2 — Opérations DB (toutes les mutations groupées après la phase réseau)
    for p_norm, pushname in pushnames.items():
        nom_valide  = _est_nom_valide(pushname)
        nom_complet = pushname if nom_valide else f"Membre_{p_norm[-4:]}"

        membre = fetchone(conn,
            "SELECT id, statut_global FROM membres WHERE whatsapp=%s", (p_norm,))
        if not membre:
            kyc_hash = hashlib.sha256(f"AUTO:{p_norm}{tontine_id}".encode()).hexdigest()
            cur = q(conn, """INSERT INTO membres
                (nom_complet, kyc_nom, kyc_hash, whatsapp, adhesion_payee,
                 statut_global, kyc_etape)
                VALUES (%s,%s,%s,%s,1,'Actif',0) RETURNING id""",
                (nom_complet, nom_complet if nom_valide else None, kyc_hash, p_norm))
            membre_id = cur.fetchone()[0]
            inscrits += 1
        else:
            membre_id = membre["id"]
            if nom_valide:
                q(conn, """UPDATE membres SET nom_complet=%s, kyc_nom=%s
                           WHERE id=%s AND nom_complet LIKE 'Membre\\_%%'""",
                  (nom_complet, nom_complet, membre_id))
            if membre["statut_global"] not in ("Banni", "Suspendu_global"):
                q(conn,
                  "UPDATE membres SET adhesion_payee=1, statut_global='Actif' WHERE id=%s",
                  (membre_id,))
        deja = fetchone(conn,
            "SELECT id FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
            (membre_id, tontine_id))
        if not deja:
            q(conn, """INSERT INTO adhesions (membre_id, tontine_id, statut)
                VALUES (%s,%s,'Actif') ON CONFLICT DO NOTHING""",
                (membre_id, tontine_id))
    return inscrits


@healed()
def _envoyer_config_dm_admins(admins_list: list, group_id: str, nom_groupe: str,
                               participants: list = None) -> int:
    """DM la Question 1/3 à chaque admin éligible du groupe et crée sa session config.
    Retourne le nombre de DMs envoyés."""
    sent = 0
    for admin_wa, admin_name in admins_list:
        if est_owner(admin_wa):
            continue
        prenom = admin_name.split()[0] if admin_name else ""
        salut  = f" {prenom}" if prenom else ""
        with _sessions_lock:
            if admin_wa in _sessions_config:
                continue
            _sessions_config[admin_wa] = {
                "group_id":   group_id,
                "group_name": nom_groupe,
                "etape":      "montant",
                "data":       {"participants": participants or []},
                "ts":         time_module.time(),
            }
        try:
            wa_prive(admin_wa,
                f"Bonjour{salut} ! Je suis TontineBot Pro dans *{nom_groupe}*.\n\n"
                "Configurez votre tontine en *3 questions*.\n\n"
                "Question *1/3*\n\n"
                "Quel est le *montant de cotisation* ? (en FCFA)\n"
                "Exemple : *5000*\n\n"
                "_TontineBot Pro - BADF Ltd_"
            )
            sent += 1
        except Exception:
            with _sessions_lock:
                _sessions_config.pop(admin_wa, None)
    return sent


def _bot_ajoute_groupe(group_id: str, group_name: str, participants: list = []):
    """
    Bot ajouté dans un groupe WhatsApp.
    - Si tontine connue → présentation directe
    - Si inconnue → DM à l'admin pour configurer en 3 questions
    - Si réseau ≥ 5 tontines → message dans le groupe pour les non-enrôlés
    """
    import time as _t

    conn = get_conn()
    tontine = fetchone(conn,
        "SELECT * FROM tontines WHERE whatsapp_groupe=%s AND statut='Active'",
        (group_id,))

    if not tontine and group_name:
        tontine = fetchone(conn,
            "SELECT * FROM tontines WHERE LOWER(nom)=LOWER(%s) AND statut='Active'",
            (group_name.strip(),))
        if tontine:
            q(conn, "UPDATE tontines SET whatsapp_groupe=%s WHERE id=%s",
              (group_id, tontine["id"]))
            conn.commit()
            log.info(f"Groupe '{group_name}' associe a tontine '{tontine['nom']}'")

    # Nombre de tontines actives dans le réseau
    nb_tontines = fetchone(conn, "SELECT COUNT(*) n FROM tontines WHERE statut='Active'")["n"]

    if tontine:
        admin = fetchone(conn,
            "SELECT * FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
            (tontine["id"],))

        # Détecter si la tontine est déjà en cours (membres existants ou cycle avancé)
        nb_membres_existants = fetchone(conn,
            "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s",
            (tontine["id"],))["n"]
        tontine_en_cours = tontine["cycle_actuel"] > 1 or nb_membres_existants > 0

        if tontine_en_cours and participants:
            # Auto-inscrire tous les participants sans demander adhésion ni KYC
            try:
                inscrits = _auto_inscrire_participants(conn, tontine["id"], participants)
                conn.commit()
            finally:
                release_conn(conn)

            _t.sleep(2)
            _wa_send_group_chatid(group_id,
                f"🏛️ *{tontine['nom']} — TontineBot Pro*\n\n"
                f"🛡️ *CADRE JURIDIQUE ET SÉCURITÉ ANTIFRAUDE*\n\n"
                f"Pour le confort et la protection absolue des fonds de nos membres honnêtes, "
                f"*TontineBot Pro* opère sous un protocole de sécurité strict :\n\n"
                f"*Conformité Institutionnelle :* Notre infrastructure est alignée sur les exigences "
                f"du règlement *COBAC R-2019/01* et collabore activement avec l'*ANIF* "
                f"(Agence Nationale d'Investigation Financière) ainsi que la *Police Judiciaire*.\n\n"
                f"*Tolérance Zéro :* En cas de tentative d'escroquerie ou de bouffage frauduleux "
                f"sans remboursement, le bot déclenche une alerte immédiate. Les numéros "
                f"*Orange Money* / *MTN MoMo* associés seront bloqués auprès des opérateurs "
                f"et une procédure judiciaire sera engagée.\n\n"
                f"📊 *BARÈME DES FRAIS ET PÉNALITÉS*\n\n"
                + (f"• *FMP (Frais de Maintenance) :* {int(FRAIS_FMP*100)} % par cotisation "
                f"_(Financement des serveurs ultra-sécurisés)_\n" if FRAIS_FMP > 0 else "")
                + (
                f"• *Discipline Collective (Retard) :* 150 FCFA / jour\n"
                f"• *Réactivation :* 1 000 FCFA "
                f"_(Après 48h de suspension sans cotisation et sans avoir prévenu l'Administrateur)_\n"
                f"• *Mise à jour Dossier :* 250 FCFA _(Changement de numéro sécurisé)_\n\n"
                f"🎁 *RÉCO-RÉCOMPENSE (PARRAINAGE)*\n\n"
                f"*Bonus Spécial :* Dès que votre groupe réalise ses *5 premières "
                f"transactions validées* via notre système, recevez "
                f"*1 000 FCFA de crédit de communication* "
                f"tous réseaux (Orange, MTN, Camtel).\n\n"
                f"_Avec TontineBot Pro, construisons ensemble une épargne forte, transparente "
                f"et sans stress. Bienvenue dans l'ère de la tontine professionnelle._"
            ))
            if admin:
                _t.sleep(1)
                wa_prive(admin["whatsapp"], msg_dm_admin_bienvenue(tontine["nom"]))
            log_audit("BOT_ADDED_GROUPE",
                      f"Groupe:{group_id} Tontine:{tontine['nom']} "
                      f"(en cours, {inscrits} auto-inscrits)")
            return

        # Tontine nouvelle (0 membres, cycle 1) → flux normal KYC/adhésion
        release_conn(conn)
        _t.sleep(2)
        _wa_send_group_chatid(group_id, msg_intro_groupe(
            nom_tontine     = tontine["nom"],
            montant         = tontine["montant_place"],
            heure_bouffage  = tontine.get("heure_bouffage",  "17:00") or "17:00",
            heure_ouverture = tontine.get("heure_ouverture", "05:00") or "05:00",
            heure_rappel    = tontine.get("heure_rappel",    "14:00") or "14:00",
            heure_limite    = tontine.get("heure_limite",    "18:00") or "18:00",
            numero_collecte = admin.get("numero_collecte", "") if admin else "",
        ))
        _t.sleep(2)
        _wa_send_group_chatid(group_id, msg_kyc_groupe(tontine["nom"]))

        if admin:
            _t.sleep(1)
            wa_prive(admin["whatsapp"], msg_dm_admin_bienvenue(tontine["nom"]))
        log_audit("BOT_ADDED_GROUPE",
                  f"Groupe:{group_id} Tontine:{tontine['nom']} (nouvelle)")
        return

    # Tontine inconnue → config via DM aux admins réels du groupe
    release_conn(conn)

    admins_list, grp_name = _greenapi_get_group_admins(group_id)
    nom_groupe = grp_name or group_name or group_id

    if not admins_list:
        wa_owner(
            f"⚠️ Nouveau groupe : *{nom_groupe}*\n"
            f"ID : {group_id}\n\n"
            "Aucun admin détecté via API.\n"
            "Demandez à l'admin de m'écrire en DM pour configurer.\n\n"
            "_TontineBot Pro - BADF Ltd_"
        )
        log_audit("BOT_ADDED_GROUPE_INCONNU",
                  f"Groupe:{group_id} Nom:{nom_groupe} (pas d'admin détecté)")
        return

    _wa_send_group_chatid(group_id,
        "🔧 *Configuration TontineBot Pro en cours...*\n"
        "L'administrateur du groupe reçoit les instructions en message privé.\n\n"
        "_TontineBot Pro - BADF Ltd_"
    )
    _envoyer_config_dm_admins(admins_list, group_id, nom_groupe, participants)

    wa_owner(
        f"NOUVEAU GROUPE : *{nom_groupe}*\n"
        f"Config DM envoyé à {len(admins_list)} admin(s).\n"
        f"ID : {group_id}"
    )
    log_audit("BOT_ADDED_GROUPE_INCONNU",
              f"Groupe:{group_id} Nom:{nom_groupe} Admins:{len(admins_list)} DMs envoyés")


def traiter_config_tontine(wa: str, texte: str) -> Optional[str]:
    """
    Flux de configuration nouvelle tontine via DM admin.
    Etapes : montant -> type -> numero collecte -> creation tontine
    """
    import time as _t
    wa_norm = normaliser_numero(wa)
    sess = _sessions_config.get(wa_norm)

    if not sess:
        return None

    if not session_valide(_sessions_config, wa_norm):
        _sessions_config.pop(wa_norm, None)
        return None

    etape = sess.get("etape")
    data  = sess.get("data", {})

    if etape == "montant":
        t = texte.strip().replace(" ", "").upper().replace("FCFA", "")
        if not t.isdigit():
            return "Envoyez juste le montant en chiffres. Exemple : *5000*"
        montant = int(t)
        if montant < 100 or montant > 500000:
            return f"Montant invalide ({montant:,} FCFA). Entre 100 et 500 000 FCFA."
        data["montant"] = montant
        sess["etape"]   = "type"
        sess["data"]    = data
        return (
            f"✅ Montant : *{montant:,} FCFA*\n\n"
            "Question *2/3*\n\n"
            "Type de tontine ?\n"
            "1 - Journalière\n"
            "2 - Hebdomadaire\n"
            "3 - Mensuelle\n\n"
            "Tapez *1*, *2* ou *3*"
        )

    if etape == "type":
        types = {"1": "Journaliere", "2": "Hebdomadaire", "3": "Mensuelle"}
        if texte.strip() not in types:
            return "Tapez *1* (Journaliere), *2* (Hebdomadaire) ou *3* (Mensuelle)."
        data["type"] = types[texte.strip()]
        sess["data"]  = data
        if data["type"] == "Journaliere":
            data["jour_semaine"] = "Lundi"
            data["jour_mois"]    = 1
            sess["etape"] = "en_cours"
            return (
                f"✅ Type : *Journalière*\n\n"
                "Question *3/3*\n\n"
                "Cette tontine est-elle *déjà en cours* ?\n"
                "(Les membres cotisent déjà, même sans le bot)\n\n"
                "Répondez *OUI* ou *NON*"
            )
        elif data["type"] == "Hebdomadaire":
            sess["etape"] = "jour"
            return (
                f"✅ Type : *Hebdomadaire*\n\n"
                "Question *2b/3* — Quel *jour de la semaine* ?\n\n"
                "L = Lundi\nM = Mardi\nX = Mercredi\nJ = Jeudi\n"
                "V = Vendredi\nS = Samedi\nD = Dimanche"
            )
        else:  # Mensuelle
            sess["etape"] = "jour"
            return (
                f"✅ Type : *Mensuelle*\n\n"
                "Question *2b/3* — Quel *jour du mois* ? (1 à 28)\n"
                "_(ex : 1 = 1er de chaque mois)_"
            )

    if etape == "jour":
        t_type = data.get("type", "Journaliere")
        if t_type == "Hebdomadaire":
            mapping_j = {"L":"Lundi","M":"Mardi","X":"Mercredi","J":"Jeudi",
                         "V":"Vendredi","S":"Samedi","D":"Dimanche"}
            jour = mapping_j.get(texte.strip().upper())
            if not jour:
                return "❌ Tapez L, M, X, J, V, S ou D."
            data["jour_semaine"] = jour
            data["jour_mois"]    = 1
            label_j = f"*{jour}*"
        else:  # Mensuelle
            try:
                j = int(texte.strip())
                if not (1 <= j <= 28):
                    return "❌ Entrez un nombre entre 1 et 28."
            except ValueError:
                return "❌ Entrez un nombre entre 1 et 28."
            data["jour_mois"]    = j
            data["jour_semaine"] = "Lundi"
            label_j = f"le *{j}* de chaque mois"
        sess["etape"] = "en_cours"
        sess["data"]  = data
        return (
            f"✅ Jour : {label_j}\n\n"
            "Question *3/3*\n\n"
            "Cette tontine est-elle *déjà en cours* ?\n"
            "(Les membres cotisent déjà, même sans le bot)\n\n"
            "Répondez *OUI* ou *NON*"
        )

    if etape == "en_cours":
        rep = texte.strip().upper()
        if rep in ("OUI", "O", "YES", "1"):
            data["deja_en_cours"] = True
        elif rep in ("NON", "N", "NO", "0"):
            data["deja_en_cours"] = False
        else:
            return "Répondez *OUI* ou *NON* s'il vous plaît."
        sess["etape"] = "collecte"
        sess["data"]  = data
        return (
            "Votre *numéro de collecte* MTN/Orange Money ?\n"
            "(Le numéro où les membres virent l'argent)\n\n"
            "Format : *+237690123456*"
        )

    if etape == "collecte":
        num = normaliser_numero(texte)
        if not num or len(num) < 12:
            return "Numero invalide. Format : *+237690123456*"

        with _sessions_lock:
            if wa_norm not in _sessions_config:
                return None
            _sessions_config.pop(wa_norm)

        data["collecte"] = num
        group_id   = sess["group_id"]
        group_name = sess["group_name"]
        montant    = data["montant"]
        type_t     = data["type"]

        conn = get_conn()
        try:
            cur = q(conn, """
                INSERT INTO tontines
                    (nom, type_tontine, montant_place, whatsapp_groupe,
                     statut, caution_pourcent, caution_active,
                     heure_limite, heure_ouverture, heure_rappel, heure_bouffage,
                     jour_semaine, jour_mois)
                VALUES (%s,%s,%s,%s,'Active',10,1,'18:00','05:00','14:00','17:00',%s,%s)
                ON CONFLICT (whatsapp_groupe) DO UPDATE SET
                    nom             = EXCLUDED.nom,
                    montant_place   = EXCLUDED.montant_place,
                    type_tontine    = EXCLUDED.type_tontine,
                    jour_semaine    = EXCLUDED.jour_semaine,
                    jour_mois       = EXCLUDED.jour_mois
                RETURNING id
            """, (group_name, type_t, montant, group_id,
                  data.get("jour_semaine", "Lundi"), data.get("jour_mois", 1)))
            tontine_id = cur.fetchone()[0]
            q(conn, """
                INSERT INTO admins_groupe (tontine_id, whatsapp, numero_collecte)
                VALUES (%s,%s,%s)
                ON CONFLICT (tontine_id, whatsapp)
                DO UPDATE SET numero_collecte = EXCLUDED.numero_collecte
            """, (tontine_id, wa_norm, num))
            conn.commit()
            log.info(f"Tontine creee auto: {group_name} {type_t} {montant}F")
            log_audit("TONTINE_CREEE_AUTO",
                      f"{group_name}|{type_t}|{montant}F|Admin:{wa_norm}")
        except Exception as e:
            conn.rollback()
            release_conn(conn)
            log.error(f"Erreur creation tontine: {e}")
            return f"Erreur creation tontine: {str(e)[:80]}"
        release_conn(conn)

        _t.sleep(2)
        if data.get("deja_en_cours"):
            participants = data.get("participants") or _greenapi_get_group_members(group_id)
            inscrits = 0
            if participants:
                conn2 = get_conn()
                inscrits = _auto_inscrire_participants(conn2, tontine_id, participants)
                conn2.commit()
                release_conn(conn2)
            _wa_send_groupe(group_id,
                f"🤖 *TontineBot Pro — {group_name}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Tontine configurée avec succès !\n\n"
                f"✅ *{inscrits} membre(s) auto-inscrit(s)* — aucun frais d'adhésion requis.\n\n"
                f"📸 Continuez à envoyer vos screenshots de cotisation comme d'habitude.\n\n"
                f"📋 La vérification d'identité (KYC) sera demandée à chacun "
                f"à la *fin du cycle en cours*.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
        else:
            _wa_send_groupe(group_id, msg_intro_groupe(
                nom_tontine     = group_name,
                montant         = montant,
                heure_bouffage  = "17:00",
                heure_ouverture = "05:00",
                heure_rappel    = "14:00",
                heure_limite    = "18:00",
                numero_collecte = num,
            ))
            _t.sleep(2)
            _wa_send_groupe(group_id, msg_kyc_groupe(group_name))

        wa_owner(
            f"NOUVELLE TONTINE CONFIGUREE\n"
            f"Nom: {group_name} | Type: {type_t} | "
            f"Montant: {montant:,}F | Admin: {wa_norm}"
        )

        msg_statut = (
            "Membres auto-inscrits — KYC reporté à la fin du cycle."
            if data.get("deja_en_cours")
            else "Message d'intro envoyé dans le groupe."
        )
        return (
            f"🎉 Tontine *{group_name}* configurée !\n\n"
            f"Type    : *{type_t}*\n"
            f"Montant : *{montant:,} FCFA*\n"
            f"Collecte: *{num}*\n\n"
            f"{msg_statut}\n\n"
            "Envoyez maintenant la liste de passage :\n"
            "*01- Prenom JJ/MM/AA*\n"
            "_TontineBot Pro - BADF Ltd_"
        )

    return None


# ══════════════════════════════════════════════════════════════════════════
# COMMANDES GROUPE — liste / rappel
# ══════════════════════════════════════════════════════════════════════════

def _groupe_liste_cotisations(wa_admin: str, group_id: str) -> str:
    """
    Commande "liste" tapée dans le groupe par l'admin.
    Affiche :
    - ✅ Membres ayant cotisé (nom + montant + FMP BADF)
    - ❌ Membres n'ayant pas cotisé (nom)
    - Totaux collecté / FMP BADF / net tontine
    """
    conn    = get_conn()
    tontine = fetchone(conn,
        "SELECT * FROM tontines WHERE whatsapp_groupe=%s AND statut='Active'",
        (group_id,))

    if not tontine:
        release_conn(conn)
        return None

    today   = datetime.now().date()
    tid     = tontine["id"]
    tnom    = tontine["nom"]
    montant = tontine["montant_place"]
    fmp_pct = FRAIS_FMP  # 0.02

    # Membres actifs avec leurs places
    membres = fetchall(conn, """
        SELECT m.id, m.nom_complet, m.whatsapp, a.nombre_places
        FROM membres m
        JOIN adhesions a ON a.membre_id = m.id
        WHERE a.tontine_id=%s AND a.statut='Actif'
        ORDER BY m.nom_complet
    """, (tid,))

    # Cotisations confirmées aujourd'hui
    cotis_ok = fetchall(conn, """
        SELECT DISTINCT cm.membre_id, m.nom_complet,
               cm.montant_declare, cm.date_confirmation
        FROM cotisations_manuelles cm
        JOIN membres m ON m.id = cm.membre_id
        WHERE cm.tontine_id=%s
          AND cm.statut='Confirme'
          AND cm.date_confirmation::date = %s
        ORDER BY m.nom_complet
    """, (tid, today))

    release_conn(conn)

    ids_ok      = {c["membre_id"] for c in cotis_ok}
    ont_paye    = [m for m in membres if m["id"] in ids_ok]
    nont_pas    = [m for m in membres if m["id"] not in ids_ok]

    nb_total    = len(membres)
    nb_ok       = len(ont_paye)
    total_coll  = sum(c["montant_declare"] or montant for c in cotis_ok)
    total_fmp   = int(total_coll * fmp_pct)
    net_tontine = total_coll - total_fmp

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    lignes  = [
        f"📊 *{tnom} — {now_str}*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
    ]

    # Ont cotisé
    if ont_paye:
        lignes.append(f"✅ *ONT COTISÉ ({nb_ok}/{nb_total})*")
        for m in ont_paye:
            c      = next((x for x in cotis_ok if x["membre_id"] == m["id"]), None)
            mont   = c["montant_declare"] if c and c["montant_declare"] else montant
            fmp_m  = int(mont * fmp_pct)
            nb_pl  = m.get("nombre_places") or 1
            pl_txt = f" ×{nb_pl}" if nb_pl > 1 else ""
            lignes.append(
                f"  @{m['nom_complet']}{pl_txt} — *{mont:,} FCFA*"
                + (f" _(FMP BADF : {fmp_m:,} FCFA)_" if fmp_m > 0 else "")
            )
    else:
        lignes.append(f"✅ *ONT COTISÉ (0/{nb_total})*")
        lignes.append("  Aucun pour l'instant.")

    lignes.append("")

    # N'ont pas cotisé
    if nont_pas:
        lignes.append(f"❌ *N'ONT PAS COTISÉ ({len(nont_pas)}/{nb_total})*")
        for m in nont_pas:
            nb_pl  = m.get("nombre_places") or 1
            attendu = montant * nb_pl
            pl_txt  = f" ×{nb_pl} = {attendu:,} FCFA attendu" if nb_pl > 1 else f" — {attendu:,} FCFA attendu"
            lignes.append(f"  @{m['nom_complet']}{pl_txt}")
    else:
        lignes.append("🎉 *TOUS LES MEMBRES ONT COTISÉ !*")

    lignes += [
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Collecté     : *{total_coll:,} FCFA*",
        *(([f"💼 FMP BADF {int(fmp_pct*100)}%  : *{total_fmp:,} FCFA*",
            f"📦 Net tontine  : *{net_tontine:,} FCFA*"]) if total_fmp > 0 else []),
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"_TontineBot Pro — BADF Ltd_",
    ]

    log_audit("LISTE_GROUPE", f"Admin:{wa_admin} Tontine:{tid} {nb_ok}/{nb_total}", wa_admin)
    return "\n".join(lignes)


def _traiter_commande_groupe(wa: str, texte: str, group_id: str, group_name: str = ""):
    """Dispatch des commandes texte admin dans le groupe @g.us.
    Si le groupe n'a pas de tontine liée, déclenche la config automatique via DM admin."""
    global _groupes_intro_envoyes

    conn = get_conn()
    try:
        tontine = fetchone(conn,
            "SELECT * FROM tontines WHERE whatsapp_groupe=%s AND statut='Active'",
            (group_id,))
    finally:
        release_conn(conn)

    already_triggered = False
    with _sessions_lock:
        already_triggered = group_id in _groupes_intro_envoyes
        if not tontine and not already_triggered:
            already_triggered = any(
                v.get("group_id") == group_id
                for v in _sessions_config.values()
            )
        if not already_triggered:
            _groupes_intro_envoyes.add(group_id)

    if not tontine and not already_triggered:
        admins_list, grp_name = _greenapi_get_group_admins(group_id)
        nom = grp_name or group_name or group_id
        if admins_list:
            _wa_send_group_chatid(group_id,
                "🔧 *Configuration TontineBot Pro en cours...*\n"
                "L'administrateur reçoit les instructions en message privé.\n\n"
                "_TontineBot Pro - BADF Ltd_"
            )
            participants_list = _greenapi_get_group_members(group_id)
            _envoyer_config_dm_admins(admins_list, group_id, nom, participants_list)
        else:
            wa_groupe(group_id, MSG_INTRO_GROUPE)
        log_audit("CONFIG_GROUPE_DECLENCHE",
                  f"Groupe:{group_id} Admins:{len(admins_list) if admins_list else 0}", wa)
        return

    cmd = texte.strip().lower()

    if cmd == "intro":
        if not est_admin(wa) and not est_owner(wa):
            return
        if tontine:
            msg = msg_intro_groupe(
                nom_tontine     = tontine["nom"],
                montant         = tontine["montant_place"],
                heure_bouffage  = tontine.get("heure_bouffage",  "17:00") or "17:00",
                heure_ouverture = tontine.get("heure_ouverture", "05:00") or "05:00",
                heure_rappel    = tontine.get("heure_rappel",    "14:00") or "14:00",
                heure_limite    = tontine.get("heure_limite",    "18:00") or "18:00",
            )
        else:
            msg = MSG_INTRO_GROUPE
        wa_groupe(group_id, msg)
        log_audit("INTRO_GROUPE", f"Admin:{wa} Groupe:{group_id}", wa)
        return

    if not tontine:
        return
    if not est_admin(wa) and not est_owner(wa):
        return

    if cmd == "liste":
        rep = _groupe_liste_cotisations(wa, group_id)
        if rep:
            wa_groupe(group_id, rep)
    elif cmd == "rappel":
        rep = _groupe_rappel_manuel(wa, group_id)
        if rep:
            wa_groupe(group_id, rep)


def _groupe_rappel_manuel(wa_admin: str, group_id: str) -> str:
    """
    Commande "rappel" tapée dans le groupe par l'admin.
    Mentionne uniquement les membres qui n'ont pas encore cotisé.
    """
    conn    = get_conn()
    tontine = fetchone(conn,
        "SELECT * FROM tontines WHERE whatsapp_groupe=%s AND statut='Active'",
        (group_id,))

    if not tontine:
        release_conn(conn)
        return None

    today = datetime.now().date()
    tid   = tontine["id"]

    membres = fetchall(conn, """
        SELECT m.id, m.nom_complet
        FROM membres m
        JOIN adhesions a ON a.membre_id = m.id
        WHERE a.tontine_id=%s AND a.statut='Actif'
    """, (tid,))

    ids_ok = {r["membre_id"] for r in fetchall(conn, """
        SELECT DISTINCT membre_id FROM cotisations_manuelles
        WHERE tontine_id=%s AND statut='Confirme'
          AND date_confirmation::date=%s
    """, (tid, today))}

    release_conn(conn)

    retards = [m for m in membres if m["id"] not in ids_ok]
    if not retards:
        return (
            f"✅ *{tontine['nom']}*\n\n"
            f"Tous les membres ont cotisé aujourd'hui ! 🎉\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    mentions = "\n".join(f"  @{m['nom_complet']}" for m in retards)
    conn2 = get_conn()
    try:
        adm_col = fetchone(
            conn2,
            "SELECT numero_collecte FROM admins_groupe WHERE tontine_id=%s AND numero_collecte IS NOT NULL LIMIT 1",
            (tid,)
        )
    finally:
        release_conn(conn2)
    num_col = adm_col["numero_collecte"] if adm_col else "— demandez à l'admin"

    return (
        f"⏰ *RAPPEL COTISATION — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Les membres suivants n'ont pas encore cotisé :\n\n"
        f"{mentions}\n\n"
        f"📱 Virez *{tontine['montant_place']:,} FCFA* → *{num_col}*\n"
        f"📸 Puis envoyez votre screenshot ici.\n\n"
        f"⏰ Heure limite : *{tontine['heure_limite']}*\n\n"
        f"_TontineBot Pro — BADF Ltd_"
    )


# ══════════════════════════════════════════════════════════════════════════
# WEBHOOK WHATSAPP — META CLOUD API
# ══════════════════════════════════════════════════════════════════════════


@app.route("/webhook/whatsapp", methods=["GET"])
def webhook_whatsapp_verify():
    """Health check — Green API n'utilise pas de challenge GET."""
    return jsonify({"status": "ok", "bot": "TontineBot Pro"}), 200


def _greenapi_get_pushname(wa: str) -> str:
    """Retourne le pushname WhatsApp d'un numéro via Green API getContactInfo."""
    if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
        return ""
    try:
        url = (f"{GREENAPI_BASE}/waInstance{GREENAPI_INSTANCE_ID}"
               f"/getContactInfo/{GREENAPI_TOKEN}")
        r = requests.post(url, json={"chatId": f"{wa}@c.us"}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            return (data.get("name") or data.get("pushname") or "").strip()
    except Exception:
        pass
    return ""


def _greenapi_get_group_members(group_chatid: str, exclude: str = "") -> list:
    """Green API getGroupData → liste de tuples (phone_normalisé, pushname).
    pushname issu du champ 'name' de getGroupData si disponible, sinon chaîne vide."""
    if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
        return []
    url = (
        f"{GREENAPI_BASE}/waInstance{GREENAPI_INSTANCE_ID}"
        f"/getGroupData/{GREENAPI_TOKEN}"
    )
    try:
        r = requests.post(url, json={"groupId": group_chatid}, timeout=15)
        if r.status_code != 200:
            log.warning(f"getGroupData → {r.status_code}")
            return []
        participants = r.json().get("participants", [])
        result = []
        for p in participants:
            pid      = p.get("id", "") if isinstance(p, dict) else str(p)
            pushname = (p.get("name") or p.get("pushName") or "").strip() if isinstance(p, dict) else ""
            if pid and pid != exclude and pid.endswith("@c.us"):
                num = normaliser_numero(pid.split("@")[0])
                if num:
                    result.append((num, pushname))
        return result
    except Exception as e:
        log.error(f"getGroupData : {e}")
        return []


def _greenapi_get_group_admins(group_chatid: str) -> tuple:
    """getGroupData → ([(wa_num, pushname)], group_name) pour les admins uniquement."""
    if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
        return [], ""
    url = (
        f"{GREENAPI_BASE}/waInstance{GREENAPI_INSTANCE_ID}"
        f"/getGroupData/{GREENAPI_TOKEN}"
    )
    try:
        r = requests.post(url, json={"groupId": group_chatid}, timeout=15)
        if r.status_code != 200:
            log.warning(f"getGroupData admins → {r.status_code}")
            return [], ""
        data = r.json()
        group_name = data.get("subject", "")
        admins = []
        for p in data.get("participants", []):
            if not isinstance(p, dict):
                continue
            if not (p.get("isAdmin") or p.get("isSuperAdmin")):
                continue
            pid = p.get("id", "")
            if not pid or not pid.endswith("@c.us"):
                continue
            num = normaliser_numero(pid.split("@")[0])
            if num:
                pushname = (p.get("name") or p.get("pushName") or "").strip()
                admins.append((num, pushname))
        return admins, group_name
    except Exception as e:
        log.error(f"getGroupData admins : {e}")
        return [], ""


def _traiter_groupe_participants(payload: dict):
    """
    Dispatch incomingGroupParticipantsUpdate.
    Seul cas traité : ajout du bot → _bot_ajoute_groupe().
    """
    if payload.get("typeParticipantsUpdate") != "add":
        return

    instance_data = payload.get("instanceData") or {}
    bot_wid       = instance_data.get("wid", "")        # ex: 237XXXXXXXXXX@c.us
    group_data    = payload.get("groupData") or {}
    group_id      = group_data.get("groupId", "")        # ex: 120363XXXX@g.us
    group_name    = group_data.get("groupName", "")

    if not group_id or not bot_wid:
        return

    raw_parts = group_data.get("participants", [])
    added_ids = [
        (p.get("id", "") if isinstance(p, dict) else str(p))
        for p in raw_parts
    ]

    if bot_wid not in added_ids:
        return  # C'est quelqu'un d'autre qui a été ajouté — pas le bot

    members = _greenapi_get_group_members(group_id, exclude=bot_wid)
    log.info(f"[GROUPE] Bot ajouté → {group_name!r} ({group_id}) — {len(members)} membres")

    try:
        _msg_executor.submit(_bot_ajoute_groupe, group_id, group_name, members)
    except Exception as e:
        log.error(f"_bot_ajoute_groupe dispatch : {e}")


@app.route("/webhook/whatsapp", methods=["POST"])
def webhook_whatsapp_greenapi():
    """
    Reçoit les événements WhatsApp depuis Green API.
    Format Green API : { typeWebhook, instanceData, senderData, messageData }
    """
    # ── 1) Authentification cryptographique — token secret dans le query string ──
    # GREENAPI_WEBHOOK_SECRET est configuré dans l'URL webhook du dashboard Green API :
    # https://<ngrok>/webhook/whatsapp?token=<SECRET>
    # Green API transmet ce token en query param à chaque appel entrant.
    if not GREENAPI_WEBHOOK_SECRET:
        log.error("🔴 GREENAPI_WEBHOOK_SECRET non configuré — webhook refusé")
        return jsonify({"status": "misconfigured"}), 503
    # Token accepté en query (mode Green API) OU en header Authorization: Bearer
    # (le header ne fuite pas dans les access logs comme le query string).
    incoming_token = request.args.get("token", "")
    if not incoming_token:
        _auth = request.headers.get("Authorization", "")
        if _auth.startswith("Bearer "):
            incoming_token = _auth[7:]
    if not ws.constant_time_equal(incoming_token, GREENAPI_WEBHOOK_SECRET):
        log_audit("GREENAPI_TOKEN_INVALIDE", "Webhook token mismatch", request.remote_addr)
        return jsonify({"status": "forbidden"}), 403

    # ── 2) Parsing payload ────────────────────────────────────────────────
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "bad_json"}), 400

    # ── 2b) Anti-rejeu : un idMessage déjà vu = requête rejouée → drop ─────
    #        Tue le replay d'une requête capturée (le token seul ne l'empêche pas).
    if _replay_guard.est_rejeu(payload.get("idMessage", "")):
        log_audit("WEBHOOK_REPLAY",
                  f"idMessage rejoué: {payload.get('idMessage', '')}", request.remote_addr)
        return jsonify({"status": "ok"}), 200

    # ── 3) Router par type d'événement ──────────────────────────────────
    type_webhook = payload.get("typeWebhook", "")

    if type_webhook == "incomingGroupParticipantsUpdate":
        _msg_executor.submit(_traiter_groupe_participants, payload)
        return jsonify({"status": "ok"}), 200

    if type_webhook not in ("incomingMessageReceived", "incomingAPIMessageReceived"):
        return jsonify({"status": "ignored"}), 200

    # ── 4) Extraire l'émetteur ────────────────────────────────────────────
    sender_data  = payload.get("senderData") or {}
    chat_id      = sender_data.get("chatId", "")
    sender       = sender_data.get("sender", "")
    is_group     = "@g.us" in chat_id
    wa_brut      = (sender if is_group else chat_id).split("@")[0]
    group_id     = chat_id if is_group else ""
    wa           = normaliser_numero(wa_brut)
    if not wa:
        if is_group and not sender:
            log.warning(f"Green API groupe sans sender : chatId={chat_id!r}")
        return jsonify({"status": "ok"}), 200

    # ── 4b) Vérification rate limit avant download ────────────────────────
    if not rate_limit_ok(wa):
        return jsonify({"status": "ok"}), 200

    # ── 5) Précharger l'image immédiatement (avant que l'URL expire) ──────
    img_future = None
    _type_msg = (payload.get("messageData") or {}).get("typeMessage", "")
    if _type_msg in ("imageMessage", "documentMessage"):
        _data_key  = "imageData" if _type_msg == "imageMessage" else "documentData"
        _url_media = ((payload.get("messageData") or {}).get(_data_key) or {}).get("downloadUrl", "")
        if _url_media:
            try:
                img_future = _download_executor.submit(_greenapi_telecharger_media, _url_media)
            except Exception as e:
                log.error(f"Download executor submit : {e}")
                # Future pre-resolu b"" : evite le fallback sync 31s dans le MsgWorker
                _f = concurrent.futures.Future()
                _f.set_result(b"")
                img_future = _f

    # ── 6) Soumettre le traitement au thread pool ─────────────────────────
    try:
        _msg_executor.submit(_traiter_message_greenapi, payload, wa, img_future, group_id)
    except Exception as e:
        log.error(f"Webhook Green API submit : {e}")
        if img_future is not None:
            img_future.cancel()

    return jsonify({"status": "ok"}), 200


def _greenapi_telecharger_media(url_media: str) -> bytes:
    """Télécharge un média depuis l'URL Green API et retourne le contenu en bytes.
    2 tentatives max. Timeout 15s par tentative. Détecte 403/404 = lien expiré.

    Durcissement SSRF (finding #2) :
      - whitelist sur le HOSTNAME avec frontière de label (ws.media_url_autorisee) ;
      - allow_redirects=False + rejet explicite de tout 3xx : la whitelist ne
        valide que le 1er hop ; une redirection mènerait vers 169.254.169.254,
        127.0.0.1:4040 (API ngrok locale), Postgres, etc. ;
      - streaming + plafond MEDIA_MAX_BYTES : pas de chargement mémoire illimité.
    """
    if not url_media:
        return b""
    if not ws.media_url_autorisee(url_media):
        log.warning(f"⚠️ URL média rejetée (hors whitelist/scheme) : {url_media[:80]}")
        return b""
    try:
        for attempt in range(2):
            try:
                r = requests.get(url_media, timeout=15,
                                  allow_redirects=False, stream=True)
                try:
                    if r.status_code in (301, 302, 303, 307, 308):
                        log.warning(f"⚠️ Média Green API redirige ({r.status_code}) "
                                    f"→ rejeté (anti-SSRF)")
                        return b""
                    if r.status_code == 200:
                        buf = bytearray()
                        for chunk in r.iter_content(65536):
                            if not chunk:
                                continue
                            buf.extend(chunk)
                            if len(buf) > MEDIA_MAX_BYTES:
                                log.warning("⚠️ Média Green API dépasse la limite "
                                            "de taille → rejeté")
                                return b""
                        return bytes(buf)
                    if r.status_code in (403, 404):
                        log.warning(f"⚠️ Média Green API {r.status_code} — lien expiré ou introuvable")
                        return b""
                finally:
                    r.close()
                if attempt == 0:
                    time_module.sleep(1)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 0:
                    time_module.sleep(1)
                continue
    except Exception as e:
        log.error(f"Téléchargement média Green API : {e}")
    return b""


def _traiter_message_greenapi(payload: dict, wa: str, img_future=None, group_id: str = ""):
    """Parse un événement Green API et route vers la logique métier."""
    if not group_id and not est_owner(wa):
        if not get_membre_by_wa(wa) and not est_admin(wa) and wa not in _sessions_config:
            return

    # ── Capture passive du pushname ──────────────────────────────────────────
    sender_name = (payload.get("senderData") or {}).get("senderName", "").strip()
    if _est_nom_valide(sender_name):
        m_pn = get_membre_by_wa(wa)
        if m_pn and (m_pn.get("nom_complet") or "").startswith("Membre_"):
            try:
                conn_pn = get_conn()
                try:
                    q(conn_pn,
                      "UPDATE membres SET nom_complet=%s, kyc_nom=%s "
                      "WHERE whatsapp=%s AND nom_complet LIKE 'Membre\\_%%'",
                      (sender_name, sender_name, wa))
                    conn_pn.commit()
                finally:
                    release_conn(conn_pn)
            except Exception:
                pass

    message_data = payload.get("messageData") or {}
    type_message = message_data.get("typeMessage", "")

    texte     = ""
    est_image = False
    img_bytes = b""

    if type_message == "textMessage":
        texte = (message_data.get("textMessageData") or {}).get("textMessage", "").strip()
    elif type_message in ("imageMessage", "documentMessage"):
        est_image  = True
        data_key   = "imageData" if type_message == "imageMessage" else "documentData"
        media_data = message_data.get(data_key) or {}
        url_media  = media_data.get("downloadUrl", "")
        caption    = media_data.get("caption", "")
        if img_future is not None:
            try:
                img_bytes = img_future.result(timeout=25)
            except Exception as e:
                log.error(f"img_future.result() : {e}")
                img_bytes = b""
        elif url_media:
            img_bytes = _greenapi_telecharger_media(url_media)
        if not img_bytes and caption:
            texte = caption
            est_image = False
        elif not img_bytes:
            wa_prive(wa, "❌ Impossible de télécharger le reçu. Renvoyez le screenshot.")
            return
    elif type_message == "extendedTextMessage":
        texte = (message_data.get("extendedTextMessageData") or {}).get("text", "").strip()

    log.info(f"WA ← {wa} : {texte[:60]!r}" + (" [IMG]" if est_image else ""))

    if est_image and img_bytes:
        try:
            # _traiter_screenshot_cotisation_bytes gère déjà group_id="" (DM) via
            # sa propre branche de lookup d'adhésion — la garde `if group_id:` ici
            # jetait silencieusement tout screenshot envoyé en DM, sans aucun
            # feedback au membre.
            _traiter_screenshot_cotisation_bytes(wa, img_bytes, caption, group_id or "")
        except Exception as e:
            log.error(f"Erreur image Green API : {e}")
        return

    if group_id:
        try:
            group_name = (payload.get("senderData") or {}).get("chatName", "")
            _traiter_commande_groupe(wa, texte, group_id, group_name)
        except Exception as e:
            log.error(f"Erreur commande groupe {group_id} : {e}")
        return

    # ── Config tontine via DM — intercept avant menu normal ──────────────────
    config_rep = traiter_config_tontine(wa, texte)
    if config_rep:
        wa_prive(wa, config_rep)
        return

    if est_owner(wa):
        rep = traiter_menu_owner(wa, texte)
        if rep:
            wa_prive(wa, rep)
            return

    rep_admin = traiter_menu_admin(wa, texte)
    if rep_admin:
        wa_prive(wa, rep_admin)
        return

    rep_membre = traiter_menu_membre(wa, texte, est_image)
    if rep_membre:
        wa_prive(wa, rep_membre)


def _traiter_screenshot_cotisation_bytes(wa: str, image_bytes: bytes,
                                          caption: str, group_id: str = ""):
    """
    Reçoit les bytes de l'image directement depuis Meta.
    Hash SHA-256 anti-recyclage + enregistrement cotisation.
    """
    conn   = get_conn()
    try:
        membre = fetchone(conn,
            "SELECT id, nom_complet, statut_global FROM membres WHERE whatsapp=%s", (wa,))

        if not membre:
            wa_prive(wa,
                f"❓ Vous n'êtes pas encore enregistré.\n\n"
                f"Tapez *menu* pour commencer votre inscription.\n\n"
                f"_TontineBot Pro — BADF Ltd_")
            return

        if membre["statut_global"] in ("Suspendu_global", "Banni"):
            wa_prive(wa, f"🚫 Compte *{membre['statut_global']}*. Contactez votre admin.")
            return

        # Anti-recyclage SHA-256 — avant le lookup adhesion (sécurité couche 1)
        img_hash = hash_screenshot(image_bytes)
        if screenshot_deja_utilise(conn, img_hash):
            log_audit("SCREENSHOT_RECYCLE",
                      f"Membre:{membre['id']} Hash:{img_hash[:16]}", wa)
            incrementer_tentatives_fraude(membre["id"], "Screenshot recyclé")
            return

        # Couche 2 — perceptual hash (dHash), signal SOUPLE : détecte le recyclage
        # 1px/re-compression que le byte-hash laisse passer. Ne bloque jamais seul.
        dh       = si.dhash(image_bytes)
        dh_hex   = "" if dh is None else format(dh, "016x")
        near_dup = screenshot_near_duplicate(conn, membre["id"], dh)
        ref_norm = ""   # référence de transaction normalisée (renseignée par l'OCR)

        # Récupérer l'adhesion avec nombre_places
        if group_id:
            adhesion = fetchone(conn, """
                SELECT a.tontine_id, a.nombre_places, t.nom, t.montant_place
                FROM adhesions a
                JOIN tontines t ON t.id = a.tontine_id
                WHERE a.membre_id=%s AND a.statut='Actif' AND t.statut='Active'
                  AND t.whatsapp_groupe=%s
                LIMIT 1
            """, (membre["id"], group_id))
        else:
            adhesion = fetchone(conn, """
                SELECT a.tontine_id, a.nombre_places, t.nom, t.montant_place
                FROM adhesions a
                JOIN tontines t ON t.id = a.tontine_id
                WHERE a.membre_id=%s AND a.statut='Actif' AND t.statut='Active'
                LIMIT 1
            """, (membre["id"],))

        if not adhesion:
            if group_id:
                wa_prive(wa,
                    "⚠️ *Reçu reçu — tontine non configurée*\n\n"
                    "Votre groupe n'est pas encore lié à une tontine active.\n"
                    "Contactez votre admin.\n\n"
                    "_TontineBot Pro — BADF Ltd_")
            return

        # Calcul du montant attendu selon les places
        nb_places       = adhesion["nombre_places"] or 1
        montant_attendu = adhesion["montant_place"] * nb_places

        # Signal SOUPLE near-dup (dHash) → alerte admin, jamais un blocage auto
        # (deux reçus MoMo distincts partagent le même template).
        if near_dup:
            log_audit("SCREENSHOT_NEAR_DUP",
                      f"Membre:{membre['id']} dHash proche d'un screenshot antérieur", wa)
            admin_nd = fetchone(conn,
                "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                (adhesion["tontine_id"],))
            if admin_nd:
                wa_prive(admin_nd["whatsapp"],
                    f"⚠️ *SCREENSHOT QUASI-IDENTIQUE — {adhesion['nom']}*\n\n"
                    f"Membre : *{membre['nom_complet']}*\n"
                    f"L'image ressemble fortement à un reçu déjà soumis "
                    f"(possible re-compression d'un ancien). Vérifiez la référence "
                    f"de transaction avant de confirmer.\n\n"
                    f"_TontineBot Pro — BADF Ltd_")

        # ── Lecture automatique du screenshot (OCR local Tesseract) ───────────
        lecture = lire_screenshot_mobile_money(image_bytes)
        fraude_visuelle = False

        if lecture.get("ok"):
            montant_lu  = lecture.get("montant")
            confiance   = lecture.get("confiance", "faible")
            operateur   = lecture.get("operateur", "Inconnu")
            ref_lu      = lecture.get("reference", "")
            ref_norm    = si.normaliser_reference(ref_lu)

            # Couche 3 (FORTE) — la référence de transaction est unique par paiement
            # réel : insensible au 1px/re-encodage qui défait le byte-hash. Blocage dur.
            if ref_norm and reference_deja_utilisee(conn, adhesion["tontine_id"], ref_norm):
                log_audit("SCREENSHOT_RECYCLE",
                          f"Membre:{membre['id']} Ref:{ref_norm} — transaction déjà utilisée", wa)
                incrementer_tentatives_fraude(membre["id"],
                                              f"Référence transaction recyclée: {ref_norm}")
                admin_r = fetchone(conn,
                    "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                    (adhesion["tontine_id"],))
                if admin_r:
                    wa_prive(admin_r["whatsapp"],
                        f"🚨 *RECYCLAGE BLOQUÉ — {adhesion['nom']}*\n\n"
                        f"Membre : *{membre['nom_complet']}*\n"
                        f"Référence de transaction *{ref_norm}* déjà utilisée — "
                        f"reçu recyclé rejeté automatiquement.\n\n"
                        f"_TontineBot Pro — BADF Ltd_")
                return

            # Vérification montant : comparaison avec montant attendu
            if montant_lu and montant_attendu:
                ecart = abs(montant_lu - montant_attendu)
                ecart_pct = ecart / montant_attendu * 100

                if ecart_pct > 10:
                    # Montant ne correspond pas — suspect
                    fraude_visuelle = True
                    incrementer_tentatives_fraude(
                        membre["id"],
                        f"Montant lu {montant_lu:,} FCFA ≠ attendu {montant_attendu:,} FCFA"
                    )
                    admin_alerte = fetchone(conn,
                        "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                        (adhesion["tontine_id"],))
                    if admin_alerte:
                        wa_prive(admin_alerte["whatsapp"],
                            f"🚨 *ALERTE MONTANT SUSPECT — {adhesion['nom']}*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Membre    : *{membre['nom_complet']}*\n"
                            f"Lu sur image : *{montant_lu:,} FCFA* ({operateur})\n"
                            f"Attendu      : *{montant_attendu:,} FCFA*\n"
                            f"Écart        : *{ecart_pct:.0f}%*\n\n"
                            f"⚠️ Le montant du screenshot ne correspond pas.\n"
                            f"Vérifiez avant de confirmer — possible screenshot modifié.\n\n"
                            f"_TontineBot Pro — BADF Ltd_"
                        )
                    log_audit("MONTANT_SUSPECT",
                              f"Membre:{membre['id']} Lu:{montant_lu} Attendu:{montant_attendu}",
                              wa)

            # Confiance faible → alerte admin
            if confiance == "faible" and not fraude_visuelle:
                admin_alerte = fetchone(conn,
                    "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                    (adhesion["tontine_id"],))
                if admin_alerte:
                    wa_prive(admin_alerte["whatsapp"],
                        f"⚠️ *SCREENSHOT DOUTEUX — {adhesion['nom']}*\n\n"
                        f"Membre : *{membre['nom_complet']}*\n"
                        f"L'image soumise est *floue ou tronquée*.\n"
                        f"Vérifiez attentivement avant de confirmer.\n\n"
                        f"_TontineBot Pro — BADF Ltd_"
                    )
                log_audit("SCREENSHOT_DOUTEUX",
                          f"Membre:{membre['id']} Confiance:{confiance}", wa)
        else:
            # OCR a échoué entièrement → on demande à l'admin de confirmer le montant
            operateur = "Inconnu"
            ref_lu    = ""
            # Alerter l'admin pour confirmation manuelle du montant
            admin_alerte = fetchone(conn,
                "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                (adhesion["tontine_id"],))
            if admin_alerte:
                wa_prive(admin_alerte["whatsapp"],
                    f"📸 *SCREENSHOT REÇU — confirmation manuelle*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Tontine    : *{adhesion['nom']}*\n"
                    f"Membre     : *{membre['nom_complet']}*\n"
                    f"Montant attendu : *{montant_attendu:,} FCFA*\n\n"
                    f"⚠️ Le bot n'a pas pu lire automatiquement ce reçu.\n"
                    f"Vérifiez sur votre téléphone que vous avez bien reçu le paiement, "
                    f"puis traitez via le menu admin :\n\n"
                    f"  1. Tapez *admin {adhesion['nom']}*\n"
                    f"  2. Tapez *15* (Cotisations en attente)\n"
                    f"  3. Répondez *OUI* si reçu, *NON [raison]* sinon\n\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )
            log_audit("OCR_ECHEC_MANUAL",
                      f"Membre:{membre['id']} Tontine:{adhesion['tontine_id']}", wa)

        # ── Limite : 1 screenshot par membre par période ─────────────────────
        # Détecte les soumissions multiples le même jour (Photoshop suspect)
        nb_aujourd_hui = fetchone(conn, """
            SELECT COUNT(*) n FROM cotisations_manuelles
            WHERE membre_id=%s AND tontine_id=%s
              AND date_soumission::date = CURRENT_DATE
        """, (membre["id"], adhesion["tontine_id"]))["n"]

        if nb_aujourd_hui >= 1:
            # Déjà soumis aujourd'hui — suspect
            incrementer_tentatives_fraude(
                membre["id"],
                f"Soumission multiple screenshot — {nb_aujourd_hui+1}ème tentative aujourd'hui"
            )
            # Alerte immédiate à l'admin
            admin_alerte = fetchone(conn,
                "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                (adhesion["tontine_id"],))
            if admin_alerte:
                wa_prive(admin_alerte["whatsapp"],
                    f"⚠️ *ALERTE FRAUDE POSSIBLE — {adhesion['nom']}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"*{membre['nom_complet']}* vient de soumettre un *{nb_aujourd_hui+1}ème screenshot "
                    f"aujourd'hui*.\n\n"
                    f"Ses soumissions du jour :\n"
                    f"  Déjà soumis  : *{nb_aujourd_hui}* screenshot(s)\n"
                    f"  Nouveau      : *1* (en attente)\n\n"
                    f"⚠️ Vérifiez attentivement avant de confirmer.\n"
                    f"Un screenshot modifié (Photoshop) peut ressembler à un vrai.\n\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )
            log_audit("SCREENSHOT_MULTIPLE",
                      f"Membre:{membre['id']} | {nb_aujourd_hui+1}ème soumission aujourd'hui", wa)

            # Ne pas bloquer — l'admin décide — mais informer le membre
            wa_prive(wa,
                f"⚠️ *Attention — {adhesion['nom']}*\n\n"
                f"Vous avez déjà soumis un screenshot aujourd'hui.\n"
                f"Votre nouveau reçu a été transmis à l'admin pour vérification.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )

        # Récupérer l'admin
        admin = fetchone(conn,
            "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
            (adhesion["tontine_id"],))
        admin_wa = admin["whatsapp"] if admin else OWNER_WA

        # Enregistrer la cotisation avec le bon montant
        try:
            cotis_id = enregistrer_cotisation_manuelle(
                conn,
                membre_id       = membre["id"],
                tontine_id      = adhesion["tontine_id"],
                montant         = montant_attendu,
                screenshot_hash = img_hash,
                admin_wa        = admin_wa,
                dhash_hex       = dh_hex,
                ref_norm        = ref_norm,
            )
        except Exception as e:
            log.error(f"❌ enregistrer_cotisation_manuelle échec pour {wa}: {e}")
            wa_prive(wa,
                f"⚠️ *Erreur technique — {adhesion['nom']}*\n\n"
                f"Votre reçu a été reçu mais n'a pas pu être enregistré.\n"
                f"Contactez votre admin.\n\n"
                f"_TontineBot Pro — BADF Ltd_")
            return


        # Message places multiples si applicable
        places_info = ""
        if nb_places > 1:
            places_info = (
                f"\n📋 *{nb_places} places* × {adhesion['montant_place']:,} FCFA "
                f"= *{montant_attendu:,} FCFA*"
            )

        # Accusé au membre
        wa_prive(wa,
            f"📸 *Reçu enregistré — {adhesion['nom']}*\n\n"
            f"Bonjour *{membre['nom_complet']}*,{places_info}\n\n"
            f"Votre reçu de *{montant_attendu:,} FCFA* a été transmis "
            f"à votre admin.\n\nVous serez notifié dès validation.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

        # Notifier l'admin
        places_txt  = f" ({nb_places} places)" if nb_places > 1 else ""
        vision_txt  = ""
        if lecture.get("ok") and lecture.get("montant"):
            vision_txt = (
                f"📱 Lu sur image : *{lecture['montant']:,} FCFA* "
                f"({lecture.get('operateur','?')}) "
                f"— confiance *{lecture.get('confiance','?')}*\n"
            )
        alerte_txt = "🚨 *MONTANT SUSPECT — vérifiez avant de confirmer*\n\n" if fraude_visuelle else ""

        wa_prive(admin_wa,
            f"🔔 *COTISATION — {adhesion['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{alerte_txt}"
            f"Membre  : *{membre['nom_complet']}*{places_txt}\n"
            f"Attendu : *{montant_attendu:,} FCFA*\n"
            f"{vision_txt}"
            f"Réf     : *#{cotis_id}*\n\n"
            f"Menu admin → option *15* pour confirmer.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        log_audit("SCREENSHOT_RECU",
                  f"Membre:{membre['id']} Places:{nb_places} "
                  f"Montant:{montant_attendu} Cotis#{cotis_id}", wa)
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# CASHOUT — BOUFFAGE AUTOMATIQUE
# ══════════════════════════════════════════════════════════════════════════

@healed()
def traiter_bouffages_suspects_expires():
    """
    Lancé tous les jours à 11h.
    Pour chaque bouffage suspect bloqué depuis plus de 48h sans déblocage admin :
      1. Calcule les dettes du membre (IRA + cotisations manquantes)
      2. Utilise caution + cotisations récentes pour couvrir les dettes
      3. Redistribue le reste au groupe
      4. Notifie admin + membre
    """
    conn = get_conn()
    try:
        passages = fetchall(conn, """
            SELECT lp.*, m.nom_complet, m.whatsapp, m.id AS mbr_id,
                   t.nom AS tontine_nom, t.montant_place, t.cycle_actuel,
                   t.whatsapp_groupe
            FROM liste_passage lp
            JOIN membres m  ON m.id  = lp.membre_id
            JOIN tontines t ON t.id  = lp.tontine_id
            WHERE lp.bloque_suspect = 1
              AND lp.statut = 'En_attente'
              AND lp.date_blocage < NOW() - INTERVAL '48 hours'
        """)

        for p in passages:
            membre_id  = p["mbr_id"]
            tontine_id = p["tontine_id"]

            # Dettes IRA
            dettes_ira = fetchone(conn, """
                SELECT COALESCE(SUM(montant),0) total FROM dettes_ira
                WHERE membre_id=%s AND tontine_id=%s AND statut='Due'
            """, (membre_id, tontine_id))["total"]

            # Cotisations manquantes depuis inscription
            # (nombre de périodes écoulées - cotisations payées)
            cotis_payees = fetchone(conn, """
                SELECT COUNT(*) n FROM transactions
                WHERE membre_id=%s AND tontine_id=%s
                  AND type_transaction='Cotisation' AND statut='Confirmee'
            """, (membre_id, tontine_id))["n"]

            # Position dans l'ordre — cotisations attendues = son ordre
            cotis_attendues  = p["ordre"]
            cotis_manquantes = max(0, cotis_attendues - cotis_payees)
            _places_debt = fetchone(conn,
                "SELECT nombre_places FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
                (membre_id, tontine_id))
            nb_places_debt = (_places_debt["nombre_places"] if _places_debt else 1) or 1
            dette_cotis      = cotis_manquantes * p["montant_place"] * nb_places_debt

            # Caution bloquée
            caution = fetchone(conn, """
                SELECT id, montant FROM cautions_garantie
                WHERE membre_id=%s AND tontine_id=%s AND statut='Bloquee'
            """, (membre_id, tontine_id))

            montant_caution  = caution["montant"] if caution else 0
            total_dettes     = dettes_ira + dette_cotis

            # Calcul
            if montant_caution >= total_dettes:
                solde_membre  = montant_caution - total_dettes
                couverture_txt = (
                    f"Caution ({montant_caution:,} FCFA) couvre toutes les dettes.\n"
                    f"Reversé au membre : *{solde_membre:,} FCFA*"
                )
            else:
                solde_membre     = 0
                dette_residuelle = total_dettes - montant_caution
                couverture_txt   = (
                    f"Caution ({montant_caution:,} FCFA) couvre partiellement.\n"
                    f"Dette résiduelle enregistrée : *{dette_residuelle:,} FCFA*"
                )

            # Saisir la caution
            if caution:
                q(conn, "UPDATE cautions_garantie SET statut='Saisie', date_liberation=NOW() WHERE id=%s",
                  (caution["id"],))

            # Solder les ANCIENNES dettes IRA (couvertes par la caution) — AVANT
            # de créer la nouvelle ligne résiduelle ci-dessous, sinon le blanket
            # UPDATE la marquerait 'Prelevee' alors qu'elle n'a jamais été payée.
            q(conn, """UPDATE dettes_ira SET statut='Prelevee', prelevee_le=NOW()
                       WHERE membre_id=%s AND tontine_id=%s AND statut='Due'""",
              (membre_id, tontine_id))

            # Nouvelle dette résiduelle (caution insuffisante) — créée APRÈS le
            # solde ci-dessus, reste donc 'Due' (réellement non réglée).
            if montant_caution < total_dettes:
                q(conn, """INSERT INTO dettes_ira
                           (membre_id, tontine_id, montant, motif)
                           VALUES (%s,%s,%s,%s)""",
                  (membre_id, tontine_id, dette_residuelle,
                   "Dette résiduelle — bouffage suspect non débloqué"))

            # Virer le reliquat au membre si positif
            if solde_membre > 0:
                # montant_ira = 0 : les dettes IRA sont déjà soldées par la
                # caution ci-dessus, ce reliquat n'en déduit aucune.
                q(conn, """INSERT INTO bouffages_manuels
                           (membre_id, tontine_id, passage_id, montant_brut,
                            caution, montant_net, statut, montant_ira)
                           VALUES (%s,%s,%s,%s,0,%s,'En_attente',0)""",
                  (membre_id, tontine_id, p["id"], solde_membre, solde_membre))
                conn.commit()
                wa_admins_tontine(tontine_id,
                    f"💳 *À VIRER AU MEMBRE — {p['tontine_nom']}*\n\n"
                    f"Membre : *{p['nom_complet']}*\n"
                    f"Montant net après déduction dettes : *{solde_membre:,} FCFA*\n\n"
                    f"Virez ce montant sur son numéro Mobile Money.\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )

            # Marquer le passage comme intercepté
            q(conn, """UPDATE liste_passage
                       SET statut='Intercepte', bloque_suspect=0
                       WHERE id=%s""", (p["id"],))

            # Suspendre le membre
            q(conn, """UPDATE membres SET statut_global='Suspendu_global'
                       WHERE id=%s""", (membre_id,))

            conn.commit()

            # Notifier l'admin
            wa_admins_tontine(tontine_id,
                f"⚖️ *BOUFFAGE SUSPECT TRAITÉ — {p['tontine_nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Membre    : *{p['nom_complet']}*\n"
                f"Dettes    : IRA {dettes_ira:,} + Cotis {dette_cotis:,} = "
                f"*{total_dettes:,} FCFA*\n\n"
                f"{couverture_txt}\n\n"
                f"Son compte est suspendu.\n"
                f"Pour le réactiver : option 5 du menu admin.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )

            # Notifier le membre
            wa_prive(p["whatsapp"],
                f"⚖️ *DÉCISION ADMINISTRATIVE — {p['tontine_nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Votre bouffage a été suspendu pour comportement suspect "
                f"et n'a pas été débloqué par l'administration.\n\n"
                f"Vos dettes ont été prélevées sur votre caution.\n"
                f"{couverture_txt}\n\n"
                f"Votre compte est suspendu. Contactez votre admin "
                f"pour régulariser votre situation.\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )

            log_audit("BOUFFAGE_SUSPECT_TRAITE",
                      f"{p['nom_complet']} | {p['tontine_nom']} | "
                      f"Dettes:{total_dettes:,} | Redistribué:{solde_membre:,}",
                      p["whatsapp"])

    except Exception as e:
        log.error(f"traiter_bouffages_suspects_expires : {e}")
    finally:
        release_conn(conn)


def _verifier_historique_avant_bouffage(conn, membre_id: int,
                                         tontine_id: int,
                                         ordre_passage: int) -> str:
    """
    Vérifie si un membre a un comportement suspect avant son bouffage.
    Retourne une chaîne décrivant le problème, ou "" si tout est normal.

    Schémas détectés :
    1. Taux de cotisation < 50% sur les 30 derniers jours
    2. Subite accélération — 0 cotisation les 20 premiers jours,
       cotise les 10 derniers jours seulement
    3. Dettes IRA importantes non soldées
    """
    tontine = fetchone(conn,
        "SELECT type_tontine, montant_place, cycle_actuel FROM tontines WHERE id=%s",
        (tontine_id,))
    if not tontine:
        return ""

    # Périodes attendues selon type tontine
    if tontine["type_tontine"] == "Journaliere":
        periodes_30j = 30
    elif tontine["type_tontine"] == "Hebdomadaire":
        periodes_30j = 4
    else:
        periodes_30j = 1

    # Cotisations des 30 derniers jours
    cotis_30j = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s
          AND type_transaction='Cotisation' AND statut='Confirmee'
          AND date_heure >= NOW() - INTERVAL '30 days'
    """, (membre_id, tontine_id))["n"]

    # Cotisations des 10 derniers jours
    cotis_10j = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s
          AND type_transaction='Cotisation' AND statut='Confirmee'
          AND date_heure >= NOW() - INTERVAL '10 days'
    """, (membre_id, tontine_id))["n"]

    # Cotisations des 20 premiers des 30 derniers jours
    cotis_j20_j10 = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s
          AND type_transaction='Cotisation' AND statut='Confirmee'
          AND date_heure >= NOW() - INTERVAL '30 days'
          AND date_heure < NOW() - INTERVAL '10 days'
    """, (membre_id, tontine_id))["n"]

    # Dettes IRA en cours
    dettes_ira = fetchone(conn, """
        SELECT COALESCE(SUM(montant),0) total FROM dettes_ira
        WHERE membre_id=%s AND tontine_id=%s AND statut='Due'
    """, (membre_id, tontine_id))["total"]

    # Nombre total de cotisations depuis le début
    cotis_total = fetchone(conn, """
        SELECT COUNT(*) n FROM transactions
        WHERE membre_id=%s AND tontine_id=%s
          AND type_transaction='Cotisation' AND statut='Confirmee'
    """, (membre_id, tontine_id))["n"]

    alertes = []

    # ── Schéma 1 : Taux global trop faible ───────────────────────────────
    if periodes_30j > 1:
        taux = cotis_30j / periodes_30j * 100
        if taux < 50:
            alertes.append(
                f"Taux de cotisation faible : {cotis_30j}/{periodes_30j} "
                f"périodes payées ({taux:.0f}%) sur 30 jours"
            )

    # ── Schéma 2 : Accélération suspecte ─────────────────────────────────
    # Cotise peu avant, beaucoup juste avant le bouffage
    if tontine["type_tontine"] == "Journaliere":
        if cotis_10j >= 8 and cotis_j20_j10 <= 3 and cotis_total <= 15:
            alertes.append(
                f"Accélération suspecte : {cotis_j20_j10} cotisations "
                f"les 20 premiers jours vs {cotis_10j} les 10 derniers jours"
            )

    # ── Schéma 3 : Dettes IRA non soldées importantes ─────────────────────
    seuil_ira = tontine["montant_place"] * 5
    if dettes_ira >= seuil_ira:
        alertes.append(
            f"Dettes IRA non soldées : {dettes_ira:,} FCFA "
            f"(équivalent {dettes_ira // tontine['montant_place']} jours)"
        )

    return "\n".join(f"▪ {a}" for a in alertes)


# ══════════════════════════════════════════════════════════════════════════
# FINANCE COMPORTEMENTALE — Trust Graph dans trust_graph.py (score de fugue,
# alerte quotidienne, commandes admin « risque » et « cas »)
# ══════════════════════════════════════════════════════════════════════════

from trust_graph import (
    _update_score_confiance, POIDS_MAX_TRUST_GRAPH, _agreger_score_risque_fugue,
    calculer_score_risque_fugue, alerter_risques_bouffage_imminent,
    commande_admin_score_risque, commande_admin_cas_difficile,
    commande_admin_cas_resolu, lever_pauses_echues,
)


# ══════════════════════════════════════════════════════════════════════════


@healed()
def notifier_prochain_bouffage():
    """
    Lancé toutes les heures par le scheduler.
    Pour chaque tontine dont heure_bouffage = heure actuelle :
      1. Cherche le passage prévu aujourd'hui
      2. Calcule le montant net (cagnotte - caution - IRA)
      3. DM au bénéficiaire pour lui demander son numéro MM
      4. DM à l'admin avec le montant exact à virer
    """
    heure_now = datetime.now().strftime("%H:00")
    today     = datetime.now().date()
    conn      = get_conn()
    tontines  = fetchall(conn,
        "SELECT * FROM tontines WHERE statut='Active' AND heure_bouffage=%s",
        (heure_now,))

    for t in tontines:
        passage = fetchone(conn, """
            SELECT lp.*, m.nom_complet, m.whatsapp, m.id AS mbr_id
            FROM liste_passage lp
            LEFT JOIN membres m ON m.id = lp.membre_id
            WHERE lp.tontine_id=%s AND lp.cycle=%s
              AND lp.statut='En_attente' AND lp.date_bouffage=%s
            ORDER BY lp.ordre LIMIT 1
        """, (t["id"], t["cycle_actuel"], today))

        if not passage:
            continue

        # Membre non encore enrôlé
        if not passage.get("membre_id"):
            wa_admins_tontine(t["id"],
                f"⚠️ *BOUFFAGE BLOQUÉ — {t['nom']}*\n\n"
                f"Le bénéficiaire *{passage.get('nickname','?')}* "
                f"n'est pas encore enrôlé.\n\n"
                f"Demandez-lui de taper *menu* en DM au bot.")
            continue

        # ── Vérification historique cotisation avant bouffage ────────────
        # Détecte le schéma : mauvais payeur qui cotise juste avant son tour
        alerte_suspect = _verifier_historique_avant_bouffage(
            conn, passage["mbr_id"], t["id"], passage["ordre"]
        )
        if alerte_suspect:
            # Bloquer le bouffage — admin doit valider manuellement
            q(conn, """UPDATE liste_passage
                       SET statut='En_attente', bloque_suspect=1, date_blocage=NOW()
                       WHERE id=%s""", (passage["id"],))
            conn.commit()
            wa_admins_tontine(t["id"],
                f"🚨 *BOUFFAGE SUSPENDU — {t['nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Bénéficiaire : *{passage['nom_complet']}*\n\n"
                f"⚠️ *Comportement suspect détecté :*\n"
                f"{alerte_suspect}\n\n"
                f"Le bouffage est *suspendu* en attente de votre validation.\n\n"
                f"Pour débloquer tapez :\n"
                f"*admin* → option *DEBLOQUER {passage['id']}*\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
            wa_owner(
                f"🚨 *BOUFFAGE SUSPECT BLOQUÉ*\n"
                f"Tontine : {t['nom']}\n"
                f"Membre  : {passage['nom_complet']}\n"
                f"Raison  : {alerte_suspect}"
            )
            log_audit("BOUFFAGE_SUSPECT_BLOQUE",
                      f"{passage['nom_complet']} | {t['nom']} | {alerte_suspect}",
                      passage["whatsapp"])
            continue

        # ── Trust Graph — alerte admin au moment du bouffage ─────────────────
        score_trust = calculer_score_risque_fugue(conn, passage["mbr_id"], t["id"])
        if score_trust["niveau"] == "erreur":
            # Échec du calcul — ne JAMAIS traiter comme "zéro risque" (fail-open).
            # Le bouffage se poursuit (pas de blocage automatique sur une erreur
            # technique), mais l'admin est prévenu pour vérification manuelle.
            wa_admins_tontine(t["id"],
                f"⚠️ *SCORE DE RISQUE INDISPONIBLE — {t['nom']}*\n\n"
                f"Le calcul du score de risque a échoué pour "
                f"*{passage['nom_complet']}* au moment de son bouffage.\n"
                f"Le bouffage se poursuit normalement — vérifiez sa situation "
                f"manuellement si besoin.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
            log_audit("TRUST_GRAPH_ERREUR",
                      f"{passage['nom_complet']} | {t['nom']}", passage.get("whatsapp", ""))
        elif score_trust["niveau"] in ("orange", "rouge"):
            emoji_tg = "🔴" if score_trust["niveau"] == "rouge" else "🟠"
            signaux_txt = "\n".join(f"  • {s}" for s in score_trust["signaux"])
            wa_admins_tontine(t["id"],
                f"{emoji_tg} *ALERTE TRUST GRAPH — BOUFFAGE IMMINENT — {t['nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Bénéficiaire : *{passage['nom_complet']}*\n"
                f"Score fugue  : *{score_trust['score']}/100* {emoji_tg}\n\n"
                f"Signaux :\n{signaux_txt}\n\n"
                f"👉 *{score_trust['recommandation']}*\n\n"
                f"Le bouffage se déclenche normalement. C'est vous qui décidez.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
            log_audit("BOUFFAGE_TRUST_ALERTE",
                      f"{passage['nom_complet']} | Score:{score_trust['score']} | {t['nom']}",
                      passage.get("whatsapp", ""))

        # Calcul financier — SUM(nombre_places), pas COUNT(*) : un membre peut détenir plusieurs places
        nb_places    = fetchone(conn,
            "SELECT COALESCE(SUM(nombre_places),0) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
            (t["id"],))["n"]
        montant_brut = nb_places * t["montant_place"]
        caution_pct  = t["caution_pourcent"] if t["caution_active"] else 0
        caution_mont = int(montant_brut * caution_pct / 100)

        # Dettes IRA
        dettes_ira  = fetchall(conn,
            "SELECT id, montant FROM dettes_ira WHERE membre_id=%s AND tontine_id=%s AND statut='Due'",
            (passage["mbr_id"], t["id"]))
        total_ira   = sum(d["montant"] for d in dettes_ira)

        # Cotisations manquantes — ce qu'il devait payer avant son tour
        cotis_payees     = fetchone(conn, """
            SELECT COUNT(*) n FROM transactions
            WHERE membre_id=%s AND tontine_id=%s
              AND type_transaction='Cotisation' AND statut='Confirmee'
        """, (passage["mbr_id"], t["id"]))["n"]
        cotis_attendues  = passage["ordre"]  # il devait cotiser autant de fois que son rang
        cotis_manquantes = max(0, cotis_attendues - cotis_payees)
        _places_benef = fetchone(conn,
            "SELECT nombre_places FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
            (passage["mbr_id"], t["id"]))
        nb_places_benef = (_places_benef["nombre_places"] if _places_benef else 1) or 1
        dette_cotis      = cotis_manquantes * t["montant_place"] * nb_places_benef

        # Montant net final = cagnotte - caution - IRA - cotisations manquantes
        montant_net = max(0, montant_brut - caution_mont - total_ira - dette_cotis)

        # Résumé déductions pour l'admin
        deductions_txt = f"  Cagnotte brute      : *{montant_brut:,} FCFA*\n"
        if caution_mont > 0:
            deductions_txt += f"  Caution 10%         : *-{caution_mont:,} FCFA*\n"
        if total_ira > 0:
            deductions_txt += f"  Pénalités IRA       : *-{total_ira:,} FCFA*\n"
        if dette_cotis > 0:
            deductions_txt += (
                f"  Cotis manquantes    : *-{dette_cotis:,} FCFA*"
                f" ({cotis_manquantes} × {t['montant_place']:,})\n"
            )
        deductions_txt += f"  ─────────────────────────\n"
        deductions_txt += f"  *NET À VIRER        : {montant_net:,} FCFA*"

        # Déclencher le bouffage manuel
        q(conn, "UPDATE liste_passage SET statut='Notifie' WHERE id=%s",
          (passage["id"],))
        conn.commit()

        # La caution dans declencher_bouffage_manuel inclut toutes les déductions
        total_deductions = caution_mont + total_ira + dette_cotis
        bouffage_id = declencher_bouffage_manuel(
            conn         = conn,
            membre_id    = passage["mbr_id"],
            tontine_id   = t["id"],
            passage_id   = passage["id"],
            montant_brut = montant_brut,
            caution      = total_deductions,
            deductions_detail = deductions_txt,
            caution_reelle    = caution_mont,
            dettes_ira        = dettes_ira,
        )

        # Enregistrer la caution en base (informatif)
        if caution_mont > 0:
            try:
                q(conn, """INSERT INTO cautions_garantie
                           (membre_id, tontine_id, passage_id, montant, pourcent)
                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                  (passage["mbr_id"], t["id"], passage["id"],
                   caution_mont, caution_pct))
                conn.commit()
            except Exception:
                pass

        # ── Bordereau transparent au bénéficiaire AVANT le virement ──────
        # Le membre voit le calcul ligne par ligne. Diminue 80% des contestations.
        bordereau = (
            f"🧾 *BORDEREAU DE BOUFFAGE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"*{passage['nom_complet']}*\n"
            f"{t['nom']} — Cycle {t.get('cycle_actuel', 1)}\n\n"
            f"💰 *Cagnotte brute* : *{montant_brut:,} FCFA*\n"
        )
        if caution_mont > 0:
            bordereau += (
                f"\n🔒 *Caution 10%* : -{caution_mont:,} FCFA\n"
                f"   _Cette caution n'est pas perdue._\n"
                f"   _Elle vous sera rendue intégralement_\n"
                f"   _à la fin du cycle si vous continuez_\n"
                f"   _à cotiser jusqu'au bout._\n"
            )
        if total_ira > 0:
            bordereau += (
                f"\n⏰ *Pénalités IRA* : -{total_ira:,} FCFA\n"
                f"   _150 FCFA × jours de retard accumulés_\n"
                f"   _sur vos cotisations passées._\n"
            )
        if dette_cotis > 0:
            bordereau += (
                f"\n📋 *Cotisations manquantes* : -{dette_cotis:,} FCFA\n"
                f"   _{cotis_manquantes} cotisation(s) non payée(s)_\n"
                f"   _× {t['montant_place']:,} FCFA chacune._\n"
            )
        bordereau += (
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 *NET À RECEVOIR : {montant_net:,} FCFA*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"L'administrateur va vous virer ce montant\n"
            f"sur votre Mobile Money dans les prochaines heures.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        wa_prive(passage["whatsapp"], bordereau)

        # Annonce dans le groupe (sans montant — discrétion)
        if t.get("whatsapp_groupe"):
            wa_groupe(t["whatsapp_groupe"],
                f"🏆 *{t['nom']} — BOUFFAGE DU JOUR*\n\n"
                f"Le bénéficiaire du jour a été notifié en message privé.\n"
                f"Les cotisations continuent normalement.\n\n"
                f"_TontineBot Pro — BADF Ltd_")

        log.info(f"Bouffage déclenché : {passage['nom_complet']} — {t['nom']}")
        log_audit("BOUFFAGE_DECLENCHE",
                  f"{passage['nom_complet']} | {t['nom']} | {montant_net:,} FCFA")

    release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# NETTOYAGE SESSIONS EXPIRÉES
# ══════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════
# RAPPELS HORAIRES — PRESSION SOCIALE
# ══════════════════════════════════════════════════════════════════════════

_JOURS_SEMAINE = {"Lundi":0,"Mardi":1,"Mercredi":2,"Jeudi":3,"Vendredi":4,"Samedi":5,"Dimanche":6}

def _est_jour_collecte(t: dict, d: _date) -> bool:
    """
    True si la date `d` est un jour de cotisation pour cette tontine.
    Source UNIQUE du calendrier de collecte (rappels + échéance IRA) :
      • Journaliere  → tout jour
      • Hebdomadaire → jour_semaine
      • Mensuelle    → jour_mois (1-28)
    Date-paramétrique : sert aussi à ira_engine.echeance_periode pour dater
    l'échéance d'un paiement en retard, pas seulement à tester "aujourd'hui".
    """
    tt = t.get("type_tontine", "Journaliere")
    if tt == "Journaliere":
        return True
    if tt == "Hebdomadaire":
        jour_cible = _JOURS_SEMAINE.get(t.get("jour_semaine"))
        if jour_cible is None:
            log.warning(f"jour_semaine invalide '{t.get('jour_semaine')}' tontine#{t.get('id')}")
            return False
        return d.weekday() == jour_cible
    if tt == "Mensuelle":
        return d.day == (t.get("jour_mois") or 1)
    return True


def _est_jour_cotisation(t: dict) -> bool:
    """True si aujourd'hui (tz de la tontine) est un jour de cotisation."""
    if t.get("type_tontine", "Journaliere") == "Journaliere":
        return True
    tz_name = t.get("timezone") or "Africa/Douala"
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        today = datetime.now().date()
    return _est_jour_collecte(t, today)


def rappel_ouverture():
    """Lancé toutes les heures — annonce l'ouverture des cotisations dans chaque groupe."""
    heure_now = datetime.now().strftime("%H:00")
    conn      = get_conn()
    tontines  = fetchall(conn,
        """SELECT * FROM tontines
           WHERE statut='Active' AND heure_ouverture=%s""",
        (heure_now,))
    now_str = datetime.now().strftime("%A %d %B %Y").capitalize()
    for t in tontines:
        if not _est_jour_cotisation(t):
            continue
        # Récupérer le numéro de collecte de l'admin
        admin = fetchone(conn,
            """SELECT numero_collecte FROM admins_groupe
               WHERE tontine_id=%s AND numero_collecte IS NOT NULL LIMIT 1""",
            (t["id"],))
        num_collecte = admin["numero_collecte"] if admin else "— demandez à votre admin"
        wa_groupe(t["whatsapp_groupe"] or t["nom"],
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 *{t['nom']} — TontineBot Pro*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*{now_str}*\n\n"
            f"Les dépôts sont ouverts.\n\n"
            f"📱 Effectuez votre virement de *{t['montant_place']:,} FCFA*\n"
            f"   vers le numéro : *{num_collecte}*\n\n"
            f"📸 Puis envoyez le *screenshot de confirmation* dans ce groupe.\n\n"
            f"⏰ Heure limite : *{t['heure_limite']}*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
    release_conn(conn)


def rappel_matin():
    """Rappel matin — heure_ouverture + 3h pour chaque tontine."""
    heure_now = datetime.now().strftime("%H:00")
    conn      = get_conn()
    tontines  = fetchall(conn,
        "SELECT * FROM tontines WHERE statut='Active'")
    for t in tontines:
        try:
            h_ouv          = int(t["heure_ouverture"].split(":")[0])
            h_rappel_matin = f"{(h_ouv + 3) % 24:02d}:00"
        except Exception:
            h_rappel_matin = "08:00"
        if heure_now != h_rappel_matin:
            continue
        if not _est_jour_cotisation(t):
            continue
        admin = fetchone(conn,
            "SELECT numero_collecte FROM admins_groupe WHERE tontine_id=%s AND numero_collecte IS NOT NULL LIMIT 1",
            (t["id"],))
        num_collecte = admin["numero_collecte"] if admin else "— demandez à votre admin"
        wa_groupe(t["whatsapp_groupe"] or t["nom"],
            f"☀️ *{t['nom']} — Rappel cotisation*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"N'oubliez pas votre cotisation de *{t['montant_place']:,} FCFA* !\n\n"
            f"📱 Virez vers : *{num_collecte}*\n"
            f"📸 Puis envoyez votre screenshot dans ce groupe.\n\n"
            f"⏰ Heure limite : *{t['heure_limite']}*\n"
            f"Passé ce délai : IRA *{int(t['montant_place']*IRA_CLIFF_PCT):,} FCFA* "
            f"({int(IRA_CLIFF_PCT*100)}% de la mise) +{int(IRA_DAILY_PCT*100)}%/jour\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
    release_conn(conn)


@healed()
def rappel_non_cotisants():
    """
    Lancé toutes les heures.
    Pour chaque tontine dont heure_rappel = heure actuelle
    → publie la liste des non-cotisants dans le groupe.
    """
    heure_now = datetime.now().strftime("%H:00")
    conn      = get_conn()
    tontines  = fetchall(conn,
        """SELECT * FROM tontines
           WHERE statut='Active' AND heure_rappel=%s""",
        (heure_now,))
    now_str = datetime.now().strftime("%d/%m/%Y")

    for t in tontines:
        if not _est_jour_cotisation(t):
            continue
        retards   = _get_retardataires(conn, t["id"])
        nb_actifs = fetchone(conn,
            "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
            (t["id"],))["n"]
        nb_ok     = nb_actifs - len(retards)

        admin = fetchone(conn,
            "SELECT numero_collecte FROM admins_groupe WHERE tontine_id=%s AND numero_collecte IS NOT NULL LIMIT 1",
            (t["id"],))
        num_collecte = admin["numero_collecte"] if admin else "— demandez à votre admin"

        if not retards:
            wa_groupe(t["whatsapp_groupe"] or t["nom"],
                f"✅ *{t['nom']} — {now_str}*\n\n"
                f"🏆 Félicitations ! Tous les membres ont cotisé.\n"
                f"*{nb_actifs}/{nb_actifs}* — Groupe exemplaire !\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
        else:
            # Pression sociale graduée selon l'heure
            heure_actuelle = datetime.now().hour
            if heure_actuelle < 12:
                ton = "rappel matinal"
                emoji_pression = "☀️"
                phrase = "La journée commence — régularisez avant midi."
            elif heure_actuelle < 17:
                ton = "rappel urgent"
                emoji_pression = "⚠️"
                phrase = "Il vous reste quelques heures. Ne laissez pas tomber le groupe."
            else:
                ton = "DERNIER RAPPEL"
                emoji_pression = "🚨"
                phrase = "L'heure limite approche. Chaque minute de retard = pénalité IRA."

            noms = "\n".join(
                f"  {'🔴' if r['score_confiance'] < 40 else '🟡'} {r['nom_complet']}"
                + (f" ⚠️ _{r['score_confiance']}pts_" if r['score_confiance'] < 40 else "")
                for r in retards
            )

            msg_rappel = (
                f"{emoji_pression} *{t['nom']} — {ton.upper()} — {now_str}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"*{nb_ok}/{nb_actifs}* membres ont déjà cotisé. ✅\n\n"
                f"⏳ *En attente de screenshot :*\n"
                f"{noms}\n\n"
                f"📱 Virez *{t['montant_place']:,} FCFA* → *{num_collecte}*\n"
                f"📸 Envoyez le screenshot dans ce groupe.\n\n"
                f"_{phrase}_\n\n"
                f"⏰ Limite : *{t['heure_limite']}* | "
                f"Retard → IRA *{int(IRA_CLIFF_PCT*100)}% de la mise* +{int(IRA_DAILY_PCT*100)}%/jour\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
            wa_groupe(t["whatsapp_groupe"] or t["nom"], msg_rappel)

    release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# CRÉDIT COMMUNICATION — GATE ANTI-FRAUDE 5 TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════

def _verifier_credit_comm(conn, tontine_id: int):
    """Débloque le crédit comm 1000 FCFA après 5 cotisations confirmées réelles.
    Prévient les faux groupes créés pour obtenir le bonus sans activité réelle."""
    try:
        tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine or tontine["credit_comm_statut"] != "Non_eligible":
            return
        nb_tx = fetchone(conn, """
            SELECT COUNT(*) AS n FROM transactions
            WHERE tontine_id=%s AND type_transaction='Cotisation' AND statut='Confirmee'
        """, (tontine_id,))["n"]
        if nb_tx >= 5:
            q(conn, "UPDATE tontines SET credit_comm_statut='Eligible' WHERE id=%s", (tontine_id,))
            conn.commit()
            log_audit("CREDIT_COMM_ELIGIBLE",
                      f"Tontine {tontine['nom']} — {nb_tx} tx confirmées", "system")
            try:
                wa_prive(OWNER_WA,
                    f"🎁 *CRÉDIT COMM À VERSER*\n\n"
                    f"Tontine : *{tontine['nom']}*\n"
                    f"Transactions confirmées : *{nb_tx}*\n\n"
                    f"Versez *1 000 FCFA* de crédit à l'admin de ce groupe.\n"
                    f"Tapez *CREDIT_VERSE {tontine_id}* pour confirmer le versement."
                )
            except Exception as _wa_e:
                log.warning(f"⚠️ CREDIT_COMM notif échouée — DB=Eligible, tontine#{tontine_id} : {_wa_e}. "
                            f"Tapez CREDIT_VERSE {tontine_id} pour confirmer manuellement.")
    except Exception as _e:
        log.warning(f"⚠️ _verifier_credit_comm tontine#{tontine_id} : {_e}")


# ══════════════════════════════════════════════════════════════════════════
# SUSPENSION AUTOMATIQUE 72H
# ══════════════════════════════════════════════════════════════════════════

@healed()
def verifier_suspensions_retard():
    """
    Suspend automatiquement les membres en retard depuis 72h.
    CORRECTION v9.2 : respecte jours_avance (paiements anticipés).
    Si un membre a payé 3 périodes d'avance, il ne sera pas suspendu
    avant d'avoir épuisé ses jours d'avance.
    """
    conn = None
    try:
        conn     = get_conn()
        tontines = fetchall(conn, "SELECT * FROM tontines WHERE statut='Active'")
        _mult_suspension = {"Journaliere": 1, "Hebdomadaire": 7, "Mensuelle": 30}
        for t in tontines:
            mult  = _mult_suspension.get(t.get("type_tontine", "Journaliere"), 1)
            seuil = datetime.now() - timedelta(hours=DELAI_SUSPENSION_HEURES * mult)
            en_retard = fetchall(conn, """
                SELECT m.id, m.nom_complet, m.whatsapp, a.jours_avance,
                       a.nb_avertissements_retard
                FROM adhesions a JOIN membres m ON m.id=a.membre_id
                WHERE a.tontine_id=%s AND a.statut='Actif'
                  AND m.suspendu_retard=0
                  AND m.id NOT IN (
                      SELECT DISTINCT t2.membre_id FROM transactions t2
                      WHERE t2.tontine_id=%s
                        AND t2.type_transaction='Cotisation'
                        AND t2.statut='Confirmee'
                        AND t2.date_heure + (
                            INTERVAL '1 day' *
                            COALESCE((
                                SELECT a2.jours_avance FROM adhesions a2
                                WHERE a2.membre_id=t2.membre_id AND a2.tontine_id=%s
                            ), 0)
                        ) > %s
                  )
            """, (t["id"], t["id"], t["id"], seuil))

            for m in en_retard:
                try:
                    if m["nb_avertissements_retard"] == 0:
                        # PREMIÈRE FOIS — sursis silencieux, aucun message
                        q(conn, "UPDATE adhesions SET nb_avertissements_retard=1 WHERE membre_id=%s AND tontine_id=%s",
                          (m["id"], t["id"]))
                        q(conn, """INSERT INTO sanctions (membre_id, tontine_id, type_sanction, notes)
                                   VALUES (%s,%s,'Avertissement_retard','Premier retard — sursis silencieux')""",
                          (m["id"], t["id"]))
                        conn.commit()
                        log_audit("AVERTISSEMENT_RETARD_SURSIS",
                                  f"{m['nom_complet']} — {t['nom']}", m["whatsapp"])
                    else:
                        # RÉCIDIVE — suspension complète + 1 000 FCFA
                        q(conn, """UPDATE membres
                                   SET suspendu_retard=1, date_suspension_retard=NOW(),
                                       statut_global='Suspendu_global'
                                   WHERE id=%s""", (m["id"],))
                        _update_score_confiance(conn, m["id"], delta=-25, raison="Suspension retard — récidive")
                        q(conn, "UPDATE adhesions SET statut='Suspendu' WHERE membre_id=%s AND tontine_id=%s",
                          (m["id"], t["id"]))
                        q(conn, """INSERT INTO sanctions (membre_id, tontine_id, type_sanction, notes)
                                   VALUES (%s,%s,'Suspension_72h','Suspension auto — récidive retard')""",
                          (m["id"], t["id"]))
                        conn.commit()
                        wa_prive(m["whatsapp"],
                            f"🔴 *COMPTE SUSPENDU — {t['nom']}*\n\n"
                            f"Vous n'avez pas cotisé depuis plus de 72h.\n\n"
                            f"Pour vous réactiver, payez *{FRAIS_REACTIV:,} FCFA*\n"
                            f"Code : *REACTIV*\n\n"
                            f"⚠️ Sans régularisation sous 48h, votre caution sera saisie."
                        )
                        if t.get("whatsapp_groupe"):
                            wa_groupe(t["whatsapp_groupe"],
                                f"🔴 *{t['nom']} — SUSPENSION*\n\n"
                                f"Le compte de *{m['nom_complet']}* a été suspendu "
                                f"pour non-paiement (récidive).\n\n"
                                f"Son accès est bloqué jusqu'à régularisation.\n"
                                f"Le groupe ne sera pas pénalisé.\n\n"
                                f"_TontineBot Pro — BADF Ltd_"
                            )
                        log_audit("SUSPENSION_AUTO_72H",
                                  f"{m['nom_complet']} — {t['nom']}", m["whatsapp"])
                except Exception as _me:
                    log.error(f"❌ verifier_suspensions_retard membre#{m['id']} tontine#{t['id']} : {_me}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass
    except Exception as e:
        log.error(f"❌ verifier_suspensions_retard : {e}")
    finally:
        if conn:
            release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# ANTI-FUGUE POST-BOUFFAGE
# ══════════════════════════════════════════════════════════════════════════

@healed()
def detecter_fugitifs():
    """
    Détection quotidienne des membres qui ont bouffé et n'ont plus cotisé.
    Progression : Avertissement_1 → Avertissement_2 → Blocage + saisie caution.
    """
    conn = None
    try:
        conn     = get_conn()
        tontines = fetchall(conn, "SELECT * FROM tontines WHERE statut='Active'")
        _periode_jours = {"Journaliere": 1, "Hebdomadaire": 7, "Mensuelle": 30}
        for t in tontines:
            periode = _periode_jours.get(t.get("type_tontine", "Journaliere"), 1)
            delai_alerte  = DELAI_ALERTE_FUGUE  * periode
            delai_blocage = DELAI_BLOCAGE_FUGUE * periode
            membres = fetchall(conn, """
                SELECT m.id, m.nom_complet, m.whatsapp, m.score_confiance
                FROM adhesions a JOIN membres m ON m.id=a.membre_id
                WHERE a.tontine_id=%s AND a.statut IN ('Actif','Suspendu')
                  AND m.statut_global IN ('Actif','Suspendu_global')
            """, (t["id"],))
            for m in membres:
                # `membres.dernier_bouffage` est global au membre (dernier bouffage
                # TOUTES tontines confondues) — un membre actif dans 2+ tontines
                # verrait la date d'une AUTRE tontine utilisée ici, faussant les
                # deux détections (faux négatif dans l'une, faux positif dans
                # l'autre). Seul liste_passage.date_paiement de CETTE tontine fait foi.
                dernier_bouffage_tontine = fetchone(conn, """
                    SELECT date_paiement FROM liste_passage
                    WHERE membre_id=%s AND tontine_id=%s AND statut='Paye'
                    ORDER BY date_paiement DESC LIMIT 1
                """, (m["id"], t["id"]))
                if not dernier_bouffage_tontine or not dernier_bouffage_tontine["date_paiement"]:
                    continue  # jamais bouffé dans CETTE tontine → pas de fugue post-bouffage possible ici
                dernier_bouffage = dernier_bouffage_tontine["date_paiement"]

                derniere = fetchone(conn, """
                    SELECT date_heure FROM transactions
                    WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
                      AND statut='Confirmee' AND date_heure > %s
                    ORDER BY date_heure DESC LIMIT 1
                """, (m["id"], t["id"], dernier_bouffage))
                if derniere:
                    jours = (datetime.now() - derniere["date_heure"].replace(tzinfo=None)).days
                else:
                    jours = (datetime.now() - dernier_bouffage.replace(tzinfo=None)).days
                if jours < delai_alerte:
                    continue
                # Nombre de périodes manquées (pas de jours bruts) × montant par période
                montant_du = (jours // periode) * t["montant_place"]
                alerte     = fetchone(conn, """
                    SELECT type_alerte FROM alertes_fugue
                    WHERE membre_id=%s AND tontine_id=%s AND traite=0
                    ORDER BY created_at DESC LIMIT 1
                """, (m["id"], t["id"]))

                if jours >= delai_blocage and (not alerte or alerte["type_alerte"] != "Blocage"):
                    q(conn, "UPDATE membres SET statut_global='Suspendu_global' WHERE id=%s", (m["id"],))
                    _update_score_confiance(conn, m["id"], delta=-30, raison="Blocage fugue post-bouffage")
                    q(conn, "INSERT INTO alertes_fugue (membre_id, tontine_id, type_alerte, jours_retard, montant_du) VALUES (%s,%s,'Blocage',%s,%s)",
                      (m["id"], t["id"], jours, montant_du))
                    q(conn, "UPDATE alertes_fugue SET traite=1 WHERE membre_id=%s AND tontine_id=%s AND type_alerte IN ('Avertissement_1','Avertissement_2')",
                      (m["id"], t["id"]))
                    conn.commit()
                    wa_prive(m["whatsapp"],
                        f"🔴 *COMPTE BLOQUÉ — BADF Ltd*\n"
                        f"Tontine : {t['nom']}\n"
                        f"Non-paiement depuis *{jours} jours* après bouffage.\n"
                        f"Dû : *{montant_du:,} FCFA*\n\n"
                        f"Votre caution sera saisie sous 24h.\n\n"
                        + MSG_DISSUASION)
                    wa_admins_tontine(t["id"],
                        f"🚨 *FUGITIF BLOQUÉ — {t['nom']}*\n"
                        f"{m['nom_complet']} | {m['whatsapp']}\n"
                        f"Retard: {jours}j | Doit: {montant_du:,} FCFA\n"
                        f"→ Tapez *admin {t['nom']}* puis *10* pour saisir la caution.")

                elif jours >= delai_alerte + periode and (not alerte or alerte["type_alerte"] == "Avertissement_1"):
                    q(conn, "INSERT INTO alertes_fugue (membre_id, tontine_id, type_alerte, jours_retard, montant_du) VALUES (%s,%s,'Avertissement_2',%s,%s) ON CONFLICT DO NOTHING",
                      (m["id"], t["id"], jours, montant_du))
                    _update_score_confiance(conn, m["id"], delta=-15, raison="Avertissement 2 — retard cotisation post-bouffage")
                    conn.commit()
                    wa_prive(m["whatsapp"],
                        f"⚠️ *DERNIER AVERTISSEMENT — BARACK CORP*\n"
                        f"Tontine : {t['nom']}\n"
                        f"Retard : {jours}j | Dû : *{montant_du:,} FCFA*\n\n"
                        f"24h pour régulariser, sinon :\n"
                        f"• Compte bloqué\n• Caution saisie\n• Dossier ANIF\n\n"
                        f"Contactez votre admin pour régulariser votre situation.")

                elif not alerte:
                    q(conn, "INSERT INTO alertes_fugue (membre_id, tontine_id, type_alerte, jours_retard, montant_du) VALUES (%s,%s,'Avertissement_1',%s,%s)",
                      (m["id"], t["id"], jours, montant_du))
                    _update_score_confiance(conn, m["id"], delta=-10, raison="Avertissement 1 — retard cotisation post-bouffage")
                    conn.commit()
                    wa_prive(m["whatsapp"],
                        f"⚠️ *AVERTISSEMENT — BARACK CORP*\n"
                        f"Tontine : {t['nom']}\n"
                        f"Vous n'avez pas cotisé depuis {jours}j après votre bouffage.\n"
                        f"Dû : *{montant_du:,} FCFA*\n\n"
                        f"Contactez votre admin pour régulariser votre situation.")
    except Exception as e:
        log.error(f"❌ detecter_fugitifs : {e}")
    finally:
        if conn:
            release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# RAPPORT OWNER — 21H
# ══════════════════════════════════════════════════════════════════════════


def envoyer_releve_fmp_post_bouffage():
    """
    Tourne chaque minute. Pour chaque tontine dont heure_bouffage + 10 min
    correspond à l'heure actuelle (WAT), envoie le relevé FMP à chaque admin.
    """
    from datetime import timezone, timedelta as _td, time as _time
    WAT = timezone(_td(hours=1))
    now_wat    = datetime.now(WAT)
    hhmm_now   = now_wat.strftime('%H:%M')

    conn     = get_conn()
    tontines = fetchall(conn,
        "SELECT id, nom, heure_bouffage FROM tontines WHERE statut='Active'")
    for t in tontines:
        hb = t.get("heure_bouffage") or "17:00"
        try:
            hh, mm = map(int, hb.split(":"))
            bouffage_plus_10 = (
                datetime.combine(now_wat.date(), _time(hh, mm)) + _td(minutes=10)
            ).time().strftime('%H:%M')
        except Exception:
            continue
        if hhmm_now != bouffage_plus_10:
            continue
        admins_t = fetchall(conn,
            "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s", (t["id"],))
        for adm in admins_t:
            msg = rapport_dettes_badf_admin(adm["whatsapp"])
            if msg:
                wa_prive(adm["whatsapp"], msg)
    release_conn(conn)


def rapport_groupes_fin_journee():
    """Rapport de fin de journée dans chaque groupe, à heure_limite+1h de chaque tontine."""
    conn     = get_conn()
    tontines = fetchall(conn, "SELECT * FROM tontines WHERE statut='Active'")
    heure_now = datetime.now().hour
    for t in tontines:
        if not _est_jour_cotisation(t):
            continue
        h_limite = int((t["heure_limite"] or "18:00").split(":")[0])
        if (h_limite + 1) % 24 != heure_now:
            continue
        nb_a    = fetchone(conn,
            "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
            (t["id"],))["n"]
        nb_p    = fetchone(conn, """
            SELECT COUNT(DISTINCT membre_id) n FROM transactions
            WHERE tontine_id=%s AND type_transaction='Cotisation'
              AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
        """, (t["id"],))["n"]
        nb_att  = fetchone(conn, """
            SELECT COUNT(*) n FROM cotisations_manuelles
            WHERE tontine_id=%s AND statut='En_attente'
              AND date_soumission::date=CURRENT_DATE
        """, (t["id"],))["n"]
        tot     = fetchone(conn, """
            SELECT COALESCE(SUM(montant_net),0) v FROM transactions
            WHERE tontine_id=%s AND type_transaction='Cotisation'
              AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
        """, (t["id"],))["v"]
        taux    = int(nb_p / nb_a * 100) if nb_a else 0
        retards = nb_a - nb_p

        wa_groupe(t["whatsapp_groupe"] or t["nom"],
            f"📊 *BILAN DU JOUR — {t['nom']}*\n"
            f"🗓️ {datetime.now().strftime('%d/%m/%Y')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Membres  : {nb_a}\n"
            f"✅ Confirmés : {nb_p} ({taux}%)\n"
            f"⏳ En attente: {nb_att}\n"
            f"⏰ Retards   : {retards}\n"
            f"💰 Collecté  : *{tot:,} FCFA*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + ("✅ *PARFAIT — 100% de participation !*" if retards == 0
               else f"⚠️ *{retards} membre(s) en retard.*")
            + f"\n\n_TontineBot Pro — BADF Ltd_"
        )

    release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# BACKUP AUTOMATIQUE — PG_DUMP, ROTATION 7 FICHIERS
# ══════════════════════════════════════════════════════════════════════════

@healed()
def backup_postgresql():
    """
    Backup pg_dump automatique à 2h00.
    Niveau 1 : disque local avec rotation des 7 derniers fichiers.
    Niveau 2 (si disque externe configuré) : copie automatique.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fichier   = os.path.join(BACKUP_DIR, f"barack_corp_{timestamp}.sql")
    pg_dump   = os.path.join(PG_BIN, "pg_dump.exe") if os.name == "nt" else "pg_dump"

    env = os.environ.copy()
    env["PGPASSWORD"] = PG_PASS

    try:
        resultat = subprocess.run(
            [pg_dump, "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER,
             "-d", PG_DB, "-f", fichier, "--format=plain"],
            env=env, capture_output=True, text=True, timeout=300
        )
        if resultat.returncode == 0:
            taille = os.path.getsize(fichier) // 1024
            log.info(f"✅ Backup OK : {fichier} ({taille} Ko)")
            log_audit("BACKUP_OK", f"{fichier} {taille}Ko")

            # RC — Vérification intégrité : le dump plain SQL se termine par ce marqueur
            try:
                with open(fichier, "rb") as _bf:
                    _bf.seek(-300, 2)
                    _tail = _bf.read().decode("utf-8", errors="ignore")
                if "PostgreSQL database dump complete" in _tail:
                    log_audit("BACKUP_VERIFIE", f"Backup validé : {os.path.basename(fichier)}")
                else:
                    log_audit("BACKUP_CORROMPU", f"Marqueur fin absent : {os.path.basename(fichier)}")
                    wa_owner(f"🔴 *BACKUP POTENTIELLEMENT CORROMPU*\n{os.path.basename(fichier)}\nManque le marqueur de fin — vérifier manuellement.")
            except Exception as _ve:
                log.warning(f"⚠️ Vérification backup échouée : {_ve}")

            # Rotation : garder seulement les 7 derniers
            backups = sorted([
                os.path.join(BACKUP_DIR, f)
                for f in os.listdir(BACKUP_DIR)
                if f.startswith("barack_corp_") and f.endswith(".sql")
            ])
            while len(backups) > BACKUP_ROTATION:
                ancien = backups.pop(0)
                os.remove(ancien)
                log.info(f"Backup supprimé (rotation): {ancien}")

            # Copie sur disque externe si configuré
            disque_ext = os.getenv("BACKUP_DISQUE_EXT", "")
            if disque_ext and os.path.exists(disque_ext):
                dest = os.path.join(disque_ext, f"barack_corp_{timestamp}.sql")
                shutil.copy2(fichier, dest)
                log.info(f"✅ Backup disque externe : {dest}")

        else:
            log.error(f"❌ Backup ÉCHEC : {resultat.stderr}")
            log_audit("BACKUP_ECHEC", resultat.stderr[:200])
            wa_owner(f"🚨 *BACKUP ÉCHOUÉ*\n{resultat.stderr[:100]}")
    except Exception as e:
        log.error(f"❌ Backup exception : {e}")
        wa_owner(f"🚨 *BACKUP EXCEPTION*\n{str(e)[:100]}")


# ══════════════════════════════════════════════════════════════════════════
# VÉRIFICATION DETTES BADF — IMPAYÉES DEPUIS +24H
# ══════════════════════════════════════════════════════════════════════════

def generer_codes_ussd(montant: int, numero_destinataire: str) -> dict:
    """
    Génère les codes USSD pré-remplis pour MTN et Orange Money Cameroun.
    L'admin tape le code, confirme avec son PIN, et la transaction est faite
    en 8 secondes au lieu de 90 secondes.

    Codes officiels :
      MTN MoMo Cameroun    : *126*1*<numero>*<montant>#  → envoi direct
      Orange Money Cameroun: #150*1*<numero>*<montant>#  → envoi direct
    """
    # Numéro sans préfixe international ni espaces (format local 9 chiffres)
    num = numero_destinataire.lstrip("+").replace(" ", "")
    if num.startswith("237"):
        num = num[3:]
    return {
        "mtn":     f"*126*1*{num}*{montant}#",
        "orange":  f"#150*1*{num}*{montant}#",
        "numero":  numero_destinataire,
        "montant": montant,
    }


def texte_codes_ussd_pour_admin(montant: int) -> str:
    """
    Construit un bloc texte avec les codes USSD pré-remplis vers BADF.
    À insérer dans tous les rappels de dette FMP.
    """
    codes_mtn    = generer_codes_ussd(montant, NUMERO_BADF_MTN)
    codes_orange = generer_codes_ussd(montant, NUMERO_BADF_ORANGE)
    return (
        f"💳 *Payez en 1 clic — codes pré-remplis :*\n"
        f"   MTN MoMo : `{codes_mtn['mtn']}`\n"
        f"   Orange   : `{codes_orange['orange']}`\n"
        f"   _Tapez le code → confirmez avec votre PIN → fini._"
    )


@healed()
def verifier_dettes_badf_impayees():
    """
    Lancé tous les jours à 10h00.
    Détecte les admins qui n'ont pas reversé leurs FMP depuis plus de 24h.

    Escalade en 3 niveaux :
      - Niveau 1 (24h–48h) : rappel DM à l'admin + alerte owner
      - Niveau 2 (48h–72h) : avertissement sévère admin + alerte owner + suspension admin
      - Niveau 3 (>72h)    : suspension admin + alerte owner + signalement audit
    """
    conn = get_conn()
    try:
        maintenant = datetime.now()

        # Récupérer toutes les dettes impayées groupées par admin
        dettes = fetchall(conn, """
            SELECT
                admin_wa,
                SUM(montant)                        AS total_du,
                COUNT(*)                            AS nb_dettes,
                MIN(date_creation)                  AS plus_ancienne,
                EXTRACT(EPOCH FROM (NOW() - MIN(date_creation)))/3600 AS heures_retard
            FROM dettes_badf
            WHERE statut = 'Due'
            GROUP BY admin_wa
            HAVING MIN(date_creation) < NOW() - INTERVAL '24 hours'
               AND SUM(montant) > 0
            ORDER BY heures_retard DESC
        """)

        if not dettes:
            release_conn(conn)
            return

        admins_niveau2 = []
        admins_niveau3 = []

        for d in dettes:
            admin_wa     = d["admin_wa"]
            total_du     = int(d["total_du"])
            nb_dettes    = d["nb_dettes"]
            heures       = float(d["heures_retard"])
            jours        = int(heures // 24)
            heures_reste = int(heures % 24)

            # ── Owner — jamais de rappel agressif, jamais de suspension ───
            # Le owner voit ses dettes dans son rapport 21h, c'est suffisant
            if est_owner(admin_wa):
                continue

            # ── Niveau 3 — plus de 72h ────────────────────────────────────
            if heures >= 72:
                admins_niveau3.append(d)
                log_audit("DETTE_BADF_CRITIQUE",
                          f"Admin:{admin_wa} | {total_du:,} FCFA | {jours}j de retard")

                # Récupérer les tontines de cet admin
                tontines_admin_ids = get_tontines_admin(admin_wa)
                conn2 = get_conn()
                groupes_quittes = []
                for tid in tontines_admin_ids:
                    tontine = fetchone(conn2,
                        "SELECT nom, whatsapp_groupe FROM tontines WHERE id=%s", (tid,))
                    if tontine and tontine.get("whatsapp_groupe"):
                        # Message d'au revoir dans le groupe avant de partir
                        _wa_send_groupe(tontine["whatsapp_groupe"],
                            f"🔴 *BADF Ltd — SERVICE SUSPENDU*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"TontineBot Pro se retire de ce groupe.\n\n"
                            f"*Motif :* Les frais de service BADF Ltd dus par "
                            f"l\'administrateur de *{tontine['nom']}* n\'ont pas été "
                            f"réglés dans le délai imparti.\n\n"
                            f"Le service sera rétabli dès régularisation de la dette.\n\n"
                            f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
                        )
                        time_module.sleep(3)
                        ok = wa_quitter_groupe(tontine["whatsapp_groupe"])
                        if ok:
                            groupes_quittes.append(tontine["nom"])
                            # Marquer la tontine comme suspendue
                            q(conn2,
                              "UPDATE tontines SET statut='Suspendue' WHERE id=%s",
                              (tid,))
                            conn2.commit()
                            log_audit("BOT_QUITTE_GROUPE",
                                      f"Tontine:{tontine['nom']} | Admin:{admin_wa} | Dette:{total_du:,} FCFA")
                release_conn(conn2)

                wa_prive(admin_wa,
                    f"🔴 *ALERTE CRITIQUE — BADF Ltd*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"*{jours} jours* de retard sur votre reversement BADF.\n\n"
                    f"Montant dû : *{total_du:,} FCFA*\n"
                    f"Dossiers en retard : *{nb_dettes}*\n\n"
                    + (f"🚪 *TontineBot Pro a quitté vos groupes :*\n"
                       + "\n".join(f"  • {g}" for g in groupes_quittes) + "\n\n"
                       if groupes_quittes else "") +
                    f"Le service sera rétabli dès règlement complet.\n\n"
                    f"Régularisez *immédiatement* :\n"
                    f"  MTN    : *{NUMERO_BADF_MTN}*\n"
                    f"  Orange : *{NUMERO_BADF_ORANGE}*\n\n"
                    f"Envoyez *PAIEMENT_BADF <code>* au bot pour rétablir le service.\n\n"
                    f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
                )

                # Suspendre l'accès admin en base
                tontines_admin_ids2 = get_tontines_admin(admin_wa)
                for tid in tontines_admin_ids2:
                    q(conn, """
                        INSERT INTO sanctions
                            (membre_id, tontine_id, type_sanction, notes)
                        SELECT m.id, %s, 'Blocage_permanent',
                               'Dette BADF impayée >72h — accès admin suspendu'
                        FROM membres m WHERE m.whatsapp=%s
                        ON CONFLICT DO NOTHING
                    """, (tid, admin_wa))
                conn.commit()

            # ── Niveau 2 — 48h à 72h ─────────────────────────────────────
            elif heures >= 48:
                admins_niveau2.append(d)
                log_audit("DETTE_BADF_ALERTE",
                          f"Admin:{admin_wa} | {total_du:,} FCFA | {jours}j{heures_reste}h")

                wa_prive(admin_wa,
                    f"🟠 *DEUXIÈME RAPPEL — REVERSEMENT BADF*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Votre dette BADF est impayée depuis *{jours}j {heures_reste}h*.\n\n"
                    f"Montant dû : *{total_du:,} FCFA*\n\n"
                    f"Sans règlement dans les *24 heures*, votre accès au menu admin "
                    f"sera *automatiquement suspendu*.\n\n"
                    + texte_codes_ussd_pour_admin(total_du) + "\n\n"
                    f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
                )

            # ── Niveau 1 — 24h à 48h ─────────────────────────────────────
            else:
                log_audit("DETTE_BADF_RAPPEL",
                          f"Admin:{admin_wa} | {total_du:,} FCFA | {int(heures)}h")

                wa_prive(admin_wa,
                    f"🟡 *RAPPEL — REVERSEMENT BADF DÛ*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Votre reversement BADF d'hier n'a pas été reçu.\n\n"
                    f"Montant dû : *{total_du:,} FCFA*\n"
                    f"Dossiers   : *{nb_dettes}*\n\n"
                    + texte_codes_ussd_pour_admin(total_du) + "\n\n"
                    f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
                )

        # ── Rapport owner ─────────────────────────────────────────────────
        if dettes:
            lignes = [
                f"⚠️ *DETTES BADF IMPAYÉES — {maintenant.strftime('%d/%m/%Y %H:%M')}*\n"
                f"{'━'*35}"
            ]
            for d in dettes:
                heures   = float(d["heures_retard"])
                jours    = int(heures // 24)
                niveau   = "🔴" if heures >= 72 else ("🟠" if heures >= 48 else "🟡")
                lignes.append(
                    f"{niveau} *{d['admin_wa']}*\n"
                    f"   Dû : {int(d['total_du']):,} FCFA | "
                    f"Retard : {jours}j {int(heures % 24)}h | "
                    f"Dossiers : {d['nb_dettes']}"
                )

            if admins_niveau3:
                lignes.append(
                    f"\n🔴 *{len(admins_niveau3)} admin(s) suspendus* — retard >72h"
                )
            if admins_niveau2:
                lignes.append(
                    f"🟠 *{len(admins_niveau2)} admin(s) avertis* — retard 48-72h"
                )

            lignes.append(f"\n_TontineBot Pro — BADF Ltd_")
            wa_owner("\n".join(lignes))

    except Exception as e:
        log.error(f"❌ verifier_dettes_badf_impayees : {e}")
        wa_owner(f"🚨 Erreur vérification dettes BADF : {str(e)[:100]}")
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# DASHBOARD LOCAL — http://localhost:5000/dashboard
# ══════════════════════════════════════════════════════════════════════════
# Sous-système périphérique extrait dans dashboard.py (routes /dashboard,
# /dashboard/data). Câblage par injection — aucune logique dashboard inline ici.
import dashboard
dashboard.register_dashboard(
    app,
    get_conn=get_conn, release_conn=release_conn,
    bot_start=_BOT_START, dash_token=_DASH_TOKEN, logger=log,
)


# ══════════════════════════════════════════════════════════════════════════
# ROUTE DE SANTÉ
# ══════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    if not _HEALTH_TOKEN or not hmac.compare_digest(request.args.get("token", ""), _HEALTH_TOKEN):
        return jsonify({"status": "unauthorized"}), 401
    try:
        conn = get_conn()
        fetchone(conn, "SELECT 1 AS ok")
        release_conn(conn)
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify({
        "status":    "ok" if db_ok else "db_error",
        "version":   "9.18",
        "timestamp": datetime.now().isoformat(),
        "db":        "ok" if db_ok else "error",
    }), 200 if db_ok else 503


# ══════════════════════════════════════════════════════════════════════════
# AUTO-HEALING — logique dans health.py ; ici la route de détail (app Flask)
# ══════════════════════════════════════════════════════════════════════════


@app.route("/health/detail", methods=["GET"])
def health_detail():
    """Endpoint santé détaillé pour monitoring externe."""
    if not _HEALTH_TOKEN or not hmac.compare_digest(request.args.get("token", ""), _HEALTH_TOKEN):
        return jsonify({"status": "unauthorized"}), 401
    with _health_lock:
        state = dict(_health_state)
    state["uptime_sec"] = int(time_module.time() - state["started_at"])
    state["timestamp"]  = datetime.now().isoformat()
    overall = state["db_ok"] and state["wa_ok"]
    return jsonify(state), 200 if overall else 503


# ══════════════════════════════════════════════════════════════════════════
# SCHEDULER — TOUTES LES TÂCHES AUTOMATIQUES
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# JOBS QUOTIDIENS DU PROPRIÉTAIRE — comptabilité, surveillance, supervision
# zero touch : dans supervision.py
# ══════════════════════════════════════════════════════════════════════════

from supervision import (
    comptabilite_badf_quotidienne, surveillance_anomalies_quotidienne,
    _zto_state, zto_supervision_quotidienne,
)


# ══════════════════════════════════════════════════════════════════════════


def demarrer_scheduler():
    scheduler = BackgroundScheduler(timezone="Africa/Douala")

    # ── Rappels groupe (minute=3 — fréquence naturelle) ───────────────────
    scheduler.add_job(rappel_ouverture,      "cron", minute=3,  id="rappel_ouverture")
    scheduler.add_job(rappel_matin,          "cron", minute=3,  id="rappel_matin")
    scheduler.add_job(rappel_non_cotisants,  "cron", minute=3,  id="rappel_non_cotisants")
    scheduler.add_job(notifier_prochain_bouffage, "cron", minute=9, id="bouffage")

    # ── Jobs fixes ────────────────────────────────────────────────────────
    scheduler.add_job(rapport_groupes_fin_journee,      "cron", minute=0,           id="rapport_groupes")
    scheduler.add_job(envoyer_releve_fmp_post_bouffage,"cron", minute="*",         id="fmp_post_bouffage")
    scheduler.add_job(verifier_suspensions_retard,  "cron", minute=30, id="suspensions")
    scheduler.add_job(detecter_fugitifs,            "cron", hour=8,  minute=33, id="anti_fugue")
    scheduler.add_job(backup_postgresql,            "cron", hour=HEURE_BACKUP, minute=0, id="backup")
    scheduler.add_job(traiter_bouffages_suspects_expires, "cron", hour=11, minute=6,  id="suspects_expires")
    scheduler.add_job(verifier_et_liberer_cautions, "cron", hour=9,  minute=9,  id="cautions_liberation")
    scheduler.add_job(verifier_dettes_badf_impayees,"cron", hour=10, minute=0,  id="dettes_badf")

    # ── Outbox drain — réessaie les messages WhatsApp non envoyés ────────
    scheduler.add_job(_outbox_drain,             "interval", seconds=30,  id="outbox_drain")
    scheduler.add_job(_purger_sessions_expirees, "interval", minutes=5,   id="purge_sessions")
    scheduler.add_job(_sauvegarder_sessions,     "interval", seconds=60,  id="sessions_backup")

    # ── Auto-healing — supervision silencieuse ────────────────────────────
    scheduler.add_job(_self_heal_check,  "interval", seconds=60,  id="self_heal_check")
    scheduler.add_job(_self_heal_outbox, "interval", seconds=120, id="self_heal_outbox")
    scheduler.add_job(_check_greenapi,   "interval", minutes=10,  id="check_greenapi",
                      next_run_time=datetime.now())

    # ── Zero Touch Ops — supervision quotidienne du owner ────────────────
    scheduler.add_job(zto_supervision_quotidienne,    "cron", hour=6, minute=33, id="zto_supervision")

    # ── Surveillance proactive anti-fraude (avant la compta) ─────────────
    scheduler.add_job(surveillance_anomalies_quotidienne, "cron", hour=6, minute=36, id="surveillance_anomalies")

    # ── Comptabilité BADF auto (envoyée juste après ouverture) ───────────
    scheduler.add_job(comptabilite_badf_quotidienne,  "cron", hour=7, minute=33, id="compta_badf")

    # ── Finance comportementale prédictive — risque fugue J-7 ────────────
    scheduler.add_job(alerter_risques_bouffage_imminent, "cron", hour=5, minute=33, id="risque_fugue_predictif")
    # Reprise automatique des pauses (commande 'cas' et menu admin 9 A) à date_reprise.
    scheduler.add_job(lever_pauses_echues, "cron", hour=6, minute=5, id="pauses_echues")

    scheduler.start()
    log.info("✅ Scheduler TontineBot Pro v9.18 démarré.")
    return scheduler


# ══════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════

# Un seul démarrage par processus : l'initialisation (pool, tables, sessions,
# scheduler) ne s'exécute qu'une fois, sous verrou ; un appel ultérieur peut
# encore servir HTTP. L'arrêt propre est enregistré une seule fois.
_demarrage = {"scheduler": None, "arret_enregistre": False, "initialise": False,
              "sessions_restaurees": False, "adresses_servies": set()}
_demarrage_lock = threading.Lock()
# Attente maximale des jobs en cours quand _shutdown_bot est appelé directement.
# À la sortie normale de l'interpréteur, concurrent.futures a déjà attendu les
# workers (sans limite) avant les handlers atexit : la limite ne joue pas là.
_ARRET_ATTENTE_JOBS = 10


def _enregistrer_arret():
    if not _demarrage["arret_enregistre"]:
        atexit.register(_shutdown_bot)
        _demarrage["arret_enregistre"] = True


def _arreter_scheduler(scheduler):
    """Arrête le scheduler en attendant ses jobs en cours, au plus
    _ARRET_ATTENTE_JOBS secondes ; tout échec est journalisé."""
    erreurs = []

    def _arret():
        try:
            scheduler.shutdown(wait=True)
        except SchedulerNotRunningError:
            pass                      # jamais démarré ou déjà arrêté : rien à signaler
        except Exception as e:
            erreurs.append(e)

    fil = threading.Thread(target=_arret, name="ArretScheduler", daemon=True)
    try:
        fil.start()
    except RuntimeError:
        # Python 3.12+ : aucun thread ne peut démarrer pendant atexit. Les jobs
        # sont déjà terminés à ce stade ; on arrête directement.
        _arret()
        if erreurs:
            log.warning(f"⚠️ Arrêt du scheduler en échec : {erreurs[0]}")
        return
    fil.join(_ARRET_ATTENTE_JOBS)
    if fil.is_alive():
        log.warning(f"⚠️ Jobs encore en cours après {_ARRET_ATTENTE_JOBS}s — fermeture du pool quand même")
    elif erreurs:
        log.warning(f"⚠️ Arrêt du scheduler en échec : {erreurs[0]}")


def _shutdown_bot():
    """Appelé par atexit à chaque arrêt — stoppe le scheduler (jobs en cours
    attendus, délai borné), puis ferme le pool DB."""
    scheduler = _demarrage["scheduler"]
    if scheduler is not None:
        _arreter_scheduler(scheduler)
    _expediteur.vider(timeout=10)   # laisse partir les messages déjà soumis
    if _demarrage.get("sessions_restaurees"):
        _sauvegarder_sessions()     # jamais avant la restauration : la sauvegarde serait écrasée à vide
    fermer_pool()
    _msg_executor.shutdown(wait=False)
    _download_executor.shutdown(wait=False)
    log.info("Bot arrêté proprement — pool DB fermé.")


def demarrer(servir: bool = True, hote: str = "0.0.0.0", port: Optional[int] = None):
    """Démarre le bot : secrets, PostgreSQL, pool, tables, sessions, URL
    publique, scheduler, arrêt propre, puis Waitress sur hote:port (PORT par
    défaut). Retourne le scheduler ; servir=False s'arrête avant Waitress.
    L'initialisation ne s'exécute qu'une fois par processus, même sous appels
    simultanés ; un appel ultérieur avec servir=True sert quand même HTTP."""
    port = PORT if port is None else port
    with _demarrage_lock:
        if not _demarrage["initialise"]:
            _initialiser(hote, port)
        scheduler = _demarrage["scheduler"]

    if not servir:
        return scheduler
    adresses = _demarrage.setdefault("adresses_servies", set())
    with _demarrage_lock:
        if (hote, port) in adresses:
            log.warning(f"⚠️ {hote}:{port} est déjà servi par ce processus — pas de second serveur")
            return scheduler
        adresses.add((hote, port))
    from waitress import serve
    log.info(f"🚀 Waitress WSGI — {WAITRESS_THREADS} threads — port {port}")
    serve(app, host=hote, port=port, threads=WAITRESS_THREADS,
          channel_timeout=120, cleanup_interval=30)
    return scheduler


def _initialiser(hote: str, port: int):
    """Étapes de démarrage, dans l'ordre : secrets, PostgreSQL, pool (arrêt
    propre enregistré aussitôt), tables, sessions, URL publique, scheduler."""
    log.info("━" * 60)
    log.info("🚀 TontineBot Pro v9.18 — Barack & AI Development Facilities Ltd")
    log.info("   BADF Ltd — Cameroun 🇨🇲")
    log.info("━" * 60)
    log.info(f"   Owner           : {OWNER_WA}")
    log.info(f"   WhatsApp       : Green API — Instance {GREENAPI_INSTANCE_ID or '(non configurée)'}")
    log.info(f"   Base de données : {PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}")
    log.info(f"   Écoute HTTP     : {hote}:{port}")
    log.info(f"   Numéro BADF MTN : {NUMERO_BADF_MTN}")
    log.info("━" * 60)
    log.info("💡 Mode paiement : MANUEL (screenshot → admin confirme)")
    log.info("   • Membres envoient screenshot dans le groupe")
    log.info("   • Bot détecte et enregistre automatiquement")
    log.info("   • Admin confirme en 1 tap")
    log.info("   • Bot notifie le membre")
    log.info("━" * 60)

    # ── 0. Validation fail-closed des secrets critiques ─────────────────────
    # Ce fichier est sur un repo GitHub public : aucune valeur par défaut
    # hardcodée n'est acceptable pour ces secrets. Si l'un d'eux est absent
    # ou trop faible, on refuse de démarrer plutôt que de tourner avec un
    # secret devinable par quiconque lit le code source.
    _SECRETS_CRITIQUES = {
        "FLASK_SECRET_KEY": app.secret_key,
        "DASHBOARD_TOKEN":  _DASH_TOKEN,
        "HEALTH_TOKEN":     _HEALTH_TOKEN,
    }
    _secrets_manquants = [nom for nom, val in _SECRETS_CRITIQUES.items() if not val]
    _secrets_faibles   = [nom for nom, val in _SECRETS_CRITIQUES.items() if val and len(val) < 16]
    if _secrets_manquants or _secrets_faibles:
        if _secrets_manquants:
            log.error(f"❌ ARRÊT — variable(s) d'environnement manquante(s) : {', '.join(_secrets_manquants)}")
        if _secrets_faibles:
            log.error(f"❌ ARRÊT — secret(s) trop court(s) (min 16 caractères) : {', '.join(_secrets_faibles)}")
        log.error("   Configurez-les dans le fichier ENV avant de redémarrer. Aucun fallback par défaut n'existe (repo public).")
        sys.exit(1)
    log.info("✅ Secrets critiques validés (présents, ≥16 caractères).")

    # ── 1. Vérification PostgreSQL ─────────────────────────────────────────
    log.info("🔍 Vérification PostgreSQL...")
    if not tester_connexion_postgresql():
        log.error("❌ ARRÊT — PostgreSQL inaccessible.")
        sys.exit(1)

    # ── 2. Init pool + tables ──────────────────────────────────────────────
    reinitialiser_pool()              # ouvre le pool sous verrou (ferme un éventuel ancien pool)
    _enregistrer_arret()              # le pool ouvert sera fermé même si la suite échoue
    init_db()
    _restaurer_sessions()
    _demarrage["sessions_restaurees"] = True
    log.info("✅ Base de données v9.18 initialisée.")

    # ── 3. Détecter l'URL publique ngrok ──────────────────────────────────
    log.info("🌐 Détection URL publique ngrok...")
    url_pub = detecter_url_publique()
    if url_pub:
        log.info(f"   ✅ URL publique : {url_pub}")
        log.info(f"   ✅ Webhook WhatsApp : {url_pub}/webhook/whatsapp")
        log.info(f"   ℹ️ Configurez ce webhook dans Meta developers.")
    else:
        log.warning("   ⚠️ URL ngrok non détectée — configurez NGROK_DOMAIN dans .env")

    # ── 4. Scheduler ───────────────────────────────────────────────────────
    _demarrage["scheduler"] = demarrer_scheduler()
    _demarrage["initialise"] = True
    log.info("✅ Scheduler démarré.")
    log.info("━" * 60)
    log.info("🟢 TontineBot Pro v9.18 — Prêt à recevoir des messages.")
    log.info("━" * 60)


if __name__ == "__main__":
    demarrer()
