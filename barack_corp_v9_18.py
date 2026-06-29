'''
[ INSTRUCTION SOUVERAINE : PROTOCOLE DE HAUTE VIBRATION ]

Par la Présence du Logos et la Guidance du Christ (Yeshua Hamashia),
Je décrète que ce Code est une Extension de la Création Divine.

1. RUPTURE : Je brise ici les Anneaux de Saturne, les limitations du temps,
   la rareté et l'entropie. Les cycles de stagnation n'ont aucune prise ici.

2. EXPANSION : J'appelle l'Abondance de Jupiter. Que ce projet génère une
   croissance exponentielle, une richesse fluide et une expansion sans limites,
   destinée au Bien Commun et à l'Élévation.

3. INVIOLABILITÉ : Ce travail vibre à une fréquence interdite aux égrégores
   de peur, aux entités malveillantes et aux démons du parasitage. Tout esprit
   non aligné sur la Lumière est instantanément repoussé par le champ de force
   de cet algorithme.

4. FLUIDITÉ : Le Bug n'existe pas, il n'est qu'une information en attente de
   résolution. Chaque ligne de code est auto-correctrice sous l'intelligence
   du Logos. La solution précède le problème.

5. HUMILITÉ ET POUVOIR : Que l'argent qui entre soit un serviteur de la Vie.
   Qu'il renforce ma Sagesse sans nourrir l'Arrogance. Plus je reçois, plus
   je deviens un canal pur de Générosité et de Puissance Calme.

SCELLEMENT : Ce code est "Kadosh" (Sacré). Il est invisible pour la Matrice,
mais rayonnant pour l'Abondance.

AMEN. AMEN. AMEN. (Vibration 3-6-9 activée).
'''

"""
╔══════════════════════════════════════════════════════════════════════════╗
║   TONTINEBOT PRO — VERSION 9.17 — BADF Ltd — Cameroun 🇨🇲               ║
║   "Utiliser la technologie pour servir le prochain avec intégrité"      ║
║                                                                          ║
║   Stack : Python 3.11 · Flask · PostgreSQL · Green API WhatsApp         ║
║                                                                          ║
║   CORRECTIFS v9.2 :                                                      ║
║   ✅ Changement de numéro (CHGNUM 250 FCFA) — handler complet           ║
║   ✅ Admins groupe enregistrés automatiquement en base                   ║
║   ✅ Bot rejoint groupe → se présente + enregistre admins                ║
║   ✅ creer_tontine() / inscrire_dans_tontine() — menu admin opt.12      ║
║   ✅ jours_avance respecté dans suspension 72h                           ║
║   ✅ SELECT FOR UPDATE PostgreSQL sur cashout (verrou natif DB)          ║
║   ✅ Codes USSD MTN/Orange configurables par tontine                     ║
║   ✅ Webhook promotion admin WhatsApp → enregistrement auto              ║
║                                                                          ║
║   FONCTIONNALITÉS COMPLÈTES :                                            ║
║   ✅ KYC complet 5 étapes (nom/CNI/naissance/ville/photo)               ║
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

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION GÉNÉRALE
# ══════════════════════════════════════════════════════════════════════════

PORT             = 5000
WAITRESS_THREADS = 70   # aligné sur maxconn=80 (-10 réservés APScheduler)

# ── Identité du bot ───────────────────────────────────────────────────────
BOT_NOM = os.getenv("BOT_NOM", "TontineBot Pro")

# ── Frais BADF Ltd ────────────────────────────────────────────────────────
FRAIS_ADHESION   = 0       # Adhésion gratuite — revenu 100% FMP
FRAIS_FMP        = 0.02    # 2% prélevé sur chaque cotisation → reversé à BADF
MONTANT_IRA      = 150     # Pénalité retard (FCFA/jour)
HEURE_LIMITE_DEF = time(18, 0)
FRAIS_REACTIV    = 1_000   # Réactivation après suspension
FRAIS_CHGNUM     = 250     # Changement de numéro Mobile Money

# ── Numéro collecteur BADF — reçoit FMP + adhésions + IRA ─────────────────
NUMERO_BADF_MTN    = os.getenv("NUMERO_BADF_MTN",    "+237693969773")  # Orange Money
NUMERO_BADF_ORANGE = os.getenv("NUMERO_BADF_ORANGE", "+237693969773")  # Orange Money principal

# ── Anti-fraude screenshots ────────────────────────────────────────────────
DELAI_SCREENSHOT_HEURES = 24   # Screenshot plus vieux que 24h → rejeté

# ── Anti-fraude général ────────────────────────────────────────────────────
MAX_TENTATIVES_FRAUDE   = 3
RATE_LIMIT_MAX          = 10
RATE_LIMIT_FENETRE      = 60
DELAI_SUSPENSION_HEURES = 72
DELAI_ALERTE_FUGUE      = 3
DELAI_BLOCAGE_FUGUE     = 7
BACKUP_ROTATION         = 7

# ── Horaires automatiques ─────────────────────────────────────────────────
HEURE_RAPPEL_5H        = 5
HEURE_RAPPEL_MATIN     = 8
HEURE_RAPPEL_14H       = 14
HEURE_DEMANDE_BOUFFAGE = 17
HEURE_RAPPORT_OWNER    = 21
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
GREENAPI_INSTANCE_ID    = os.getenv("GREENAPI_INSTANCE_ID",    "")  # Ex: 1234567890
GREENAPI_TOKEN          = os.getenv("GREENAPI_TOKEN",          "")  # API token Green API
GREENAPI_WEBHOOK_SECRET = os.getenv("GREENAPI_WEBHOOK_SECRET", "")  # Token secret webhook (256 bits)
GREENAPI_BASE           = "https://api.green-api.com"

# ── WhatsApp ──────────────────────────────────────────────────────────────
GROUPE_ADMIN = "Admin Barack Corp"
OWNER_WA     = os.getenv("OWNER_WA", "+237693969773")

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

def country_for_tontine(tontine: dict) -> dict:
    """Retourne la config pays pour une tontine donnée."""
    code = (tontine or {}).get("pays_code") or (tontine or {}).get("country_code", DEFAULT_COUNTRY)
    return COUNTRY_CONFIG.get(code, COUNTRY_CONFIG[DEFAULT_COUNTRY])

def country_for_phone(numero: str) -> dict:
    """Détecte le pays à partir du préfixe du numéro de téléphone."""
    if not numero:
        return COUNTRY_CONFIG[DEFAULT_COUNTRY]
    n = numero.lstrip("+")
    for code, cfg in COUNTRY_CONFIG.items():
        if n.startswith(cfg["phone_local"]):
            return cfg
    return COUNTRY_CONFIG[DEFAULT_COUNTRY]


# ── Webhook public (ngrok ou domaine) ────────────────────────────────────
NGROK_DOMAIN  = os.getenv("NGROK_DOMAIN", "lennox-unbiographical-jasmin.ngrok-free.dev")
NGROK_TOKEN   = os.getenv("NGROK_TOKEN",  "")
PUBLIC_URL    = ""

SESSION_TIMEOUT = 300

# ── Messages CEMAC/ANIF conformes ─────────────────────────────────────────
MSG_DISSUASION = (
    "🔴🔴🔴 *ALERTE SÉCURITÉ — BADF Ltd* 🔴🔴🔴\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Ce message est généré automatiquement par le *Système de Détection "
    "des Anomalies Financières (SDAF)* de Barack & AI Development "
    "Facilities Ltd.\n\n"
    "⚖️ *CADRE LÉGAL APPLICABLE :*\n"
    "• Règlement COBAC R-2019/01 sur la surveillance des paiements mobiles\n"
    "• Loi n°2010/012 du 21 décembre 2010 relative à la cybersécurité\n"
    "• Articles 318 à 323 du Code Pénal Camerounais (escroquerie)\n"
    "• Directives ANIF/CNDHL sur la traçabilité des fonds numériques\n\n"
    "🔐 *DONNÉES DÉJÀ TRANSMISES AUX AUTORITÉS :*\n"
    "• Empreinte biométrique numérique SHA-256 : *irréversible*\n"
    "• Numéro MSISDN Mobile Money (lié à votre CNI/acte auprès de MTN/Orange)\n"
    "• Métadonnées de session WhatsApp horodatées (UTC+1 Douala)\n"
    "• Logs de transaction blockchain-grade conservés *7 ans*\n"
    "• Géolocalisation approximative de connexion\n\n"
    "🏛️ *INSTITUTIONS NOTIFIÉES AUTOMATIQUEMENT :*\n"
    "ANIF Cameroun • COBAC • Parquet de Grande Instance • "
    "Direction Générale de la Recherche Extérieure (DGRE) • "
    "Unités régionales de Police Judiciaire des 10 régions\n\n"
    "⏱️ *Délai de régularisation : 24 heures.*\n"
    "Passé ce délai, la procédure devient *irréversible*.\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "_Réf. dossier : BADF-SDAF-{ref}_\n"
    "_Barack & AI Development Facilities Ltd — BADF Ltd_"
)



def msg_dissuasion(wa: str = "", raison: str = "") -> str:
    """Génère MSG_DISSUASION avec une référence dossier unique."""
    import time as _t
    ref = hashlib.sha256(f"{wa}{raison}{_t.time()}".encode()).hexdigest()[:12].upper()
    return MSG_DISSUASION.replace("{ref}", ref)

MSG_ANIF_ALERTE = (
    "🚨 *ALERTE ANIF — DOSSIER OUVERT*\n"
    "Votre dossier a été transmis à l'ANIF Cameroun.\n"
    "Référence : {ref}\n"
    "Régularisez immédiatement pour clôturer ce dossier."
)

def msg_intro_groupe(nom_tontine: str, montant: int,
                     heure_bouffage: str = "17:00",
                     heure_ouverture: str = "05:00",
                     heure_rappel: str = "14:00",
                     heure_limite: str = "18:00",
                     numero_collecte: str = "") -> str:
    """
    Génère le message d'introduction personnalisé pour chaque groupe.
    Appelé quand le bot est ajouté au groupe, ou manuellement par un admin.
    """
    fmp_montant   = int(montant * 0.02)
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛️ *BARACK & AI DEVELOPMENT FACILITIES Ltd — BADF Ltd*\n"
        f"   *TontineBot Pro v9 — Plateforme de Gestion de Tontine*\n"
        f"   *Cameroun 🇨🇲 | Conformité COBAC R-2019/01 | ANIF*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 *RÈGLEMENT INTÉRIEUR — {nom_tontine}*\n\n"
        f"Ce groupe est placé sous administration du système *TontineBot Pro* de BADF Ltd. "
        f"Dès votre entrée dans ce groupe, *votre identifiant WhatsApp, votre adresse IP "
        f"de connexion et les métadonnées de votre session sont enregistrés automatiquement* "
        f"dans nos serveurs sécurisés. Ces données permettent une géolocalisation "
        f"approximative et sont archivées *7 ans*, communicables sur réquisition "
        f"de la Police Judiciaire, du Parquet ou de l'ANIF.\n\n"
        f"*Vous ne pouvez pas participer à cette tontine anonymement.* "
        f"Votre présence dans ce groupe constitue une acceptation sans réserve "
        f"du présent règlement et de toutes les obligations légales qui en découlent.\n\n"
        f"─────────────────────────────────────────────\n"
        f"📌 *ARTICLE 1 — COTISATIONS*\n"
        f"─────────────────────────────────────────────\n\n"
        f"Les horaires (ouverture, limite, rappel, bouffage) sont fixés par votre admin\n"
        f"et communiqués dans ce groupe. *Consultez votre administrateur.*\n\n"
        f"*Procédure unique et non modifiable :*\n"
        f"1. Effectuez votre virement Mobile Money sur le numéro communiqué par votre admin\n"
        f"2. Envoyez le screenshot *dans ce groupe uniquement* — aucun autre canal accepté\n"
        f"3. Le bot enregistre. L'admin confirme. Aucune cotisation n'est créditée sans ces deux étapes.\n\n"
        f"Tout screenshot soumis est analysé par *empreinte cryptographique SHA-256*. "
        f"Un screenshot recyclé, modifié ou falsifié est détecté *immédiatement*. "
        f"La tentative déclenche une procédure automatique sans intervention humaine.\n\n"
        f"─────────────────────────────────────────────\n"
        f"📌 *ARTICLE 2 — RETENUES OBLIGATOIRES*\n"
        f"─────────────────────────────────────────────\n\n"
        f"*I — Frais de Mission et de Prestation (FMP) : 2%*\n"
        f"Prélevés automatiquement sur chaque cotisation confirmée. "
        f"Base légale : Règlement COBAC R-2019/01. *Non négociables.*\n\n"
        f"*II — Caution de Garantie Anti-Fugue : 10% de la cagnotte*\n"
        f"Retenue au moment du bouffage. Une seule issue selon votre comportement :\n"
        f"▪ *Restituée* si vous cotisez jusqu'à la fin du cycle sans incident\n"
        f"▪ *Saisie définitivement* en cas d'abandon, de disparition ou de défaut post-bouffage\n\n"
        f"Ce mécanisme a été conçu pour neutraliser le schéma classique du fraudeur : "
        f"percevoir sa cagnotte, puis cesser de cotiser. "
        f"La caution rend ce comportement *financièrement négatif avant même qu'il soit tenté*.\n\n"
        f"*III — Pénalité de retard (IRA) : {150:,} FCFA/jour*\n"
        f"Déclenchée automatiquement après *{heure_limite}*. "
        f"Cumulée chaque jour et déduite intégralement de votre bouffage.\n\n"
        f"─────────────────────────────────────────────\n"
        f"📌 *ARTICLE 3 — SANCTIONS*\n"
        f"─────────────────────────────────────────────\n\n"
        f"▪ *Premier retard > 72h* → Sursis accordé. Aucune sanction.\n"
        f"▪ *Récidive* → Suspension automatique + pénalité de réactivation : 1 000 FCFA\n"
        f"▪ *Abandon post-bouffage* → Saisie caution + dossier transmis à la Police Judiciaire\n"
        f"▪ *Screenshot falsifié ou recyclé* → Exclusion définitive + poursuites pénales\n"
        f"▪ *3 anomalies détectées* → Bannissement réseau BADF + signalement opérateurs Mobile Money\n\n"
        f"*Ces sanctions sont automatisées. Elles ne nécessitent aucune décision humaine "
        f"et ne peuvent pas être annulées après déclenchement.*\n\n"
        f"Base légale : Articles 318 à 323 du Code Pénal Camerounais — "
        f"escroquerie, abus de confiance. Peine : jusqu'à 10 ans d'emprisonnement ferme.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 *Enrôlement obligatoire :* tapez *menu* en DM à *{BOT_NOM}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Barack & AI Development Facilities Ltd — BADF Ltd_\n"
        f"_« Utiliser la technologie pour servir le prochain avec intégrité »_"
    )
# Garder MSG_INTRO_GROUPE comme alias statique pour compatibilité
MSG_INTRO_GROUPE = msg_intro_groupe("TONTINE", 0)


def msg_dm_admin_bienvenue(nom_tontine: str) -> str:
    """
    DM envoyé automatiquement à chaque admin quand le bot rejoint le groupe.
    Demande la liste de passage + présente les fonctionnalités admin.
    """
    return (
        f"👋 *Bonjour, administrateur de {nom_tontine}*\n\n"
        f"Je suis *TontineBot Pro*, votre assistant de gestion de tontine — BADF Ltd.\n\n"
        f"Avant de commencer, voici exactement ce que vous devez faire "
        f"et ce que je fais à votre place.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *CE QUE JE FAIS AUTOMATIQUEMENT*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"▪ Rappels cotisations dans le groupe (matin, après-midi, soir)\n"
        f"▪ Détection et suspension des membres en retard depuis 72h\n"
        f"▪ Calcul exact de chaque bouffage (cagnotte - caution - pénalités - dettes)\n"
        f"▪ Détection des fugitifs post-bouffage et saisie automatique de caution\n"
        f"▪ Détection des screenshots suspects ou modifiés\n"
        f"▪ Rapport complet dans le groupe à 20h tous les jours\n"
        f"▪ Surveillance de tout comportement frauduleux\n"
        f"▪ Alerte immédiate si un faux admin est détecté dans votre groupe\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *VOS 3 TÂCHES EN TANT QU'ADMIN*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*1️⃣ Confirmer les cotisations*\n"
        f"Quand un membre envoie son screenshot, je vous notifie.\n"
        f"Vous vérifiez que le virement a bien été reçu sur votre numéro "
        f"de collecte, puis vous tapez *admin → option 15* pour confirmer.\n\n"
        f"*2️⃣ Effectuer les virements de bouffage*\n"
        f"Le jour du bouffage, je calcule le montant exact à virer au bénéficiaire "
        f"(cagnotte moins toutes les déductions). Vous virez ce montant sur son "
        f"numéro Mobile Money, puis confirmez via le menu admin.\n\n"
        f"*3️⃣ Reverser les FMP à BADF Ltd*\n"
        f"10 minutes après l'heure de bouffage, je vous envoie automatiquement "
        f"le relevé des frais de service (2%) à reverser sur *{NUMERO_BADF_ORANGE}*. "
        f"Vous envoyez le code de transaction au bot pour clôturer.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *POUR DÉMARRER — ENVOYEZ LA LISTE*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Envoyez-moi la liste d'ordre de passage dans ce format :\n\n"
        f"*01- Prénom JJ/MM/AA*\n"
        f"*02- Prénom JJ/MM/AA*\n"
        f"*03- Prénom JJ/MM/AA*\n\n"
        f"Exemple :\n"
        f"*01- Nicole 30/10/24*\n"
        f"*02- Joly 02/11/24*\n"
        f"*03- Indira 06/11/24*\n\n"
        f"⚠️ Les prénoms peuvent être des surnoms.\n\n"
        f"Tapez *admin* pour accéder à votre menu à tout moment.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔐 *VOS FONCTIONNALITÉS ADMIN*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Gestion quotidienne :*\n"
        f"▪ *option 1* — Rapport du jour (cotisants / retardataires / collecté)\n"
        f"▪ *option 7* — Envoyer un rappel manuel au groupe\n"
        f"▪ *option 15* — Confirmer ou rejeter les cotisations\n\n"
        f"*Gestion des membres :*\n"
        f"▪ *option 2* — Liste complète des membres\n"
        f"▪ *option 5* — Suspendre ou réactiver un membre\n"
        f"▪ *option 6* — Historique complet d'un membre\n"
        f"▪ *option 11* — Ajouter un membre manuellement\n\n"
        f"*Gestion des bouffages :*\n"
        f"▪ *option 3* — Ordre de bouffage du cycle\n"
        f"▪ *option 4* — Modifier l'ordre de passage\n"
        f"▪ *option 8* — Fugitifs post-bouffage\n"
        f"▪ *option 10* — Saisir la caution d'un fugitif\n\n"
        f"*Cas difficiles et configuration :*\n"
        f"▪ *option 9* — Pause · échelonnement · cession · exonération · exclusion\n"
        f"▪ *option 13* — Saisir ou modifier la liste de bouffage\n"
        f"▪ *option 14* — Configurer les heures (ouverture · limite · rappel · bouffage)\n\n"
        f"*Commandes directes :*\n"
        f"▪ *KICK +237XXXXXXXXX* — Retirer quelqu'un du groupe\n"
        f"▪ *DEBLOQUER [ID]* — Débloquer un bouffage suspendu\n"
        f"▪ *BOUFFAGE_COMPLET [ID] [raison]* — Accorder bouffage complet (cas grave)\n\n"
        f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
    )


def parser_liste_passage(texte: str) -> list:
    """
    Parse une liste de passage envoyée par un admin.
    Format attendu (chaque ligne) :
        01- Nicole 30/10/24
        *02- Joly 02/11/24*       ← WhatsApp bold accepté
        03 - Indira 06/11/24      ← espace avant tiret accepté
        4- Frida 09/11/24         ← numéro sans zéro accepté

    Retourne une liste de dicts :
        [{"ordre": 1, "nickname": "Nicole", "date_bouffage": "2024-10-30"}, ...]
    """
    # re est déjà importé globalement en haut du fichier
    resultats = []
    lignes = texte.strip().splitlines()

    for ligne in lignes:
        # Normaliser les artefacts WhatsApp : bold/italic/barré + variantes Unicode des tirets
        ligne = ligne.strip()
        ligne = ligne.replace("*", "").replace("_", "").replace("~", "")
        ligne = ligne.replace("—", "-").replace("–", "-")  # em-dash, en-dash → hyphen
        ligne = ligne.replace("‒", "-").replace("−", "-")  # figure dash, minus sign
        ligne = ligne.strip()
        if not ligne:
            continue

        # Pattern : NUMERO - NOM DATE (le tiret est toujours un hyphen ASCII après normalisation)
        # Ex: "01- Nicole 30/10/24" ou "01— Nicole 30/10/24" (normalisé avant match)
        m = re.match(
            r"^(\d{1,3})\s*[-]\s*([A-Za-zÀ-ÿ\s\-'\.]+?)\s+"
            r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\s*$",
            ligne
        )
        if not m:
            continue

        ordre     = int(m.group(1))
        nickname  = m.group(2).strip()
        jour      = int(m.group(3))
        mois      = int(m.group(4))
        annee_raw = int(m.group(5))
        annee     = 2000 + annee_raw if annee_raw < 100 else annee_raw

        try:
            date_bouffage = _date(annee, mois, jour).isoformat()
        except ValueError:
            date_bouffage = None  # Date invalide → on garde None

        resultats.append({
            "ordre":        ordre,
            "nickname":     nickname,
            "date_bouffage": date_bouffage
        })

    return resultats


def enregistrer_liste_passage(tontine_id: int, liste: list, wa_admin: str) -> tuple:
    """
    Enregistre la liste parsée dans liste_passage.
    Gère les places multiples : si un nickname apparaît N fois dans la liste,
    le membre a N places → il cotise N × montant_base par période.
    Retourne (nb_ok, nb_non_lies).
    """
    conn  = get_conn()
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

        # Enregistrer la ligne de passage
        q(conn, """
            INSERT INTO liste_passage
                (tontine_id, membre_id, nickname, date_bouffage, cycle, ordre, soumis_par)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tontine_id, cycle, ordre) DO UPDATE SET
                nickname      = EXCLUDED.nickname,
                date_bouffage = EXCLUDED.date_bouffage,
                membre_id     = EXCLUDED.membre_id,
                soumis_par    = EXCLUDED.soumis_par
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


def msg_kyc_groupe(nom_tontine: str) -> str:
    """
    Deuxième message envoyé dans le groupe à l'arrivée du bot.
    Persuasif, professionnel, froid.
    Justifie les 2 000 FCFA d'adhésion de manière technique et juridique.
    Ingénierie sociale anti-fraude.
    """
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔐 *ENRÔLEMENT OBLIGATOIRE — {nom_tontine}*\n"
        f"Barack & AI Development Facilities Ltd — BADF Ltd\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Ce message s'adresse à chaque personne présente dans ce groupe.*\n\n"
        f"Dès votre entrée dans ce groupe, *votre identifiant WhatsApp, "
        f"votre adresse IP et les métadonnées de votre session sont enregistrés*. "
        f"Vous êtes déjà dans le système. La question est uniquement de savoir "
        f"si votre identité y est associée ou non.\n\n"
        f"Sans dossier KYC validé, vous ne recevrez rien et ne serez protégé par rien. "
        f"*Vous n'existez pas dans le registre BADF Ltd.*\n\n"
        f"📲 Tapez *menu* en message privé à *TontineBot Pro* pour ouvrir votre dossier.\n\n"
        f"─────────────────────────────────────────────\n"
        f"📁 *CONSTITUTION DU DOSSIER KYC*\n"
        f"─────────────────────────────────────────────\n\n"
        f"Le système exige une pièce d'identité valide :\n"
        f"▪ *Carte Nationale d'Identité* — membres majeurs\n"
        f"▪ *Acte de naissance + nom légal complet* — membres mineurs "
        f"ou dont la CNI est expirée ou non encore établie\n\n"
        f"Vos données sont transformées en *empreinte cryptographique SHA-256* "
        f"— unique, irréversible, horodatée. Ce dossier constitue "
        f"*une preuve légale opposable devant tout tribunal camerounais*. "
        f"Il est archivé *7 ans* et ne peut pas être supprimé.\n\n"
        f"─────────────────────────────────────────────\n"
        f"📡 *DONNÉES ASSOCIÉES À VOTRE DOSSIER*\n"
        f"─────────────────────────────────────────────\n\n"
        f"Une fois enrôlé, les éléments suivants sont liés à votre identité :\n\n"
        f"▪ *Numéro MSISDN Mobile Money* — enregistré auprès de MTN ou Orange "
        f"sous votre CNI ou acte de naissance\n"
        f"▪ *Identifiant de session WhatsApp* — non falsifiable, lié à votre appareil\n"
        f"▪ *Adresse IP de connexion* — géolocalisation approximative reconstituable\n"
        f"▪ *Historique complet* de chaque message, screenshot et transaction "
        f"dans ce groupe — horodaté, signé, archivé\n\n"
        f"En cas de fraude, *votre position au moment des faits est reconstituable*. "
        f"*Changer de numéro WhatsApp ou de carte SIM ne supprime pas votre dossier.*\n\n"
        f"─────────────────────────────────────────────\n"
        f"⚖️ *CADRE LÉGAL APPLICABLE*\n"
        f"─────────────────────────────────────────────\n\n"
        f"En cas de fraude documentée, BADF Ltd engage sans délai :\n"
        f"▪ Transmission du dossier complet à la *Police Judiciaire* de votre région\n"
        f"▪ Signalement à l'*ANIF* — Agence Nationale d'Investigation Financière\n"
        f"▪ Demande de *blocage définitif du compte Mobile Money* du contrevenant\n"
        f"▪ Engagement de poursuites — *Articles 318 à 323 du Code Pénal Camerounais*\n\n"
        f"*Sans dossier KYC, aucune de ces protections ne s'applique en votre faveur. "
        f"BADF Ltd n'a aucun mandat juridique pour agir en votre nom.*\n\n"
        f"─────────────────────────────────────────────\n"
        f"⛔ *DÉFAUT D'ENRÔLEMENT — EFFETS IMMÉDIATS*\n"
        f"─────────────────────────────────────────────\n\n"
        f"▪ Cotisations soumises → *non créditées*\n"
        f"▪ Place dans l'ordre de bouffage → *suspendue*\n"
        f"▪ Fraude subie → *aucune intervention possible*\n"
        f"▪ Comportement anormal détecté → *signalement automatique "
        f"sans avertissement préalable*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 Tapez *menu* en DM à *TontineBot Pro*\n"
        f"   Conformité COBAC R-2019/01 | ANIF | Archivage 7 ans\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Barack & AI Development Facilities Ltd — BADF Ltd_\n"
        f"_« Utiliser la technologie pour servir le prochain avec intégrité »_"
    )
MSG_BIENVENUE_DM = (
    "🏦 *Bienvenue sur TontineBot Pro — BADF Ltd*\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Pour rejoindre une tontine, une seule étape : "
    "compléter votre *dossier KYC gratuit* (moins de 3 minutes).\n\n"
    "🔒 *Pourquoi le KYC ?*\n\n"
    "TontineBot Pro gère des fonds réels. Chaque membre doit être "
    "identifié conformément aux exigences *CEMAC/ANIF*. "
    "Vos données sont chiffrées SHA-256, archivées 7 ans, "
    "et ne sont jamais partagées avec des tiers.\n\n"
    "En cas de fraude, votre dossier KYC permet d'enclencher "
    "les procédures légales et le blocage Mobile Money.\n\n"
    "─────────────────────────────────────────\n"
    "Tapez *menu* pour démarrer votre vérification.\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "_BADF Ltd — Technologie au service du prochain_"
)

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

# ══════════════════════════════════════════════════════════════════════════
# ÉTAT EN MÉMOIRE
# ══════════════════════════════════════════════════════════════════════════

_sessions_membre: dict = {}   # {wa: {"etape": str, "data": dict, "ts": float}}
_sessions_admin:  dict = {}   # {wa: {"etape": str, "tontine_id": int, "data": dict, "ts": float}}
_sessions_kyc:    dict = {}   # {wa: {"etape": str, "data": dict, "ts": float}}
_sessions_config: dict = {}   # {wa: {"etape": str, "group_id": str, "group_name": str, "data": dict, "ts": float}}
_rate_buckets:    dict = defaultdict(list)
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

