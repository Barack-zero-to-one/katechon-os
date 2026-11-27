"""
Menu membre WhatsApp et accueil : premier contact et collecte du prénom,
options du menu (profil, tontines, rang, caution, historique, paiement,
paiement avancé, changement de numéro, signalement) et raccourcis en
langage naturel pour le rang et la caution.

Extrait de barack_corp_v9_18.py sans changement de comportement. Ne dépend
jamais du bot ; le webhook l'appelle pour les messages privés des membres.
"""
import logging
import hashlib
import re
import time as time_module
from typing import Optional

from katechon.core.audit import log_audit
from katechon.core.db import fetchall, fetchone, get_conn, q, release_conn
from katechon.core.etat import _sessions_kyc, _sessions_lock, _sessions_membre, session_valide
from katechon.messaging.messages import MENU_MEMBRE_TXT, MSG_BIENVENUE_DM, msg_dissuasion
from katechon.finance.paiement import traiter_reponse_numero_bouffage
from katechon.core.socle import (
    FRAIS_CHGNUM,
    FRAIS_FMP,
    NUMERO_BADF_MTN,
    NUMERO_BADF_ORANGE,
    est_telegram,
    get_membre_by_wa,
    normaliser_numero,
    valider_numero_cameroun,
)
from katechon.messaging.whatsapp import wa_admin, wa_prive

__all__ = ["_demarrer_collecte_nom", "traiter_kyc", "_finaliser_nom", "traiter_menu_membre",
    "_repondre_rang", "_repondre_caution", "_init_paiement_cotisation"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


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
    elif texte == "9" and sess["etape"] == "menu" and est_telegram(wa):
        return ("📱 Votre compte est lié à Telegram, pas à un numéro de téléphone : "
                "il n'y a pas de numéro à changer.\n_Tapez *menu*_")

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
