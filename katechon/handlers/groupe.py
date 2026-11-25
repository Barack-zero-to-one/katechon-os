"""
Groupes WhatsApp : arrivée du bot dans un groupe, configuration d'une
nouvelle tontine en trois questions par DM à l'admin, auto-inscription des
participants, commandes de groupe « liste » et « rappel », et lectures
Green API des membres, des admins et des noms affichés d'un groupe.

Extrait de barack_corp_v9_18.py sans changement de comportement. Ne dépend
jamais du bot ; le webhook l'appelle pour les messages de groupe et les
événements d'ajout du bot.
"""
import logging
import hashlib
import re
import time as time_module
from datetime import datetime
from typing import Optional

import requests
from katechon.core.audit import log_audit
from katechon.core.db import fetchall, fetchone, get_conn, q, release_conn
from katechon.core.etat import _groupes_intro_envoyes, _sessions_config, _sessions_lock, session_valide
from katechon.core.health import healed
from katechon.messaging.messages import MSG_INTRO_GROUPE, msg_dm_admin_bienvenue, msg_intro_groupe, msg_kyc_groupe
from katechon.core.socle import FRAIS_FMP, est_admin, est_owner, normaliser_numero
from katechon.messaging.whatsapp import (
    GREENAPI_BASE,
    GREENAPI_INSTANCE_ID,
    GREENAPI_TOKEN,
    OWNER_WA,
    _wa_send_group_chatid,
    _wa_send_groupe,
    wa_groupe,
    wa_owner,
    wa_prive,
)

__all__ = ["_est_nom_valide", "_auto_inscrire_participants", "_envoyer_config_dm_admins",
    "_bot_ajoute_groupe", "traiter_config_tontine", "_groupe_liste_cotisations",
    "_traiter_commande_groupe", "_groupe_rappel_manuel", "_greenapi_get_pushname",
    "_greenapi_get_group_members", "_greenapi_get_group_admins"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


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
                f"✅ *{inscrits} membre(s) auto-inscrit(s)* — inscription automatique, rien à faire de votre côté.\n\n"
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