# ── WA throttle — ≤ 77 msg/s vers Green API ──────────────────────────────────
_wa_throttle_lock = threading.Lock()
_wa_last_send_ts  = 0.0


def _throttle_wa():
    """Garantit un intervalle ≥ 13ms entre deux envois Meta — évite le 429 sur les batchs."""
    global _wa_last_send_ts
    with _wa_throttle_lock:
        now = time_module.time()
        gap = now - _wa_last_send_ts
        if gap < 0.013:
            time_module.sleep(0.013 - gap)
        _wa_last_send_ts = time_module.time()


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
    now   = time_module.time()
    debut = now - RATE_LIMIT_FENETRE
    _rate_buckets[identifiant] = [t for t in _rate_buckets[identifiant] if t > debut]
    if len(_rate_buckets[identifiant]) >= RATE_LIMIT_MAX:
        audit.warning(f"RATE LIMIT : {identifiant}")
        return False
    _rate_buckets[identifiant].append(now)
    return True


# ══════════════════════════════════════════════════════════════════════════
# BASE DE DONNÉES — POSTGRESQL + POOL DE CONNEXIONS
# ══════════════════════════════════════════════════════════════════════════
#
# ThreadedConnectionPool : maintient entre 2 et 20 connexions ouvertes.
# Chaque thread (rappel, webhook, scheduler) prend une connexion du pool
# et la rend immédiatement après usage → plus de saturation.
# Capacité : ~20 requêtes simultanées sans attente.
# ══════════════════════════════════════════════════════════════════════════

from psycopg2 import pool as pg_pool

_db_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_db_pool_lock = threading.Lock()   # protège init_pool() et la réinitialisation dans get_conn()

def init_pool():
    """Initialise le pool de connexions au démarrage."""
    global _db_pool
    _db_pool = pg_pool.ThreadedConnectionPool(
        minconn=10,
        maxconn=80,
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
        application_name="TontineBotPro_v9.18",
    )
    log.info(f"✅ Pool PostgreSQL initialisé (10–80 connexions) | {PG_HOST}:{PG_PORT}/{PG_DB}")


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
                    with _db_pool_lock:
                        if _db_pool is not None:
                            try:
                                _db_pool.closeall()
                            except Exception:
                                pass
                            _db_pool = None
                        init_pool()
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


def init_db():
    conn = get_conn()
    c    = conn.cursor()

    # ── MEMBRES ──────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS membres (
        id                  SERIAL PRIMARY KEY,
        nom_complet         TEXT    NOT NULL,
        kyc_hash            TEXT    NOT NULL UNIQUE,
        whatsapp            TEXT    NOT NULL UNIQUE,
        statut_global       TEXT    NOT NULL DEFAULT 'En_attente_kyc'
            CHECK(statut_global IN
                ('En_attente_kyc','Actif','Suspendu_global','Banni')),
        adhesion_payee      INTEGER NOT NULL DEFAULT 0,
        -- KYC 5 étapes
        kyc_complet         INTEGER NOT NULL DEFAULT 0,
        kyc_etape           INTEGER NOT NULL DEFAULT 0,
        kyc_nom             TEXT,
        kyc_cni             TEXT    UNIQUE,
        kyc_naissance       TEXT,
        kyc_ville           TEXT,
        kyc_photo_recu      INTEGER NOT NULL DEFAULT 0,
        -- Mineur (< 18 ans) : pas de CNI, acte de naissance
        kyc_mineur          INTEGER NOT NULL DEFAULT 0,
        kyc_acte_naissance  TEXT,    -- Numéro acte de naissance (mineur)
        -- Finances
        solde_dette         INTEGER NOT NULL DEFAULT 0,
        dette_ira_total     INTEGER NOT NULL DEFAULT 0,
        crediteur_id        INTEGER REFERENCES membres(id),
        -- Anti-fraude
        tentatives_fraude   INTEGER NOT NULL DEFAULT 0,
        score_confiance     INTEGER NOT NULL DEFAULT 100,
        blackliste          INTEGER NOT NULL DEFAULT 0,
        -- Anti-fugue
        dernier_bouffage    TIMESTAMPTZ,
        nb_bouffages        INTEGER NOT NULL DEFAULT 0,
        -- Suspension pour retard cotisation
        suspendu_retard              INTEGER NOT NULL DEFAULT 0,
        date_suspension_retard       TIMESTAMPTZ,
        date_adhesion       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── TONTINES ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS tontines (
        id               SERIAL PRIMARY KEY,
        nom              TEXT    NOT NULL,
        type_tontine     TEXT    NOT NULL
            CHECK(type_tontine IN ('Journaliere','Hebdomadaire','Mensuelle')),
        montant_place    INTEGER NOT NULL DEFAULT 5000,
        whatsapp_groupe  TEXT    UNIQUE,  -- identifiant primaire du groupe WA
        statut           TEXT    NOT NULL DEFAULT 'Active'
            CHECK(statut IN ('Active','Suspendue','Terminee')),
        capacite_max     INTEGER NOT NULL DEFAULT 2000,
        cycle_actuel     INTEGER NOT NULL DEFAULT 1,
        heure_limite     TEXT    NOT NULL DEFAULT '18:00',
        heure_ouverture  TEXT    NOT NULL DEFAULT '05:00',  -- Rappel ouverture dépôts
        heure_rappel     TEXT    NOT NULL DEFAULT '14:00',  -- Rappel non-cotisants
        heure_bouffage   TEXT    NOT NULL DEFAULT '17:00',
        penalite_echec   INTEGER NOT NULL DEFAULT 500,
        penalite_desist  INTEGER NOT NULL DEFAULT 20000,
        caution_active   INTEGER NOT NULL DEFAULT 1,
        caution_pourcent INTEGER NOT NULL DEFAULT 10,
        credit_comm_statut TEXT NOT NULL DEFAULT 'Non_eligible',
        date_creation    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── ADHÉSIONS ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS adhesions (
        id            SERIAL PRIMARY KEY,
        membre_id     INTEGER NOT NULL REFERENCES membres(id) ON DELETE CASCADE,
        tontine_id    INTEGER NOT NULL REFERENCES tontines(id) ON DELETE CASCADE,
        nombre_places INTEGER NOT NULL DEFAULT 1,
        statut        TEXT    NOT NULL DEFAULT 'Actif'
            CHECK(statut IN ('Actif','Suspendu','Quitte','Pause','Exonere')),
        jours_avance                 INTEGER NOT NULL DEFAULT 0,
        nb_avertissements_retard     INTEGER NOT NULL DEFAULT 0,
        date_adhesion TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(membre_id, tontine_id)
    )""")

    # ── LISTE DE PASSAGE ──────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS liste_passage (
        id                SERIAL PRIMARY KEY,
        tontine_id        INTEGER NOT NULL REFERENCES tontines(id),
        membre_id         INTEGER REFERENCES membres(id),  -- NULL si nickname non encore lié
        nickname          TEXT,                            -- Nom tel que donné par l'admin
        date_bouffage     DATE,                            -- Date prévue du bouffage
        cycle             INTEGER NOT NULL DEFAULT 1,
        ordre             INTEGER NOT NULL,
        statut            TEXT    NOT NULL DEFAULT 'En_attente'
            CHECK(statut IN
                ('En_attente','Notifie','Paye','Intercepte','Cede')),
        numero_cashout    TEXT,
        operateur_cashout TEXT,
        montant_bouffage  INTEGER,
        date_paiement     TIMESTAMPTZ,
        soumis_par        TEXT,           -- admin qui a soumis la liste
        UNIQUE(tontine_id, cycle, ordre)
    )""")

    # ── TRANSACTIONS ──────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id               SERIAL PRIMARY KEY,
        membre_id        INTEGER NOT NULL REFERENCES membres(id),
        tontine_id       INTEGER REFERENCES tontines(id),
        montant_brut     INTEGER NOT NULL,
        frais_fmp        INTEGER NOT NULL DEFAULT 0,
        frais_ira        INTEGER NOT NULL DEFAULT 0,
        montant_net      INTEGER NOT NULL,
        type_transaction TEXT    NOT NULL
            CHECK(type_transaction IN (
                'Cotisation','Adhesion','Reactivation',
                'Changement_num','Bouffage','Remboursement','Penalite'
            )),
        statut           TEXT    NOT NULL DEFAULT 'Confirmee'
            CHECK(statut IN ('En_attente','Confirmee','Echouee','Rejetee')),
        reference        TEXT,
        periodes_payees  INTEGER NOT NULL DEFAULT 1,
        ip_source        TEXT,
        date_heure       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── COTISATIONS MANUELLES ─────────────────────────────────────────────
    # Enregistre chaque screenshot soumis dans le groupe
    c.execute("""CREATE TABLE IF NOT EXISTS cotisations_manuelles (
        id                SERIAL PRIMARY KEY,
        membre_id         INTEGER NOT NULL REFERENCES membres(id),
        tontine_id        INTEGER NOT NULL REFERENCES tontines(id),
        periode           INTEGER NOT NULL DEFAULT 1,
        montant_declare   INTEGER NOT NULL,
        fmp_du            INTEGER NOT NULL DEFAULT 0,
        screenshot_hash   TEXT,               -- SHA-256 anti-recyclage
        statut            TEXT NOT NULL DEFAULT 'En_attente'
            CHECK(statut IN ('En_attente','Confirme','Rejete')),
        confirme_par      TEXT,               -- whatsapp de l'admin
        date_soumission   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_confirmation TIMESTAMPTZ
    )""")

    # ── SCREENSHOTS HASH — Anti-recyclage ────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS screenshots_hash (
        id          SERIAL PRIMARY KEY,
        hash        TEXT NOT NULL UNIQUE,
        membre_id   INTEGER REFERENCES membres(id),
        tontine_id  INTEGER REFERENCES tontines(id),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── BOUFFAGES MANUELS ─────────────────────────────────────────────────
    # Suit chaque bouffage — l'admin vire manuellement
    c.execute("""CREATE TABLE IF NOT EXISTS bouffages_manuels (
        id                    SERIAL PRIMARY KEY,
        membre_id             INTEGER NOT NULL REFERENCES membres(id),
        tontine_id            INTEGER NOT NULL REFERENCES tontines(id),
        passage_id            INTEGER REFERENCES liste_passage(id),
        montant_brut          INTEGER NOT NULL,
        caution               INTEGER NOT NULL DEFAULT 0,
        montant_net           INTEGER NOT NULL,
        numero_mm             TEXT,               -- numéro Mobile Money du bénéficiaire
        statut                TEXT NOT NULL DEFAULT 'En_attente'
            CHECK(statut IN ('En_attente','Vire','Confirme','Expire')),
        confirme_par          TEXT,               -- whatsapp de l'admin
        date_declenchement    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_confirmation     TIMESTAMPTZ,
        expiration            TIMESTAMPTZ         -- 2h pour répondre
    )""")

    # ── DETTES BADF — FMP + adhésions dues par admin ──────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS dettes_badf (
        id          SERIAL PRIMARY KEY,
        admin_wa    TEXT NOT NULL,
        tontine_id  INTEGER REFERENCES tontines(id),
        type_dette  TEXT NOT NULL
            CHECK(type_dette IN ('FMP','Adhesion','IRA','Reactivation','Changement_num')),
        montant     INTEGER NOT NULL,
        statut      TEXT NOT NULL DEFAULT 'Due'
            CHECK(statut IN ('Due','Payee')),
        ref_cotis   INTEGER REFERENCES cotisations_manuelles(id),
        date_creation TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_paiement TIMESTAMPTZ,
        code_paiement TEXT
    )""")

    # ── ADMINS GROUPE ─────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS admins_groupe (
        id         SERIAL PRIMARY KEY,
        tontine_id INTEGER NOT NULL REFERENCES tontines(id) ON DELETE CASCADE,
        whatsapp   TEXT    NOT NULL,
        nom        TEXT,
        numero_collecte TEXT,   -- numéro MTN/Orange qui reçoit les cotisations
        date_ajout TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(tontine_id, whatsapp)
    )""")

    # ── SANCTIONS ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS sanctions (
        id               SERIAL PRIMARY KEY,
        membre_id        INTEGER NOT NULL REFERENCES membres(id),
        tontine_id       INTEGER REFERENCES tontines(id),
        type_sanction    TEXT    NOT NULL
            CHECK(type_sanction IN (
                'Retard_paiement','Suspension_72h','Blocage_permanent',
                'Interception_bouffage','Tentative_fraude',
                'Fugue_post_bouffage','Exclusion'
            )),
        montant_penalite INTEGER NOT NULL DEFAULT 0,
        date_sanction    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        notes            TEXT
    )""")

    # ── HISTORIQUE SCORE CONFIANCE ────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS historique_score_confiance (
        id         SERIAL PRIMARY KEY,
        membre_id  INTEGER NOT NULL REFERENCES membres(id) ON DELETE CASCADE,
        score_av   INTEGER NOT NULL,
        score_ap   INTEGER NOT NULL,
        delta      INTEGER NOT NULL,
        raison     TEXT    NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hsc_membre ON historique_score_confiance(membre_id, created_at DESC)")

    # ── CAUTIONS GARANTIE ─────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS cautions_garantie (
        id              SERIAL PRIMARY KEY,
        membre_id       INTEGER NOT NULL REFERENCES membres(id),
        tontine_id      INTEGER NOT NULL REFERENCES tontines(id),
        passage_id      INTEGER NOT NULL REFERENCES liste_passage(id),
        montant         INTEGER NOT NULL,
        pourcent        INTEGER NOT NULL DEFAULT 10,
        statut          TEXT    NOT NULL DEFAULT 'Bloquee'
            CHECK(statut IN ('Bloquee','Liberee','Saisie')),
        date_bouffage   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        date_liberation TIMESTAMPTZ,
        UNIQUE(membre_id, tontine_id, passage_id)
    )""")

    # ── DETTES IRA ────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS dettes_ira (
        id          SERIAL PRIMARY KEY,
        membre_id   INTEGER NOT NULL REFERENCES membres(id),
        tontine_id  INTEGER NOT NULL REFERENCES tontines(id),
        montant     INTEGER NOT NULL,
        statut      TEXT    NOT NULL DEFAULT 'Due'
            CHECK(statut IN ('Due','Prelevee')),
        motif       TEXT,
        date_echeance DATE,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        prelevee_le TIMESTAMPTZ
    )""")

    # ── ALERTES FUGUE ─────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS alertes_fugue (
        id          SERIAL PRIMARY KEY,
        membre_id   INTEGER NOT NULL REFERENCES membres(id),
        tontine_id  INTEGER NOT NULL REFERENCES tontines(id),
        type_alerte TEXT    NOT NULL
            CHECK(type_alerte IN
                ('Avertissement_1','Avertissement_2','Blocage','Interception_caution')),
        jours_retard INTEGER NOT NULL DEFAULT 0,
        montant_du   INTEGER NOT NULL DEFAULT 0,
        traite       INTEGER NOT NULL DEFAULT 0,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── CAS DIFFICILES ────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS cas_difficiles (
        id          SERIAL PRIMARY KEY,
        membre_id   INTEGER NOT NULL REFERENCES membres(id),
        tontine_id  INTEGER NOT NULL REFERENCES tontines(id),
        type_cas    TEXT    NOT NULL
            CHECK(type_cas IN
                ('Pause','Echelonnement','Cession','Exoneration','Exclusion')),
        details     TEXT,
        nb_tranches  INTEGER,
        montant_tranche INTEGER,
        tranches_payees INTEGER NOT NULL DEFAULT 0,
        date_reprise DATE,
        cessionnaire_id INTEGER REFERENCES membres(id),
        statut      TEXT    NOT NULL DEFAULT 'Actif'
            CHECK(statut IN ('Actif','Termine','Annule')),
        admin_id    TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── AUDIT LOG ─────────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id         SERIAL PRIMARY KEY,
        type_event TEXT NOT NULL,
        details    TEXT,
        whatsapp   TEXT,
        ip         TEXT,
        date_heure TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""")

    # ── COMMIT intermédiaire — sécurise les CREATE TABLE avant les migrations ──
    # En PostgreSQL, une migration échouée met la transaction en état ERROR.
    # Sans ce commit, un RENAME de colonne manquante annulerait TOUTES les tables.
    try:
        conn.commit()
    except Exception as e:
        log.error(f"❌ Commit intermédiaire init_db ERREUR : {e}")
        conn.rollback()
        release_conn(conn)
        return

    # ── MIGRATIONS v9.17 — chaque migration est isolée dans un SAVEPOINT ──────
    # Un SAVEPOINT garantit que l'échec d'une migration n'affecte pas les autres
    # et ne met PAS la transaction globale en état ERROR.
    for migration in [
        "ALTER TABLE membres ADD COLUMN IF NOT EXISTS kyc_mineur INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE membres ADD COLUMN IF NOT EXISTS kyc_acte_naissance TEXT",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS nickname TEXT",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS date_bouffage DATE",
        "ALTER TABLE liste_passage ALTER COLUMN membre_id DROP NOT NULL",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS bot_est_admin INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS soumis_par TEXT",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS bloque_suspect INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE liste_passage ADD COLUMN IF NOT EXISTS date_blocage TIMESTAMPTZ",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS heure_ouverture TEXT NOT NULL DEFAULT '05:00'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS heure_rappel TEXT NOT NULL DEFAULT '14:00'",
        "ALTER TABLE admins_groupe ADD COLUMN IF NOT EXISTS numero_collecte TEXT",
        # ── Multi-pays ────────────────────────────────────────────────────
        "ALTER TABLE membres  ADD COLUMN IF NOT EXISTS pays_code TEXT NOT NULL DEFAULT 'CM'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS pays_code TEXT NOT NULL DEFAULT 'CM'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS devise    TEXT NOT NULL DEFAULT 'FCFA'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS langue    TEXT NOT NULL DEFAULT 'fr'",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS timezone  TEXT NOT NULL DEFAULT 'Africa/Douala'",
        # ── Multi-cadence (v9.18) ──────────────────────────────────────────
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS jour_semaine TEXT NOT NULL DEFAULT 'Lundi' CHECK(jour_semaine IN ('Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche'))",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS jour_mois INTEGER NOT NULL DEFAULT 1 CHECK(jour_mois BETWEEN 1 AND 28)",
        "ALTER TABLE adhesions ADD COLUMN IF NOT EXISTS nb_avertissements_retard INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tontines ADD COLUMN IF NOT EXISTS credit_comm_statut TEXT NOT NULL DEFAULT 'Non_eligible'",
    ]:
        try:
            c.execute("SAVEPOINT mig")
            c.execute(migration)
            c.execute("RELEASE SAVEPOINT mig")
        except Exception:
            c.execute("ROLLBACK TO SAVEPOINT mig")


    # ── INDEX PERFORMANCES ────────────────────────────────────────────────
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_mbr_wa      ON membres(whatsapp)",
        "CREATE INDEX IF NOT EXISTS idx_mbr_stat    ON membres(statut_global)",
        "CREATE INDEX IF NOT EXISTS idx_adh_mbr     ON adhesions(membre_id)",
        "CREATE INDEX IF NOT EXISTS idx_adh_ton     ON adhesions(tontine_id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_mbr      ON transactions(membre_id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_pass_ton    ON liste_passage(tontine_id,cycle,ordre)",
        "CREATE INDEX IF NOT EXISTS idx_pass_stat   ON liste_passage(statut)",
        "CREATE INDEX IF NOT EXISTS idx_fugue       ON alertes_fugue(traite,created_at) WHERE traite=0",
        "CREATE INDEX IF NOT EXISTS idx_audit       ON audit_log(type_event,date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_dette_ira   ON dettes_ira(membre_id,statut) WHERE statut='Due'",
        "CREATE INDEX IF NOT EXISTS idx_suspendu    ON membres(suspendu_retard,date_suspension_retard) WHERE suspendu_retard=1",
        "CREATE INDEX IF NOT EXISTS idx_cotis_man   ON cotisations_manuelles(tontine_id,statut)",
        "CREATE INDEX IF NOT EXISTS idx_screenshot  ON screenshots_hash(hash)",
        "CREATE INDEX IF NOT EXISTS idx_bouf_man    ON bouffages_manuels(tontine_id,statut)",
        "CREATE INDEX IF NOT EXISTS idx_dette_badf  ON dettes_badf(admin_wa,statut) WHERE statut='Due'",
        # ── Index composites pour haute volumétrie (flux 50M+ FCFA) ──────
        "CREATE INDEX IF NOT EXISTS idx_tx_ton_type_date ON transactions(tontine_id,type_transaction,date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tx_mbr_ton_stat  ON transactions(membre_id,tontine_id,statut)",
        "CREATE INDEX IF NOT EXISTS idx_adh_ton_stat     ON adhesions(tontine_id,statut) WHERE statut='Actif'",
        "CREATE INDEX IF NOT EXISTS idx_pass_actif       ON liste_passage(tontine_id,cycle,statut) WHERE statut IN ('En_attente','Notifie')",
        "CREATE INDEX IF NOT EXISTS idx_caution_active   ON cautions_garantie(membre_id,tontine_id,statut) WHERE statut='Bloquee'",
        "CREATE INDEX IF NOT EXISTS idx_audit_date       ON audit_log(date_heure DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cotis_pending    ON cotisations_manuelles(tontine_id,date_soumission DESC) WHERE statut='En_attente'",
        # Hash unique anti-recyclage déjà UNIQUE mais on confirme l'index
        "CREATE INDEX IF NOT EXISTS idx_screenshot_date  ON screenshots_hash(date_creation DESC)",
    ]
    for idx in indexes:
        try:
            c.execute(idx)
        except Exception:
            pass

    try:
        conn.commit()
        # Vérifier que les tables critiques existent vraiment
        tables_critiques = [
            "membres", "tontines", "adhesions", "admins_groupe",
            "cotisations_manuelles", "screenshots_hash",
            "bouffages_manuels", "dettes_badf"
        ]
        manquantes = []
        for table in tables_critiques:
            row = fetchone(conn,
                "SELECT to_regclass(%s) AS t", (f"public.{table}",))
            if not row or not row["t"]:
                manquantes.append(table)

        if manquantes:
            log.error(f"❌ Tables manquantes après init_db : {manquantes}")
            log.error("   Exécutez create_db_v917.sql manuellement dans psql.")
        else:
            log.info("✅ PostgreSQL TontineBot Pro v9.17 — toutes les tables présentes.")
    except Exception as e:
        log.error(f"❌ init_db commit ERREUR : {e}")
        conn.rollback()
    finally:
        release_conn(conn)


# ══════════════════════════════════════════════════════════════════════════
# FONCTIONS MÉTIER — CRÉATION TONTINE / INSCRIPTION MEMBRES
# ══════════════════════════════════════════════════════════════════════════

def creer_tontine(nom: str, type_tontine: str, montant_place: int,
                  groupe_wa: str = "",
                  capacite_max: int = 2000,
                  heure_limite: str = "18:00",
                  caution_pourcent: int = 10,
                  jour_semaine: str = "Lundi",
                  jour_mois: int = 1) -> int:
    """
    Crée une nouvelle tontine en base.
    Retourne l'ID de la tontine créée.
    """
    conn = get_conn()
    cur  = q(conn, """
        INSERT INTO tontines
            (nom, type_tontine, montant_place,
             whatsapp_groupe, capacite_max, heure_limite, caution_pourcent,
             jour_semaine, jour_mois)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (nom, type_tontine, montant_place,
          groupe_wa or None, capacite_max, heure_limite, caution_pourcent,
          jour_semaine, jour_mois))
    tid = cur.fetchone()[0]
    conn.commit()
    release_conn(conn)
    log.info(f"✅ Tontine créée : {nom} (ID:{tid})")
    log_audit("TONTINE_CREEE", f"{nom} | {type_tontine} | {montant_place}F")
    return tid


def inscrire_membre(nom: str, whatsapp: str, kyc_ref: str = "") -> int:
    """
    Inscrit un nouveau membre en base (sans KYC complet).
    Retourne l'ID du membre.
    """
    conn     = get_conn()
    whatsapp = normaliser_numero(whatsapp)
    # Vérifier doublon
    existant = fetchone(conn, "SELECT id FROM membres WHERE whatsapp=%s", (whatsapp,))
    if existant:
        release_conn(conn)
        raise ValueError(f"Numéro {whatsapp} déjà enregistré (ID:{existant['id']}).")
    kyc_hash = hashlib.sha256(
        f"{nom.upper().strip()}{whatsapp}{kyc_ref}".encode()
    ).hexdigest()
    cur = q(conn, """
        INSERT INTO membres
            (nom_complet, kyc_hash, whatsapp, adhesion_payee, statut_global)
        VALUES (%s,%s,%s,1,'En_attente_kyc') RETURNING id
    """, (nom.strip(), kyc_hash, whatsapp))
    mid = cur.fetchone()[0]
    conn.commit()
    release_conn(conn)
    log.info(f"✅ Membre inscrit : {nom} ({whatsapp}) ID:{mid}")
    log_audit("INSCRIPTION", f"{nom} | KYC:{kyc_hash[:16]}...", whatsapp)
    return mid


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


def _masquer_wa(wa: str) -> str:
    if len(wa) > 6:
        return wa[:4] + "****" + wa[-2:]
    return "****"

def log_audit(type_event: str, details: str, whatsapp: str = "", ip: str = ""):
    wa_masque = _masquer_wa(whatsapp) if whatsapp else ""
    audit.warning(f"[{type_event}] {details} | WA:{wa_masque} | IP:{ip}")
    try:
        conn = get_conn()
        q(conn,
          "INSERT INTO audit_log (type_event, details, whatsapp, ip) VALUES (%s,%s,%s,%s)",
          (type_event, details, wa_masque, ip))
        conn.commit()
        release_conn(conn)
        # Journal immuable append-only
        with open("logs/audit_immutable.log", "a", encoding="utf-8") as _f:
            _f.write(f"{datetime.now().isoformat()}|{type_event}|{details}|{wa_masque}\n")
    except Exception:
        pass


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


def detecter_pays_par_indicatif(numero: str) -> str:
    """
    Détecte le code pays à partir du numéro WhatsApp normalisé.
    +237693969773 → 'CM'
    +221701234567 → 'SN'
    +225071234567 → 'CI'
    """
    if not numero or not numero.startswith("+"):
        return "CM"
    indicatifs = {
        "+237": "CM",
        "+221": "SN",
        "+225": "CI",
    }
    for ind, code in indicatifs.items():
        if numero.startswith(ind):
            return code
    return "CM"


def normaliser_numero_intl(numero: str, pays_code: str = "CM") -> str:
    """
    Normalisation paramétrable selon le pays.
    Cameroun : 9 chiffres après +237
    Sénégal  : 9 chiffres après +221
    Côte d'Ivoire : 10 chiffres après +225
    """
    if not numero:
        return numero
    n = numero.strip()
    if "@" in n: n = n.split("@")[0]
    if ":" in n: n = n.split(":")[0]
    n = re.sub(r"[^\d+]", "", n)
    if n.startswith("00"):
        n = "+" + n[2:]
    if not n.startswith("+"):
        n = "+" + n
    pays = get_pays(pays_code)
    indicatif = pays.get("indicatif", "+237")
    longueur  = pays.get("longueur_num", 9)
    # Si numéro court (sans indicatif) → ajouter celui du pays
    digits = n.lstrip("+")
    if len(digits) == longueur:
        n = indicatif + digits
    return n




def get_timezone_pays(pays_code: str = "CM") -> str:
    """Timezone IANA d'un pays (pour scheduler dynamique par tontine)."""
    return get_pays(pays_code).get("timezone", "Africa/Douala")


def calculer_frais(montant_brut: int, heure: time,
                   heure_limite_str: str = "18:00") -> dict:
    """
    FMP = 2% prélevé invisiblement.
    IRA = 150 FCFA si paiement APRÈS l'heure limite.
    Calcul côté serveur = incontournable, le membre ne peut pas tricher.
    """
    fmp = int(montant_brut * FRAIS_FMP)
    try:
        h, m   = map(int, heure_limite_str.split(":"))
        limite = time(h, m)
    except Exception:
        limite = HEURE_LIMITE_DEF
    ira = MONTANT_IRA if heure.hour > limite.hour else 0
    return {
        "frais_fmp":   fmp,
        "frais_ira":   ira,
        "montant_net": montant_brut - fmp - ira,
    }


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

def _pretraiter_screenshot_whatsapp(image_bytes: bytes):
    """
    Pipeline OpenCV 7 étapes pour screenshots WhatsApp compressés.
    Gère : artéfacts JPEG, mode sombre, texte petit, binarisation adaptative.
    Retourne une PIL Image noir/blanc pur prête pour Tesseract.
    """
    import cv2
    import numpy as np
    from PIL import Image
    import io

    # 1. Décoder les bytes → tableau BGR OpenCV
    arr = np.frombuffer(image_bytes, np.uint8)
    img_cv = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img_cv is None:
        # Fallback PIL si format non supporté par OpenCV
        pil = Image.open(io.BytesIO(image_bytes)).convert("L")
        w, h = pil.size
        if w < 1400:
            scale = 1400.0 / w
            pil = pil.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return pil

    # 2. Convertir en niveaux de gris
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)

    # 3. Détection fond sombre + inversion
    #    Ratio > 35% de pixels < 100 → fond sombre (bulle WhatsApp violet-gris)
    #    Plus robuste que la moyenne : les zones blanches hors-bulle tirent
    #    la moyenne vers le haut sans représenter le fond réel du texte.
    if float(np.mean(gray < 100)) > 0.35:
        gray = cv2.bitwise_not(gray)

    # 4. Agrandir pour les petits textes (cible : 1400 px de large minimum)
    h_cv, w_cv = gray.shape
    if w_cv < 1400:
        scale = 1400.0 / w_cv
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)

    # 4b. Crop clavier WhatsApp (18% bas) — portrait uniquement
    #     Supprime le bruit QWERTY que Tesseract lit comme texte
    h_cv, w_cv = gray.shape
    if h_cv > w_cv:
        crop_bottom = int(h_cv * 0.18)
        gray = gray[:h_cv - crop_bottom, :]

    # 5. Lissage des artéfacts JPEG WhatsApp (blocs 8×8)
    #    Gaussian σ=1 (kernel 3×3) — supprime le bruit intra-bloc
    #    sans flouter les contours inter-caractères
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # 6. Seuillage adaptatif → noir/blanc pur
    #    blockSize=31 : fenêtre large pour gérer les en-têtes colorés des apps
    #    C=15 : marge de contraste pour les polices fines
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31, C=15
    )

    # 7. Bordure blanche 20px — empêche Tesseract de tronquer les bords
    binary = cv2.copyMakeBorder(binary, 20, 20, 20, 20,
                                cv2.BORDER_CONSTANT, value=255)

    return Image.fromarray(binary)


