"""
Jobs planifiés : bouffages suspects expirés et cashout, notification du
prochain bouffage (avec contrôle de l'historique), rappels d'ouverture, du
matin et des non-cotisants, suspension automatique 72 h, détection des
fugitifs après bouffage, sauvegarde pg_dump avec rotation, codes USSD de
reversement et escalade des dettes BADF impayées.

Extrait de barack_corp_v9_18.py sans changement de comportement. Ne dépend
jamais du bot ; katechon/core/scheduler.py les planifie.
"""
import logging
import os
import shutil
import subprocess
import time as time_module
from datetime import datetime, timedelta

from katechon.core.audit import log_audit
from katechon.core.db import fetchall, fetchone, get_conn, q, release_conn
from katechon.core.health import healed
from katechon.core.signalement import signaler_erreur
from katechon.core.socle import (
    BACKUP_DIR,
    BACKUP_ROTATION,
    DELAI_ALERTE_FUGUE,
    DELAI_BLOCAGE_FUGUE,
    DELAI_SUSPENSION_HEURES,
    FRAIS_REACTIV,
    IRA_CLIFF_PCT,
    IRA_DAILY_PCT,
    NUMERO_BADF_MTN,
    NUMERO_BADF_ORANGE,
    PG_BIN,
    PG_DB,
    PG_HOST,
    PG_PASS,
    PG_PORT,
    PG_USER,
    _est_jour_cotisation,
    est_owner,
    get_tontines_admin,
)
from katechon.finance.paiement import _get_retardataires, declencher_bouffage_manuel
from katechon.finance.trust_graph import _update_score_confiance, calculer_score_risque_fugue
from katechon.messaging.messages import MSG_DISSUASION
from katechon.messaging.whatsapp import (
    _wa_send_groupe,
    wa_admins_tontine,
    wa_groupe,
    wa_owner,
    wa_prive,
    wa_quitter_groupe,
)

__all__ = ["traiter_bouffages_suspects_expires", "_verifier_historique_avant_bouffage",
    "notifier_prochain_bouffage", "rappel_ouverture", "rappel_matin", "rappel_non_cotisants",
    "verifier_suspensions_retard", "detecter_fugitifs", "backup_postgresql", "generer_codes_ussd",
    "texte_codes_ussd_pour_admin", "verifier_dettes_badf_impayees"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


@healed(financier=True)
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

            # Suspendre le membre, en traçant la tontine qui suspend (réactivation isolée)
            # (un membre banni le reste : jamais rétrogradé en suspendu levable)
            if q(conn, """UPDATE membres SET statut_global='Suspendu_global'
                          WHERE id=%s AND statut_global <> 'Banni'""", (membre_id,)).rowcount:
                q(conn, """INSERT INTO sanctions (membre_id, tontine_id, type_sanction, notes)
                           VALUES (%s,%s,'Interception_bouffage','Bouffage suspect non débloqué sous 48h')""",
                  (membre_id, tontine_id))

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
        signaler_erreur(e, "traiter_bouffages_suspects_expires", financier=True)
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


@healed(financier=True)
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
            except Exception as e_caution:   # 10 % retenus sans caution enregistrée
                signaler_erreur(e_caution, "notifier_prochain_bouffage", financier=True,
                                contexte={"membre_id": passage["mbr_id"], "tontine_id": t["id"],
                                          "passage_id": passage["id"], "caution": caution_mont,
                                          "pourcent": caution_pct})

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


@healed(financier=True)
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
                  AND m.suspendu_retard=0 AND m.statut_global NOT IN ('Suspendu_global','Banni')
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
                    # Sursis : la récidive ne compte qu'après un délai complet depuis
                    # l'avertissement (même retard prolongé ou nouveau retard).
                    if m["nb_avertissements_retard"] > 0 and fetchone(conn, """
                            SELECT 1 AS x FROM sanctions
                            WHERE membre_id=%s AND tontine_id=%s AND type_sanction='Retard_paiement'
                              AND date_sanction > %s""", (m["id"], t["id"], seuil)):
                        continue
                    if m["nb_avertissements_retard"] == 0:
                        # PREMIÈRE FOIS — sursis silencieux, aucun message
                        q(conn, "UPDATE adhesions SET nb_avertissements_retard=1 WHERE membre_id=%s AND tontine_id=%s",
                          (m["id"], t["id"]))
                        q(conn, """INSERT INTO sanctions (membre_id, tontine_id, type_sanction, notes)
                                   VALUES (%s,%s,'Retard_paiement','Premier retard — sursis silencieux')""",
                          (m["id"], t["id"]))
                        conn.commit()
                        log_audit("AVERTISSEMENT_RETARD_SURSIS",
                                  f"{m['nom_complet']} — {t['nom']}", m["whatsapp"])
                    else:
                        # RÉCIDIVE — suspension complète + 1 000 FCFA
                        if not q(conn, """UPDATE membres
                                   SET suspendu_retard=1, date_suspension_retard=NOW(),
                                       statut_global='Suspendu_global'
                                   WHERE id=%s AND statut_global NOT IN ('Suspendu_global','Banni')""",
                                   (m["id"],)).rowcount:
                            conn.rollback()      # banni ou suspendu entre-temps : rien à faire
                            continue
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


@healed(financier=True)
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
                    if q(conn, "UPDATE membres SET statut_global='Suspendu_global' WHERE id=%s AND statut_global <> 'Banni'",
                         (m["id"],)).rowcount:
                        q(conn, """INSERT INTO sanctions (membre_id, tontine_id, type_sanction, notes)
                                   VALUES (%s,%s,'Fugue_post_bouffage',%s)""",
                          (m["id"], t["id"], f"Blocage après {jours} jours sans cotiser"))
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


@healed(financier=True)
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
