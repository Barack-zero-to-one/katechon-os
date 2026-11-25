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
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers import SchedulerNotRunningError

import ira_engine  # Moteur IRA v2 (barème pur, testé hors DB)
from katechon.security import webhook_security as ws        # Durcissement webhook : anti-rejeu + token + SSRF (module pur)
from katechon.security import screenshot_integrity as si    # Anti-recyclage renforcé : référence + perceptual hash (module pur)
from katechon.messaging import wa_dispatch                   # File d'envoi WhatsApp : un expéditeur, priorités, écart par destinataire (module pur)
from ocr import (                    # Lecture des reçus : OpenCV, PDF SwitchN, Tesseract (module pur)
    _pretraiter_screenshot_whatsapp, _lire_pdf_switchn, lire_screenshot_mobile_money,
)
from katechon.messaging.messages import (               # Textes du bot et liste de passage (module pur)
    BOT_NOM, MSG_DISSUASION, msg_dissuasion, msg_intro_groupe, MSG_INTRO_GROUPE,
    msg_dm_admin_bienvenue, parser_liste_passage, msg_kyc_groupe, MSG_BIENVENUE_DM,
    MENU_MEMBRE_TXT, MENU_ADMIN_TXT, MENU_CAS_DIFFICILES,
)

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION GÉNÉRALE
# ══════════════════════════════════════════════════════════════════════════

PORT             = 5000
# Frais, barème IRA, numéros collecteurs, seuil de fraude et réglages webhook : katechon/core/socle.py.
from katechon.core.socle import (   # katechon/core/socle.py
    FRAIS_FMP, HEURE_LIMITE_DEF, IRA_CLIFF_PCT, IRA_DAILY_PCT, IRA_HOURLY_PCT, IRA_CAP_PCT,
    IRA_FLOOR, IRA_GRACE_MIN, FRAIS_REACTIV, FRAIS_CHGNUM, NUMERO_BADF_MTN, NUMERO_BADF_ORANGE,
    MAX_TENTATIVES_FRAUDE, GREENAPI_WEBHOOK_SECRET, WEBHOOK_REPLAY_TTL, _replay_guard,
    MEDIA_MAX_BYTES, SESSION_TIMEOUT, enregistrer_liste_passage, inscrire_dans_tontine,
    normaliser_numero, valider_numero_cameroun, calculer_ira_mise, get_membre_by_wa,
    get_tontines_admin, est_owner, est_admin, incrementer_tentatives_fraude, _alerter_burst_fraude,
    _JOURS_SEMAINE, _est_jour_collecte, _est_jour_cotisation, _verifier_credit_comm,
)
from katechon.core.etat import (   # sessions, limiteur, exécuteurs : katechon/core/etat.py
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
from katechon.messaging.whatsapp import (               # Envoi Green API : outbox, expéditeur, wa_* (katechon/messaging/whatsapp.py)
    GREENAPI_INSTANCE_ID, GREENAPI_TOKEN, GREENAPI_BASE, OWNER_WA,
    _outbox_lock, _outbox_path, _outbox_enqueue, _outbox_drain, _wa_request, _chat_id,
    _wa_send_direct, WA_ECART_PAR_CHAT, WA_DEBIT_GLOBAL, _expediteur, _wa_send_groupe_direct,
    _wa_send, _wa_send_groupe, _wa_send_group_chatid, wa_kick_membre, wa_quitter_groupe,
    wa_prive, wa_owner, wa_admins_tontine, wa_groupe, wa_admin,
)
from katechon.core.health import (                 # @healed, état de santé, auto-réparation (katechon/core/health.py)
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

from katechon.core.app import (   # journal, app Flask, jetons, /health : katechon/core/app.py
    log, audit, app, _DASH_TOKEN, _HEALTH_TOKEN, _BOT_START_FILE, _BOT_START, _add_security_headers,
    health, health_detail,
)
from katechon.core.audit import log_audit, _masquer_wa   # Journal d'audit (katechon/core/audit.py)

# ══════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES — POSTGRESQL + POOL DE CONNEXIONS
# ══════════════════════════════════════════════════════════════════════════
#
# Le pool (ThreadedConnectionPool 10 à 80 connexions), le disjoncteur et les
# helpers de requête vivent dans db.py ; le bot lui passe ses PG_* une fois.
# Chaque thread prend une connexion et la rend aussitôt avec release_conn().
# ══════════════════════════════════════════════════════════════════════════

from katechon.core.db import (                     # Accès PostgreSQL : pool, disjoncteur, helpers (katechon/core/db.py)
    configurer as _configurer_db, init_pool, fermer_pool, reinitialiser_pool, pool_actif,
    get_conn, q, release_conn, fetchone, fetchall, verrou_ordre_tontine,
)

_configurer_db(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS)


from katechon.core.schema import init_db   # Schéma PostgreSQL : tables, migrations, index (katechon/core/schema.py)


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
# MENU ADMIN — 15 options, commandes directes, exclusion et rappels (menu_admin.py)
# ══════════════════════════════════════════════════════════════════════════


from menu_admin import (   # extrait dans menu_admin.py
    kick_membre_si_bot_admin, wa_mentionner_retardataires, _passer_a_cotisation_suivante,
    traiter_menu_admin,
)


# ══════════════════════════════════════════════════════════════════════════
# ONBOARDING — COLLECTE NOM
# ══════════════════════════════════════════════════════════════════════════


from menu_membre import (   # extrait dans menu_membre.py
    _demarrer_collecte_nom, traiter_kyc, _finaliser_nom, traiter_menu_membre, _repondre_rang,
    _repondre_caution, _init_paiement_cotisation,
)


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
# WEBHOOK WHATSAPP — Green API (webhook.py)
# ══════════════════════════════════════════════════════════════════════════


from webhook import (   # extrait dans webhook.py
    webhook_whatsapp_verify, _traiter_groupe_participants, webhook_whatsapp_greenapi,
    _greenapi_telecharger_media, _traiter_message_greenapi, _traiter_screenshot_cotisation_bytes,
    register_webhook,
)
register_webhook(app)   # GET et POST /webhook/whatsapp, endpoints inchangés


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