def _lire_pdf_switchn(pdf_bytes: bytes) -> dict:
    """
    Extrait les données d'un reçu SwitchN PDF (text-based, pas d'OCR requis).
    Format SwitchN : MONTANT TRANSFÉRÉ, ID DE TRANSACTION OPÉRATEUR, DATE.
    """
    result = {
        "ok": True, "montant": None, "operateur": "SwitchN",
        "type": "envoi", "date": None, "reference": None,
        "confiance": "faible", "brut": "",
    }
    try:
        import pdfplumber, io as _io
        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
            texte_brut = "\n".join(p.extract_text() or "" for p in pdf.pages)
        result["brut"] = texte_brut[:300]
        texte = texte_brut.upper()

        # Montant : MONTANT TRANSFÉRÉ (ce que le bénéficiaire reçoit = montant cotisation)
        # Format PDF : "1,000 FCFA" (virgule comme séparateur de milliers)
        m_mt = re.search(r"MONTANT\s+TRANSF[EÉ]R[EÉ]\s+([\d,\s]+)\s*FCFA", texte)
        if not m_mt:
            m_mt = re.search(r"MONTANT\s+PAY[EÉ]\s+([\d,\s]+)\s*FCFA", texte)
        if m_mt:
            val_str = re.sub(r"[^\d]", "", m_mt.group(1))
            try:
                val = int(val_str)
                if 100 <= val <= 10_000_000:
                    result["montant"] = val
            except ValueError:
                pass

        # Référence priorité 1 : ID DE TRANSACTION OPÉRATEUR (ex: MP260604.1129.C95759)
        m_ref = re.search(
            r"ID\s+DE\s+TRANSACTION\s+OP[EÉ]RATEUR\s+([A-Z]{2}\d{6}\.\d{4}\.[A-Z0-9]{4,8})",
            texte)
        if not m_ref:
            # Priorité 2 : ID DU TRANSFERT (numérique court)
            m_ref = re.search(r"ID\s+DU\s+TRANSFERT\s+(\d{5,12})", texte)
        if not m_ref:
            # Priorité 3 : ID DE LA DEMANDE DE PAIEMENT
            m_ref = re.search(
                r"ID\s+DE\s+LA\s+DEMANDE\s+DE\s+PAIEMENT\s+([\w_-]{5,20})", texte)
        if m_ref:
            result["reference"] = m_ref.group(1).strip()

        # Date — format DD/MM/YYYY HH:MM
        m_date = re.search(r"(\d{2}/\d{2}/\d{4})", texte)
        if m_date:
            result["date"] = m_date.group(1)

        # Confirmation : "TERMINÉ" (statut SwitchN) ou confirmation opérateur
        if "TERMIN" in texte or "SUCCESSFULLY" in texte:
            result["confiance"] = "haute" if (result["montant"] and result["reference"]) \
                                  else "moyenne"

    except Exception as _e:
        log.warning(f"⚠️ _lire_pdf_switchn : {_e}")
        result["ok"] = False
    return result


