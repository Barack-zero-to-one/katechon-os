"""
Menu admin WhatsApp : sélection de la tontine, commandes directes
(PAIEMENT_BADF, BOUFFAGE_VIRE, risque, cas, cas_resolu), saisie de la liste
de passage, et les 15 options du menu (rapport, membres, ordre, suspension,
historique, rappel, fugitifs, cas difficiles, caution, ajout de membre,
cotisations en attente...), plus l'exclusion d'un membre des groupes et le
rappel individuel aux retardataires.

Extrait de barack_corp_v9_18.py sans changement de comportement. Ne dépend
jamais du bot ; le webhook l'appelle pour les messages privés des admins.
"""
import logging
import hashlib
import re
import time as time_module
from datetime import datetime

from katechon.messaging import wa_dispatch
from katechon.core.audit import log_audit
from katechon.core.db import fetchall, fetchone, get_conn, q, release_conn, verrou_ordre_tontine
from katechon.core.etat import _sessions_admin, session_valide
from katechon.messaging.messages import MENU_ADMIN_TXT, MENU_CAS_DIFFICILES, msg_dissuasion, parser_liste_passage
from katechon.finance.paiement import (
    _get_retardataires,
    _solder_versement,
    _verifier_fin_cycle,
    cloturer_tontine,
    confirmer_bouffage_vire,
    confirmer_cotisation,
    demarrer_nouveau_cycle,
    enregistrer_paiement_badf,
    rejeter_cotisation,
    saisir_caution_et_compenser_groupe,
)
from katechon.core.socle import (
    FRAIS_FMP,
    FRAIS_REACTIV,
    IRA_CAP_PCT,
    IRA_CLIFF_PCT,
    IRA_DAILY_PCT,
    NUMERO_BADF_MTN,
    NUMERO_BADF_ORANGE,
    enregistrer_liste_passage,
    est_owner,
    get_tontines_admin,
    inscrire_dans_tontine,
    normaliser_numero,
    valider_numero_cameroun,
)
from katechon.finance.trust_graph import (
    _update_score_confiance,
    commande_admin_cas_difficile,
    commande_admin_cas_resolu,
    commande_admin_score_risque,
    message_pause_refusee,
)
from katechon.messaging.whatsapp import OWNER_WA, wa_groupe, wa_kick_membre, wa_owner, wa_prive

