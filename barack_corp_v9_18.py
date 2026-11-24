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
# Frais, barème IRA, numéros collecteurs, seuil de fraude et réglages webhook : socle.py.
from socle import (   # extrait dans socle.py
    FRAIS_FMP, HEURE_LIMITE_DEF, IRA_CLIFF_PCT, IRA_DAILY_PCT, IRA_HOURLY_PCT, IRA_CAP_PCT,
    IRA_FLOOR, IRA_GRACE_MIN, FRAIS_REACTIV, FRAIS_CHGNUM, NUMERO_BADF_MTN, NUMERO_BADF_ORANGE,
    MAX_TENTATIVES_FRAUDE, GREENAPI_WEBHOOK_SECRET, WEBHOOK_REPLAY_TTL, _replay_guard,
    MEDIA_MAX_BYTES, SESSION_TIMEOUT, enregistrer_liste_passage, inscrire_dans_tontine,
    normaliser_numero, valider_numero_cameroun, calculer_ira_mise, get_membre_by_wa,
    get_tontines_admin, est_owner, est_admin, incrementer_tentatives_fraude, _alerter_burst_fraude,
    _JOURS_SEMAINE, _est_jour_collecte, _est_jour_cotisation, _verifier_credit_comm,
)
from etat import (   # sessions, limiteur, exécuteurs : etat.py
    WAITRESS_THREADS, RATE_LIMIT_MAX, RATE_LIMIT_FENETRE, RATE_LIMIT_GLOBAL_MAX, _sessions_membre,
    _sessions_admin, _sessions_kyc, _sessions_config, _rate_limiter, _sessions_lock, _msg_executor,
    _download_executor, _groupes_intro_envoyes, session_valide, rate_limit_ok, _sessions_path,
    _sessions_bak_lock, _sessions_owner, _purger_sessions_expirees, _sauvegarder_sessions,
    _restaurer_sessions,
)
MONTANT_IRA      = 150     # DÉPRÉCIÉ — conservé le temps de migrer les anciens messages

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
from health import (                 # @healed, état de santé, auto-réparation (module health.py)
    healed, _health_state, _health_lock, _self_heal_db, _self_heal_check,
    _self_heal_outbox, _check_greenapi,
)


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


# ══════════════════════════════════════════════════════════════════════════
# PAIEMENT MANUEL — Screenshot + Confirmation Admin
# ══════════════════════════════════════════════════════════════════════════


from paiement import (   # extrait dans paiement.py
    hash_screenshot, screenshot_deja_utilise, enregistrer_screenshot, reference_deja_utilisee,
    enregistrer_reference, screenshot_near_duplicate, MontantAberrantError,
    enregistrer_cotisation_manuelle, _incrementer_slip_cycle, _reclasser_en_dernier,
    confirmer_cotisation, rejeter_cotisation, declencher_bouffage_manuel,
    traiter_reponse_numero_bouffage, _solder_versement, confirmer_bouffage_vire,
    _rapport_ponctualite_cycle, _verifier_fin_cycle, demarrer_nouveau_cycle, cloturer_tontine,
    rapport_dettes_badf_admin, enregistrer_paiement_badf, _get_retardataires,
    saisir_caution_et_compenser_groupe, verifier_et_liberer_cautions, _verifier_liberation_caution,
)


# ══════════════════════════════════════════════════════════════════════════
# WHATSAPP — envoi dans whatsapp.py, sessions dans etat.py ; restent ici les
# fonctions d'envoi liées au bot (exclusion d'un membre du groupe)
# ══════════════════════════════════════════════════════════════════════════


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


from menu_membre import (   # extrait dans menu_membre.py
    _demarrer_collecte_nom, traiter_kyc, _finaliser_nom, traiter_menu_membre, _repondre_rang,
    _repondre_caution, _init_paiement_cotisation,
)


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
        # Date lue en JJ/MM/AAAA ici, pas par le DateStyle du serveur (souvent MDY).
        try:
            date_reprise = datetime.strptime(texte, "%d/%m/%Y").date()
        except ValueError:
            return "❌ Date invalide. Ex: 15/06/2026"
        if date_reprise <= datetime.now().date():
            return "❌ La date de reprise doit être dans le futur."
        d    = sess["data"]
        conn = get_conn()
        try:
            adh = fetchone(conn, """SELECT statut FROM adhesions WHERE membre_id=%s AND tontine_id=%s
                                    FOR UPDATE""", (d["cas_mbr_id"], tid))
            if not adh or adh["statut"] != "Actif":
                sess["etape"] = "menu"
                return message_pause_refusee(d["cas_mbr_nom"], tnom, adh["statut"] if adh else None)
            q(conn, "UPDATE adhesions SET statut='Pause' WHERE membre_id=%s AND tontine_id=%s", (d["cas_mbr_id"], tid))
            q(conn, """INSERT INTO cas_difficiles (membre_id, tontine_id, type_cas, details, date_reprise, admin_id)
                       VALUES (%s,%s,'Pause',%s,%s,%s)""",
              (d["cas_mbr_id"], tid, f"Pause jusqu'au {texte}", date_reprise, wa))
            conn.commit()
        finally:
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


from owner import (   # extrait dans owner.py
    traiter_menu_owner, _owner_menu_principal, _bloc_ira_membres, _owner_rapport_jour,
    _owner_rapport_semaine, _owner_rapport_mois, _owner_historique_n_jours,
    _owner_tontines_actives, _owner_reserve_badf, _owner_membres_stats, _owner_fugitifs_stats,
    envoyer_releve_fmp_post_bouffage, rapport_groupes_fin_journee,
)


# ── Sous-fonctions owner ────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════
# ÉVÉNEMENTS GROUPE — bot ajouté / membre quitte
# ══════════════════════════════════════════════════════════════════════════

from groupe import (   # extrait dans groupe.py
    _est_nom_valide, _auto_inscrire_participants, _envoyer_config_dm_admins, _bot_ajoute_groupe,
    traiter_config_tontine, _groupe_liste_cotisations, _traiter_commande_groupe,
    _groupe_rappel_manuel, _greenapi_get_pushname, _greenapi_get_group_members,
    _greenapi_get_group_admins,
)


# ══════════════════════════════════════════════════════════════════════════
# WEBHOOK WHATSAPP — META CLOUD API
# ══════════════════════════════════════════════════════════════════════════


@app.route("/webhook/whatsapp", methods=["GET"])
def webhook_whatsapp_verify():
    """Health check — Green API n'utilise pas de challenge GET."""
    return jsonify({"status": "ok", "bot": "TontineBot Pro"}), 200


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
    commande_admin_cas_resolu, lever_pauses_echues, message_pause_refusee,
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
# RAPPELS HORAIRES — PRESSION SOCIALE
# ══════════════════════════════════════════════════════════════════════════


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