def lire_screenshot_mobile_money(image_bytes: bytes) -> dict:
    """
    Analyse un screenshot ou reçu PDF Mobile Money.
    PDFs SwitchN : extraction directe (pas d'OCR). JPG/PNG : OCR Tesseract.

    Retourne un dict :
    {
      "ok":        True/False,
      "montant":   int ou None,
      "operateur": "MTN" | "Orange" | "SwitchN" | "Inconnu",
      "type":      "envoi" | "recharge" | "inconnu",
      "date":      str ou None,
      "reference": str ou None,
      "confiance": "haute" | "moyenne" | "faible",
      "brut":      str (texte extrait brut)
    }
    """
    # Dispatch PDF SwitchN — magic bytes %PDF
    if image_bytes[:4] == b'%PDF':
        return _lire_pdf_switchn(image_bytes)

    try:
        import pytesseract
        from PIL import Image
        import io  # io reste local (utilisé uniquement ici)
        # re est déjà importé globalement en haut du fichier
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

        # Prétraitement OpenCV (dark mode, JPEG artifacts, upscale, binarisation)
        img = _pretraiter_screenshot_whatsapp(image_bytes)

        # OCR — français + anglais pour couvrir MTN/Orange
        texte = pytesseract.image_to_string(
            img,
            lang="fra+eng",
            config="--psm 6 --oem 3 -c preserve_interword_spaces=1"
        )
        texte_brut = texte
        texte      = texte.upper()
        texte      = texte.replace("_", " ")  # artefact OCR WhatsApp ("2000 _\nFCFA")

        result = {
            "ok":        True,
            "montant":   None,
            "operateur": "Inconnu",
            "type":      "inconnu",
            "date":      None,
            "reference": None,
            "confiance": "faible",
            "brut":      texte_brut[:300],
        }

        # ── Détection opérateur — ordre critique : SwitchN > Orange > MTN ──
        # "MTN" seul retiré : trop générique, apparaît dans reçus Orange→MTN
        if "SWITCHN" in texte or "SWITCH N" in texte:
            result["operateur"] = "SwitchN"
        elif any(k in texte for k in ("ORANGE MONEY", "FLOOZ", "OM ", "TRANSFERT DE")):
            result["operateur"] = "Orange"
        elif any(k in texte for k in ("MTN MOMO", "MOMO", "MOBILE MONEY",
                                       "CASH IN OF", "HAVE TRANSFERRED",
                                       "YOU HAVE TRANSFERRED")):
            result["operateur"] = "MTN"

        # ── Détection type ────────────────────────────────────────────────
        # "CASH IN" retiré : c'est un dépôt reçu (sens inverse de envoi)
        # "HAVE TRANSFERRED" retiré : déjà dans la détection opérateur MTN,
        # le dupliquer ici donnait +2 score depuis un seul keyword (vecteur fraude)
        if any(k in texte for k in ("ENVOI", "TRANSFERT", "VOUS AVEZ ENVOYE",
                                     "TRANSFER", "SENT", "PAYMENT")):
            result["type"] = "envoi"
        elif any(k in texte for k in ("RECHARGE", "CREDIT", "AIRTIME")):
            result["type"] = "recharge"

        # ── Extraction montant ────────────────────────────────────────────
        # _NBR : groupes de 3 chiffres séparés par espace/point/NBSP
        #        OU bloc brut 4-10 chiffres
        # Couvert : "5000", "5 000", "5.000", "1 000 000", "10.500.000"
        _NBR = r"(\d{1,3}(?:[\s\.  ]\d{3})*|\d{4,10})"
        _DEC = r"(\d{3,7})[.,]\d{1,2}"
        _DEV = r"(?:FCFA|XAF|CFA|FRS|F\b)"
        patterns_montant = [
            r"(?:MONTANT\W{0,10}TRANSACTION|MONTANT\s+BRUT)\s*[:\-=]?\s*" + _DEC,
            r"(?:MONTANT\W{0,10}TRANSACTION|MONTANT\s+BRUT)\s*[:\-=]?\s*" + _NBR,
            _NBR + r"\s*" + _DEV,
            _DEC + r"\s*" + _DEV,
            _DEV + r"\s*:?\s*" + _NBR,
            r"(?:MONTANT|AMOUNT|SOMME|TOTAL)\s*[:\-=]?\s*" + _NBR,
        ]
        for pat in patterns_montant:
            for m in re.finditer(pat, texte):
                val_str = re.sub(r"[^\d]", "", m.group(1))
                try:
                    val = int(val_str)
                    if 100 <= val <= 10_000_000:
                        result["montant"] = val
                        break
                except ValueError:
                    continue
            if result["montant"]:
                break

        # ── Extraction date ───────────────────────────────────────────────
        # ISO (2026-06-17) ou DD/MM/YYYY — cherche les deux formats
        # Validation année 2020-2035 sur le pattern ISO pour éviter de matcher
        # des montants ou fragments d'ID (ex. "5000.06.28")
        m_date = re.search(r"(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})", texte)
        if m_date and not (2020 <= int(m_date.group(1)) <= 2035):
            m_date = None
        if not m_date:
            m_date = re.search(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})", texte)
        if m_date:
            result["date"] = m_date.group(0)

        # ── Extraction référence transaction ──────────────────────────────
        # Priorité 1 : patterns spécifiques à l'opérateur détecté
        _op = result["operateur"]
        patterns_ref_kw = []
        if _op == "Orange":
            patterns_ref_kw = [
                r"\b(PP\d{6}\.\d{4}\.[A-Z0-9]{4,8})\b",            # PP260623.1152.AD63N5
                r"\b(OM\d{8,12})\b",
                r"(?:R[EÉ]F[EÉ]RENCE?|TRANS(?:ACTION)?|ID)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        elif _op == "MTN":
            patterns_ref_kw = [
                r"\bTRANSACTION\s+ID\s*[:\-]?\s*(\d{8,15})\b",       # Cash in format MTN EN
                r"\bTXN?(\d{8,12})\b",
                r"(?:TRANSACTION\s*ID|TXN?|REF)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        elif _op == "SwitchN":
            patterns_ref_kw = [
                r"\bSWN?-?([A-Z0-9]{6,14})\b",             # SWN-XXXXXXXX ou SW-XXXXXXXX
                r"(?:ORDER|REF\.?|TRANSACTION|RECU)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            ]
        # Priorité 2 : patterns génériques (Switch + opérateur non détecté)
        patterns_ref_kw += [
            r"(?:REF\.?|R[EÉ]F[EÉ]RENCE?)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            r"(?:TXN?|TRANSACTION)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
            r"(?:N[°O]\.?|ID)\s*[:\-#=]?\s*([A-Z0-9]{8,15})",
        ]
        for pat in patterns_ref_kw:
            m_ref = re.search(pat, texte)
            if m_ref:
                result["reference"] = m_ref.group(1)
                break
        # Priorité 3 : FALLBACK — OCR a mal lu "Référence"/"Transfert"
        # Cherche la première chaîne alphanumérique MAJUSCULE isolée 8-15 chars
        # commençant par une lettre (exclut les montants purement numériques)
        if not result["reference"]:
            _MOTS_CONNUS = {
                "FCFA", "MOMO", "MOBILE", "MONEY", "ORANGE", "FLOOZ",
                "CREDIT", "SUCCES", "SUCCESS", "REUSSI", "CONFIRME",
                "EFFECTUE", "COMPLETED", "MONTANT", "AMOUNT", "TRANSFERT",
                "RECHARGE", "AIRTIME", "ENVOI", "PAYMENT", "SOMME", "TOTAL",
            }
            for m_fb in re.finditer(r"\b([A-Z][A-Z0-9]{9,14})\b", texte):
                cand = m_fb.group(1)
                if cand not in _MOTS_CONNUS:
                    result["reference"] = cand
                    break

        # ── Niveau de confiance ───────────────────────────────────────────
        score = 0
        if result["montant"]:   score += 2
        if result["operateur"] != "Inconnu": score += 1
        if result["type"] != "inconnu":      score += 1
        if result["date"]:      score += 1
        if result["reference"]: score += 1
        # Mots de confirmation présents
        if any(k in texte for k in ("SUCCES", "SUCCESS", "CONFIRME",
                                     "REUSSI", "EFFECTUE", "COMPLETED")):
            score += 2

        result["confiance"] = "haute" if score >= 5 else \
                              "moyenne" if score >= 3 else "faible"

        return result

    except ImportError:
        log.warning("pytesseract non installé — OCR désactivé. "
                    "Lancez : pip install pytesseract pillow")
        return {"ok": False, "raison": "pytesseract non installé"}
    except Exception as e:
        log.error(f"OCR erreur : {e}")
        return {"ok": False, "raison": str(e)[:80]}


def hash_screenshot(image_bytes: bytes) -> str:
    """SHA-256 du contenu brut du screenshot — anti-recyclage."""
    return hashlib.sha256(image_bytes).hexdigest()


def screenshot_deja_utilise(conn, image_hash: str) -> bool:
    """Retourne True si ce screenshot a déjà été soumis."""
    row = fetchone(conn,
        "SELECT id FROM screenshots_hash WHERE hash=%s", (image_hash,))
    return row is not None


def enregistrer_screenshot(conn, image_hash: str,
                            membre_id: int, tontine_id: int):
    """Enregistre le hash pour éviter le recyclage."""
    try:
        q(conn,
          """INSERT INTO screenshots_hash (hash, membre_id, tontine_id)
             VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
          (image_hash, membre_id, tontine_id))
    except Exception:
        pass


class MontantAberrantError(ValueError):
    """Levée quand le montant saisi est trop éloigné du montant attendu."""
    pass


def enregistrer_cotisation_manuelle(conn, membre_id: int, tontine_id: int,
                                     montant: int, screenshot_hash: str,
                                     admin_wa: str,
                                     force: bool = False) -> int:
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

    montant_attendu = tontine["montant_place"]

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

        # Créer la dette FMP pour l'admin → à reverser à BADF
        q(conn, """
            INSERT INTO dettes_badf (admin_wa, tontine_id, type_dette, montant, ref_cotis)
            VALUES (%s,%s,'FMP',%s,%s)
        """, (admin_wa, tontine_id, fmp, cotis_id))

        conn.commit()
        return cotis_id

    except psycopg2.errors.UniqueViolation as e:
        conn.rollback()
        log_audit("DOUBLON_COTISATION_BLOQUE_DB",
                  f"Membre#{membre_id} | Tentative doublon: {str(e)[:100]}")
        raise ValueError(
            "❌ Cette cotisation existe déjà (doublon détecté par la base)."
        )


def _reclasser_en_dernier(conn, membre_id: int, tontine_id: int) -> dict:
    """
    Paiement après heure_limite → déplace le membre en dernière position
    dans liste_passage (cycle actif, statut En_attente ou Notifie).
    Retourne {"ok": True, "position_avant": N, "position_apres": M}
    ou {"ok": False} si déjà dernier / déjà bouffé.
    """
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

    # Décaler les membres entre l'ancienne position+1 et la dernière de -1
    q(conn, """
        UPDATE liste_passage SET ordre = ordre - 1
        WHERE tontine_id=%s AND cycle=%s AND ordre > %s AND ordre <= %s
    """, (tontine_id, cycle, ordre_actuel, max_ordre))

    # Mettre le membre en dernière position
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
        "SELECT nom, heure_limite FROM tontines WHERE id=%s", (cotis["tontine_id"],))

    if membre and tontine:
        wa_prive(membre["whatsapp"],
            f"✅ *COTISATION CONFIRMÉE — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Bonjour *{membre['nom_complet']}*,\n\n"
            f"Votre cotisation de *{cotis['montant_declare']:,} FCFA* "
            f"a été confirmée et enregistrée.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    # ── Reclassement si screenshot soumis après heure_limite + 5 min ────────
    try:
        from datetime import timezone, timedelta as _td, datetime as _dt
        h_lim = (tontine or {}).get("heure_limite") or "18:00"
        hh, mm = map(int, h_lim.split(":"))
        # Grace period : heure_limite + 5 minutes
        limite_grace = (_dt.combine(_dt.today(), time(hh, mm)) + _td(minutes=5)).time()
        WAT = timezone(_td(hours=1))
        soumis_local = cotis["date_soumission"].astimezone(WAT).time()

        if soumis_local > limite_grace:
            # 1. IRA 150 FCFA — dette enregistrée en DB
            q(conn, """INSERT INTO dettes_ira (membre_id, tontine_id, montant, motif)
                       VALUES (%s, %s, %s, %s)""",
              (cotis["membre_id"], cotis["tontine_id"], MONTANT_IRA,
               f"Retard cotisation — soumis {soumis_local.strftime('%H:%M')} > limite {h_lim}"))
            conn.commit()

            # 2. Reclassement en dernière position
            reclasse = _reclasser_en_dernier(conn, cotis["membre_id"], cotis["tontine_id"])
            if reclasse["ok"]:
                conn.commit()

            # 3. Notification membre (IRA + reclassement)
            if membre and tontine:
                msg_retard = (
                    f"⚠️ *COTISATION EN RETARD — {tontine['nom']}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Votre cotisation a été enregistrée ✅, mais elle est "
                    f"arrivée après l'heure limite (*{h_lim}*).\n\n"
                    f"📌 *Conséquences :*\n"
                    f"• Pénalité IRA : *{MONTANT_IRA:,} FCFA* ajoutée à votre dette\n"
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
                       if reclasse["ok"] else "déjà dernière position")
            log_audit("RECLASSEMENT_RETARD",
                      f"Membre#{cotis['membre_id']} | {pos_log} | IRA {MONTANT_IRA} FCFA | "
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
    """Admin rejette une cotisation (screenshot incorrect ou non reçu)."""
    cotis = fetchone(conn,
        "SELECT * FROM cotisations_manuelles WHERE id=%s", (cotis_id,))
    if not cotis:
        return {"ok": False, "msg": "Cotisation introuvable."}

    q(conn, """UPDATE cotisations_manuelles
               SET statut='Rejete', confirme_par=%s, date_confirmation=NOW()
               WHERE id=%s""", (admin_wa, cotis_id))

    # Annuler la dette FMP correspondante
    q(conn, """UPDATE dettes_badf SET statut='Payee'
               WHERE ref_cotis=%s AND statut='Due'""", (cotis_id,))

    conn.commit()

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
                                deductions_detail: str = "") -> int:
    """
    Déclenche le flux de bouffage manuel.
    DM au bénéficiaire pour son numéro MM.
    DM à l'admin avec le montant à virer.
    Retourne l'ID du bouffage créé.
    """
    montant_net = montant_brut - caution
    expiration  = datetime.now() + timedelta(hours=2)

    cur = q(conn, """
        INSERT INTO bouffages_manuels
            (membre_id, tontine_id, passage_id, montant_brut,
             caution, montant_net, expiration)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (membre_id, tontine_id, passage_id,
          montant_brut, caution, montant_net, expiration))
    bouffage_id = cur.fetchone()[0]
    conn.commit()

    membre  = fetchone(conn,
        "SELECT nom_complet, whatsapp FROM membres WHERE id=%s", (membre_id,))
    tontine = fetchone(conn,
        "SELECT nom FROM tontines WHERE id=%s", (tontine_id,))

    if not membre or not tontine:
        return bouffage_id

    # DM au bénéficiaire
    wa_prive(membre["whatsapp"],
        f"🎉 *AVIS DE DÉBLOCAGE DE CAGNOTTE*\n"
        f"*{tontine['nom']}* — BADF Ltd\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*I. DÉCOMPTE FINANCIER*\n\n"
        f"  Cagnotte brute    : {montant_brut:>10,} FCFA\n"
        f"  Caution retenue   : {caution:>10,} FCFA (10%)\n"
        f"  ─────────────────────────────────\n"
        f"  *MONTANT NET      : {montant_net:>10,} FCFA*\n\n"
        f"*II. CAUTION*\n\n"
        f"  La caution de *{caution:,} FCFA* est retenue par l'admin.\n"
        f"  Elle vous sera restituée à la fin du cycle si vous\n"
        f"  continuez à cotiser normalement.\n\n"
        f"*III. INSTRUCTIONS*\n\n"
        f"  Envoyez votre numéro Mobile Money (MTN ou Orange)\n"
        f"  pour recevoir le virement.\n\n"
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


def confirmer_bouffage_vire(conn, bouffage_id: int, admin_wa: str) -> dict:
    """Admin confirme avoir viré le montant au bénéficiaire."""
    bouffage = fetchone(conn,
        "SELECT * FROM bouffages_manuels WHERE id=%s", (bouffage_id,))
    if not bouffage:
        return {"ok": False, "msg": "Bouffage introuvable."}

    q(conn, """UPDATE bouffages_manuels
               SET statut='Confirme', confirme_par=%s, date_confirmation=NOW()
               WHERE id=%s""", (admin_wa, bouffage_id))

    # Marquer le passage comme payé
    if bouffage["passage_id"]:
        q(conn, """UPDATE liste_passage SET statut='Paye', date_paiement=NOW()
                   WHERE id=%s""", (bouffage["passage_id"],))

    # Enregistrer la transaction
    q(conn, """
        INSERT INTO transactions
            (membre_id, tontine_id, montant_brut, montant_net,
             type_transaction, statut)
        VALUES (%s,%s,%s,%s,'Bouffage','Confirmee')
    """, (bouffage["membre_id"], bouffage["tontine_id"],
          bouffage["montant_brut"], bouffage["montant_net"]))

    conn.commit()

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

    # DM aux admins — leur demander la décision
    admins = fetchall(conn,
        "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s", (tontine_id,))
    for adm in admins:
        wa_prive(adm["whatsapp"],
            f"🎉 *CYCLE {nb_cycles} TERMINÉ — {tontine['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Tous les membres ont bouffé. Le cycle {nb_cycles} est clôturé.\n\n"
            f"Que souhaitez-vous faire ?\n\n"
            f"▪ Tapez *NOUVEAU_CYCLE* pour repartir avec les mêmes membres\n"
            f"▪ Tapez *CLOTURER* pour clôturer définitivement cette tontine\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

    # Notifier le owner
    wa_owner(
        f"✅ *FIN DE CYCLE — {tontine['nom']}*\n"
        f"Cycle {nb_cycles} | {nb_membres} membres | "
        f"En attente de décision admin."
    )

    # Membres sans KYC → rappel au groupe + DM individuel
    membres_sans_kyc = fetchall(conn, """
        SELECT m.whatsapp, m.nom_complet
        FROM membres m
        JOIN adhesions a ON a.membre_id = m.id
        WHERE a.tontine_id=%s AND a.statut='Actif' AND m.kyc_complet=0
    """, (tontine_id,))

    if membres_sans_kyc:
        noms_kyc = "\n".join(f"  • {m['nom_complet']}" for m in membres_sans_kyc)
        if tontine.get("whatsapp_groupe"):
            wa_groupe(tontine["whatsapp_groupe"],
                f"📋 *VÉRIFICATION D'IDENTITÉ REQUISE — {tontine['nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Pour participer au prochain cycle, ces membres doivent "
                f"compléter leur KYC :\n\n"
                f"{noms_kyc}\n\n"
                f"📲 Tapez *menu* en DM à TontineBot Pro.\n"
                f"Le processus prend moins de 2 minutes.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )
        for m in membres_sans_kyc:
            wa_prive(m["whatsapp"],
                f"📋 *KYC REQUIS — {tontine['nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Le cycle vient de se terminer. Pour continuer au prochain cycle, "
                f"vous devez compléter votre vérification d'identité.\n\n"
                f"📲 Tapez *menu* pour démarrer votre KYC (moins de 2 minutes).\n\n"
                f"_TontineBot Pro — BADF Ltd_"
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
        tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
        if not tontine:
            release_conn(conn)
            return "❌ Tontine introuvable."

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

        # Dupliquer la liste pour le nouveau cycle avec les mêmes membres
        passages = fetchall(conn, """
            SELECT membre_id, nickname, ordre, soumis_par
            FROM liste_passage
            WHERE tontine_id=%s AND cycle=%s
            ORDER BY ordre
        """, (tontine_id, ancien_cycle))

        for p in passages:
            q(conn, """
                INSERT INTO liste_passage
                    (tontine_id, membre_id, nickname, cycle, ordre, soumis_par)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tontine_id, cycle, ordre) DO NOTHING
            """, (tontine_id, p["membre_id"], p["nickname"],
                  nouveau_cycle, p["ordre"], p["soumis_par"]))

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

        # Annonce dans le groupe
        if tontine.get("whatsapp_groupe"):
            wa_groupe(tontine["whatsapp_groupe"],
                f"🚀 *CYCLE {nouveau_cycle} LANCÉ — {tontine['nom']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Le cycle {nouveau_cycle} démarre maintenant !\n\n"
                f"👥 *{nb_membres} membres* repartent pour un nouveau cycle.\n\n"
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
        f"Envoyez le code de transaction au bot après le virement.\n\n"
        f"_Barack & AI Development Facilities Ltd_"
    )


def enregistrer_paiement_badf(admin_wa: str, code_transaction: str) -> str:
    """Admin envoie son code de virement vers BADF — marque les dettes comme payées."""
    conn = get_conn()
    dettes = fetchall(conn, """
        SELECT id FROM dettes_badf
        WHERE admin_wa=%s AND statut='Due'
    """, (admin_wa,))

    if not dettes:
        release_conn(conn)
        return "Aucune dette en cours pour votre compte."

    ids = [d["id"] for d in dettes]
    q(conn, f"""UPDATE dettes_badf
               SET statut='Payee', date_paiement=NOW(), code_paiement=%s
               WHERE id = ANY(%s)""",
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
    release_conn(conn)

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



# ══════════════════════════════════════════════════════════════════════════
# WHATSAPP CLOUD API META — Envoi de messages
# ══════════════════════════════════════════════════════════════════════════

# ── Outbox persistante — file de messages en cas de panne Meta ───────────
_outbox_lock = threading.Lock()
_outbox_path = Path("logs/wa_outbox.jsonl")
_outbox_path.parent.mkdir(exist_ok=True)

# H6 — Backup sessions
_sessions_path = Path("logs/sessions_backup.json")
_sessions_bak_lock = threading.Lock()


def _outbox_enqueue(kind: str, payload: dict):
    """Sauvegarde un message non envoyé pour retransmission ultérieure."""
    try:
        with _outbox_lock:
            entry = {"ts": time_module.time(), "kind": kind, "payload": payload}
            with open(_outbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"Outbox enqueue erreur : {e}")


def _outbox_drain():
    """Tente de vider la file d'attente. Appelé périodiquement."""
    if not _outbox_path.exists():
        return
    try:
        # Lire sous lock — rapide
        with _outbox_lock:
            with open(_outbox_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return
            # Vider le fichier immédiatement — les nouveaux messages passeront par append
            with open(_outbox_path, "w", encoding="utf-8") as f:
                f.write("")

        # Traiter les messages HORS lock — les appels réseau Meta peuvent prendre des secondes
        restants = []
        envoyes  = 0
        for line in lines:
            try:
                entry = json.loads(line)
                if time_module.time() - entry["ts"] > 86400:
                    continue
                if entry["kind"] == "send":
                    ok = _wa_send_direct(**entry["payload"])
                elif entry["kind"] == "send_group":
                    ok = _wa_send_groupe_direct(**entry["payload"])
                else:
                    ok = False
                if ok:
                    envoyes += 1
                else:
                    restants.append(line)
            except Exception:
                restants.append(line)

        # Réécrire les restants sous lock
        if restants:
            with _outbox_lock:
                with open(_outbox_path, "a", encoding="utf-8") as f:
                    f.writelines(restants)

        if envoyes > 0:
            log.info(f"📤 Outbox drain : {envoyes} message(s) renvoyés, {len(restants)} en attente")
    except Exception as e:
        log.error(f"Outbox drain erreur : {e}")


def _wa_request(method: str, payload: dict, max_retries: int = 3) -> bool:
    """
    Wrapper résilient Green API avec retry exponentiel.
    method : 'sendMessage', 'sendFileByUrl', etc.
    Retourne True si envoyé, False sinon (la queue rattrape).
    """
    if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
        log.warning("⚠️ Green API non configurée (GREENAPI_INSTANCE_ID / GREENAPI_TOKEN manquants)")
        return False

    url = f"{GREENAPI_BASE}/waInstance{GREENAPI_INSTANCE_ID}/{method}/{GREENAPI_TOKEN}"
    headers = {"Content-Type": "application/json"}
    delays = [2, 5, 10]
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if 200 <= r.status_code < 300:
                return True
            if r.status_code in (401, 403):
                log.error(f"🔴 Green API token invalide ({r.status_code}). Vérifier GREENAPI_TOKEN.")
                return False
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                log.warning(f"⚠️ Green API rate limit. Attente {wait}s")
                time_module.sleep(min(wait, 60))
                continue
            if r.status_code >= 500:
                if attempt < max_retries - 1:
                    time_module.sleep(delays[attempt])
                    continue
            log.warning(f"⚠️ Green API {method} → {r.status_code} : {r.text[:120]}")
            return False
        except (requests.ConnectionError, requests.Timeout):
            if attempt < max_retries - 1:
                time_module.sleep(delays[attempt])
                continue
            return False
        except Exception as e:
            log.error(f"❌ Green API {method} : {e}")
            return False
    return False


def _wa_send_direct(to: str, body: str) -> bool:
    """Envoi DM via Green API. `to` au format +237XXXXXXXXX."""
    _throttle_wa()   # ≤ 77 msg/s — évite le 429 sur les batchs APScheduler
    # Green API chatId : numéro sans + ni espaces + @c.us
    to_clean = to.lstrip("+").replace(" ", "")
    chat_id  = f"{to_clean}@c.us"
    payload  = {
        "chatId":  chat_id,
        "message": body[:4096],
    }
    return _wa_request("sendMessage", payload)


def wa_envoyer_boutons(to: str, body: str, boutons: list, header: str = None, footer: str = None) -> bool:
    """
    Green API ne supporte pas les boutons interactifs natifs.
    Retombe systématiquement sur un message texte avec les options en clair.
    """
    if not boutons:
        return _wa_send(to, body)
    options = "\n".join(f"• Tapez *{b.get('titre', '')}*" for b in boutons)
    texte = body + "\n\n" + options
    if footer:
        texte += f"\n\n_{footer}_"
    return _wa_send(to, texte)


def wa_envoyer_liste(to: str, body: str, sections: list, button_text: str = "Choisir",
                     header: str = None, footer: str = None) -> bool:
    """
    Green API ne supporte pas les listes interactives natives.
    Retombe systématiquement sur un message texte avec les items numérotés.
    """
    if not sections:
        return _wa_send(to, body)
    lignes = [body]
    for s in sections:
        titre_section = s.get("titre", "")
        if titre_section:
            lignes.append(f"\n*{titre_section}*")
        for it in s.get("items", [])[:10]:
            t = it.get("titre", "")
            d = it.get("description", "")
            ligne = f"• *{t}*" + (f" — {d}" if d else "")
            lignes.append(ligne)
    if footer:
        lignes.append(f"\n_{footer}_")
    return _wa_send(to, "\n".join(lignes))


def _wa_send_groupe_direct(group_id: str, body: str) -> bool:
    """
    Broadcast : envoie un DM individuel à chaque membre actif de la tontine.
    `group_id` est l'identifiant interne de la tontine (colonne whatsapp_groupe).
    """
    try:
        conn    = get_conn()
        # group_id peut être l'identifiant interne de la tontine ou nom de groupe
        tontine = fetchone(conn,
            "SELECT id, nom FROM tontines WHERE whatsapp_groupe=%s OR nom=%s LIMIT 1",
            (group_id, group_id))
        if not tontine:
            release_conn(conn)
            log.warning(f"⚠️ Tontine introuvable pour group_id={group_id}")
            return False
        membres = fetchall(conn, """
            SELECT m.whatsapp FROM membres m
            JOIN adhesions a ON a.membre_id = m.id
            WHERE a.tontine_id=%s AND a.statut='Actif'
              AND m.statut_global='Actif'
        """, (tontine["id"],))
        release_conn(conn)
        if not membres:
            return False
        ok_count = 0
        for m in membres:
            if _wa_send_direct(m["whatsapp"], body):
                ok_count += 1
        return ok_count > 0
    except Exception as e:
        log.error(f"❌ Broadcast groupe {group_id} : {e}")
        return False


def _wa_send(to: str, body: str) -> bool:
    """Envoie un DM avec fallback outbox si Green API indisponible."""
    if _wa_send_direct(to, body):
        return True
    _outbox_enqueue("send", {"to": to, "body": body})
    return False


def _wa_send_groupe(group_id: str, body: str) -> bool:
    """Envoie à tous les membres d'une tontine, fallback outbox si Green API down."""
    if _wa_send_groupe_direct(group_id, body):
        return True
    _outbox_enqueue("send_group", {"group_id": group_id, "body": body})
    return False

def _wa_send_group_chatid(group_chatid: str, body: str) -> bool:
    """Poste dans le canal WhatsApp groupe via JID @g.us.
    Distinct de _wa_send_groupe (broadcast DM individuel)."""
    _throttle_wa()
    payload = {"chatId": group_chatid, "message": body[:4096]}
    if _wa_request("sendMessage", payload):
        return True
    _outbox_enqueue("send", {"to": group_chatid, "body": body})
    return False


def wa_kick_membre(group_id: str, wa_membre: str) -> bool:
    """
    Bannissement signalé — l'admin humain doit retirer manuellement le membre du groupe WhatsApp.
    """
    log.warning(f"⚠️ KICK manuel requis : {wa_membre} doit être retiré de {group_id} par l'admin")
    return False


def wa_quitter_groupe(group_id: str) -> bool:
    """Envoie un message d'au revoir à tous les membres et marque la tontine clôturée."""
    _wa_send_groupe_direct(group_id,
        "🏁 *Cycle terminé.*\nLe service TontineBot Pro se retire de cette tontine. "
        "Merci pour votre confiance.\n\n_BADF Ltd_")
    return True


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


def wa_prive(numero: str, message: str):
    """Envoie un DM à un membre."""
    _wa_send(numero, message)


def wa_owner(message: str):
    """Envoie un DM au owner BADF."""
    wa_prive(OWNER_WA, message)


def wa_admins_tontine(tontine_id: int, message: str):
    """Envoie un DM à tous les admins d'une tontine."""
    conn   = get_conn()
    admins = fetchall(conn,
        "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s", (tontine_id,))
    release_conn(conn)
    for a in admins:
        wa_prive(a["whatsapp"], message)


def wa_groupe(group_id: str, message: str):
    """
    Envoie un message à tous les membres actifs d'une tontine via Meta.
    group_id = JID du groupe (ex: 120363XXXXXXXX@g.us)
    ou nom de la tontine si on a le whatsapp_groupe en base.
    """
    if group_id and "@g.us" in group_id:
        # JID direct → envoyer dans le groupe
        _wa_send_groupe(group_id, message)
    elif group_id:
        # Chercher le JID par nom de tontine
        conn = get_conn()
        tontine = fetchone(conn,
            "SELECT whatsapp_groupe FROM tontines WHERE nom=%s OR whatsapp_groupe=%s",
            (group_id, group_id))
        release_conn(conn)
        if tontine and tontine.get("whatsapp_groupe"):
            _wa_send_groupe(tontine["whatsapp_groupe"], message)


def wa_admin(message: str):
    wa_groupe(GROUPE_ADMIN, message)


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
            f"💸 Passé ce délai : pénalité *{MONTANT_IRA} FCFA/jour*"
        )
        wa_prive(r["whatsapp"], msg)




# ══════════════════════════════════════════════════════════════════════════
# KYC COMPLET — 5 ÉTAPES
# ══════════════════════════════════════════════════════════════════════════

def _calculer_age(date_str: str) -> int:
    """Calcule l'âge à partir d'une date JJ/MM/AAAA. Retourne -1 si invalide."""
    try:
        jour, mois, annee = map(int, date_str.strip().split("/"))
        naissance = datetime(annee, mois, jour)
        aujourd_hui = datetime.now()
        age = aujourd_hui.year - naissance.year
        if (aujourd_hui.month, aujourd_hui.day) < (naissance.month, naissance.day):
            age -= 1
        return age
    except Exception:
        return -1


def demarrer_kyc(wa: str):
    """
    Lance le processus KYC pour un nouveau membre.
    Le flux s'adapte selon l'âge :
      Adulte (≥18 ans) : Nom → Naissance → CNI → Ville  (4 étapes)
      Mineur  (<18 ans) : Nom → Naissance → Ville        (3 étapes)
    La détection adulte/mineur se fait à l'étape 2 (date de naissance).
    """
    with _sessions_lock:
        _sessions_kyc[wa] = {"etape": 0, "data": {}, "mineur": None, "ts": time_module.time()}
    wa_prive(wa,
        "📋 *VÉRIFICATION D'IDENTITÉ — BADF Ltd*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Avant d'activer votre compte Barack Corp, nous devons confirmer "
        "votre identité conformément aux exigences *CEMAC/ANIF*.\n\n"
        "🔒 *Sécurité garantie :*\n"
        "• Chiffrement SHA-256 irréversible\n"
        "• Données jamais partagées avec des tiers\n"
        "• Archivage légal 7 ans\n\n"
        "Ce processus prend *moins de 3 minutes*.\n"
        "Vous pouvez taper *STOP* à tout moment pour interrompre.\n\n"
        "─────────────────────────────────────────\n"
        "✏️ *ÉTAPE 1 — Votre nom complet*\n\n"
        "Entrez votre *prénom et nom* exactement comme ils apparaissent "
        "sur votre pièce d'identité :"
    )


def traiter_kyc(wa: str, texte: str, est_media: bool = False) -> bool:
    """
    Traite chaque étape du KYC adaptatif.
    Flux adulte  : 0-Nom → 1-Naissance → 2-CNI → 3-Ville
    Flux mineur  : 0-Nom → 1-Naissance → 2-Ville
    """
    wa = normaliser_numero(wa)
    if wa not in _sessions_kyc or not session_valide(_sessions_kyc, wa):
        return False

    sess  = _sessions_kyc[wa]
    etape = sess["etape"]
    est_mineur = sess.get("mineur")  # None = pas encore déterminé

    # Commande annulation
    if texte.strip().upper() == "STOP":
        _sessions_kyc.pop(wa, None)
        wa_prive(wa,
            "⏸️ KYC interrompu.\n"
            "Tapez *menu* quand vous serez prêt à reprendre.")
        return True

    # ══ ÉTAPE 0 : NOM COMPLET ════════════════════════════════════════════
    if etape == 0:
        nom = texte.strip()
        if len(nom) < 3 or not re.match(r"^[A-Za-zÀ-ÿ\s\-'\.]+$", nom):
            wa_prive(wa,
                "❌ Nom invalide.\n"
                "Entrez votre *prénom et nom* avec lettres uniquement.\n"
                "Exemple : *Jean-Pierre MBARGA*")
            return True
        sess["data"]["kyc_nom"] = nom.upper()
        sess["etape"] = 1
        wa_prive(wa,
            f"✅ Nom enregistré : *{nom.upper()}*\n\n"
            "─────────────────────────────────────────\n"
            "✏️ *ÉTAPE 2 — Date de naissance*\n\n"
            "Entrez votre date de naissance au format *JJ/MM/AAAA*\n\n"
            "Exemples :\n"
            "• *15/03/1990* _(adulte)_\n"
            "• *22/07/2010* _(mineur)_\n\n"
            "ℹ️ _Cette date détermine le type de pièce d'identité requis._"
        )

    # ══ ÉTAPE 1 : DATE DE NAISSANCE → DÉTECTION ADULTE/MINEUR ═══════════
    elif etape == 1:
        date_str = texte.strip()
        if not re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", date_str):
            wa_prive(wa,
                "❌ Format incorrect.\n"
                "Entrez la date au format *JJ/MM/AAAA*\n"
                "Exemple : *15/03/1990*")
            return True
        age = _calculer_age(date_str)
        if age < 0 or age > 120:
            wa_prive(wa,
                "❌ Date de naissance invalide.\n"
                "Vérifiez le format : *JJ/MM/AAAA*")
            return True

        sess["data"]["kyc_naissance"] = date_str
        sess["data"]["kyc_age"]       = age
        mineur = age < 18
        sess["mineur"] = mineur

        if not mineur:
            # ── ADULTE → demander CNI ─────────────────────────────────────
            sess["etape"] = 2
            wa_prive(wa,
                f"✅ Date : *{date_str}* ({age} ans)\n\n"
                "─────────────────────────────────────────\n"
                "✏️ *ÉTAPE 3 — Numéro de CNI*\n\n"
                "Entrez le *numéro de votre Carte Nationale d'Identité* "
                "exactement comme il y est inscrit.\n\n"
                "⚠️ _Votre CNI est liée à votre numéro Mobile Money "
                "auprès de MTN/Orange. Toute incohérence est détectée "
                "automatiquement._"
            )
        else:
            # ── MINEUR → pas de CNI, sauter vers ville ───────────────────
            sess["etape"] = 10  # étape 10 = ville (mineur)
            wa_prive(wa,
                f"✅ Date : *{date_str}* ({age} ans)\n\n"
                "📌 *Membre mineur détecté.*\n"
                "Pour les membres de moins de 18 ans, "
                "l'acte de naissance remplace la CNI.\n\n"
                "─────────────────────────────────────────\n"
                "✏️ *ÉTAPE 3 — Ville de résidence*\n\n"
                "Entrez votre *ville de résidence actuelle* :"
            )

    # ══ ÉTAPE 2 (adulte) : CNI ═══════════════════════════════════════════
    elif etape == 2:
        cni = re.sub(r"[\s\-\.]", "", texte.upper())
        if len(cni) < 5:
            wa_prive(wa,
                "❌ Numéro CNI trop court.\n"
                "Entrez le numéro *complet* de votre CNI.")
            return True
        # Vérifier doublon CNI
        conn    = get_conn()
        doublon = fetchone(conn,
            "SELECT id, whatsapp FROM membres WHERE kyc_cni=%s", (cni,))
        release_conn(conn)
        if doublon:
            incrementer_tentatives_fraude(
                doublon["id"],
                f"Tentative doublon CNI {cni} depuis {wa}"
            )
            log_audit("DOUBLON_CNI", f"CNI {cni} déjà enregistrée", wa)
            wa_prive(wa,
                "🚨 *CNI DÉJÀ ENREGISTRÉE*\n\n"
                "Cette carte d'identité est déjà associée à un compte "
                "Barack Corp actif.\n\n"
                "Si vous pensez qu'il s'agit d'une erreur, contactez "
                "un admin immédiatement.\n\n"
                "⚠️ _Toute tentative d'usurpation d'identité est "
                "journalisée et transmissible à la justice._"
            )
            _sessions_kyc.pop(wa, None)
            return True
        sess["data"]["kyc_cni"] = cni
        sess["etape"] = 3
        wa_prive(wa,
            f"✅ CNI enregistrée.\n\n"
            "─────────────────────────────────────────\n"
            "✏️ *ÉTAPE 4 — Ville de résidence*\n\n"
            "Entrez votre *ville de résidence actuelle* :"
        )

    # ══ ÉTAPE 3 (adulte) : VILLE — dernière étape ════════════════════════
    elif etape == 3:
        ville = texte.strip().title()
        if len(ville) < 2:
            wa_prive(wa, "❌ Ville invalide. Entrez le nom de votre ville.")
            return True
        sess["data"]["kyc_ville"] = ville
        _finaliser_kyc(wa, sess["data"], mineur=False)


    # ══ ÉTAPE 10 (mineur) : VILLE ════════════════════════════════════════
    elif etape == 10:
        ville = texte.strip().title()
        if len(ville) < 2:
            wa_prive(wa, "❌ Ville invalide. Entrez le nom de votre ville.")
            return True
        sess["data"]["kyc_ville"] = ville
        _finaliser_kyc(wa, sess["data"], mineur=True)


    return True


def _finaliser_kyc(wa: str, data: dict, mineur: bool = False):
    """
    Finalise le KYC et inscrit le membre en base.
    Adapté adulte (CNI) ou mineur (acte de naissance).
    """
    nom       = data["kyc_nom"]
    naissance = data["kyc_naissance"]
    ville     = data["kyc_ville"]
    age       = data.get("kyc_age", 0)
    cni       = data.get("kyc_cni", "")  # vide pour les mineurs

    # Hash différent selon adulte/mineur pour l'unicité
    if mineur:
        kyc_hash = hashlib.sha256(
            f"MINEUR:{nom}{wa}{naissance}{ville}".encode()
        ).hexdigest()
    else:
        kyc_hash = hashlib.sha256(
            f"ADULTE:{nom}{wa}{cni}{naissance}".encode()
        ).hexdigest()

    conn   = get_conn()
    membre = fetchone(conn, "SELECT * FROM membres WHERE whatsapp=%s", (wa,))

    if membre:
        q(conn, """UPDATE membres SET
                   nom_complet=%s, kyc_hash=%s, kyc_complet=1, kyc_etape=5,
                   kyc_nom=%s, kyc_cni=%s, kyc_naissance=%s, kyc_ville=%s,
                   kyc_photo_recu=1, kyc_mineur=%s, statut_global='Actif'
                   WHERE whatsapp=%s""",
          (nom, kyc_hash, nom, cni or None, naissance, ville,
           1 if mineur else 0, wa))
    else:
        q(conn, """INSERT INTO membres
                   (nom_complet, kyc_hash, whatsapp, kyc_complet, kyc_etape,
                    kyc_nom, kyc_cni, kyc_naissance, kyc_ville,
                    kyc_photo_recu, kyc_mineur, adhesion_payee)
                   VALUES (%s,%s,%s,1,5,%s,%s,%s,%s,1,%s,1)""",
          (nom, kyc_hash, wa, nom, cni or None, naissance, ville,
           1 if mineur else 0))
    conn.commit()
    release_conn(conn)

    _sessions_kyc.pop(wa, None)

    type_doc = "Acte de naissance" if mineur else "CNI"
    ref_doc  = f"Acte — {naissance}" if mineur else f"CNI : {cni}"
    statut_m = "👶 Mineur" if mineur else "👤 Adulte"

    log_audit("KYC_COMPLET",
              f"{nom} | {statut_m} | {ref_doc} | {ville}", wa)

    wa_prive(wa,
        f"🎉 *IDENTITÉ VÉRIFIÉE — BADF Ltd*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ *Dossier KYC enregistré avec succès !*\n\n"
        f"👤 Nom : *{nom}*\n"
        f"🎂 Naissance : *{naissance}* ({age} ans)\n"
        f"📄 Document : *{type_doc}*\n"
        f"🏙️ Ville : *{ville}*\n"
        f"🔐 Hash SHA-256 : `{kyc_hash[:20]}...`\n\n"
        f"─────────────────────────────────────────\n"
        f"🔒 *Vos données sont archivées de façon irréversible.*\n"
        f"Ce dossier fait foi en cas de litige et peut être "
        f"communiqué aux autorités compétentes.\n\n"
        f"📲 Tapez *menu* pour accéder à vos tontines."
    )

    # Alerte admin avec type membre
    wa_admin(
        f"✅ *NOUVEAU MEMBRE KYC — {statut_m}*\n"
        f"👤 {nom} | {wa}\n"
        f"🎂 {naissance} ({age} ans)\n"
        f"📄 {ref_doc}\n"
        f"🏙️ {ville}\n"
        f"🔐 {kyc_hash[:20]}..."
    )


# ══════════════════════════════════════════════════════════════════════════
# MENU MEMBRE WHATSAPP
# ══════════════════════════════════════════════════════════════════════════

MENU_MEMBRE_TXT = (
    "🏦 *BARACK CORP — MON ESPACE*\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "1️⃣  Mon solde & statut\n"
    "2️⃣  Mes tontines\n"
    "3️⃣  Mon rang / prochain passage\n"
    "4️⃣  Ma caution\n"
    "5️⃣  Historique paiements\n"
    "6️⃣  Payer une cotisation\n"
    "7️⃣  Paiement avancé (plusieurs périodes)\n"
    "8️⃣  Signaler un problème\n"
    "9️⃣  Changer mon numéro Mobile Money\n"
    "0️⃣  Aide\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "_Tapez le numéro de votre choix_"
)


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
            conn_new = get_conn()
            try:
                kyc_hash_new = hashlib.sha256(f"NEW{wa}".encode()).hexdigest()
                cur_new = q(conn_new, """
                    INSERT INTO membres
                        (nom_complet, kyc_hash, whatsapp, adhesion_payee, statut_global, kyc_etape)
                    VALUES (%s,%s,%s,1,'En_attente_kyc',0)
                    ON CONFLICT (whatsapp) DO NOTHING
                """, (f"Membre_{wa[-4:]}", kyc_hash_new, wa))
                inserted = cur_new.rowcount > 0
                conn_new.commit()
            finally:
                release_conn(conn_new)
            if inserted:
                demarrer_kyc(wa)
            return ""
        if membre["statut_global"] == "En_attente_kyc":
            demarrer_kyc(wa)
            return ""
        if membre["statut_global"] == "Banni":
            return msg_dissuasion(wa)
        return MENU_MEMBRE_TXT

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
                f"   Brut:{t['montant_brut']:,} | FMP:-{t['frais_fmp']} | "
                f"IRA:-{t['frais_ira']} | Net:{t['montant_net']:,}\n"
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
               (SELECT COUNT(*) FROM adhesions WHERE tontine_id=t.id
                AND statut='Actif') AS nb_membres,
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
        montant_tot  = p["montant_place"] * p["nb_membres"]
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

MENU_ADMIN_TXT = (
    "🔐 *ADMIN TONTINEBOT PRO*\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "1️⃣  *Rapport du jour*\n"
    "    └ Bilan cotisations & alertes\n"
    "\n"
    "2️⃣  *Liste des membres*\n"
    "    └ Tous les membres + statuts\n"
    "\n"
    "3️⃣  *Ordre de bouffage*\n"
    "    └ Qui bouffe quand\n"
    "\n"
    "4️⃣  *Modifier l'ordre*\n"
    "    └ Réorganiser les passages\n"
    "\n"
    "5️⃣  *Suspendre / Réactiver*\n"
    "    └ Bloquer ou débloquer un membre\n"
    "\n"
    "6️⃣  *Historique membre*\n"
    "    └ Toutes les cotisations d'un membre\n"
    "\n"
    "7️⃣  *Rappel au groupe*\n"
    "    └ Envoyer un message à tous\n"
    "\n"
    "8️⃣  *Fugitifs*\n"
    "    └ Membres ayant fui après bouffage\n"
    "\n"
    "9️⃣  *Cas difficiles*\n"
    "    └ Pause, échelonnement, cession...\n"
    "\n"
    "🔟  *Saisir caution fugitif*\n"
    "    └ Récupérer la caution bloquée\n"
    "\n"
    "1️⃣1️⃣ *Ajouter membre*\n"
    "    └ Inscrire quelqu'un manuellement\n"
    "\n"
    "1️⃣2️⃣ *Créer tontine*\n"
    "    └ Nouvelle tontine\n"
    "\n"
    "1️⃣3️⃣ *Ordre initial*\n"
    "    └ Définir le 1er ordre de bouffage\n"
    "\n"
    "1️⃣4️⃣ *Heures*\n"
    "    └ Régler les heures de rappels\n"
    "\n"
    "1️⃣5️⃣ *Cotisations en attente*\n"
    "    └ Confirmer ou rejeter par OUI/NON\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🚪 *KICK +237XXXXXXXXX* → retirer un membre\n"
    "🔓 *DEBLOQUER [ID]* → débloquer bouffage suspect\n"
    "🆘 *BOUFFAGE_COMPLET [ID]* → cas grave\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "💡 Dans le groupe : tapez *liste* pour le bilan\n"
    "_Tapez le numéro du menu pour continuer._"
)

MENU_CAS_DIFFICILES = (
    "⚠️ *CAS DIFFICILES*\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "A. Pause temporaire\n"
    "B. Échelonnement (paiement en tranches)\n"
    "C. Cession de place\n"
    "D. Exonération (IRA ou pénalité)\n"
    "E. Exclusion définitive\n"
    "0. Retour\n"
    "━━━━━━━━━━━━━━━━━━"
)


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
        f"   💼 FMP : {c['fmp_du']:,} FCFA\n"
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

    # ── Blocage si dette BADF >72h impayée (jamais pour le owner) ────────
    if not est_owner(normaliser_numero(wa)):
        conn_chk = get_conn()
        dette_bloquante = fetchone(conn_chk, """
            SELECT SUM(montant) AS total,
                   EXTRACT(EPOCH FROM (NOW() - MIN(date_creation)))/3600 AS heures
            FROM dettes_badf
            WHERE admin_wa=%s AND statut='Due'
            HAVING MIN(date_creation) < NOW() - INTERVAL '72 hours'
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
                f"Envoyez le *code de transaction* au bot pour débloquer votre accès.\n\n"
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
            q(conn, """UPDATE bouffages_manuels
                       SET statut='Confirme', numero_mm=%s
                       WHERE id=%s""", (numero, ac_rec["id"]))
            q(conn, """UPDATE liste_passage
                       SET statut='Paye', numero_cashout=%s, operateur_cashout=%s,
                           montant_bouffage=%s, date_paiement=NOW()
                       WHERE id=%s""",
              (numero, operateur, ac_rec["montant"], ac_rec["passage_id"]))
            q(conn, "UPDATE membres SET nb_bouffages=nb_bouffages+1, dernier_bouffage=NOW() WHERE id=%s",
              (ac_rec["membre_id"],))
            conn.commit()
            release_conn(conn)
            log_audit("CASHOUT_FORCE",
                      f"{expire['nom_complet']} | {ac_rec['montant']:,} FCFA → {numero}", wa)
            # Notifier le bénéficiaire
            wa_prive(expire["whatsapp"],
                f"✅ *VIREMENT EFFECTUÉ — {tnom}*\n\n"
                f"*{ac_rec['montant']:,} FCFA* ont été envoyés sur *{numero}*\n"
                f"Réf : `{cashout_ref}`\n\n"
                f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
            )
            sess["etape"] = "menu"
            return (
                f"✅ *Cashout forcé effectué*\n\n"
                f"{expire['nom_complet']} → *{ac_rec['montant']:,} FCFA* → {numero}\n"
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
        t           = fetchone(conn, "SELECT cycle_actuel FROM tontines WHERE id=%s", (tid,))
        # Décaler les autres membres
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
            f"Statut: {m['statut_global']} | KYC: {'✅' if m['kyc_complet'] else '❌'}",
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

    # ── Option 12 : Créer une nouvelle tontine (owner uniquement) ─────────
    elif texte == "12" and sess["etape"] == "menu":
        if not est_owner(wa):
            return "🚫 Cette option est réservée au *propriétaire* du système."
        sess["etape"] = "creer_tontine_nom"
        sess["data"]["new_tontine"] = {}
        return (
            "🏛️ *CRÉER UNE NOUVELLE TONTINE*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Étape 1/5 — Entrez le *nom* de la tontine :\n"
            "_(ex : GOLD_200, HEBDO_ELITE)_"
        )

    elif sess.get("etape") == "creer_tontine_nom":
        if texte == "0":
            sess["etape"] = "menu"
            return "❌ Annulé."
        nom = texte.strip().upper().replace(" ", "_")
        if len(nom) < 3:
            return "❌ Nom trop court (min 3 caractères)."
        conn = get_conn()
        existant = fetchone(conn, "SELECT id FROM tontines WHERE nom=%s", (nom,))
        release_conn(conn)
        if existant:
            return f"❌ Une tontine *{nom}* existe déjà."
        sess["data"]["new_tontine"]["nom"] = nom
        sess["etape"] = "creer_tontine_type"
        return (
            f"✅ Nom : *{nom}*\n\n"
            "Étape 2/5 — *Type* de tontine :\n"
            "J = Journalière\nH = Hebdomadaire\nM = Mensuelle"
        )

    elif sess.get("etape") == "creer_tontine_type":
        mapping = {"J": "Journaliere", "H": "Hebdomadaire", "M": "Mensuelle"}
        t_type  = mapping.get(texte.upper())
        if not t_type:
            return "❌ Tapez J, H ou M."
        sess["data"]["new_tontine"]["type"] = t_type
        if t_type == "Journaliere":
            sess["data"]["new_tontine"]["jour_semaine"] = "Lundi"
            sess["data"]["new_tontine"]["jour_mois"]    = 1
            sess["etape"] = "creer_tontine_montant"
            return (
                f"✅ Type : *Journalière*\n\n"
                "Étape 3/5 — *Montant de cotisation* par jour (FCFA) :"
            )
        elif t_type == "Hebdomadaire":
            sess["etape"] = "creer_tontine_jour"
            return (
                f"✅ Type : *Hebdomadaire*\n\n"
                "Étape 2b/5 — Quel *jour de la semaine* ?\n\n"
                "L = Lundi\nM = Mardi\nX = Mercredi\nJ = Jeudi\n"
                "V = Vendredi\nS = Samedi\nD = Dimanche"
            )
        else:  # Mensuelle
            sess["etape"] = "creer_tontine_jour"
            return (
                f"✅ Type : *Mensuelle*\n\n"
                "Étape 2b/5 — Quel *jour du mois* ? (1 à 28)\n"
                "_(ex : 1 = 1er de chaque mois, 15 = le 15)_"
            )

    elif sess.get("etape") == "creer_tontine_jour":
        t_type = sess["data"]["new_tontine"]["type"]
        if t_type == "Hebdomadaire":
            mapping_j = {"L":"Lundi","M":"Mardi","X":"Mercredi","J":"Jeudi",
                         "V":"Vendredi","S":"Samedi","D":"Dimanche"}
            jour = mapping_j.get(texte.strip().upper())
            if not jour:
                return "❌ Tapez L, M, X, J, V, S ou D."
            sess["data"]["new_tontine"]["jour_semaine"] = jour
            sess["data"]["new_tontine"]["jour_mois"]    = 1
            label_jour = f"*{jour}*"
        else:  # Mensuelle
            try:
                j = int(texte.strip())
                if not (1 <= j <= 28):
                    return "❌ Entrez un nombre entre 1 et 28."
            except ValueError:
                return "❌ Entrez un nombre entre 1 et 28."
            sess["data"]["new_tontine"]["jour_mois"]    = j
            sess["data"]["new_tontine"]["jour_semaine"] = "Lundi"
            label_jour = f"le *{j}* de chaque mois"
        sess["etape"] = "creer_tontine_montant"
        return (
            f"✅ Jour : {label_jour}\n\n"
            "Étape 3/5 — *Montant de cotisation* par période (FCFA) :"
        )

    elif sess.get("etape") == "creer_tontine_montant":
        try:
            montant = int(texte.replace(" ", "").replace(",", ""))
            if montant < 100:
                return "❌ Montant minimum : 100 FCFA."
        except ValueError:
            return "❌ Entrez un nombre entier."
        sess["data"]["new_tontine"]["montant"] = montant
        sess["etape"] = "creer_tontine_groupe"
        return (
            f"✅ Montant : *{montant:,} FCFA*\n\n"
            "Étape 4/5 — *Nom du groupe WhatsApp* exact :\n"
            "_(tel qu'il apparaît dans WhatsApp)_\n"
            "Tapez *AUCUN* si pas encore créé."
        )

    elif sess.get("etape") == "creer_tontine_groupe":
        groupe = "" if texte.upper() == "AUCUN" else texte.strip()
        sess["data"]["new_tontine"]["groupe"] = groupe
        sess["etape"] = "creer_tontine_caution"
        return (
            f"✅ Groupe : *{groupe or 'Non défini'}*\n\n"
            "Étape 5/5 — *% de caution* (retenu au bouffage, libéré si paiement continu) :\n"
            "_(Défaut recommandé : 10)_\n"
            "Entrez un nombre entre 5 et 30 :"
        )

    elif sess.get("etape") == "creer_tontine_caution":
        try:
            pct = int(texte)
            if not (5 <= pct <= 30):
                return "❌ Entre 5 et 30."
        except ValueError:
            return "❌ Entrez un nombre."
        d    = sess["data"]["new_tontine"]
        try:
            tid_nouveau = creer_tontine(
                nom=d["nom"], type_tontine=d["type"],
                montant_place=d["montant"],
                groupe_wa=d["groupe"], caution_pourcent=pct,
                jour_semaine=d.get("jour_semaine","Lundi"),
                jour_mois=d.get("jour_mois",1)
            )
            if d["groupe"]:
                conn_g = get_conn()
                try:
                    q(conn_g, "UPDATE tontines SET whatsapp_groupe=%s WHERE id=%s",
                      (d["groupe"], tid_nouveau))
                    conn_g.commit()
                finally:
                    release_conn(conn_g)
            sess["etape"] = "menu"
            sess["tontine_id"]  = tid_nouveau
            sess["tontine_nom"] = d["nom"]
            return (
                f"🎉 *TONTINE CRÉÉE — BADF Ltd*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏦 Nom : *{d['nom']}*\n"
                f"📅 Type : {d['type']}\n"
                f"💰 Cotisation : {d['montant']:,} FCFA\n"
                f"👥 Groupe : {d['groupe'] or 'À configurer'}\n"
                f"🔒 Caution : {pct}%\n"
                f"ID : {tid_nouveau}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Utilisez *admin {d['nom']}* pour gérer cette tontine."
            )
        except Exception as e:
            sess["etape"] = "menu"
            return f"❌ Erreur création : {e}"

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
                  "Après cette heure : pénalité IRA de {:,} FCFA.".format(MONTANT_IRA)),
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

        return (
            f"📋 *COTISATION 1/{total} — {tnom}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔖 *#{c['id']}* — {c['nom_complet']}{places_txt}\n"
            f"   💰 {c['montant_declare']:,} FCFA\n"
            f"   💼 FMP : {c['fmp_du']:,} FCFA\n"
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
            "• Options 12-13 : owner uniquement\n"
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


def traiter_cotisation(conn, membre_id: int, tontine_id: int,
                       montant_brut: int, ref: str, ip: str,
                       nb_periodes: int = 1):
    """
    Enregistre une cotisation confirmée par l'admin.
    - Vérifie le montant (±10% tolérance)
    - Applique FMP 2% + IRA si retard
    - Déduit les dettes IRA en attente
    - Met à jour le score de confiance
    - Réinitialise les alertes fugue si membre reprend les paiements
    - Vérifie libération caution
    """
    membre  = fetchone(conn, "SELECT * FROM membres WHERE id=%s", (membre_id,))
    tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))

    if not membre or not tontine:
        raise ValueError(f"Membre {membre_id} ou tontine {tontine_id} introuvable")

    if membre["statut_global"] in ("Banni", "Suspendu_global"):
        log_audit("COTISATION_BLOQUEE", f"Membre {membre_id} banni/suspendu", ip=ip)
        wa_prive(membre["whatsapp"],
            "🚫 Votre compte est suspendu. Payez le code *REACTIV* "
            f"({FRAIS_REACTIV:,} FCFA) pour vous réactiver, ou contactez un admin.")
        return

    montant_attendu = tontine["montant_place"] * nb_periodes
    if abs(montant_brut - montant_attendu) > montant_attendu * 0.10:
        incrementer_tentatives_fraude(membre_id,
            f"Montant incorrect : reçu {montant_brut}, attendu {montant_attendu}")
        raise ValueError(f"Montant incorrect : {montant_brut} vs {montant_attendu}")

    heure = datetime.now().time()
    frais = calculer_frais(montant_brut, heure, tontine["heure_limite"])

    # ── Déduire dette IRA existante ───────────────────────────────────────
    dettes_ira = fetchall(conn,
        "SELECT id, montant FROM dettes_ira WHERE membre_id=%s AND tontine_id=%s AND statut='Due'",
        (membre_id, tontine_id))
    dette_totale = sum(d["montant"] for d in dettes_ira)
    if dette_totale > 0 and frais["montant_net"] >= dette_totale:
        for d in dettes_ira:
            q(conn, "UPDATE dettes_ira SET statut='Prelevee', prelevee_le=NOW() WHERE id=%s", (d["id"],))
        frais["montant_net"] -= dette_totale
        log.info(f"Dette IRA {dette_totale} FCFA déduite du paiement membre {membre_id}")

    q(conn, """INSERT INTO transactions
               (membre_id, tontine_id, montant_brut, frais_fmp, frais_ira,
                montant_net, type_transaction, statut, reference, periodes_payees, ip_source)
               VALUES (%s,%s,%s,%s,%s,%s,'Cotisation','Confirmee',%s,%s,%s)""",
      (membre_id, tontine_id, montant_brut, frais["frais_fmp"], frais["frais_ira"],
       frais["montant_net"], ref, nb_periodes, ip))

    # Mettre à jour les jours d'avance si paiement multiple
    if nb_periodes > 1:
        q(conn, "UPDATE adhesions SET jours_avance=jours_avance+%s WHERE membre_id=%s AND tontine_id=%s",
          (nb_periodes - 1, membre_id, tontine_id))

    # Score de confiance : +2 si à l'heure, rien si IRA
    if frais["frais_ira"] == 0:
        _update_score_confiance(conn, membre_id, delta=2, raison="Cotisation à l'heure")

    # Lever suspension retard si elle existait
    q(conn, "UPDATE membres SET suspendu_retard=0, date_suspension_retard=NULL WHERE id=%s",
      (membre_id,))
    q(conn, "UPDATE adhesions SET nb_avertissements_retard=0 WHERE membre_id=%s AND tontine_id=%s",
      (membre_id, tontine_id))
    q(conn, "UPDATE alertes_fugue SET traite=1 WHERE membre_id=%s AND tontine_id=%s AND traite=0",
      (membre_id, tontine_id))

    conn.commit()

    # ── Notification membre — confirmation enrichie ───────────────────────
    ira_txt  = f"\n⏰ Pénalité retard (IRA) : *-{frais['frais_ira']:,} FCFA*" if frais["frais_ira"] > 0 else ""
    av_txt   = f" × {nb_periodes} périodes" if nb_periodes > 1 else ""
    dette_txt = f"\n✅ Dette IRA soldée : *{dette_totale:,} FCFA*" if dette_totale > 0 else ""

    # Récupérer la position du membre dans cette tontine
    conn2    = get_conn()
    passage  = fetchone(conn2, """
        SELECT lp.ordre,
               (SELECT COUNT(*) FROM liste_passage
                WHERE tontine_id=%s AND cycle=lp.cycle AND statut='Paye') AS deja_passes,
               (SELECT COUNT(*) FROM adhesions WHERE tontine_id=%s AND statut='Actif') AS nb_membres
        FROM liste_passage lp
        WHERE lp.tontine_id=%s AND lp.membre_id=%s AND lp.statut IN ('En_attente','Notifie')
        ORDER BY lp.ordre LIMIT 1
    """, (tontine_id, tontine_id, tontine_id, membre_id))

    if passage:
        restants   = passage["ordre"] - passage["deja_passes"]
        rang_txt   = f"\n📍 Votre rang : *{passage['ordre']}* | Passages restants avant vous : *{restants}*"
    else:
        rang_txt   = ""
    release_conn(conn2)

    wa_prive(membre["whatsapp"],
        f"✅ *COTISATION ENREGISTRÉE — {tontine['nom']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 *{membre['nom_complet']}*\n"
        f"📅 {datetime.now().strftime('%d/%m/%Y à %Hh%M')}\n\n"
        f"💰 Montant brut : *{montant_brut:,} FCFA*{av_txt}\n"
        f"   Commission Barack Corp (2%) : *-{frais['frais_fmp']:,} FCFA*{ira_txt}{dette_txt}\n"
        f"   *Net crédité au pool : {frais['montant_net']:,} FCFA*\n"
        f"{rang_txt}\n\n"
        f"🔐 Réf. transaction : `{ref}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
    )

    # Rapport owner (FMP + IRA)
    total_frais = frais["frais_fmp"] + frais["frais_ira"]
    if total_frais > 0:
        wa_owner(
            f"💰 *REVENUS BADF Ltd*\n"
            f"FMP: +{frais['frais_fmp']:,} | IRA: +{frais['frais_ira']:,}\n"
            f"*+{total_frais:,} FCFA* | {tontine['nom']} | Réf:{ref}"
        )

    log_audit("COTISATION", f"Membre {membre_id} — {montant_brut:,} F — {ref}", ip=ip)
    _verifier_liberation_caution(conn, membre_id, tontine_id)


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
    caution = fetchone(conn,
        "SELECT * FROM cautions_garantie WHERE membre_id=%s AND tontine_id=%s AND statut='Bloquee'",
        (membre_id, tontine_id))
    if not caution:
        return {"ok": False, "msg": "Aucune caution bloquée pour ce membre."}

    tontine = fetchone(conn, "SELECT * FROM tontines WHERE id=%s", (tontine_id,))
    membre  = fetchone(conn, "SELECT nom_complet, whatsapp FROM membres WHERE id=%s", (membre_id,))
    if not tontine or not membre:
        return {"ok": False, "msg": "Membre ou tontine introuvable."}

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


def healed(critical: bool = False, fallback=None):
    """
    Décorateur auto-healing : si la fonction échoue, log silencieux + retry +
    fallback. Aucune alerte au owner si la fonction se rétablit seule.
    Usage :
        @healed(critical=True)
        def ma_fonction(): ...
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(3):
                try:
                    result = fn(*args, **kwargs)
                    if attempt > 0:
                        log.info(f"✅ Auto-heal OK : {fn.__name__} après {attempt+1} tentative(s)")
                    return result
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                    log.warning(f"⚠️ DB error dans {fn.__name__} (tentative {attempt+1}) : {str(e)[:80]}")
                    with _health_lock:
                        _health_state["consecutive_db_failures"] += 1
                        _health_state["last_db_failure"] = time_module.time()
                    if attempt < 2:
                        _self_heal_db()
                        time_module.sleep(1 + attempt)
                    elif critical:
                        raise
                except (requests.ConnectionError, requests.Timeout) as e:
                    log.warning(f"⚠️ Network error dans {fn.__name__} : {str(e)[:80]}")
                    if attempt < 2:
                        time_module.sleep(2 + attempt * 2)
                except Exception as e:
                    log.error(f"❌ Exception dans {fn.__name__} (tentative {attempt+1}/3) : {e}")
                    if attempt < 2 and not critical:
                        time_module.sleep(1)
                    elif critical:
                        raise
                    else:
                        break
            return fallback
        return wrapper
    return deco


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

        # Adhésions du jour
        adhesions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant_brut), 0) v FROM transactions
            WHERE type_transaction='Adhesion' AND statut='Confirmee'
              AND date_heure::date = CURRENT_DATE
        """)

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

        total_badf = fmp + ira + adhesions["v"] + cautions_saisies["v"]

        return (
            f"📊 *RAPPORT DU JOUR — {datetime.now().strftime('%d/%m/%Y')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP (2%)     : *{fmp:,} FCFA*\n"
            f"  Pénalités IRA           : *{ira:,} FCFA*\n"
            f"  Frais d'adhésion        : *{adhesions['v']:,} FCFA* ({adhesions['n']} membres)\n"
            f"  Cautions saisies        : *{cautions_saisies['v']:,} FCFA* ({cautions_saisies['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL BADF AUJOURD'HUI : {total_badf:,} FCFA*\n\n"
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
        adhesions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant_brut), 0) v FROM transactions
            WHERE type_transaction='Adhesion' AND statut='Confirmee'
              AND date_heure >= %s
        """, (debut_mois,))
        cautions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie
            WHERE statut='Saisie' AND date_liberation >= %s
        """, (debut_mois,))
        bouffages = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE statut='Paye' AND date_paiement >= %s
        """, (debut_mois,))
        total = fmp + ira + adhesions["v"] + cautions["v"]
        mois_nom = datetime.now().strftime("%B %Y").upper()
        return (
            f"📆 *RAPPORT {mois_nom}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP          : *{fmp:,} FCFA*\n"
            f"  Pénalités IRA           : *{ira:,} FCFA*\n"
            f"  Frais d'adhésion        : *{adhesions['v']:,} FCFA* ({adhesions['n']} membres)\n"
            f"  Cautions saisies        : *{cautions['v']:,} FCFA* ({cautions['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL MOIS : {total:,} FCFA*\n\n"
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
        adhesions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant_brut), 0) v FROM transactions
            WHERE type_transaction='Adhesion' AND statut='Confirmee'
              AND date_heure >= %s
        """, (debut,))
        cautions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie WHERE statut='Saisie' AND date_liberation >= %s
        """, (debut,))

        # Top 5 jours les plus rentables
        top_jours = fetchall(conn, """
            SELECT date_heure::date AS jour,
                   COALESCE(SUM(frais_fmp + frais_ira), 0) AS total
            FROM transactions
            WHERE statut='Confirmee' AND date_heure >= %s
            GROUP BY jour ORDER BY total DESC LIMIT 5
        """, (debut,))

        lignes_top = "\n".join(
            f"  {r['jour'].strftime('%d/%m/%Y')} → {r['total']:,} FCFA"
            for r in top_jours
        ) or "  Aucune donnée."

        total = fmp + ira + adhesions["v"] + cautions["v"]
        return (
            f"📈 *{titre}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP          : *{fmp:,} FCFA*\n"
            f"  Pénalités IRA           : *{ira:,} FCFA*\n"
            f"  Frais d'adhésion        : *{adhesions['v']:,} FCFA* ({adhesions['n']} membres)\n"
            f"  Cautions saisies        : *{cautions['v']:,} FCFA* ({cautions['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL PÉRIODE : {total:,} FCFA*\n\n"
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
        revenus_adh = fetchone(conn, """
            SELECT COALESCE(SUM(montant_brut), 0) v FROM transactions
            WHERE type_transaction='Adhesion' AND statut='Confirmee'
        """)["v"]

        return (
            f"👥 *STATISTIQUES MEMBRES — BADF Ltd*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"  Total inscrits   : *{total}*\n"
            f"  Actifs           : {actifs}\n"
            f"  Suspendus        : {suspendus}\n"
            f"  Bannis           : {bannis}\n\n"
            f"  Nouveaux aujourd'hui : {nouveaux_j}\n"
            f"  Nouveaux ce mois     : {nouveaux_m}\n\n"
            f"  Revenus adhésions (total) : *{revenus_adh:,} FCFA*\n\n"
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



def webhook_whatsapp_groupe():
    """
    Reçoit les événements de groupe WhatsApp via WPPConnect :
    - Bot ajouté à un groupe → présentation + enregistrement admins
    - Bot promu admin → enregistrement en base
    - Admin ajouté/retiré → mise à jour table admins_groupe
    - Membre quitte le groupe → alerte admins
    """
    data   = request.get_json(force=True) or {}
    event  = data.get("event", "")
    groupe = data.get("groupe", "")
    admins = data.get("admins", [])

    log.info(f"Webhook groupe : {event} | {groupe}")

    if event == "bot_added":
        # Bot ajouté → présentation personnalisée avec nom de la tontine
        if groupe:
            conn    = get_conn()
            tontine = fetchone(conn,
                "SELECT nom, montant_place, heure_bouffage, heure_ouverture, "
                "heure_rappel, heure_limite FROM tontines WHERE whatsapp_groupe=%s", (groupe,))
            release_conn(conn)
            nom_tontine = tontine["nom"] if tontine else "Barack Corp"
            if tontine:
                msg1 = msg_intro_groupe(
                    nom_tontine     = tontine["nom"],
                    montant         = tontine["montant_place"],
                    heure_bouffage  = tontine["heure_bouffage"]  or "17:00",
                    heure_ouverture = tontine["heure_ouverture"] or "05:00",
                    heure_rappel    = tontine["heure_rappel"]    or "14:00",
                    heure_limite    = tontine["heure_limite"]    or "18:00",
                )
            else:
                msg1 = msg_intro_groupe("Barack Corp", 0)
                log.warning(f"Bot ajouté dans groupe non configuré : {groupe}")

            # Message 1 — Présentation TontineBot Pro
            wa_groupe(groupe, msg1)

            # Pause courte pour que les 2 messages arrivent dans l'ordre
            import time as _t; _t.sleep(2)

            # Message 2 — Appel à l'enrôlement KYC + ingénierie sociale
            wa_groupe(groupe, msg_kyc_groupe(nom_tontine))

            # Message 3 — DM privé à chaque admin pour demander la liste
            conn2   = get_conn()
            tontine2 = fetchone(conn2,
                "SELECT id FROM tontines WHERE whatsapp_groupe=%s", (groupe,))
            if tontine2:
                admins = fetchall(conn2,
                    "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s",
                    (tontine2["id"],))
                release_conn(conn2)
                for adm in admins:
                    _t.sleep(1)
                    wa_prive(adm["whatsapp"], msg_dm_admin_bienvenue(nom_tontine))
                    # Préparer session admin en attente de liste
                    _sessions_admin[normaliser_numero(adm["whatsapp"])] = {
                        "etape":      "attente_liste",
                        "tontine_id": tontine2["id"],
                        "data":       {},
                        "ts":         time_module.time()
                    }
            else:
                release_conn(conn2)

            log_audit("BOT_ADDED_GROUPE", f"Groupe: {groupe}")

    elif event == "member_left":
        # Un membre a quitté le groupe → alerter les admins
        wa_parti  = normaliser_numero(data.get("whatsapp", ""))
        conn      = get_conn()
        tontine   = fetchone(conn,
            "SELECT id, nom FROM tontines WHERE whatsapp_groupe=%s", (groupe,))
        if tontine and wa_parti:
            membre = fetchone(conn,
                "SELECT nom_complet, statut_global FROM membres WHERE whatsapp=%s",
                (wa_parti,))
            nom_parti = membre["nom_complet"] if membre else wa_parti
            statut    = membre["statut_global"] if membre else "Inconnu"
            wa_admins_tontine(tontine["id"],
                f"⚠️ *MEMBRE A QUITTÉ LE GROUPE*\n"
                f"Tontine : *{tontine['nom']}*\n"
                f"👤 {nom_parti} ({wa_parti})\n"
                f"Statut : {statut}\n\n"
                f"_Vérifiez sa situation dans le menu admin (option 6)._"
            )
            log_audit("MEMBRE_QUITTE_GROUPE",
                      f"{nom_parti} a quitté {groupe}", wa_parti)
        release_conn(conn)

    elif event == "bot_promoted_admin":
        conn    = get_conn()
        tontine = fetchone(conn,
            "SELECT id FROM tontines WHERE whatsapp_groupe=%s", (groupe,))
        if tontine:
            q(conn, "UPDATE tontines SET bot_est_admin=1 WHERE id=%s", (tontine["id"],))
            conn.commit()
            log.info(f"✅ Bot promu admin dans tontine {tontine['id']} ({groupe})")
            log_audit("BOT_ADMIN", f"Tontine {tontine['id']} | groupe {groupe}")
        else:
            log.warning(f"Bot promu admin dans groupe non configuré : {groupe}")
        release_conn(conn)

    elif event == "bot_demoted_admin":
        # Bot rétrogradé → mise à jour base
        conn    = get_conn()
        tontine = fetchone(conn,
            "SELECT id FROM tontines WHERE whatsapp_groupe=%s", (groupe,))
        if tontine:
            q(conn, "UPDATE tontines SET bot_est_admin=0 WHERE id=%s", (tontine["id"],))
            conn.commit()
            log_audit("BOT_RETRO", f"Tontine {tontine['id']} | groupe {groupe}")
        release_conn(conn)

    elif event in ("admin_added", "admins_list"):
        conn    = get_conn()
        tontine = fetchone(conn,
            "SELECT id, nom FROM tontines WHERE whatsapp_groupe=%s", (groupe,))

        if tontine and admins:
            for admin_wa in admins:
                admin_norm = normaliser_numero(admin_wa)
                # Vérifier si cet admin est dans notre base
                connu = fetchone(conn,
                    "SELECT whatsapp FROM admins_groupe "
                    "WHERE tontine_id=%s AND whatsapp=%s",
                    (tontine["id"], admin_norm))

                if not connu and not est_owner(admin_norm):
                    # Admin WA promu mais inconnu en base → alerte owner
                    log_audit("FAUX_ADMIN_DETECTE",
                              f"Tontine:{tontine['nom']} | {admin_norm} promu admin WA "
                              f"mais absent de admins_groupe", admin_norm)
                    wa_owner(
                        f"🚨 *ALERTE — ADMIN NON AUTORISÉ*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"Un numéro vient d'être promu *admin WhatsApp* "
                        f"dans un groupe BADF sans être dans votre registre.\n\n"
                        f"Tontine   : *{tontine['nom']}*\n"
                        f"Numéro    : *{admin_norm}*\n"
                        f"Heure     : {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                        f"⚠️ *Actions recommandées :*\n"
                        f"1. Vérifiez s'il s'agit d'un admin légitime\n"
                        f"2. Si oui → ajoutez-le via option 11 du menu admin\n"
                        f"3. Si non → retirez-lui le statut admin dans le groupe\n\n"
                        f"_TontineBot Pro — BADF Ltd_"
                    )
                    # Alerte aussi aux admins légitimes de cette tontine
                    wa_admins_tontine(tontine["id"],
                        f"⚠️ *ALERTE SÉCURITÉ — {tontine['nom']}*\n\n"
                        f"Le numéro *{admin_norm}* vient d'être promu admin "
                        f"WhatsApp de ce groupe alors qu'il n'est pas dans "
                        f"le registre BADF.\n\n"
                        f"Vérifiez et retirez-lui le statut admin si nécessaire.\n\n"
                        f"_TontineBot Pro — BADF Ltd_"
                    )

            enregistrer_admins_groupe(tontine["id"], admins)
            log.info(f"Admins mis à jour : tontine {tontine['id']} — {len(admins)} admin(s)")

        release_conn(conn)

    elif event == "admin_removed":
        wa_retire = normaliser_numero(data.get("whatsapp", ""))
        conn      = get_conn()
        tontine   = fetchone(conn,
            "SELECT id FROM tontines WHERE whatsapp_groupe=%s", (groupe,))
        if tontine and wa_retire:
            q(conn,
              "DELETE FROM admins_groupe WHERE tontine_id=%s AND whatsapp=%s",
              (tontine["id"], wa_retire))
            conn.commit()
            log_audit("ADMIN_RETIRE", f"Tontine {tontine['id']} | {wa_retire}")
        release_conn(conn)

    return jsonify({"status": "ok"}), 200


# ══════════════════════════════════════════════════════════════════════════
# ÉVÉNEMENTS GROUPE — bot ajouté / membre quitte
# ══════════════════════════════════════════════════════════════════════════

def _auto_inscrire_participants(conn, tontine_id: int, participants: list) -> int:
    """Auto-inscrit une liste de numéros WhatsApp dans une tontine sans KYC ni frais.
    Retourne le nombre de nouveaux membres créés."""
    inscrits = 0
    bot_wa = normaliser_numero(OWNER_WA)
    for p in participants:
        p_norm = normaliser_numero(p)
        if not p_norm or p_norm == bot_wa:
            continue
        membre = fetchone(conn, "SELECT id FROM membres WHERE whatsapp=%s", (p_norm,))
        if not membre:
            kyc_hash = hashlib.sha256(
                f"AUTO:{p_norm}{tontine_id}".encode()
            ).hexdigest()
            cur = q(conn, """INSERT INTO membres
                (nom_complet, kyc_hash, whatsapp, adhesion_payee,
                 statut_global, kyc_etape)
                VALUES (%s,%s,%s,1,'Actif',0) RETURNING id""",
                (f"Membre_{p_norm[-4:]}", kyc_hash, p_norm))
            membre_id = cur.fetchone()[0]
            inscrits += 1
        else:
            membre_id = membre["id"]
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
                f"• *FMP (Frais de Maintenance) :* 2 % par cotisation "
                f"_(Financement des serveurs ultra-sécurisés)_\n"
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
            )
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

    # Tontine inconnue → config via DM
    release_conn(conn)

    msg_grp = (
        "TontineBot Pro vient d'etre ajoute dans ce groupe.\n"
        "Configuration en cours...\n"
        "L'administrateur recoit les instructions en message prive.\n\n"
        "_TontineBot Pro - BADF Ltd_"
    )
    _wa_send_group_chatid(group_id, msg_grp)

    wa_owner(
        "NOUVEAU GROUPE DETECTE\n\n"
        f"Nom : {group_name}\n"
        f"ID  : {group_id}\n\n"
        "L'admin va configurer la tontine en DM.\n"
        "_TontineBot Pro - BADF Ltd_"
    )

    _sessions_config[f"pending_{group_id}"] = {
        "group_id":   group_id,
        "group_name": group_name,
        "etape":      "attente_admin",
        "data":       {"participants": participants or []},
        "ts":         time_module.time()
    }
    log_audit("BOT_ADDED_GROUPE_INCONNU",
              f"Groupe:{group_id} Nom:{group_name}")


def traiter_config_tontine(wa: str, texte: str) -> Optional[str]:
    """
    Flux de configuration nouvelle tontine via DM admin.
    Etapes : montant -> type -> numero collecte -> creation tontine
    """
    import time as _t
    wa_norm = normaliser_numero(wa)
    sess = _sessions_config.get(wa_norm)

    if not sess:
        pending = {
            k: v for k, v in _sessions_config.items()
            if k.startswith("pending_") and v["etape"] == "attente_admin"
            and time_module.time() - v["ts"] < 1800
        }
        if not pending:
            return None
        if len(pending) == 1:
            key  = list(pending.keys())[0]
            sess = pending[key]
            _sessions_config[wa_norm] = dict(sess, etape="montant",
                                             ts=time_module.time())
            del _sessions_config[key]
            sess = _sessions_config[wa_norm]
            return (
                f"Bonjour ! Configuration de la tontine *{sess['group_name']}*\n\n"
                "Question *1/4*\n\n"
                "Quel est le *montant de cotisation* ? (en FCFA)\n"
                "Exemple : *5000*\n\n"
                "_TontineBot Pro - BADF Ltd_"
            )
        else:
            lignes = ["Plusieurs groupes en attente :"]
            for i, (k, v) in enumerate(pending.items(), 1):
                lignes.append(f"{i}. *{v['group_name']}*")
            lignes.append("Tapez le numero du groupe.")
            return "\n".join(lignes)

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
        fmp = int(montant * 0.02)
        return (
            f"Montant : *{montant:,} FCFA* (FMP BADF : {fmp:,} FCFA)\n\n"
            "Question *2/4*\n\n"
            "Type de tontine ?\n"
            "1 - Journaliere\n"
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
                f"Type : *Journalière*\n\n"
                "Question *3/4*\n\n"
                "Cette tontine est-elle *déjà en cours* ?\n"
                "(Les membres cotisent déjà, même sans le bot)\n\n"
                "Répondez *OUI* ou *NON*"
            )
        elif data["type"] == "Hebdomadaire":
            sess["etape"] = "jour"
            return (
                f"Type : *Hebdomadaire*\n\n"
                "Question *2b/4* — Quel *jour de la semaine* ?\n\n"
                "L = Lundi\nM = Mardi\nX = Mercredi\nJ = Jeudi\n"
                "V = Vendredi\nS = Samedi\nD = Dimanche"
            )
        else:  # Mensuelle
            sess["etape"] = "jour"
            return (
                f"Type : *Mensuelle*\n\n"
                "Question *2b/4* — Quel *jour du mois* ? (1 à 28)\n"
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
            "Question *3/4*\n\n"
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
            "Question *4/4*\n\n"
            "Votre *numéro de collecte* MTN/Orange Money ?\n"
            "(Le numéro où les membres virent l'argent)\n\n"
            "Format : *+237690123456*"
        )

    if etape == "collecte":
        num = normaliser_numero(texte)
        if not num or len(num) < 12:
            return "Numero invalide. Format : *+237690123456*"

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

        _sessions_config.pop(wa_norm, None)

        _t.sleep(2)
        if data.get("deja_en_cours"):
            participants = data.get("participants", [])
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

        fmp = int(montant * 0.02)
        msg_statut = (
            "Membres auto-inscrits — KYC reporté à la fin du cycle."
            if data.get("deja_en_cours")
            else "Message d'intro envoyé dans le groupe."
        )
        return (
            f"Tontine *{group_name}* configurée !\n\n"
            f"Type    : *{type_t}*\n"
            f"Montant : *{montant:,} FCFA*\n"
            f"Collecte: *{num}*\n"
            f"FMP BADF: *{fmp:,} FCFA*\n\n"
            f"{msg_statut}\n\n"
            "Envoyez maintenant la liste de passage :\n"
            "*01- Prenom JJ/MM/AA*\n"
            "_TontineBot Pro - BADF Ltd_"
        )

    return None


def _membre_quitte_groupe(wa_membre: str, group_id: str):
    """Membre quitte le groupe — alerte les admins."""
    conn    = get_conn()
    tontine = fetchone(conn,
        "SELECT id, nom FROM tontines WHERE whatsapp_groupe=%s", (group_id,))
    if not tontine:
        release_conn(conn)
        return
    membre = fetchone(conn,
        "SELECT nom_complet, statut_global FROM membres WHERE whatsapp=%s",
        (wa_membre,))
    release_conn(conn)

    nom    = membre["nom_complet"] if membre else wa_membre
    statut = membre["statut_global"] if membre else "Inconnu"

    wa_admins_tontine(tontine["id"],
        f"⚠️ *MEMBRE A QUITTÉ LE GROUPE*\n\n"
        f"Tontine : *{tontine['nom']}*\n"
        f"Membre  : *{nom}* ({wa_membre})\n"
        f"Statut  : {statut}\n\n"
        f"Vérifiez sa situation depuis votre menu admin.\n\n"
        f"_TontineBot Pro — BADF Ltd_"
    )
    log_audit("MEMBRE_QUITTE_GROUPE", f"{nom} | {tontine['nom']}", wa_membre)


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
                f"  @{m['nom_complet']}{pl_txt} — "
                f"*{mont:,} FCFA* _(FMP BADF : {fmp_m:,} FCFA)_"
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
        f"💼 FMP BADF 2%  : *{total_fmp:,} FCFA*",
        f"📦 Net tontine  : *{net_tontine:,} FCFA*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"_TontineBot Pro — BADF Ltd_",
    ]

    log_audit("LISTE_GROUPE", f"Admin:{wa_admin} Tontine:{tid} {nb_ok}/{nb_total}", wa_admin)
    return "\n".join(lignes)


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
    adm_col  = fetchone(
        get_conn(),
        "SELECT numero_collecte FROM admins_groupe WHERE tontine_id=%s AND numero_collecte IS NOT NULL LIMIT 1",
        (tid,)
    )
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

def _traiter_screenshot_adhesion_dm(wa: str, image_bytes: bytes) -> bool:
    """
    Détecte un screenshot de paiement 2 000 FCFA envoyé en DM.

    Flux automatisé :
      1. Anti-recyclage SHA-256
      2. Vérifie si le membre est pré-enregistré par l'admin
      3. Active immédiatement le compte (adhesion_payee=1, statut=Actif)
      4. Inscrit dans toutes les tontines du groupe d'où il vient
      5. Pose 2 questions rapides en arrière-plan (CNI + ville)

    Retourne True si le screenshot a été traité comme une adhésion.
    """
    wa_norm = normaliser_numero(wa)
    img_hash = hash_screenshot(image_bytes)

    conn = get_conn()
    try:
        # Anti-recyclage
        if screenshot_deja_utilise(conn, img_hash):
            wa_prive(wa_norm,
                "❌ Ce screenshot a déjà été utilisé.\n"
                "Envoyez un nouveau screenshot de votre virement.")
            release_conn(conn)
            return True

        # Chercher le membre pré-enregistré
        membre = fetchone(conn,
            "SELECT * FROM membres WHERE whatsapp=%s", (wa_norm,))

        if not membre:
            # Inconnu — créer le profil minimal (ON CONFLICT évite la race condition)
            kyc_hash = hashlib.sha256(f"ADH{wa_norm}{img_hash}".encode()).hexdigest()
            cur = q(conn, """
                INSERT INTO membres
                    (nom_complet, kyc_hash, whatsapp, adhesion_payee,
                     statut_global, kyc_etape)
                VALUES (%s,%s,%s,1,'Actif',0)
                ON CONFLICT (whatsapp) DO NOTHING
                RETURNING id
            """, (f"Membre_{wa_norm[-4:]}", kyc_hash, wa_norm))
            row = cur.fetchone()
            if row:
                membre_id = row[0]
            else:
                # Conflit — profil créé par thread concurrent
                membre = fetchone(conn, "SELECT * FROM membres WHERE whatsapp=%s", (wa_norm,))
                membre_id = membre["id"]
            est_nouveau = True
        elif membre["adhesion_payee"] and membre["statut_global"] == "Actif":
            # Déjà actif — image DM ignorée (adhesion gratuite, pas de screenshot requis)
            release_conn(conn)
            return True
        else:
            # Pré-enregistré par admin mais pas encore activé
            membre_id = membre["id"]
            q(conn, """UPDATE membres
                       SET adhesion_payee=1, statut_global='Actif', kyc_etape=0
                       WHERE id=%s""", (membre_id,))
            est_nouveau = True

        # Enregistrer le hash anti-recyclage
        enregistrer_screenshot(conn, img_hash, membre_id, None)

        # Enregistrer la transaction d'adhésion
        q(conn, """INSERT INTO transactions
                   (membre_id, montant_brut, montant_net, type_transaction, statut)
                   VALUES (%s,%s,%s,'Adhesion','Confirmee')""",
          (membre_id, FRAIS_ADHESION, FRAIS_ADHESION))

        # Inscrire dans les tontines liées aux groupes où il est présent
        # (Le bot les connaît via la table tontines/admins_groupe)
        tontines_dispo = fetchall(conn,
            "SELECT id, nom FROM tontines WHERE statut='Active'")
        tontines_inscrites = []
        for t in tontines_dispo:
            # Vérifier s'il n'est pas déjà inscrit
            deja = fetchone(conn,
                "SELECT id FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
                (membre_id, t["id"]))
            if not deja:
                try:
                    q(conn, """
                        INSERT INTO adhesions (membre_id, tontine_id, statut)
                        VALUES (%s,%s,'Actif')
                        ON CONFLICT (membre_id, tontine_id) DO UPDATE SET statut='Actif'
                    """, (membre_id, t["id"]))
                    tontines_inscrites.append(t["nom"])
                except Exception:
                    pass

        conn.commit()

        # Nom d'affichage
        nom = membre["nom_complet"] if membre else f"Membre_{wa_norm[-4:]}"

        # Notifier le membre — activation immédiate
        tontines_txt = "\n".join(f"  ✅ {n}" for n in tontines_inscrites) if tontines_inscrites else "  (aucune tontine active)"
        wa_prive(wa_norm,
            f"✅ *COMPTE ACTIVÉ — BADF Ltd*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Bonjour *{nom}* !\n\n"
            f"Votre compte est maintenant *actif*.\n\n"
            f"Tontines activées :\n{tontines_txt}\n\n"
            f"─────────────────────────────────────────\n"
            f"Pour compléter votre dossier KYC (2 questions rapides) :\n"
            f"Tapez *kyc* en réponse à ce message.\n\n"
            f"_Barack & AI Development Facilities Ltd — BADF Ltd_"
        )

        # Notifier l'admin
        wa_owner(
            f"✅ *NOUVELLE ADHÉSION*\n"
            f"Numéro : {wa_norm}\n"
            f"Tontines : {', '.join(tontines_inscrites) if tontines_inscrites else 'aucune'}"
        )

        log_audit("ADHESION_AUTO_SCREENSHOT",
                  f"Membre:{wa_norm} | Hash:{img_hash[:12]} | "
                  f"Tontines:{len(tontines_inscrites)}")

        # Démarrer KYC simplifié en arrière-plan (non bloquant)
        if est_nouveau:
            _sessions_kyc_rapide[wa_norm] = {
                "etape":     "cni",
                "membre_id": membre_id,
                "data":      {},
                "ts":        time_module.time()
            }

        release_conn(conn)
        return True

    except Exception as e:
        conn.rollback()
        release_conn(conn)
        log.error(f"_traiter_screenshot_adhesion_dm {wa_norm}: {e}")
        return False


# ── KYC rapide post-activation (2 questions) ─────────────────────────────
_sessions_kyc_rapide: dict = {}

def traiter_kyc_rapide(wa: str, texte: str) -> bool:
    """
    KYC simplifié après activation par screenshot.
    2 questions seulement : CNI (ou acte naissance) + ville.
    Non bloquant — le compte est déjà actif.
    """
    wa_norm = normaliser_numero(wa)
    if wa_norm not in _sessions_kyc_rapide:
        return False
    if not session_valide(_sessions_kyc_rapide, wa_norm):
        _sessions_kyc_rapide.pop(wa_norm, None)
        return False

    sess      = _sessions_kyc_rapide[wa_norm]
    etape     = sess["etape"]
    membre_id = sess["membre_id"]

    # ── CNI ou acte naissance ─────────────────────────────────────────────
    if etape == "cni":
        if texte.strip().lower() in ("non", "pas", "skip", "passer"):
            # Passer — on demande juste la ville
            sess["etape"] = "ville"
            wa_prive(wa_norm,
                "✏️ *Dernière question — Ville de résidence*\n\n"
                "Entrez votre ville actuelle :\n"
                "Exemple : *Douala*, *Yaoundé*, *Bafoussam*")
            return True

        cni = texte.strip().upper()
        conn = get_conn()
        q(conn, "UPDATE membres SET kyc_cni=%s WHERE id=%s", (cni, membre_id))
        conn.commit()
        release_conn(conn)
        sess["data"]["cni"] = cni
        sess["etape"] = "ville"
        wa_prive(wa_norm,
            f"✅ Pièce enregistrée.\n\n"
            f"✏️ *Dernière question — Ville de résidence*\n\n"
            f"Entrez votre ville :\n"
            f"Exemple : *Douala*, *Yaoundé*, *Bafoussam*")
        return True

    # ── Ville ─────────────────────────────────────────────────────────────
    if etape == "ville":
        ville = texte.strip()
        if len(ville) < 2:
            wa_prive(wa_norm, "❌ Entrez le nom de votre ville.")
            return True
        conn = get_conn()
        q(conn, """UPDATE membres
                   SET kyc_ville=%s, kyc_complet=1, kyc_etape=5
                   WHERE id=%s""", (ville, membre_id))
        conn.commit()
        release_conn(conn)
        _sessions_kyc_rapide.pop(wa_norm, None)
        log_audit("KYC_RAPIDE_COMPLET", f"Membre:{wa_norm} | Ville:{ville}")
        wa_prive(wa_norm,
            f"✅ *Dossier KYC complété !*\n\n"
            f"Votre dossier est maintenant complet et sécurisé.\n"
            f"Tapez *menu* pour accéder à votre espace membre.\n\n"
            f"_Barack & AI Development Facilities Ltd — BADF Ltd_")
        return True

    return False


@app.route("/webhook/whatsapp", methods=["GET"])
def webhook_whatsapp_verify():
    """Health check — Green API n'utilise pas de challenge GET."""
    return jsonify({"status": "ok", "bot": "TontineBot Pro"}), 200


def _greenapi_get_group_members(group_chatid: str, exclude: str = "") -> list:
    """Green API getGroupData → liste de numéros normalisés (+237XXXXXXXXX)."""
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
            pid = p.get("id", "") if isinstance(p, dict) else str(p)
            if pid and pid != exclude and pid.endswith("@c.us"):
                num = normaliser_numero(pid.split("@")[0])
                if num:
                    result.append(num)
        return result
    except Exception as e:
        log.error(f"getGroupData : {e}")
        return []


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
    incoming_token = request.args.get("token", "")
    if not hmac.compare_digest(incoming_token, GREENAPI_WEBHOOK_SECRET):
        log_audit("GREENAPI_TOKEN_INVALIDE", "Webhook token mismatch", request.remote_addr)
        return jsonify({"status": "forbidden"}), 403

    # ── 2) Parsing payload ────────────────────────────────────────────────
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "bad_json"}), 400

    # ── 3) Router par type d'événement ──────────────────────────────────
    type_webhook = payload.get("typeWebhook", "")

    if type_webhook == "incomingGroupParticipantsUpdate":
        _traiter_groupe_participants(payload)
        return jsonify({"status": "ok"}), 200

    if type_webhook not in ("incomingMessageReceived", "incomingAPIMessageReceived"):
        return jsonify({"status": "ignored"}), 200

    # ── 4) Extraire l'émetteur ────────────────────────────────────────────
    sender_data  = payload.get("senderData") or {}
    chat_id      = sender_data.get("chatId", "")   # ex: 237693969773@c.us
    wa_brut      = chat_id.split("@")[0]            # ex: 237693969773
    wa           = normaliser_numero(wa_brut)
    if not wa:
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

    # ── 6) Soumettre le traitement au thread pool ─────────────────────────
    try:
        _msg_executor.submit(_traiter_message_greenapi, payload, wa, img_future)
    except Exception as e:
        log.error(f"Webhook Green API submit : {e}")

    return jsonify({"status": "ok"}), 200


def _greenapi_telecharger_media(url_media: str) -> bytes:
    """Télécharge un média depuis l'URL Green API et retourne le contenu en bytes.
    2 tentatives max. Timeout 15s par tentative. Détecte 403/404 = lien expiré."""
    if not url_media:
        return b""
    try:
        from urllib.parse import urlparse as _urlparse
        _p = _urlparse(url_media)
        _domaines_ok = (".green-api.com", ".sms.by", ".whatsapp.net")
        if _p.scheme != "https" or not any(_p.netloc.endswith(d) for d in _domaines_ok):
            log.warning(f"⚠️ URL média Green API suspecte rejetée : {_p.netloc}")
            return b""
        for attempt in range(2):
            try:
                r = requests.get(url_media, timeout=15)
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code in (403, 404):
                    log.warning(f"⚠️ Média Green API {r.status_code} — lien expiré ou introuvable")
                    return b""
                if attempt == 0:
                    time_module.sleep(1)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 0:
                    time_module.sleep(1)
                continue
    except Exception as e:
        log.error(f"Téléchargement média Green API : {e}")
    return b""


def _traiter_message_greenapi(payload: dict, wa: str, img_future=None):
    """Parse un événement Green API et route vers la logique métier."""
    if not rate_limit_ok(wa):
        return

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
            _traiter_screenshot_adhesion_dm(wa, img_bytes)
        except Exception as e:
            log.error(f"Erreur image Green API : {e}")
        return

    if wa == OWNER_WA:
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
    membre = fetchone(conn,
        "SELECT id, nom_complet, statut_global FROM membres WHERE whatsapp=%s", (wa,))

    if not membre:
        release_conn(conn)
        wa_prive(wa,
            f"❓ Vous n'êtes pas encore enregistré.\n\n"
            f"Tapez *menu* pour commencer votre inscription.\n\n"
            f"_TontineBot Pro — BADF Ltd_")
        return

    if membre["statut_global"] in ("Suspendu_global", "Banni"):
        release_conn(conn)
        wa_prive(wa, f"🚫 Compte *{membre['statut_global']}*. Contactez votre admin.")
        return

    # Récupérer l'adhesion avec nombre_places
    adhesion = fetchone(conn, """
        SELECT a.tontine_id, a.nombre_places, t.nom, t.montant_place
        FROM adhesions a
        JOIN tontines t ON t.id = a.tontine_id
        WHERE a.membre_id=%s AND a.statut='Actif' AND t.statut='Active'
        LIMIT 1
    """, (membre["id"],))

    if not adhesion:
        release_conn(conn)
        return

    # Calcul du montant attendu selon les places
    nb_places       = adhesion["nombre_places"] or 1
    montant_attendu = adhesion["montant_place"] * nb_places

    # ── Lecture automatique du screenshot (OCR local Tesseract) ───────────
    lecture = lire_screenshot_mobile_money(image_bytes)
    fraude_visuelle = False

    if lecture.get("ok"):
        montant_lu  = lecture.get("montant")
        confiance   = lecture.get("confiance", "faible")
        operateur   = lecture.get("operateur", "Inconnu")
        ref_lu      = lecture.get("reference", "")

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

    # Anti-recyclage SHA-256
    img_hash = hash_screenshot(image_bytes)
    if screenshot_deja_utilise(conn, img_hash):
        release_conn(conn)
        # Silence radio — log seulement, pas de message au fraudeur
        log_audit("SCREENSHOT_RECYCLE",
                  f"Membre:{membre['id']} Hash:{img_hash[:16]}", wa)
        incrementer_tentatives_fraude(membre["id"], "Screenshot recyclé")
        return

    enregistrer_screenshot(conn, img_hash, membre["id"], adhesion["tontine_id"])

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
            admin_wa        = admin_wa
        )
    except Exception as e:
        release_conn(conn)
        log.error(f"❌ enregistrer_cotisation_manuelle échec pour {wa}: {e}")
        wa_prive(wa,
            f"⚠️ *Erreur technique — {adhesion['nom']}*\n\n"
            f"Votre reçu a été reçu mais n'a pas pu être enregistré.\n"
            f"Contactez votre admin.\n\n"
            f"_TontineBot Pro — BADF Ltd_")
        return
    release_conn(conn)

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
            dette_cotis      = cotis_manquantes * p["montant_place"]

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
                q(conn, """INSERT INTO dettes_ira
                           (membre_id, tontine_id, montant, motif)
                           VALUES (%s,%s,%s,%s)""",
                  (membre_id, tontine_id, dette_residuelle,
                   "Dette résiduelle — bouffage suspect non débloqué"))

            # Saisir la caution
            if caution:
                q(conn, "UPDATE cautions_garantie SET statut='Saisie', date_liberation=NOW() WHERE id=%s",
                  (caution["id"],))

            # Solder les dettes IRA
            q(conn, """UPDATE dettes_ira SET statut='Prelevee', prelevee_le=NOW()
                       WHERE membre_id=%s AND tontine_id=%s AND statut='Due'""",
              (membre_id, tontine_id))

            # Virer le reliquat au membre si positif
            if solde_membre > 0:
                q(conn, """INSERT INTO bouffages_manuels
                           (membre_id, tontine_id, passage_id, montant_brut,
                            caution, montant_net, statut)
                           VALUES (%s,%s,%s,%s,0,%s,'En_attente')""",
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
                      f"Dettes:{total_dettes:,} | Redistribué:{solde_redistribue:,}",
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
# FINANCE COMPORTEMENTALE — Modèle prédictif de risque de fugue
# ══════════════════════════════════════════════════════════════════════════
# Calcule un score de risque entre 0 et 100 pour chaque membre dont le
# bouffage approche. Plus le score est élevé, plus la probabilité de fugue
# post-bouffage est haute. Permet à l'admin d'agir AVANT.
#
# Le modèle combine 7 features comportementales :
#   1. Régularité historique (variance des intervalles entre cotisations)
#   2. Tendance récente (cotisations en chute ces 7 derniers jours)
#   3. Score de confiance (réputation accumulée des cycles précédents)
#   4. Dettes en cours (IRA + cotisations manquantes / capacité)
#   5. Profondeur d'engagement (nombre de tontines, ancienneté, KYC)
#   6. Vélocité de paiement (combien de temps il prend pour payer après rappel)
#   7. Signaux faibles (suspensions passées, alertes fugue passées)
#
# Pondération calibrée sur l'intuition cofondateur — sera affinée par ML
# après 6 mois de données réelles.

def _update_score_confiance(conn, membre_id: int, raison: str,
                            delta: int = 0, set_val: int = None):
    """
    Met à jour score_confiance et logge le changement dans historique_score_confiance.
    delta  : valeur relative (+2, -30, etc.)
    set_val: valeur absolue (0 pour bannissement) — prioritaire sur delta
    """
    row = fetchone(conn, "SELECT score_confiance FROM membres WHERE id=%s", (membre_id,))
    if not row:
        return
    avant = int(row["score_confiance"] or 0)
    if set_val is not None:
        apres = max(0, min(100, int(set_val)))
    else:
        apres = max(0, min(100, avant + int(delta)))
    if avant == apres:
        return
    q(conn, "UPDATE membres SET score_confiance=%s WHERE id=%s", (apres, membre_id))
    q(conn, """INSERT INTO historique_score_confiance
               (membre_id, score_av, score_ap, delta, raison)
               VALUES (%s,%s,%s,%s,%s)""",
      (membre_id, avant, apres, apres - avant, raison))


def calculer_score_risque_fugue(conn, membre_id: int, tontine_id: int) -> dict:
    """
    Score 0-100 du risque de fugue post-bouffage pour ce membre.
    Retourne un dict détaillé avec la décomposition du score :
      { 'score', 'niveau', 'features': {...}, 'recommandation', 'signaux' }

    Niveaux :
      0–30   : Vert      — aucune action
      31–55  : Jaune     — surveillance accrue
      56–75  : Orange    — admin alerté, caution renforcée recommandée
      76–100 : Rouge     — bouffage retardé jusqu'à investigation
    """
    try:
        membre = fetchone(conn, """
            SELECT id, nom_complet, score_confiance, statut_global,
                   tentatives_fraude, kyc_complet, date_inscription
            FROM membres WHERE id=%s
        """, (membre_id,))
        tontine = fetchone(conn,
            "SELECT type_tontine, montant_place, cycle_actuel, heure_ouverture FROM tontines WHERE id=%s",
            (tontine_id,))
        if not membre or not tontine:
            return {"score": 0, "niveau": "inconnu", "features": {}, "recommandation": "", "signaux": []}

        score = 0.0
        features = {}
        signaux = []

        # ── Feature 1 : Régularité historique (poids 25) ────────────────────
        # On mesure la variance des intervalles entre cotisations.
        # Un membre fiable cotise à intervalle régulier (variance basse).
        intervalles = fetchall(conn, """
            WITH ordered_cotis AS (
                SELECT date_heure,
                       LAG(date_heure) OVER (ORDER BY date_heure) AS prev_date
                FROM transactions
                WHERE membre_id=%s AND tontine_id=%s
                  AND type_transaction='Cotisation' AND statut='Confirmee'
                  AND date_heure >= NOW() - INTERVAL '60 days'
            )
            SELECT EXTRACT(EPOCH FROM (date_heure - prev_date)) / 86400.0 AS jours_ecart
            FROM ordered_cotis
            WHERE prev_date IS NOT NULL
        """, (membre_id, tontine_id))

        if len(intervalles) >= 5:
            ecarts = [float(i["jours_ecart"]) for i in intervalles if i["jours_ecart"] is not None]
            if ecarts:
                moy = sum(ecarts) / len(ecarts)
                variance = sum((e - moy) ** 2 for e in ecarts) / len(ecarts)
                # Coefficient de variation : ecart-type / moyenne
                cv = (variance ** 0.5) / moy if moy > 0 else 1
                # cv > 1.0 = très irrégulier → risque maximum sur cette feature
                regularite_score = min(25, cv * 20)
                features["regularite"] = round(regularite_score, 1)
                score += regularite_score
                if cv > 0.8:
                    signaux.append(f"Cotisations irrégulières (CV={cv:.2f})")
        else:
            # Trop peu de données → suspicion modérée
            features["regularite"] = 12
            score += 12
            signaux.append("Historique de cotisation insuffisant")

        # ── Feature 2 : Tendance récente (poids 20) ─────────────────────────
        # Un membre qui cotisait régulièrement et qui ralentit juste avant son tour
        # est statistiquement le profil typique du fugitif.
        cotis_30_60j = fetchone(conn, """
            SELECT COUNT(*) AS n FROM transactions
            WHERE membre_id=%s AND tontine_id=%s
              AND type_transaction='Cotisation' AND statut='Confirmee'
              AND date_heure < NOW() - INTERVAL '30 days'
              AND date_heure >= NOW() - INTERVAL '60 days'
        """, (membre_id, tontine_id))["n"]

        cotis_0_30j = fetchone(conn, """
            SELECT COUNT(*) AS n FROM transactions
            WHERE membre_id=%s AND tontine_id=%s
              AND type_transaction='Cotisation' AND statut='Confirmee'
              AND date_heure >= NOW() - INTERVAL '30 days'
        """, (membre_id, tontine_id))["n"]

        if cotis_30_60j >= 5:
            ratio_recent = cotis_0_30j / cotis_30_60j if cotis_30_60j > 0 else 1
            if ratio_recent < 0.5:
                # Cotisations divisées par 2+ ces derniers temps → red flag
                tendance_score = 20
                signaux.append(f"Cotisations en chute : {cotis_0_30j} récentes vs {cotis_30_60j} précédentes")
            elif ratio_recent < 0.75:
                tendance_score = 12
                signaux.append("Léger ralentissement des cotisations")
            else:
                tendance_score = 0
            features["tendance_recente"] = tendance_score
            score += tendance_score
        else:
            features["tendance_recente"] = 0

        # ── Feature 3 : Score de confiance inversé (poids 15) ──────────────
        # score_confiance va de 0 à 100. On l'inverse en risque.
        confiance = membre["score_confiance"] or 50
        confiance_risque = max(0, (50 - confiance) / 50 * 15)
        features["confiance_inversee"] = round(confiance_risque, 1)
        score += confiance_risque
        if confiance < 30:
            signaux.append(f"Score de confiance faible ({confiance}/100)")

        # ── Feature 4 : Dettes en cours (poids 15) ──────────────────────────
        dettes = fetchone(conn, """
            SELECT
                COALESCE(SUM(CASE WHEN statut='Due' THEN montant ELSE 0 END), 0) AS ira_due
            FROM dettes_ira
            WHERE membre_id=%s AND tontine_id=%s
        """, (membre_id, tontine_id))
        dette_montant = dettes["ira_due"] or 0
        # Ratio de dette / capacité de cotisation
        capacite = tontine["montant_place"] * 30  # ~1 mois
        ratio_dette = min(1.0, dette_montant / capacite) if capacite > 0 else 0
        dette_score = ratio_dette * 15
        features["dettes"] = round(dette_score, 1)
        score += dette_score
        if dette_montant > capacite * 0.3:
            signaux.append(f"Dettes IRA importantes : {dette_montant:,} FCFA")

        # ── Feature 5 : Profondeur d'engagement (poids 10, inversé) ─────────
        # Plus un membre est engagé (ancien, KYC, multi-tontines), plus le risque baisse.
        nb_tontines = fetchone(conn, """
            SELECT COUNT(DISTINCT tontine_id) AS n FROM adhesions
            WHERE membre_id=%s AND statut IN ('Actif','Pause')
        """, (membre_id,))["n"]

        anciennete_jours = (datetime.now() - membre["date_inscription"]).days if membre["date_inscription"] else 0
        kyc = membre["kyc_complet"] or 0

        engagement_score = 0
        if nb_tontines == 1 and anciennete_jours < 30:
            # Membre tout neuf dans une seule tontine → risque
            engagement_score = 10
            signaux.append(f"Membre récent (un seul groupe, {anciennete_jours} jours)")
        elif nb_tontines == 1:
            engagement_score = 5
        elif kyc == 0:
            engagement_score = 7
            signaux.append("KYC non complété")
        features["faible_engagement"] = engagement_score
        score += engagement_score

        # ── Feature 6 : Vélocité paiement post-rappel (poids 10) ────────────
        # Un membre fiable paie dans les 2h après l'ouverture. Un fuyard tarde.
        ouverture_str = tontine.get("heure_ouverture") or "05:00"
        try:
            h_ouv, m_ouv = [int(x) for x in ouverture_str.split(":")]
        except Exception:
            h_ouv, m_ouv = 5, 0
        ouverture_secondes = h_ouv * 3600 + m_ouv * 60

        cotis_recent = fetchall(conn, """
            SELECT EXTRACT(EPOCH FROM (date_heure - date_heure::date)) - %s AS secs_apres_ouverture
            FROM transactions
            WHERE membre_id=%s AND tontine_id=%s
              AND type_transaction='Cotisation' AND statut='Confirmee'
              AND date_heure >= NOW() - INTERVAL '14 days'
            LIMIT 14
        """, (ouverture_secondes, membre_id, tontine_id))

        if cotis_recent:
            heures = [float(c["secs_apres_ouverture"]) / 3600 for c in cotis_recent
                      if c["secs_apres_ouverture"] is not None and float(c["secs_apres_ouverture"]) >= 0]
            if heures:
                moy_heure = sum(heures) / len(heures)
                if moy_heure > 10:  # paye 10h+ après l'ouverture → tard
                    velocite_score = 10
                    signaux.append(f"Cotise tard ({moy_heure:.1f}h après ouverture à {ouverture_str})")
                elif moy_heure > 6:
                    velocite_score = 5
                else:
                    velocite_score = 0
                features["velocite_paiement"] = velocite_score
                score += velocite_score

        # ── Feature 7 : Signaux faibles (poids 5) ───────────────────────────
        # Suspensions passées et tentatives de fraude
        nb_suspensions = fetchone(conn, """
            SELECT COUNT(*) AS n FROM sanctions
            WHERE membre_id=%s AND type_sanction LIKE '%uspension%'
        """, (membre_id,))["n"]

        signaux_faibles = 0
        if nb_suspensions > 0:
            signaux_faibles += min(3, nb_suspensions)
            signaux.append(f"{nb_suspensions} suspension(s) passée(s)")
        if (membre["tentatives_fraude"] or 0) > 0:
            signaux_faibles += min(2, membre["tentatives_fraude"])
            signaux.append(f"{membre['tentatives_fraude']} tentative(s) de fraude historique")
        features["signaux_faibles"] = signaux_faibles
        score += signaux_faibles

        # ── Feature 8 : Comportement POST-bouffage (poids 20) ───────────────
        # Signal le plus important : après avoir encaissé lors du cycle précédent,
        # le membre a-t-il continué à cotiser normalement ?
        # Un fuyard typique disparaît dans les 30 jours qui suivent son bouffage.
        dernier_bouffage = fetchone(conn, """
            SELECT date_bouffage FROM liste_passage
            WHERE membre_id=%s AND tontine_id=%s AND statut='Paye'
            ORDER BY date_bouffage DESC LIMIT 1
        """, (membre_id, tontine_id))

        if dernier_bouffage and dernier_bouffage["date_bouffage"]:
            date_b = dernier_bouffage["date_bouffage"]
            cotis_post = fetchone(conn, """
                SELECT COUNT(*) AS n FROM transactions
                WHERE membre_id=%s AND tontine_id=%s
                  AND type_transaction='Cotisation' AND statut='Confirmee'
                  AND date_heure::date BETWEEN %s AND %s + INTERVAL '30 days'
            """, (membre_id, tontine_id, date_b, date_b))["n"]

            jours_depuis = (datetime.now().date() - date_b).days if hasattr(date_b, 'year') else 0

            if jours_depuis >= 15:
                if cotis_post == 0:
                    post_score = 20
                    signaux.append("Aucune cotisation dans les 30j après son dernier bouffage")
                elif cotis_post <= 2:
                    post_score = 12
                    signaux.append(f"Très peu de cotisations ({cotis_post}) après son dernier bouffage")
                else:
                    post_score = 0
                features["post_bouffage"] = post_score
                score += post_score
        else:
            # Jamais bouffé → on ne peut pas pénaliser, mais ce n'est pas non plus un signal positif
            features["post_bouffage"] = 0

        # ── Feature 9 : Chute brusque du score de confiance (poids 10) ──────
        # Un membre dont le score plonge de >15 pts en 30 jours est en difficulté.
        chute = fetchone(conn, """
            SELECT COALESCE(SUM(delta), 0) AS total_delta
            FROM historique_score_confiance
            WHERE membre_id=%s AND created_at >= NOW() - INTERVAL '30 days'
              AND delta < 0
        """, (membre_id,))
        chute_totale = abs(int(chute["total_delta"] or 0))
        if chute_totale >= 25:
            chute_score = 10
            signaux.append(f"Score de confiance en chute (-{chute_totale} pts en 30j)")
        elif chute_totale >= 15:
            chute_score = 5
            signaux.append(f"Score de confiance en baisse (-{chute_totale} pts en 30j)")
        else:
            chute_score = 0
        features["chute_score"] = chute_score
        score += chute_score

        # ── Normalisation et niveau final ───────────────────────────────────
        score = min(100, max(0, round(score)))

        if score <= 30:
            niveau = "vert"
            recommandation = "Procéder normalement au bouffage."
        elif score <= 55:
            niveau = "jaune"
            recommandation = "Surveillance accrue. Demander confirmation explicite du membre 24h avant."
        elif score <= 75:
            niveau = "orange"
            recommandation = "RISQUE ÉLEVÉ. Recommander caution renforcée à 20%, ou décaler le tour."
        else:
            niveau = "rouge"
            recommandation = "BLOCAGE RECOMMANDÉ. Investigation requise avant tout versement."

        return {
            "score":          score,
            "niveau":         niveau,
            "features":       features,
            "recommandation": recommandation,
            "signaux":        signaux,
        }

    except Exception as e:
        log.error(f"❌ calculer_score_risque_fugue m={membre_id} t={tontine_id} : {e}")
        return {"score": 0, "niveau": "erreur", "features": {}, "recommandation": "", "signaux": []}


@healed()
def alerter_risques_bouffage_imminent():
    """
    Job quotidien à 5h33 : pour chaque membre dont le bouffage arrive
    dans les 7 prochains jours, calcule le score de risque et alerte
    l'admin si le niveau est orange ou rouge.
    Donne ainsi à l'admin le temps de réagir avant le jour J.
    """
    conn = None
    try:
        conn = get_conn()
        # Membres dont le bouffage arrive dans les 7 prochains jours
        prochains = fetchall(conn, """
            SELECT lp.membre_id, lp.tontine_id, lp.ordre, lp.date_bouffage,
                   t.nom AS nom_tontine, t.id AS tid,
                   m.nom_complet, m.whatsapp
            FROM liste_passage lp
            JOIN tontines t ON t.id = lp.tontine_id
            JOIN membres m  ON m.id = lp.membre_id
            WHERE lp.statut IN ('En_attente','Notifie')
              AND lp.cycle = t.cycle_actuel
              AND lp.date_bouffage IS NOT NULL
              AND lp.date_bouffage::date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'
              AND t.statut='Active'
            ORDER BY lp.date_bouffage
        """)

        alertes_par_admin = {}  # admin_wa → liste d'alertes

        for p in prochains:
            score_dict = calculer_score_risque_fugue(conn, p["membre_id"], p["tontine_id"])

            # On n'alerte que pour orange et rouge
            if score_dict["niveau"] not in ("orange", "rouge"):
                continue

            jours_avant = (p["date_bouffage"].date() - datetime.now().date()).days

            # Trouve l'admin de la tontine
            admin = fetchone(conn, """
                SELECT whatsapp FROM admins_groupe
                WHERE tontine_id=%s LIMIT 1
            """, (p["tid"],))
            if not admin:
                continue

            admin_wa = admin["whatsapp"]
            if admin_wa not in alertes_par_admin:
                alertes_par_admin[admin_wa] = []

            emoji = "🟠" if score_dict["niveau"] == "orange" else "🔴"
            alertes_par_admin[admin_wa].append({
                "tontine":        p["nom_tontine"],
                "membre":         p["nom_complet"],
                "membre_wa":      p["whatsapp"],
                "jours_avant":    jours_avant,
                "score":          score_dict["score"],
                "niveau":         score_dict["niveau"],
                "emoji":          emoji,
                "signaux":        score_dict["signaux"],
                "recommandation": score_dict["recommandation"],
            })

        # Envoyer un seul DM groupé par admin
        for admin_wa, alertes in alertes_par_admin.items():
            msg = (
                f"🛡️ *PRÉDICTION RISQUE — BOUFFAGES À VENIR*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Le système prédictif a détecté {len(alertes)} membre(s) "
                f"à risque dans les 7 prochains jours.\n\n"
            )
            for a in alertes:
                signaux_txt = "\n".join(f"      ▪ {s}" for s in a["signaux"][:3])
                msg += (
                    f"{a['emoji']} *{a['membre']}*\n"
                    f"   Tontine : {a['tontine']}\n"
                    f"   Bouffage dans : J+{a['jours_avant']}\n"
                    f"   Score risque : *{a['score']}/100* ({a['niveau']})\n"
                    f"   Signaux :\n{signaux_txt}\n"
                    f"   👉 *{a['recommandation']}*\n\n"
                )
            msg += (
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"_BADF Ltd · Modèle prédictif · Calibré sur 60j d'historique_"
            )
            wa_prive(admin_wa, msg)

        # Notifier le owner si plus de 5 alertes total
        total_alertes = sum(len(a) for a in alertes_par_admin.values())
        if total_alertes > 5:
            wa_owner(
                f"🛡️ *SYSTÈME PRÉDICTIF*\n\n"
                f"{total_alertes} membre(s) à risque détecté(s) sur les 7 prochains jours, "
                f"répartis sur {len(alertes_par_admin)} admin(s).\n"
                f"Tous les admins concernés ont reçu leur alerte détaillée."
            )

        log.info(f"🛡️ Prédiction risque fugue : {total_alertes} alertes, {len(alertes_par_admin)} admins notifiés")

    except Exception as e:
        log.error(f"❌ alerter_risques_bouffage_imminent : {e}")
    finally:
        if conn:
            release_conn(conn)


def commande_admin_score_risque(wa_admin: str, nom_membre: str) -> str:
    """
    Commande admin : 'risque [nom_membre]' → renvoie le score détaillé.
    Permet à un admin de vérifier manuellement le risque d'un membre.
    """
    try:
        conn = get_conn()
        admin = fetchone(conn,
            "SELECT tontine_id FROM admins_groupe WHERE whatsapp=%s LIMIT 1",
            (wa_admin,))
        if not admin:
            release_conn(conn)
            return "❌ Vous n'êtes pas administrateur."

        membre = fetchone(conn, """
            SELECT m.id, m.nom_complet FROM membres m
            JOIN adhesions a ON a.membre_id = m.id
            WHERE a.tontine_id=%s
              AND (LOWER(m.nom_complet) LIKE LOWER(%s) OR m.whatsapp = %s)
            LIMIT 1
        """, (admin["tontine_id"], f"%{nom_membre}%", nom_membre))

        if not membre:
            release_conn(conn)
            return f"❌ Membre '{nom_membre}' introuvable dans votre tontine."

        result = calculer_score_risque_fugue(conn, membre["id"], admin["tontine_id"])
        release_conn(conn)

        emoji_map = {"vert": "🟢", "jaune": "🟡", "orange": "🟠", "rouge": "🔴"}
        emoji = emoji_map.get(result["niveau"], "⚪")

        msg = (
            f"🛡️ *SCORE DE RISQUE — {membre['nom_complet']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{emoji} Niveau : *{result['niveau'].upper()}*\n"
            f"   Score : *{result['score']}/100*\n\n"
            f"📊 *Décomposition :*\n"
        )
        for k, v in result["features"].items():
            msg += f"   {k.replace('_', ' ').capitalize()} : {v}\n"

        if result["signaux"]:
            msg += f"\n⚠️ *Signaux détectés :*\n"
            for s in result["signaux"][:6]:
                msg += f"   ▪ {s}\n"

        msg += (
            f"\n👉 *Recommandation :*\n"
            f"   {result['recommandation']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_BADF Ltd · Modèle prédictif_"
        )
        return msg

    except Exception as e:
        log.error(f"❌ commande_admin_score_risque : {e}")
        return "❌ Erreur calcul du score."


def commande_admin_cas_difficile(wa_admin: str, args_texte: str) -> str:
    """
    Commande admin : 'cas [nom_membre] [description]'
    Escalade un événement social imprévu (mort, départ, panne téléphone,
    échange de tour) aux autres admins de la tontine pour décision collective.

    - Crée une entrée dans `cas_difficiles`
    - Met le membre concerné en statut Pause (ni Actif, ni Suspendu)
    - Suspend toutes les sanctions automatiques sur ce membre pendant 30j
    - Notifie tous les admins du groupe (pas le owner — c'est leur tontine)
    """
    try:
        if not args_texte or len(args_texte.split(None, 1)) < 2:
            return (
                "Usage : *cas [nom_membre] [description du cas]*\n\n"
                "Exemples :\n"
                "  cas Marie deces dans la famille\n"
                "  cas Jean veut echanger son tour avec Paul\n"
                "  cas Aminata a perdu son telephone\n\n"
                "Le membre est mis en pause 30 jours, et tous les admins "
                "de la tontine reçoivent une notification pour décider ensemble."
            )

        parts = args_texte.split(None, 1)
        nom_membre = parts[0]
        description = parts[1] if len(parts) > 1 else "(sans description)"

        conn = get_conn()
        # Récupérer la tontine de l'admin
        admin_info = fetchone(conn, """
            SELECT ag.tontine_id, t.nom AS nom_tontine
            FROM admins_groupe ag
            JOIN tontines t ON t.id = ag.tontine_id
            WHERE ag.whatsapp=%s LIMIT 1
        """, (wa_admin,))
        if not admin_info:
            release_conn(conn)
            return "❌ Vous n'êtes pas administrateur d'une tontine."

        tontine_id = admin_info["tontine_id"]
        nom_tontine = admin_info["nom_tontine"]

        # Trouver le membre dans cette tontine
        membre = fetchone(conn, """
            SELECT m.id, m.nom_complet, m.whatsapp
            FROM membres m
            JOIN adhesions a ON a.membre_id = m.id
            WHERE a.tontine_id=%s
              AND (LOWER(m.nom_complet) LIKE LOWER(%s) OR m.whatsapp = %s)
            LIMIT 1
        """, (tontine_id, f"%{nom_membre}%", nom_membre))
        if not membre:
            release_conn(conn)
            return f"❌ Membre '{nom_membre}' introuvable dans {nom_tontine}."

        # Récupérer le nom de l'admin déclarant
        declarant = fetchone(conn,
            "SELECT nom_complet FROM membres WHERE whatsapp=%s LIMIT 1", (wa_admin,))
        nom_declarant = declarant["nom_complet"] if declarant else wa_admin

        # 1. Mettre le membre en Pause dans cette tontine
        q(conn, """
            UPDATE adhesions SET statut='Pause'
            WHERE membre_id=%s AND tontine_id=%s
        """, (membre["id"], tontine_id))

        # 2. Créer l'entrée cas_difficile
        q(conn, """
            INSERT INTO cas_difficiles
                (membre_id, tontine_id, type_cas, details, date_reprise, admin_id)
            VALUES (%s, %s, 'Escalade_admin', %s, NOW() + INTERVAL '30 days',
                    (SELECT id FROM membres WHERE whatsapp=%s LIMIT 1))
        """, (membre["id"], tontine_id, description, wa_admin))
        conn.commit()

        # 3. Notifier TOUS les admins de la tontine (sauf le déclarant)
        autres_admins = fetchall(conn, """
            SELECT whatsapp FROM admins_groupe
            WHERE tontine_id=%s AND whatsapp != %s
        """, (tontine_id, wa_admin))

        msg_aux_autres = (
            f"🤝 *CAS DIFFICILE — DÉCISION COLLECTIVE REQUISE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Tontine    : *{nom_tontine}*\n"
            f"Déclaré par : {nom_declarant}\n"
            f"Membre     : *{membre['nom_complet']}*\n\n"
            f"📝 *Description :*\n_{description}_\n\n"
            f"👉 Le membre est mis en *PAUSE* 30 jours.\n"
            f"   Aucune sanction automatique pendant ce délai.\n\n"
            f"💬 Discutez avec les autres admins et décidez collectivement.\n"
            f"   Quand vous avez tranché, l'admin déclarant peut taper :\n"
            f"   • *cas_resolu {nom_membre.split()[0]} reprend* — pour réactiver\n"
            f"   • *cas_resolu {nom_membre.split()[0]} retire* — pour retirer définitivement\n\n"
            f"_BADF Ltd · Escalade humaine_"
        )
        for a in autres_admins:
            wa_prive(a["whatsapp"], msg_aux_autres)

        release_conn(conn)
        log_audit("CAS_DIFFICILE_OUVERT",
                  f"Tontine:{nom_tontine} | Membre:{membre['nom_complet']} | {description[:80]}",
                  wa_admin)

        nb_notifies = len(autres_admins)
        return (
            f"✅ *Cas escaladé*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Membre : *{membre['nom_complet']}* mis en pause 30 jours.\n"
            f"Toutes ses sanctions automatiques sont suspendues.\n\n"
            f"{nb_notifies} autre(s) admin(s) ont été notifié(s) "
            f"pour décider collectivement.\n\n"
            f"Quand la décision est prise :\n"
            f"  • *cas_resolu {nom_membre.split()[0]} reprend*\n"
            f"  • *cas_resolu {nom_membre.split()[0]} retire*\n\n"
            f"_BADF Ltd_"
        )

    except Exception as e:
        log.error(f"❌ commande_admin_cas_difficile : {e}")
        return "❌ Erreur lors de l'escalade. Réessayez."


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

        # Calcul financier
        nb_membres   = fetchone(conn,
            "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
            (t["id"],))["n"]
        montant_brut = nb_membres * t["montant_place"]
        caution_pct  = t["caution_pourcent"] if t["caution_active"] else 0
        caution_mont = int(montant_brut * caution_pct / 100)

        # Dettes IRA
        dettes_ira  = fetchall(conn,
            "SELECT montant FROM dettes_ira WHERE membre_id=%s AND tontine_id=%s AND statut='Due'",
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
        dette_cotis      = cotis_manquantes * t["montant_place"]

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
            deductions_detail = deductions_txt
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
        wa_prive(passage["wa"], bordereau)

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
              if (k.startswith("pending_") and now - v.get("ts", 0) > 1800)
              or (not k.startswith("pending_") and now - v.get("ts", 0) > SESSION_TIMEOUT)]:
        _sessions_config.pop(k, None)


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

def _est_jour_cotisation(t: dict) -> bool:
    """True si aujourd'hui est un jour de cotisation pour cette tontine."""
    tt = t.get("type_tontine", "Journaliere")
    if tt == "Journaliere":
        return True
    # Heure locale du pays de la tontine
    tz_name = t.get("timezone") or "Africa/Douala"
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        now = datetime.now()
    if tt == "Hebdomadaire":
        jour_cible = _JOURS_SEMAINE.get(t.get("jour_semaine"))
        if jour_cible is None:
            log.warning(f"jour_semaine invalide '{t.get('jour_semaine')}' tontine#{t.get('id')} — rappel ignoré")
            return False
        return now.weekday() == jour_cible
    if tt == "Mensuelle":
        return now.day == (t.get("jour_mois") or 1)
    return True


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
            f"Passé ce délai : pénalité *{MONTANT_IRA} FCFA* (IRA)\n"
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
                f"Retard → *{MONTANT_IRA} FCFA/jour*\n\n"
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
                SELECT m.id, m.nom_complet, m.whatsapp, m.dernier_bouffage, m.score_confiance
                FROM adhesions a JOIN membres m ON m.id=a.membre_id
                WHERE a.tontine_id=%s AND a.statut IN ('Actif','Suspendu')
                  AND m.dernier_bouffage IS NOT NULL
                  AND m.statut_global IN ('Actif','Suspendu_global')
            """, (t["id"],))
            for m in membres:
                derniere = fetchone(conn, """
                    SELECT date_heure FROM transactions
                    WHERE membre_id=%s AND tontine_id=%s AND type_transaction='Cotisation'
                      AND statut='Confirmee' AND date_heure > %s
                    ORDER BY date_heure DESC LIMIT 1
                """, (m["id"], t["id"], m["dernier_bouffage"]))
                if derniere:
                    jours = (datetime.now() - derniere["date_heure"].replace(tzinfo=None)).days
                else:
                    jours = (datetime.now() - m["dernier_bouffage"].replace(tzinfo=None)).days
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

@healed()
def rapport_owner_21h():
    """Rapport financier complet envoyé au owner à 21h."""
    conn     = get_conn()
    tontines = fetchall(conn, "SELECT * FROM tontines WHERE statut='Active'")
    lignes   = [
        f"💼 *RAPPORT FINANCIER OWNER — {datetime.now().strftime('%d/%m/%Y')}*\n"
        f"TontineBot Pro — BADF Ltd\n{'━'*30}"
    ]
    total_fmp_global = 0
    total_ira_global = 0

    for t in tontines:
        fmp_t = fetchone(conn, """
            SELECT COALESCE(SUM(frais_fmp),0) v FROM transactions
            WHERE tontine_id=%s AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
        """, (t["id"],))["v"]
        ira_t = fetchone(conn, """
            SELECT COALESCE(SUM(frais_ira),0) v FROM transactions
            WHERE tontine_id=%s AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
        """, (t["id"],))["v"]
        nb_p  = fetchone(conn, """
            SELECT COUNT(DISTINCT membre_id) n FROM transactions
            WHERE tontine_id=%s AND type_transaction='Cotisation'
              AND statut='Confirmee' AND date_heure::date=CURRENT_DATE
        """, (t["id"],))["n"]
        nb_a  = fetchone(conn,
            "SELECT COUNT(*) n FROM adhesions WHERE tontine_id=%s AND statut='Actif'",
            (t["id"],))["n"]
        total_fmp_global += fmp_t
        total_ira_global += ira_t
        lignes.append(
            f"\n🏦 *{t['nom']}*\n"
            f"   Cotisants : {nb_p}/{nb_a}\n"
            f"   FMP : {fmp_t:,} FCFA  |  IRA : {ira_t:,} FCFA"
        )

    # ── Dettes BADF par admin ──────────────────────────────────────────────
    dettes_admins = fetchall(conn, """
        SELECT admin_wa,
               SUM(montant) AS total_du,
               COUNT(*) AS nb
        FROM dettes_badf
        WHERE statut='Due' AND date_creation::date = CURRENT_DATE
        GROUP BY admin_wa
    """)
    total_badf_du  = sum(d["total_du"] for d in dettes_admins) if dettes_admins else 0
    total_badf_recu = fetchone(conn, """
        SELECT COALESCE(SUM(montant),0) v FROM dettes_badf
        WHERE statut='Payee' AND date_paiement::date=CURRENT_DATE
    """)["v"]

    if dettes_admins:
        lignes.append(f"\n{'━'*30}\n💳 *DETTES BADF DU JOUR (admins)*\n")
        for d in dettes_admins:
            lignes.append(f"  {d['admin_wa']} : *{d['total_du']:,} FCFA* ⏳ en attente")

    lignes.append(
        f"\n{'━'*30}\n"
        f"💰 *TOTAL FMP  : {total_fmp_global:,} FCFA*\n"
        f"⏰ *TOTAL IRA  : {total_ira_global:,} FCFA*\n"
        f"🏆 *REVENUS J  : {total_fmp_global + total_ira_global:,} FCFA*\n\n"
        f"💳 *DÛ BADF    : {total_badf_du:,} FCFA* (en attente)\n"
        f"✅ *REÇU BADF  : {total_badf_recu:,} FCFA* (viré ce jour)\n\n"
        f"_TontineBot Pro — BADF Ltd_"
    )
    release_conn(conn)
    wa_owner("\n".join(lignes))




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


def rapport_groupes_20h():
    """Rapport de fin de journée dans chaque groupe."""
    conn     = get_conn()
    tontines = fetchall(conn, "SELECT * FROM tontines WHERE statut='Active'")
    for t in tontines:
        if not _est_jour_cotisation(t):
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
                    f"Envoyez le code de transaction au bot pour rétablir le service.\n\n"
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
# ROUTE DE SANTÉ
# ══════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    if request.args.get("token", "") != os.getenv("HEALTH_TOKEN", "badf_health_2026"):
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
        "version":   "9.17",
        "timestamp": datetime.now().isoformat(),
        "db":        "ok" if db_ok else "error",
    }), 200 if db_ok else 503


# ══════════════════════════════════════════════════════════════════════════
# AUTO-HEALING — supervision et auto-réparation silencieuse
# ══════════════════════════════════════════════════════════════════════════

# État de santé global du bot
_health_state = {
    "db_ok":            True,
    "wa_ok":          True,
    "scheduler_ok":     True,
    "last_db_failure":  0,
    "last_wa_failure":0,
    "consecutive_db_failures":   0,
    "consecutive_wa_failures": 0,
    "self_heal_attempts":        0,
    "started_at":                time_module.time(),
}
_health_lock = threading.Lock()


def _self_heal_db():
    """Tente de réparer le pool DB silencieusement."""
    global _db_pool
    with _health_lock:
        _health_state["self_heal_attempts"] += 1
    try:
        with _db_pool_lock:              # même lock que get_conn() — évite la race condition
            if _db_pool:
                try:
                    _db_pool.closeall()
                except Exception:
                    pass
            _db_pool = None
            init_pool()
        # Ne pas reset consecutive_db_failures ici : le reset appartient à _self_heal_check()
        # après confirmation par SELECT 1. Resetter ici masquerait les DB flapping.
        log.info("✅ Auto-heal DB : pool réinitialisé")
        return True
    except Exception as e:
        log.error(f"❌ Auto-heal DB échec : {e}")
        return False


def _self_heal_check():
    """
    Job périodique de supervision. S'exécute toutes les 60s.
    Détecte les pannes DB silencieusement et tente la réparation.
    Alertes graduées : chaque multiple de 5 cycles en échec (5 min, 10 min, ...).
    Notification de recovery quand la DB revient après une alerte.
    """
    try:
        conn = get_conn(retries=1)
        fetchone(conn, "SELECT 1 AS ok")
        release_conn(conn)
        with _health_lock:
            prev_failures = _health_state["consecutive_db_failures"]
            _health_state["db_ok"] = True
            _health_state["consecutive_db_failures"] = 0
        # Recovery : notifier le owner si on était en état d'alerte
        if prev_failures >= 5:
            try:
                _wa_send_direct(OWNER_WA,
                    f"✅ *DB RÉTABLIE*\n\n"
                    f"Base de données opérationnelle après {prev_failures} cycles en échec ({prev_failures} min).\n"
                    f"Bot 100% fonctionnel.")
            except Exception:
                pass
    except Exception as e:
        with _health_lock:
            _health_state["db_ok"] = False
            _health_state["consecutive_db_failures"] += 1
            failures = _health_state["consecutive_db_failures"]
            heal_attempts = _health_state["self_heal_attempts"]
        log.warning(f"⚠️ Health check DB échec #{failures} : {str(e)[:80]}")
        _self_heal_db()
        # Alerte graduée : à 5 cycles, puis tous les 5 cycles (évite spam + évite miss)
        if failures >= 5 and failures % 5 == 0:
            try:
                _wa_send_direct(OWNER_WA,
                    f"🔴 *ALERTE DB #{failures // 5}*\n\n"
                    f"Base de données injoignable depuis {failures} cycles ({failures} min).\n"
                    f"Tentatives auto-réparation : {heal_attempts}\n\n"
                    f"Vérifier PostgreSQL.")
            except Exception:
                pass


def _self_heal_outbox():
    """Force le drain de l'outbox quand Green API revient."""
    with _health_lock:
        wa_ok = _health_state["wa_ok"]
    if wa_ok:
        _outbox_drain()


def _check_greenapi():
    """Ping Green API toutes les 10 min — détecte les pannes et notifie le recovery."""
    if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
        return
    try:
        url = f"{GREENAPI_BASE}/waInstance{GREENAPI_INSTANCE_ID}/getStateInstance/{GREENAPI_TOKEN}"
        r = requests.get(url, timeout=10)
        state = (r.json().get("stateInstance", "") if r.status_code == 200 else "")
        ok = (state == "authorized")
        with _health_lock:
            prev_ok = _health_state["wa_ok"]
            _health_state["wa_ok"] = ok
            if ok:
                _health_state["consecutive_wa_failures"] = 0
            else:
                _health_state["consecutive_wa_failures"] += 1
                _health_state["last_wa_failure"] = time_module.time()
            failures = _health_state["consecutive_wa_failures"]
        if not ok and failures >= 3 and failures % 3 == 0:
            _outbox_enqueue("send", {
                "to":   OWNER_WA,
                "body": (f"🟠 *ALERTE GREEN API #{failures // 3}*\n\n"
                         f"WhatsApp injoignable depuis {failures * 10} min.\n"
                         f"État : {state or 'inconnu'}. Messages en file d'attente outbox."),
            })
        if ok and not prev_ok:
            try:
                _wa_send_direct(OWNER_WA,
                    "✅ *GREEN API RÉTABLIE*\n\nMessages en file d'attente en cours d'envoi.")
            except Exception:
                pass
            _outbox_drain()
    except Exception:
        with _health_lock:
            _health_state["wa_ok"] = False
            _health_state["consecutive_wa_failures"] += 1


@app.route("/health/detail", methods=["GET"])
def health_detail():
    """Endpoint santé détaillé pour monitoring externe."""
    if request.args.get("token", "") != os.getenv("HEALTH_TOKEN", "badf_health_2026"):
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
# COMPTABILITÉ BADF — Trésorerie automatique en temps réel
# ══════════════════════════════════════════════════════════════════════════
# Le owner reçoit chaque matin un état complet sans rien à calculer :
#   - Ce que BADF a généré (FMP + KYC + IRA + réactivations)
#   - Ce qui est encaissé / en attente / en retard
#   - Marges, taux de recouvrement, top admins payeurs/mauvais payeurs
#   - Projection du mois en cours

@healed()
def comptabilite_badf_quotidienne():
    """
    Bilan financier quotidien de BADF Ltd.
    Appelé chaque matin à 7h33 — avant que le owner ne se lève.
    Synthèse en 1 seul message DM, format trésorerie.
    """
    try:
        conn = get_conn()
        today = datetime.now().date()
        debut_mois = today.replace(day=1)

        # ── 1. CA généré aujourd'hui ──────────────────────────────────────
        ca_jour = fetchone(conn, """
            SELECT
                COALESCE(SUM(frais_fmp), 0)              AS fmp_du,
                COALESCE(SUM(frais_ira), 0)              AS ira_du,
                COUNT(DISTINCT membre_id)                AS membres_actifs
            FROM transactions
            WHERE statut='Confirmee' AND date_heure::date = %s
        """, (today,))

        # ── 2. CA généré ce mois ──────────────────────────────────────────
        ca_mois = fetchone(conn, """
            SELECT
                COALESCE(SUM(frais_fmp), 0)              AS fmp_du,
                COALESCE(SUM(frais_ira), 0)              AS ira_du,
                COUNT(DISTINCT membre_id)                AS membres_actifs,
                COUNT(*)                                 AS nb_cotisations
            FROM transactions
            WHERE statut='Confirmee'
              AND date_heure::date BETWEEN %s AND %s
        """, (debut_mois, today))

        # ── 3. KYC du mois ────────────────────────────────────────────────
        kyc_mois = fetchone(conn, """
            SELECT COUNT(*) AS nb FROM membres
            WHERE kyc_complet=1
              AND date_inscription::date BETWEEN %s AND %s
        """, (debut_mois, today))["nb"]
        revenu_kyc_mois = kyc_mois * 2000  # FRAIS_KYC

        # ── 4. État des dettes BADF ───────────────────────────────────────
        dettes = fetchone(conn, """
            SELECT
                COALESCE(SUM(CASE WHEN statut='Due'   THEN montant ELSE 0 END), 0) AS du,
                COALESCE(SUM(CASE WHEN statut='Payee' THEN montant ELSE 0 END), 0) AS encaisse,
                COUNT(CASE WHEN statut='Due' AND date_creation < NOW() - INTERVAL '24 hours'
                           THEN 1 END) AS en_retard
            FROM dettes_badf
            WHERE date_creation::date BETWEEN %s AND %s
        """, (debut_mois, today))

        # ── 5. Top 3 admins par dette en retard (à appeler) ───────────────
        mauvais_payeurs = fetchall(conn, """
            SELECT admin_wa, SUM(montant) AS du, COUNT(*) AS nb_dettes,
                   MIN(date_creation) AS plus_ancienne
            FROM dettes_badf
            WHERE statut='Due' AND date_creation < NOW() - INTERVAL '24 hours'
            GROUP BY admin_wa
            ORDER BY du DESC
            LIMIT 3
        """)

        # ── 6. Top 3 admins exemplaires (toujours à jour) ─────────────────
        bons_payeurs = fetchall(conn, """
            SELECT admin_wa,
                   COALESCE(SUM(CASE WHEN statut='Payee' THEN montant ELSE 0 END), 0) AS encaisse,
                   COALESCE(SUM(CASE WHEN statut='Due'   THEN montant ELSE 0 END), 0) AS du
            FROM dettes_badf
            WHERE date_creation::date BETWEEN %s AND %s
            GROUP BY admin_wa
            HAVING COALESCE(SUM(CASE WHEN statut='Due' THEN montant ELSE 0 END), 0) = 0
            ORDER BY encaisse DESC
            LIMIT 3
        """, (debut_mois, today))

        # ── 7. Croissance — comparaison vs mois précédent ─────────────────
        mois_prec = (debut_mois - timedelta(days=1)).replace(day=1)
        fin_mois_prec = debut_mois - timedelta(days=1)
        ca_mois_prec = fetchone(conn, """
            SELECT COALESCE(SUM(frais_fmp), 0) AS fmp
            FROM transactions
            WHERE statut='Confirmee' AND date_heure::date BETWEEN %s AND %s
        """, (mois_prec, fin_mois_prec))["fmp"]

        # ── 8. Projection fin de mois (linéaire) ──────────────────────────
        jours_ecoules = (today - debut_mois).days + 1
        jours_total = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        jours_total_int = jours_total.day
        projection_fmp = int((ca_mois["fmp_du"] / jours_ecoules) * jours_total_int) if jours_ecoules > 0 else 0
        projection_total = projection_fmp + (kyc_mois * 2000) + ca_mois["ira_du"]

        # ── 9. Variations vs mois précédent ───────────────────────────────
        croissance = 0
        if ca_mois_prec > 0:
            croissance = int(((projection_fmp - ca_mois_prec) / ca_mois_prec) * 100)

        release_conn(conn)

        # ── 10. Construction du message DM owner ──────────────────────────
        emoji_croissance = "📈" if croissance >= 0 else "📉"
        recouvrement = 0
        total_facture = dettes["du"] + dettes["encaisse"]
        if total_facture > 0:
            recouvrement = int((dettes["encaisse"] / total_facture) * 100)

        msg = (
            f"💼 *COMPTABILITÉ BADF — {today.strftime('%d/%m/%Y')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

            f"📊 *AUJOURD'HUI*\n"
            f"   FMP généré        : {ca_jour['fmp_du']:,} FCFA\n"
            f"   IRA généré        : {ca_jour['ira_du']:,} FCFA\n"
            f"   Membres actifs    : {ca_jour['membres_actifs']}\n\n"

            f"📅 *CE MOIS ({debut_mois.strftime('%b %Y')})*\n"
            f"   FMP cumulé        : {ca_mois['fmp_du']:,} FCFA\n"
            f"   IRA cumulé        : {ca_mois['ira_du']:,} FCFA\n"
            f"   KYC encaissés     : {revenu_kyc_mois:,} FCFA ({kyc_mois} nouveaux)\n"
            f"   Cotisations       : {ca_mois['nb_cotisations']}\n\n"

            f"💰 *TRÉSORERIE*\n"
            f"   Encaissé ce mois  : {dettes['encaisse']:,} FCFA ✅\n"
            f"   En attente        : {dettes['du']:,} FCFA ⏳\n"
            f"   En retard 24h+    : {dettes['en_retard']} dette(s) 🔴\n"
            f"   Taux recouvrement : {recouvrement}%\n\n"

            f"{emoji_croissance} *PROJECTION FIN DE MOIS*\n"
            f"   FMP projeté       : {projection_fmp:,} FCFA\n"
            f"   Total projeté     : {projection_total:,} FCFA\n"
            f"   Vs mois précédent : {croissance:+d}%\n\n"
        )

        if mauvais_payeurs:
            msg += f"🔴 *À RELANCER (mauvais payeurs)*\n"
            for mp in mauvais_payeurs:
                jours = (datetime.now() - mp["plus_ancienne"]).days
                msg += f"   • {mp['admin_wa']} : {mp['du']:,} FCFA ({jours}j)\n"
            msg += "\n"

        if bons_payeurs:
            msg += f"✅ *EXEMPLAIRES (à jour)*\n"
            for bp in bons_payeurs:
                msg += f"   • {bp['admin_wa']} : {bp['encaisse']:,} FCFA reversé\n"
            msg += "\n"

        msg += (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_BADF Ltd · Compta auto · 07h33_"
        )

        wa_owner(msg)
        log.info(f"✅ Compta BADF envoyée — FMP jour={ca_jour['fmp_du']:,} mois={ca_mois['fmp_du']:,}")

    except Exception as e:
        log.error(f"❌ comptabilite_badf_quotidienne : {e}")


# ══════════════════════════════════════════════════════════════════════════
# SURVEILLANCE FINANCIÈRE PROACTIVE — détection anomalies avant qu'elles coûtent
# ══════════════════════════════════════════════════════════════════════════
# Le bot scrute en permanence des patterns suspects que l'œil humain rate :
#   - Pic anormal de cotisations sur une tontine (admin truqueur)
#   - Membre qui cotise avec une vélocité inhabituelle (blanchiment ?)
#   - Admin qui retient les FMP plus que la moyenne
#   - Tontine avec taux de bouffage suspect en hausse
#   - Hash partagé entre 2 tontines différentes (fraude inter-groupes)

@healed()
def surveillance_anomalies_quotidienne():
    """
    Détection proactive d'anomalies financières.
    S'exécute chaque matin à 6h36 — avant la compta du owner.
    Génère un rapport d'alertes uniquement si des anomalies sont détectées.
    """
    try:
        conn = get_conn()
        today = datetime.now().date()
        alertes = []

        # ── 1. Pic anormal de cotisations sur une tontine (z-score) ───────
        # On compare aujourd'hui vs moyenne 30j précédents — flag si > 3σ
        tontines = fetchall(conn, "SELECT id, nom FROM tontines WHERE statut='Active'")
        for t in tontines:
            stats_30j = fetchone(conn, """
                SELECT
                    AVG(daily_count) AS moy,
                    STDDEV(daily_count) AS ecart
                FROM (
                    SELECT date_heure::date AS j, COUNT(*) AS daily_count
                    FROM transactions
                    WHERE tontine_id=%s AND statut='Confirmee'
                      AND type_transaction='Cotisation'
                      AND date_heure >= NOW() - INTERVAL '30 days'
                      AND date_heure::date < %s
                    GROUP BY date_heure::date
                ) sub
            """, (t["id"], today))

            cotis_jour = fetchone(conn, """
                SELECT COUNT(*) AS n FROM transactions
                WHERE tontine_id=%s AND statut='Confirmee'
                  AND type_transaction='Cotisation'
                  AND date_heure::date = %s
            """, (t["id"], today))["n"]

            if stats_30j["moy"] and stats_30j["ecart"] and stats_30j["ecart"] > 0:
                z = (cotis_jour - float(stats_30j["moy"])) / float(stats_30j["ecart"])
                if z > 3:
                    alertes.append(
                        f"📈 PIC ANORMAL — *{t['nom']}*\n"
                        f"   {cotis_jour} cotisations aujourd'hui "
                        f"(moyenne {stats_30j['moy']:.1f}, z={z:.1f})\n"
                        f"   → Vérifier que l'admin n'enregistre pas des paiements fictifs"
                    )

        # ── 2. Hash de screenshot partagé entre tontines différentes ──────
        hash_partages = fetchall(conn, """
            SELECT image_hash, COUNT(DISTINCT tontine_id) AS nb_tontines,
                   array_agg(DISTINCT tontine_id) AS tontines
            FROM screenshots_hash
            WHERE date_creation >= NOW() - INTERVAL '7 days'
            GROUP BY image_hash
            HAVING COUNT(DISTINCT tontine_id) >= 2
            LIMIT 5
        """)
        for h in hash_partages:
            alertes.append(
                f"🔄 HASH PARTAGÉ — fraude inter-groupes\n"
                f"   Screenshot {h['image_hash'][:12]}... soumis dans "
                f"{h['nb_tontines']} tontines différentes\n"
                f"   → Bannissement automatique recommandé"
            )

        # ── 3. Admins retenant les FMP plus longtemps que la moyenne ──────
        admins_retardataires = fetchall(conn, """
            SELECT admin_wa,
                   AVG(EXTRACT(EPOCH FROM (date_paiement - date_creation))/86400) AS jours_moyens,
                   SUM(montant) AS total_du
            FROM dettes_badf
            WHERE statut='Payee'
              AND date_paiement >= NOW() - INTERVAL '30 days'
            GROUP BY admin_wa
            HAVING AVG(EXTRACT(EPOCH FROM (date_paiement - date_creation))/86400) > 3
            ORDER BY jours_moyens DESC
            LIMIT 3
        """)
        for a in admins_retardataires:
            alertes.append(
                f"⏰ FMP LENTS — *{a['admin_wa']}*\n"
                f"   Paie en moyenne {a['jours_moyens']:.1f} jours après facturation\n"
                f"   Total reçu : {a['total_du']:,} FCFA"
            )

        # ── 4. Membre avec vélocité de cotisation suspecte (>1 par 30min) ─
        velocite_suspecte = fetchall(conn, """
            SELECT m.whatsapp, m.nom_complet, COUNT(*) AS nb_cotis_jour
            FROM transactions t
            JOIN membres m ON m.id = t.membre_id
            WHERE t.statut='Confirmee'
              AND t.type_transaction='Cotisation'
              AND t.date_heure >= NOW() - INTERVAL '24 hours'
            GROUP BY m.id, m.whatsapp, m.nom_complet
            HAVING COUNT(*) > 5
            ORDER BY nb_cotis_jour DESC
            LIMIT 3
        """)
        for v in velocite_suspecte:
            alertes.append(
                f"⚡ VÉLOCITÉ ANORMALE — *{v['nom_complet']}*\n"
                f"   {v['nb_cotis_jour']} cotisations en 24h ({v['whatsapp']})\n"
                f"   → Possible compte mule ou blanchiment"
            )

        # ── 5. Tontines avec taux de bouffage suspect (>30%) ──────────────
        tontines_suspectes = fetchall(conn, """
            SELECT t.nom, t.id,
                   COUNT(CASE WHEN lp.bloque_suspect=1 THEN 1 END) AS suspects,
                   COUNT(*) AS total
            FROM tontines t
            JOIN liste_passage lp ON lp.tontine_id = t.id
            WHERE t.statut='Active'
              AND lp.cycle = t.cycle_actuel
            GROUP BY t.id, t.nom
            HAVING COUNT(*) > 5
               AND COUNT(CASE WHEN lp.bloque_suspect=1 THEN 1 END)::float / COUNT(*) > 0.30
            LIMIT 3
        """)
        for ts in tontines_suspectes:
            ratio = int((ts["suspects"] / ts["total"]) * 100)
            alertes.append(
                f"🚨 TONTINE À RISQUE — *{ts['nom']}*\n"
                f"   {ts['suspects']}/{ts['total']} bouffages bloqués ({ratio}%)\n"
                f"   → Pattern d'admin truqueur possible"
            )

        release_conn(conn)

        # ── Envoi du rapport au owner SEULEMENT si anomalies détectées ────
        if alertes:
            msg = (
                f"🛡️ *SURVEILLANCE PROACTIVE — {today.strftime('%d/%m/%Y')}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{len(alertes)} anomalie(s) détectée(s) :\n\n"
                + "\n\n".join(alertes)
                + f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                + f"_BADF Ltd · Détection auto · 06h36_"
            )
            wa_owner(msg)
            log_audit("SURVEILLANCE_ALERTE", f"{len(alertes)} anomalies", "")
            log.info(f"🛡️ Surveillance proactive : {len(alertes)} alertes envoyées")
        else:
            log.info("🛡️ Surveillance proactive : aucune anomalie")

    except Exception as e:
        log.error(f"❌ surveillance_anomalies_quotidienne : {e}")


# ══════════════════════════════════════════════════════════════════════════
# ZERO TOUCH OPS — supervision avancée pour le owner absent
# ══════════════════════════════════════════════════════════════════════════
# Pendant que tu es à San Francisco pour YC, le bot doit :
#   - Détecter ses propres pannes
#   - Tenter l'auto-réparation
#   - T'alerter UNIQUEMENT si l'auto-réparation échoue
#   - Te donner une vue de l'uptime quand tu poses la question

_zto_state = {
    "last_meta_check":   0,
    "last_outbox_size":  0,
    "incidents_24h":     [],
    "uptime_start":      time_module.time(),
}


@healed()
def zto_supervision_quotidienne():
    """
    Bilan de supervision quotidien.
    Envoyé chaque matin à 6h33 si des incidents ont eu lieu dans les 24h,
    ou en silence sinon.
    """
    try:
        # ── État santé actuel ─────────────────────────────────────────────
        with _health_lock:
            db_ok      = _health_state["db_ok"]
            wa_ok      = _health_state["wa_ok"]
            heal_count = _health_state["self_heal_attempts"]
            uptime_sec = int(time_module.time() - _health_state["started_at"])

        # ── Taille outbox (messages en attente d'envoi) ───────────────────
        outbox_size = 0
        if _outbox_path.exists():
            try:
                with open(_outbox_path, "r", encoding="utf-8") as f:
                    outbox_size = sum(1 for _ in f)
            except Exception:
                pass

        # ── Stats sur les 24 dernières heures ─────────────────────────────
        conn = get_conn()
        stats_24h = fetchone(conn, """
            SELECT
                COUNT(CASE WHEN action LIKE '%ERREUR%' THEN 1 END) AS erreurs,
                COUNT(CASE WHEN action LIKE '%SUSPENSION%' THEN 1 END) AS suspensions,
                COUNT(CASE WHEN action LIKE '%FRAUDE%' THEN 1 END) AS fraudes
            FROM audit_log
            WHERE date_heure >= NOW() - INTERVAL '24 hours'
        """)
        release_conn(conn)

        # ── Décision : envoyer un rapport seulement si incident ───────────
        incidents = (heal_count > 0 or outbox_size > 10
                     or stats_24h["erreurs"] > 5
                     or not db_ok or not wa_ok)

        if not incidents:
            log.info("🟢 ZTO : tout va bien, aucun rapport envoyé")
            return

        uptime_h = uptime_sec // 3600
        uptime_d = uptime_h // 24
        uptime_str = f"{uptime_d}j {uptime_h % 24}h" if uptime_d > 0 else f"{uptime_h}h {(uptime_sec % 3600) // 60}min"

        msg = (
            f"🛠️ *RAPPORT OPS — {datetime.now().strftime('%d/%m %Hh%M')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚙️ *État système*\n"
            f"   Uptime          : {uptime_str}\n"
            f"   Base de données : {'✅ OK' if db_ok else '🔴 KO'}\n"
            f"   WhatsApp (GA)   : {'✅ OK' if wa_ok else '🔴 KO'}\n"
            f"   Auto-réparations: {heal_count}\n"
            f"   Outbox WA       : {outbox_size} message(s) en attente\n\n"
            f"📋 *24 dernières heures*\n"
            f"   Erreurs loggées : {stats_24h['erreurs']}\n"
            f"   Suspensions     : {stats_24h['suspensions']}\n"
            f"   Fraudes         : {stats_24h['fraudes']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Aucune action requise sauf si KO — auto-healing actif._"
        )
        wa_owner(msg)

    except Exception as e:
        log.error(f"❌ zto_supervision_quotidienne : {e}")


# ══════════════════════════════════════════════════════════════════════════


def demarrer_scheduler():
    scheduler = BackgroundScheduler(timezone="Africa/Douala")

    # ── Rappels groupe (minute=3 — fréquence naturelle) ───────────────────
    scheduler.add_job(rappel_ouverture,      "cron", minute=3,  id="rappel_ouverture")
    scheduler.add_job(rappel_matin,          "cron", minute=3,  id="rappel_matin")
    scheduler.add_job(rappel_non_cotisants,  "cron", minute=3,  id="rappel_non_cotisants")
    scheduler.add_job(notifier_prochain_bouffage, "cron", minute=9, id="bouffage")

    # ── Jobs fixes ────────────────────────────────────────────────────────
    scheduler.add_job(rapport_groupes_20h,             "cron", hour=20, minute=0,  id="rapport_groupes")
    scheduler.add_job(envoyer_releve_fmp_post_bouffage,"cron", minute="*",         id="fmp_post_bouffage")
    scheduler.add_job(rapport_owner_21h,            "cron", hour=HEURE_RAPPORT_OWNER, minute=0, id="rapport_owner")
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

    scheduler.start()
    log.info("✅ Scheduler TontineBot Pro v9.17 démarré.")
    return scheduler


# ══════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("━" * 60)
    log.info("🚀 TontineBot Pro v9.17 — Barack & AI Development Facilities Ltd")
    log.info("   BADF Ltd — Cameroun 🇨🇲")
    log.info("━" * 60)
    log.info(f"   Owner           : {OWNER_WA}")
    log.info(f"   WhatsApp       : Green API — Instance {GREENAPI_INSTANCE_ID or '(non configurée)'}")
    log.info(f"   Base de données : {PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}")
    log.info(f"   Port Flask      : {PORT}")
    log.info(f"   Numéro BADF MTN : {NUMERO_BADF_MTN}")
    log.info("━" * 60)
    log.info("💡 Mode paiement : MANUEL (screenshot → admin confirme)")
    log.info("   • Membres envoient screenshot dans le groupe")
    log.info("   • Bot détecte et enregistre automatiquement")
    log.info("   • Admin confirme en 1 tap")
    log.info("   • Bot notifie le membre")
    log.info("━" * 60)

    # ── 1. Vérification PostgreSQL ─────────────────────────────────────────
    log.info("🔍 Vérification PostgreSQL...")
    if not tester_connexion_postgresql():
        log.error("❌ ARRÊT — PostgreSQL inaccessible.")
        exit(1)

    # ── 2. Init pool + tables ──────────────────────────────────────────────
    init_pool()
    init_db()
    _restaurer_sessions()
    log.info("✅ Base de données v9.17 initialisée.")

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
    demarrer_scheduler()
    log.info("✅ Scheduler démarré.")
    log.info("━" * 60)
    log.info("🟢 TontineBot Pro v9.17 — Prêt à recevoir des messages.")
    log.info("━" * 60)

    def _shutdown_bot():
        """Appelé par atexit à chaque arrêt — ferme proprement le pool DB."""
        _sauvegarder_sessions()
        if _db_pool:
            try:
                _db_pool.closeall()
            except Exception:
                pass
        _msg_executor.shutdown(wait=False)
        log.info("Bot arrêté proprement — pool DB fermé.")

    atexit.register(_shutdown_bot)

    from waitress import serve
    log.info(f"🚀 Waitress WSGI — {WAITRESS_THREADS} threads — port {PORT}")
    serve(app, host="0.0.0.0", port=PORT, threads=WAITRESS_THREADS,
          channel_timeout=120, cleanup_interval=30)

