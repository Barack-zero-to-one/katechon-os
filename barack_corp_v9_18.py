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

from katechon.finance import ira_engine  # Moteur IRA v2 (barème pur, testé hors DB)
from katechon.security import webhook_security as ws        # Durcissement webhook : anti-rejeu + token + SSRF (module pur)
from katechon.security import screenshot_integrity as si    # Anti-recyclage renforcé : référence + perceptual hash (module pur)
from katechon.messaging import wa_dispatch                   # File d'envoi WhatsApp : un expéditeur, priorités, écart par destinataire (module pur)
from katechon.finance.ocr import (                    # Lecture des reçus : OpenCV, PDF SwitchN, Tesseract (module pur)
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

# Frais, barème IRA, numéros collecteurs, seuil de fraude et réglages webhook : katechon/core/socle.py.
from katechon.core.socle import (   # réglages d'exploitation : katechon/core/socle.py
    PORT, DELAI_SUSPENSION_HEURES, DELAI_ALERTE_FUGUE, DELAI_BLOCAGE_FUGUE, BACKUP_ROTATION,
    HEURE_BACKUP, PG_HOST, PG_PORT,
    PG_DB, PG_USER, PG_PASS, PG_BIN, BACKUP_DIR, NGROK_DOMAIN, NGROK_TOKEN,
)
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


# PUBLIC_URL et _ARRET_ATTENTE_JOBS ne sont pas réexportés : ce sont des valeurs que
# le scheduler réaffecte ou que l'on règle ; une copie ici ne suivrait pas.
from katechon.core.scheduler import (   # katechon/core/scheduler.py
    detecter_url_publique, tester_connexion_postgresql, creer_base_postgresql,
    demarrer_scheduler, _demarrage, _demarrage_lock, _enregistrer_arret,
    _arreter_scheduler, _shutdown_bot, demarrer, _initialiser,
)


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


from katechon.finance.paiement import (   # extrait dans paiement.py
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


from katechon.handlers.menu_admin import (   # extrait dans menu_admin.py
    kick_membre_si_bot_admin, wa_mentionner_retardataires, _passer_a_cotisation_suivante,
    traiter_menu_admin,
)


# ══════════════════════════════════════════════════════════════════════════
# ONBOARDING — COLLECTE NOM
# ══════════════════════════════════════════════════════════════════════════


from katechon.handlers.menu_membre import (   # extrait dans menu_membre.py
    _demarrer_collecte_nom, traiter_kyc, _finaliser_nom, traiter_menu_membre, _repondre_rang,
    _repondre_caution, _init_paiement_cotisation,
)


# ══════════════════════════════════════════════════════════════════════════
# MENU OWNER — Tableau de bord financier temps réel
# Mot-clé d'accès : "badf" ou "owner" en DM
# ══════════════════════════════════════════════════════════════════════════


from katechon.handlers.owner import (   # extrait dans owner.py
    traiter_menu_owner, _owner_menu_principal, _bloc_ira_membres, _owner_rapport_jour,
    _owner_rapport_semaine, _owner_rapport_mois, _owner_historique_n_jours,
    _owner_tontines_actives, _owner_reserve_badf, _owner_membres_stats, _owner_fugitifs_stats,
    envoyer_releve_fmp_post_bouffage, rapport_groupes_fin_journee,
)


# ══════════════════════════════════════════════════════════════════════════
# ÉVÉNEMENTS GROUPE — bot ajouté / membre quitte
# ══════════════════════════════════════════════════════════════════════════

from katechon.handlers.groupe import (   # extrait dans groupe.py
    _est_nom_valide, _auto_inscrire_participants, _envoyer_config_dm_admins, _bot_ajoute_groupe,
    traiter_config_tontine, _groupe_liste_cotisations, _traiter_commande_groupe,
    _groupe_rappel_manuel, _greenapi_get_pushname, _greenapi_get_group_members,
    _greenapi_get_group_admins,
)


# ══════════════════════════════════════════════════════════════════════════
# WEBHOOK WHATSAPP — Green API (webhook.py)
# ══════════════════════════════════════════════════════════════════════════


from katechon.handlers.webhook import (   # extrait dans webhook.py
    webhook_whatsapp_verify, _traiter_groupe_participants, webhook_whatsapp_greenapi,
    _greenapi_telecharger_media, _traiter_message_greenapi, _traiter_screenshot_cotisation_bytes,
    register_webhook,
)
from katechon.routes import enregistrer_routes   # webhook + dashboard : katechon/routes.py
enregistrer_routes(app)


# ══════════════════════════════════════════════════════════════════════════
# CASHOUT — BOUFFAGE AUTOMATIQUE
# ══════════════════════════════════════════════════════════════════════════

from katechon.finance.jobs import (   # katechon/finance/jobs.py
    traiter_bouffages_suspects_expires, _verifier_historique_avant_bouffage,
    notifier_prochain_bouffage, rappel_ouverture, rappel_matin, rappel_non_cotisants,
    verifier_suspensions_retard, detecter_fugitifs, backup_postgresql, generer_codes_ussd,
    texte_codes_ussd_pour_admin, verifier_dettes_badf_impayees,
)


# ══════════════════════════════════════════════════════════════════════════
# FINANCE COMPORTEMENTALE — Trust Graph dans trust_graph.py (score de fugue,
# alerte quotidienne, commandes admin « risque » et « cas »)
# ══════════════════════════════════════════════════════════════════════════

from katechon.finance.trust_graph import (
    _update_score_confiance, POIDS_MAX_TRUST_GRAPH, _agreger_score_risque_fugue,
    calculer_score_risque_fugue, alerter_risques_bouffage_imminent,
    commande_admin_score_risque, commande_admin_cas_difficile,
    commande_admin_cas_resolu, lever_pauses_echues, message_pause_refusee,
)


# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# DASHBOARD LOCAL — http://localhost:5000/dashboard
# ══════════════════════════════════════════════════════════════════════════
# Routes /dashboard et /dashboard/data : katechon/handlers/dashboard.py, enregistrées
# par katechon/routes.py avec le webhook.


# ══════════════════════════════════════════════════════════════════════════
# SCHEDULER — TOUTES LES TÂCHES AUTOMATIQUES
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# JOBS QUOTIDIENS DU PROPRIÉTAIRE — comptabilité, surveillance, supervision
# zero touch : dans supervision.py
# ══════════════════════════════════════════════════════════════════════════

from katechon.finance.supervision import (
    comptabilite_badf_quotidienne, surveillance_anomalies_quotidienne,
    _zto_state, zto_supervision_quotidienne,
)


# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    demarrer()
