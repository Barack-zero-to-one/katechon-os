"""
Textes du bot et analyse de la liste de passage (module pur).

Extrait de barack_corp_v9_18.py sans changement : messages envoyés aux
membres et aux admins, menus texte, et parser_liste_passage. Aucune
dépendance au bot (ni base, ni réseau) ; le bot réexporte chaque nom.
"""
import hashlib
import os
import re
from datetime import date as _date

__all__ = [
    "BOT_NOM", "MSG_DISSUASION", "msg_dissuasion", "msg_intro_groupe",
    "MSG_INTRO_GROUPE", "msg_dm_admin_bienvenue", "parser_liste_passage",
    "msg_kyc_groupe", "MSG_BIENVENUE_DM", "MENU_MEMBRE_TXT", "MENU_ADMIN_TXT",
    "MENU_CAS_DIFFICILES",
]

# ── Identité du bot ───────────────────────────────────────────────────────
BOT_NOM = os.getenv("BOT_NOM", "TontineBot Pro")


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
        f"*I — Caution de Garantie Anti-Fugue : 10% de la cagnotte*\n"
        f"Retenue au moment du bouffage. Une seule issue selon votre comportement :\n"
        f"▪ *Restituée* si vous cotisez jusqu'à la fin du cycle sans incident\n"
        f"▪ *Saisie définitivement* en cas d'abandon, de disparition ou de défaut post-bouffage\n\n"
        f"Ce mécanisme a été conçu pour neutraliser le schéma classique du fraudeur : "
        f"percevoir sa cagnotte, puis cesser de cotiser. "
        f"La caution rend ce comportement *financièrement négatif avant même qu'il soit tenté*.\n\n"
        f"*II — Pénalité de retard (IRA) : {150:,} FCFA/jour*\n"
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
        f"▪ *ANNULER_ACCORD [ID]* — Retirer un accord pas encore versé\n"
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


def msg_kyc_groupe(nom_tontine: str) -> str:
    """Message envoyé dans le groupe à l'arrivée du bot."""
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 *BIENVENUE — {nom_tontine}*\n"
        f"Barack & AI Development Facilities Ltd — BADF Ltd\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📲 Pour rejoindre, tapez *menu* en *message privé* au bot.\n"
        f"C'est gratuit. 30 secondes.\n\n"
        f"─────────────────────────────────────────────\n"
        f"🔐 *SÉCURITÉ*\n"
        f"─────────────────────────────────────────────\n\n"
        f"Dès votre entrée dans ce groupe, votre identifiant WhatsApp "
        f"et les métadonnées de votre session sont enregistrés.\n\n"
        f"En cas de fraude documentée, BADF Ltd engage sans délai :\n"
        f"▪ Transmission à la *Police Judiciaire*\n"
        f"▪ Signalement à l'*ANIF* — Agence Nationale d'Investigation Financière\n"
        f"▪ Blocage définitif *Mobile Money*\n"
        f"▪ Poursuites — *Articles 318 à 323 du Code Pénal Camerounais*\n\n"
        f"_« Utiliser la technologie pour servir le prochain avec intégrité »_"
    )


MSG_BIENVENUE_DM = (
    "🏦 *Bienvenue sur TontineBot Pro — BADF Ltd*\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Votre compte est actif.\n\n"
    "Tapez *menu* pour voir vos tontines et options.\n\n"
    "_BADF Ltd — Technologie au service du prochain_"
)


# ── Menus texte ───────────────────────────────────────────────────────────
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
    "↩️ *ANNULER_ACCORD [ID]* → retirer un accord non versé\n"
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