__all__ = ["kick_membre_si_bot_admin", "wa_mentionner_retardataires",
    "_passer_a_cotisation_suivante", "traiter_menu_admin"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


# Sanctions qui mettent le membre en Suspendu_global ; chacune porte la tontine
# qui l'a prononcée (tontine_id) et sa levée éventuelle (levee_le).
_TYPES_SUSPENSION = ("Suspension_72h", "Interception_bouffage", "Fugue_post_bouffage")


def _bouffage_a_regler(conn, tid: int, membre_id: int = None) -> bool:
    """Un tour du cycle en cours est-il bloqué (contrôle suspect) ou approuvé
    (DEBLOQUER, BOUFFAGE_COMPLET) sans avoir encore été versé ?

    Tant que c'est le cas, ni une cession ni une nouvelle liste ne doivent changer
    le titulaire ou supprimer le tour : l'accord serait perdu, ou la sanction du
    job d'expiration frapperait un autre membre.
    """
    return bool(fetchone(conn, """
        SELECT 1 AS x FROM liste_passage lp JOIN tontines t ON t.id = lp.tontine_id
        WHERE lp.tontine_id=%s AND lp.cycle = t.cycle_actuel AND lp.statut='En_attente'
          AND (lp.bloque_suspect=1 OR lp.debloque_le IS NOT NULL)
          AND (%s::int IS NULL OR lp.membre_id = %s::int)
        LIMIT 1""", (tid, membre_id, membre_id)))


MSG_BOUFFAGE_A_REGLER = ("❌ Un bouffage bloqué ou approuvé attend son règlement : réglez-le "
                         "(DEBLOQUER, BOUFFAGE_COMPLET ou expiration) avant de modifier la liste.")


def _membre_de_la_tontine(conn, numero: str, tid: int, tnom: str, inclure_partis: bool = False):
    """Membre de la tontine de la session admin, recherché par numéro.

    Retourne (membre, None) s'il a une adhésion dans cette tontine qu'il n'a
    pas quittée (ou, avec inclure_partis, une adhésion même quittée : saisie
    de la caution d'un exclu, historique, KICK). Sinon (None, refus), avec la même réponse pour un numéro
    inconnu et pour le membre d'une autre tontine : un admin ne peut ni agir
    sur un autre groupe, ni sonder l'annuaire du réseau.
    """
    membre = fetchone(conn, """SELECT * FROM membres WHERE whatsapp=%s
                               AND id IN (SELECT membre_id FROM adhesions
                                          WHERE tontine_id=%s AND (%s OR statut <> 'Quitte'))""",
                      (numero, tid, inclure_partis))
    if membre:
        return membre, None
    return None, f"❌ Ce numéro n'est pas membre de {tnom}."


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
        _conn_chk = get_conn()
        try:
            _a_regler = _bouffage_a_regler(_conn_chk, tid)
        finally:
            release_conn(_conn_chk)
        if _a_regler:
            return MSG_BOUFFAGE_A_REGLER
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
            ORDER BY bm.id LIMIT 1
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        if not m:
            release_conn(conn)
            return refus
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        release_conn(conn)
        if not m:
            return refus
        if m["statut_global"] == "Banni":
            # Suspendre un banni le rendrait réactivable (option 5 B) : le bannissement serait perdu.
            sess["etape"] = "menu"
            return f"❌ {m['nom_complet']} est banni : il ne peut être ni suspendu ni réactivé."
        sess["data"].update({"susp_id": m["id"], "susp_nom": m["nom_complet"], "susp_wa": num})
        sess["etape"] = "susp_raison"
        return f"👤 *{m['nom_complet']}*\nEntrez la raison de la suspension :"

    elif sess.get("etape") == "susp_raison":
        d      = sess["data"]
        raison = texte
        conn   = get_conn()
        if not q(conn, "UPDATE membres SET statut_global='Suspendu_global' WHERE id=%s AND statut_global <> 'Banni'",
                 (d["susp_id"],)).rowcount:
            conn.rollback()
            release_conn(conn)
            sess["etape"] = "menu"
            return f"❌ {d['susp_nom']} est banni : il ne peut être ni suspendu ni réactivé."
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        release_conn(conn)
        if not m:
            return refus
        if m["statut_global"] not in ("Suspendu_global",):
            sess["etape"] = "menu"
            return f"ℹ️ {m['nom_complet']} n'est pas suspendu (statut: {m['statut_global']})."
        sess["etape"] = "menu"
        conn = get_conn()
        try:
            # Membre verrouillé d'abord : un job qui le suspend en parallèle attend, et
            # son statut est relu dans la même transaction.
            courant = fetchone(conn, "SELECT statut_global FROM membres WHERE id=%s FOR UPDATE", (m["id"],))
            if not courant or courant["statut_global"] != "Suspendu_global":
                return f"ℹ️ {m['nom_complet']} n'est pas suspendu (statut: {courant['statut_global'] if courant else '?'})."
            # Une suspension appartient à la tontine qui l'a prononcée (sanction non levée).
            # Elle ne bloque que si cette tontine peut encore la lever : tontine non terminée
            # (une tontine Suspendue pour dette BADF redevient Active) où le membre a une adhésion non quittée. Sinon (tontine quittée, terminée, ou sans
            # tontine) personne d'autre ne pourrait la lever : elle est levée ici.
            actives = fetchall(conn, """
                SELECT s.id, s.tontine_id,
                       (s.tontine_id IS NOT NULL AND s.tontine_id <> %s AND EXISTS (
                            SELECT 1 FROM adhesions a JOIN tontines t ON t.id = a.tontine_id
                            WHERE a.membre_id = s.membre_id AND a.tontine_id = s.tontine_id
                              AND a.statut <> 'Quitte' AND t.statut <> 'Terminee')) AS bloquante
                FROM sanctions s
                WHERE s.membre_id=%s AND s.levee_le IS NULL AND s.type_sanction = ANY(%s)
                FOR UPDATE OF s""", (tid, m["id"], list(_TYPES_SUSPENSION)))
            ailleurs = [s for s in actives if s["bloquante"]]
            levables = [s["id"] for s in actives if not s["bloquante"]]
            if ailleurs and not any(s["tontine_id"] == tid for s in actives):
                return (f"❌ {m['nom_complet']} a été suspendu par une autre tontine : "
                        f"seul son admin peut lever cette suspension.")
            if levables:
                q(conn, "UPDATE sanctions SET levee_le=NOW() WHERE id = ANY(%s)", (levables,))
                # Un blocage fugitif levé est clos : s'il ne paie toujours pas,
                # detecter_fugitifs pourra le détecter à nouveau.
                q(conn, """UPDATE alertes_fugue SET traite=1
                           WHERE membre_id=%s AND type_alerte='Blocage' AND traite=0
                             AND tontine_id IN (SELECT tontine_id FROM sanctions WHERE id = ANY(%s))""",
                  (m["id"], levables))
            # Adhésion suspendue remise Actif (une pause ou une exonération reste telle quelle),
            # et nouveau sursis : sans cela le job 72 h la resuspendrait dans l'heure.
            q(conn, """UPDATE adhesions SET statut='Actif' WHERE membre_id=%s AND tontine_id=%s
                       AND statut='Suspendu'""", (m["id"], tid))
            q(conn, "UPDATE adhesions SET nb_avertissements_retard=0 WHERE membre_id=%s AND tontine_id=%s",
              (m["id"], tid))
            if not ailleurs:
                q(conn, "UPDATE membres SET statut_global='Actif', suspendu_retard=0 WHERE id=%s", (m["id"],))
                # Réactivé sur tout le réseau : nouveau sursis dans chacune de ses tontines,
                # où les retards ont couru pendant qu'il ne pouvait pas payer.
                q(conn, "UPDATE adhesions SET nb_avertissements_retard=0 WHERE membre_id=%s", (m["id"],))
            conn.commit()
        finally:
            release_conn(conn)
        if ailleurs:
            return (f"✅ Suspension de *{m['nom_complet']}* levée dans *{tnom}*.\n"
                    f"⚠️ Il reste bloqué sur tout le réseau (messages refusés) tant qu'une autre tontine "
                    f"n'a pas levé sa propre suspension.")
        wa_prive(num, "✅ *Compte réactivé — Barack Corp*\nBienvenue de retour !")
        return f"✅ *{m['nom_complet']}* réactivé."

    # ── Option 6 : Historique complet membre ──────────────────────────────
    elif texte == "6" and sess["etape"] == "menu":
        sess["etape"] = "histo_wa"
        return "🔍 *HISTORIQUE MEMBRE*\nEntrez le numéro WhatsApp du membre :"

    elif sess.get("etape") == "histo_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom, inclure_partis=True)
        if not m:
            release_conn(conn)
            return refus
        txs     = fetchall(conn, """
            SELECT type_transaction, montant_brut, frais_fmp, frais_ira,
                   montant_net, statut, date_heure, periodes_payees
            FROM transactions WHERE membre_id=%s AND tontine_id=%s ORDER BY date_heure DESC LIMIT 20
        """, (m["id"], tid))
        adhs    = fetchall(conn, """
            SELECT t.nom, a.statut, a.nombre_places, a.jours_avance
            FROM adhesions a JOIN tontines t ON t.id=a.tontine_id
            WHERE a.membre_id=%s AND a.tontine_id=%s
        """, (m["id"], tid))
        caution = fetchone(conn,
            "SELECT COALESCE(SUM(montant),0) t FROM cautions_garantie "
            "WHERE membre_id=%s AND tontine_id=%s AND statut='Bloquee'", (m["id"], tid))["t"]
        sanctions = fetchall(conn,
            "SELECT type_sanction, date_sanction, notes FROM sanctions "
            "WHERE membre_id=%s AND (tontine_id=%s OR tontine_id IS NULL) "
            "ORDER BY date_sanction DESC LIMIT 5", (m["id"], tid))
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        release_conn(conn)
        if not m:
            return refus
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        release_conn(conn)
        if not m:
            return refus
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        release_conn(conn)
        if not m:
            return refus
        sess["data"].update({"cas_mbr_id": m["id"], "cas_mbr_nom": m["nom_complet"]})
        sess["etape"] = "cession_cessionnaire"
        return f"👤 Cédant : *{m['nom_complet']}*\nEntrez le numéro du *bénéficiaire* (cessionnaire) :"

    elif sess.get("etape") == "cession_cessionnaire":
        num2 = normaliser_numero(texte)
        conn = get_conn()
        m2, _ = _membre_de_la_tontine(conn, num2, tid, tnom)
        if not m2:
            release_conn(conn)
            return f"❌ Le bénéficiaire doit d'abord être membre de {tnom} (option 11 pour l'ajouter)."
        d    = sess["data"]
        if _bouffage_a_regler(conn, tid, d["cas_mbr_id"]):
            release_conn(conn)
            sess["etape"] = "menu"
            return (f"❌ Un bouffage bloqué ou approuvé de {d['cas_mbr_nom']} attend son règlement : "
                    f"réglez-le avant la cession.")
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        release_conn(conn)
        if not m:
            return refus
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
        if t == "0":
            sess["etape"] = "menu"
            return "↩️ Exonération annulée."
        if t not in ("A", "B", "C"):
            return "❌ Tapez A, B ou C (0 pour annuler)."
        conn = get_conn()
        if t in ("A", "C"):
            # Seules les dettes IRA de CETTE tontine : celles d'un autre groupe
            # reviennent à ses membres ponctuels et ne relèvent pas de cet admin.
            q(conn, """UPDATE dettes_ira SET statut='Prelevee', prelevee_le=NOW()
                       WHERE membre_id=%s AND tontine_id=%s AND statut='Due'""",
              (d["cas_mbr_id"], tid))
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
            f"Vos pénalités dans *{tnom}* ont été effacées par l'administration.")
        sess["etape"] = "menu"
        return f"✅ Exonération accordée à *{d['cas_mbr_nom']}*."

    # ── Exclusion ──────────────────────────────────────────────────────────
    elif sess.get("etape") == "exclus_wa":
        num  = normaliser_numero(texte)
        conn = get_conn()
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom)
        release_conn(conn)
        if not m:
            return refus
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
        m, refus = _membre_de_la_tontine(conn, num, tid, tnom, inclure_partis=True)
        if not m:
            release_conn(conn)
            return refus
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
                   VALUES (%s,%s,'Blocage_permanent',%s,'Saisie caution fugitif — réserve BADF')""",
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
            sess["etape"] = "menu"
            try:
                inscrire_dans_tontine(mid, tid)
            except ValueError as e:              # refus métier : tontine pleine, déjà inscrit
                return f"❌ {e}"
            except Exception as e:               # erreur technique : jamais le texte SQL à l'admin
                log.error(f"❌ ajout membre existant {mid} → tontine {tid} : {e}")
                return "❌ Inscription impossible pour le moment. Réessayez plus tard."
            log_audit("INSCRIPTION_MANUELLE", f"Membre {mid} → tontine {tid}", wa)
            wa_prive(num,
                f"👋 Bienvenue dans *{tnom}* !\n"
                f"Vous avez été ajouté à cette tontine par un admin.")
            return f"✅ Membre existant (ID:{mid}) inscrit dans *{tnom}*."
        kyc_hash = hashlib.sha256(f"{nom}{num}MANUEL".encode()).hexdigest()
        cur = q(conn, """INSERT INTO membres (nom_complet, kyc_hash, whatsapp, adhesion_payee, statut_global)
                         VALUES (%s,%s,%s,1,'En_attente_kyc') RETURNING id""",
                (nom, kyc_hash, num))
        mid = cur.fetchone()[0]
        conn.commit()
        release_conn(conn)
        log_audit("AJOUT_MANUEL", f"{nom} par admin {wa}", num)   # le membre existe désormais
        try:
            inscrire_dans_tontine(mid, tid)
        except Exception as e:
            # Le membre est créé ; l'inscription a échoué : l'admin le sait, sans texte SQL.
            sess["etape"] = "menu"
            if not isinstance(e, ValueError):
                log.error(f"❌ ajout nouveau membre {mid} → tontine {tid} : {e}")
            raison = str(e) if isinstance(e, ValueError) else "erreur technique, réessayez plus tard"
            return f"❌ {nom} ({num}) est enregistré mais pas inscrit dans *{tnom}* : {raison}"
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
        if _bouffage_a_regler(conn, tid):
            release_conn(conn)
            sess["etape"] = "menu"
            return MSG_BOUFFAGE_A_REGLER
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

    # ── Commandes BOUFFAGE_COMPLET (cagnotte intégrale) et DEBLOQUER — accord de l'admin ──
    if (texte.upper().startswith("BOUFFAGE_COMPLET ") or texte.upper().startswith("DEBLOQUER ")) \
            and sess["etape"] == "menu":
        complet = texte.upper().startswith("BOUFFAGE_COMPLET ")
        if complet:
            parts = texte.strip().split(None, 2)
            if len(parts) < 2 or not parts[1].isdigit():
                return "❌ Format : *BOUFFAGE_COMPLET 42 raison*"
            raison = parts[2] if len(parts) > 2 else "Cas exceptionnel — décision admin"
        else:
            parts = texte.strip().split()
            if len(parts) < 2 or not parts[1].isdigit():
                return "❌ Format : *DEBLOQUER 42* (ID du passage)"
        passage_id = int(parts[1])
        conn = get_conn()
        try:
            # Ligne verrouillée : deux décisions simultanées se sérialisent, et le job des
            # bouffages suspects expirés relit la ligne sous verrou avant d'intercepter.
            passage = fetchone(conn, """
                SELECT lp.*, m.nom_complet, m.whatsapp, t.cycle_actuel, t.heure_bouffage,
                       t.statut AS statut_tontine
                FROM liste_passage lp
                JOIN tontines t ON t.id = lp.tontine_id
                LEFT JOIN membres m ON m.id = lp.membre_id
                WHERE lp.id=%s AND lp.tontine_id=%s
                FOR UPDATE OF lp
            """, (passage_id, tid))
            if not passage:
                return f"❌ Passage #{passage_id} introuvable pour cette tontine."
            if passage["statut_tontine"] != "Active":
                # Le job de bouffage ne sert que les tontines actives : un accord ici
                # promettrait un versement qui ne viendrait pas.
                return (f"❌ {tnom} n'est pas active (statut : {passage['statut_tontine']}) : "
                        f"aucun bouffage ne peut être versé pour l'instant.")
            # Seul un bouffage retenu par le contrôle suspect et encore en attente se
            # débloque : remettre en attente un tour payé, cédé ou intercepté le ferait repayer.
            if passage["statut"] != "En_attente":
                return (f"❌ Le passage #{passage_id} n'est pas un bouffage bloqué en attente "
                        f"(statut : {passage['statut']}) : rien à débloquer.")
            if not passage["bloque_suspect"]:
                return f"❌ Le passage #{passage_id} n'est pas bloqué par le contrôle suspect : rien à débloquer."
            if passage["cycle"] != passage["cycle_actuel"]:
                return (f"❌ Le passage #{passage_id} appartient au cycle {passage['cycle']}, "
                        f"terminé : rien à débloquer.")
            if passage.get("whatsapp") == wa:
                return "❌ Vous ne pouvez pas débloquer votre propre bouffage."
            # Accord lié au titulaire actuel : une cession ou une nouvelle liste l'annule.
            q(conn, """UPDATE liste_passage
                       SET bloque_suspect=0, date_blocage=NULL,
                           debloque_le=NOW(), debloque_pour=%s, bouffage_complet=%s
                       WHERE id=%s""", (passage["membre_id"], 1 if complet else 0, passage_id))
            conn.commit()
        finally:
            release_conn(conn)
        heure = passage.get("heure_bouffage") or "17:00"
        if complet:
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
                f"Il recevra la cagnotte intégrale à la prochaine heure de bouffage ({heure}).\n"
                f"Cette décision a été notifiée au owner BADF."
            )
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
            f"Il sera versé à la prochaine heure de bouffage ({heure})."
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
        membre, _ = _membre_de_la_tontine(conn, num, tid, tnom, inclure_partis=True)   # nom et DM : membres de ce groupe
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
