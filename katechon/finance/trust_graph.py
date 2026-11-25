"""
Trust Graph : score de risque de fugue post-bouffage (10 caractéristiques
comportementales), alerte quotidienne avant les bouffages, commandes admin
« risque » et « cas ».

Extrait de barack_corp_v9_18.py sans changement de comportement. Dépend de
db.py, health.py, audit.py et whatsapp.py, jamais du bot. Le job de
versement notifier_prochain_bouffage reste dans le bot et appelle
calculer_score_risque_fugue.
"""
import logging
from datetime import datetime

from katechon.core.audit import log_audit
from katechon.core.db import get_conn, q, release_conn, fetchone, fetchall
from katechon.core.health import healed
from katechon.messaging.whatsapp import wa_prive, wa_owner

__all__ = [
    "_update_score_confiance", "POIDS_MAX_TRUST_GRAPH", "_agreger_score_risque_fugue",
    "calculer_score_risque_fugue", "alerter_risques_bouffage_imminent",
    "commande_admin_score_risque", "commande_admin_cas_difficile",
    "commande_admin_cas_resolu", "lever_pauses_echues", "message_pause_refusee",
]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée

# Calcule un score de risque entre 0 et 100 pour chaque membre dont le
# bouffage approche. Plus le score est élevé, plus la probabilité de fugue
# post-bouffage est haute. Permet à l'admin d'agir AVANT.
#
# Le modèle combine 10 features comportementales (poids bruts, somme 145,
# normalisée sur 100 par _agreger_score_risque_fugue) :
#   1. Régularité historique (variance des intervalles entre cotisations)  25
#   2. Tendance récente (cotisations 0–30 j contre 30–60 j)                20
#   3. Score de confiance inversé                                          15
#   4. Dettes en cours (dette IRA / capacité mensuelle)                    15
#   5. Profondeur d'engagement (ancienneté, nombre de tontines)            10
#   6. Vélocité de paiement (délai après heure_ouverture)                  10
#   7. Signaux faibles (suspensions, tentatives de fraude)                  5
#   8. Comportement post-bouffage                                          20
#   9. Chute du score de confiance (> 25 pts en 30 j)                      10
#  10. Position dans le cycle                                              15
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


POIDS_MAX_TRUST_GRAPH = 145  # somme des poids max des 10 features (25+20+15+15+10+10+5+20+10+15)


def _agreger_score_risque_fugue(score_brut: float, features: dict, signaux: list) -> dict:
    """
    Fonction pure — aucun accès DB, aucun I/O.
    Prend le score brut déjà accumulé par les 10 features (échelle 0-145) et
    produit le résultat final normalisé sur 0-100 : score, niveau, recommandation.
    Isolée de calculer_score_risque_fugue pour être testable indépendamment
    du calcul SQL des features (cf. stress_test_katechon.py).
    """
    score = score_brut * (100.0 / POIDS_MAX_TRUST_GRAPH)
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
                   tentatives_fraude, date_adhesion
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
        confiance = membre["score_confiance"] if membre["score_confiance"] is not None else 50
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
        # Plus un membre est engagé (ancien, multi-tontines), plus le risque baisse.
        nb_tontines = fetchone(conn, """
            SELECT COUNT(DISTINCT tontine_id) AS n FROM adhesions
            WHERE membre_id=%s AND statut IN ('Actif','Pause')
        """, (membre_id,))["n"]

        anciennete_jours = (datetime.now().date() - membre["date_adhesion"].date()).days if membre["date_adhesion"] else 0

        engagement_score = 0
        if nb_tontines == 1 and anciennete_jours < 30:
            # Membre tout neuf dans une seule tontine → risque
            engagement_score = 10
            signaux.append(f"Membre récent (un seul groupe, {anciennete_jours} jours)")
        elif nb_tontines == 1:
            engagement_score = 5
        # nb_tontines >= 2 → membre engagé sur plusieurs tontines → 0 risque
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
            WHERE membre_id=%s AND type_sanction LIKE %s
        """, (membre_id, "%uspension%"))["n"]

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

        # ── Feature 10 : Position dans le cycle (poids 15) ──────────────────
        # Signal structurel — le membre ne contrôle pas sa position dans la rotation.
        # Plus il passe tard, plus la tentation est forte (N-1 paiements vus avant lui).
        passage_pos = fetchone(conn, """
            SELECT lp.ordre,
                   (SELECT COUNT(*) FROM liste_passage
                    WHERE tontine_id=%s AND cycle=%s) AS total
            FROM liste_passage lp
            WHERE lp.membre_id=%s AND lp.tontine_id=%s
              AND lp.cycle = %s
            LIMIT 1
        """, (tontine_id, tontine["cycle_actuel"], membre_id, tontine_id, tontine["cycle_actuel"]))

        if passage_pos and passage_pos["total"] and passage_pos["total"] > 0:
            rang_relatif = passage_pos["ordre"] / passage_pos["total"]
            if rang_relatif >= 0.75:
                position_score = 15
                signaux.append(
                    f"Position tardive : {passage_pos['ordre']}/{passage_pos['total']} "
                    f"({rang_relatif*100:.0f}% du cycle)"
                )
            elif rang_relatif >= 0.5:
                position_score = 8
                signaux.append(
                    f"Milieu de cycle : {passage_pos['ordre']}/{passage_pos['total']}"
                )
            else:
                position_score = 0
            features["position_cycle"] = position_score
            score += position_score

        # ── Normalisation et niveau final ───────────────────────────────────
        return _agreger_score_risque_fugue(score, features, signaux)

    except Exception as e:
        log.error(f"❌ calculer_score_risque_fugue m={membre_id} t={tontine_id} : {e}", exc_info=True)
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
        erreurs_par_admin = {}  # admin_wa → liste de membres en échec de calcul

        for p in prochains:
            score_dict = calculer_score_risque_fugue(conn, p["membre_id"], p["tontine_id"])

            if score_dict["niveau"] == "erreur":
                # Échec du calcul — ne JAMAIS traiter comme "zéro risque" (fail-open).
                # Signale pour revue manuelle, sans pénalité automatique ni score fictif.
                admin_err = fetchone(conn, """
                    SELECT whatsapp FROM admins_groupe
                    WHERE tontine_id=%s LIMIT 1
                """, (p["tid"],))
                if admin_err:
                    erreurs_par_admin.setdefault(admin_err["whatsapp"], []).append(p)
                continue

            # On n'alerte que pour orange et rouge
            if score_dict["niveau"] not in ("orange", "rouge"):
                continue

            jours_avant = (p["date_bouffage"] - datetime.now().date()).days

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

            # Propagation cross-tontine — pénalité unique sur score_confiance global
            # Permet à Feature 3 de répercuter le risque dans les autres tontines du membre
            type_alerte_tg = f"TrustGraph_{score_dict['niveau']}"
            deja_penalise = fetchone(conn, """
                SELECT id FROM alertes_fugue
                WHERE membre_id=%s AND tontine_id=%s AND type_alerte=%s AND traite=0
            """, (p["membre_id"], p["tontine_id"], type_alerte_tg))

            if not deja_penalise:
                delta = -15 if score_dict["niveau"] == "rouge" else -7
                _update_score_confiance(conn, p["membre_id"], delta=delta,
                    raison=f"Trust Graph {score_dict['niveau']} J-{jours_avant} — {p['nom_tontine']}")
                q(conn, """INSERT INTO alertes_fugue
                           (membre_id, tontine_id, type_alerte, jours_retard, montant_du)
                           VALUES (%s, %s, %s, %s, 0) ON CONFLICT DO NOTHING""",
                  (p["membre_id"], p["tontine_id"], type_alerte_tg, jours_avant))
                conn.commit()

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

        # Notifier les admins des échecs de calcul (revue manuelle requise)
        for admin_wa, membres_erreur in erreurs_par_admin.items():
            lignes = "\n".join(
                f"  • {p['nom_complet']} (bouffage J+{(p['date_bouffage'] - datetime.now().date()).days})"
                for p in membres_erreur
            )
            wa_prive(admin_wa,
                f"⚠️ *SCORE DE RISQUE INDISPONIBLE*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Le calcul du score de risque a échoué pour {len(membres_erreur)} "
                f"membre(s) dont le bouffage arrive dans les 7 prochains jours :\n\n"
                f"{lignes}\n\n"
                f"Vérifiez leur situation manuellement avant le bouffage.\n\n"
                f"_BADF Ltd · Modèle prédictif_"
            )
            log_audit("TRUST_GRAPH_ERREUR_PREDICTIF",
                      f"{len(membres_erreur)} membre(s) en échec de calcul", admin_wa)

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
    conn = None
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

        # 0. Un seul cas actif par membre et par tontine. L'adhésion est
        #    verrouillée d'abord : deux escalades simultanées se sérialisent.
        adhesion = fetchone(conn, """SELECT statut FROM adhesions
                                     WHERE membre_id=%s AND tontine_id=%s FOR UPDATE""",
                            (membre["id"], tontine_id))
        statut_adh = adhesion["statut"] if adhesion else None
        cas_ouvert = fetchone(conn, """SELECT id FROM cas_difficiles WHERE membre_id=%s AND tontine_id=%s
                                       AND statut='Actif' AND type_cas='Pause'""",
                              (membre["id"], tontine_id))
        if cas_ouvert and statut_adh == "Pause":
            conn.rollback()
            release_conn(conn)
            return (f"ℹ️ Un cas est déjà ouvert pour *{membre['nom_complet']}*. "
                    f"Clôturez-le avec *cas_resolu {nom_membre} reprend|retire*.")
        # Seul un membre actif peut être mis en pause : sinon la reprise
        # réactiverait un suspendu (sans frais de réactivation) ou un exclu.
        if statut_adh != "Actif":
            conn.rollback()
            release_conn(conn)
            return message_pause_refusee(membre["nom_complet"], nom_tontine, statut_adh)

        # Un cas Pause resté Actif alors que l'adhésion est Actif est périmé
        # (pause levée par un nouveau cycle) : il est clos avant le nouveau.
        q(conn, """UPDATE cas_difficiles SET statut='Termine'
                   WHERE membre_id=%s AND tontine_id=%s AND statut='Actif' AND type_cas='Pause'""",
          (membre["id"], tontine_id))

        # 1. Mettre le membre en Pause dans cette tontine
        q(conn, """
            UPDATE adhesions SET statut='Pause'
            WHERE membre_id=%s AND tontine_id=%s
        """, (membre["id"], tontine_id))

        # 2. Créer l'entrée cas_difficile
        q(conn, """
            INSERT INTO cas_difficiles
                (membre_id, tontine_id, type_cas, details, date_reprise, admin_id)
            VALUES (%s, %s, 'Pause', %s, NOW() + INTERVAL '30 days',
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
        release_conn(conn)
        conn = None
        # Le cas est enregistré : un échec d'envoi ne doit plus faire croire
        # à l'admin que l'escalade a échoué (sa relance créerait un doublon).
        for a in autres_admins:
            try:
                wa_prive(a["whatsapp"], msg_aux_autres)
            except Exception as e:
                log.error(f"❌ cas difficile : notification {a['whatsapp']} : {e}")
        try:
            log_audit("CAS_DIFFICILE_OUVERT",
                      f"Tontine:{nom_tontine} | Membre:{membre['nom_complet']} | {description[:80]}",
                      wa_admin)
        except Exception as e:
            log.error(f"❌ cas difficile : audit : {e}")

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
        # Tous les retours normaux de la fonction relâchent déjà `conn` avant
        # de sortir (return n'atteint jamais ce bloc) — donc si on arrive ici,
        # `conn` (si acquis) n'a encore jamais été relâché. Sans ce release,
        # la connexion reste éternellement sortie du pool à chaque exception.
        if conn:
            release_conn(conn)
        log.error(f"❌ commande_admin_cas_difficile : {e}")
        return "❌ Erreur lors de l'escalade. Réessayez."


def _message_reprise(nom_tontine: str) -> str:
    return (f"▶️ *REPRISE — {nom_tontine}*\n"
            f"Votre pause est terminée : vous êtes de nouveau membre actif.\n"
            f"Les cotisations reprennent au prochain jour de collecte.")


def message_pause_refusee(nom_membre: str, nom_tontine: str, statut) -> str:
    return (f"❌ *{nom_membre}* n'est pas actif dans {nom_tontine} "
            f"(statut : {statut or 'aucune adhésion'}) : pas de mise en pause.")


def _motif_like(texte: str) -> str:
    return "%" + texte.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def commande_admin_cas_resolu(wa_admin: str, args_texte: str) -> str:
    """
    Commande admin : 'cas_resolu [nom_membre] reprend|retire [tontine]'
    Clôt la décision collective ouverte par 'cas' :
      - reprend : l'adhésion repasse Actif (si elle est encore en Pause)
      - retire  : l'adhésion passe Quitte, sans bannissement, et ses tours
                  en attente passent Cede (plus de bouffage pour ce membre)
    Le cas passe Termine. Seuls les cas Pause actifs des tontines dont
    l'expéditeur est admin sont visibles ; un nom exact (ou un numéro) est
    préféré à une correspondance partielle ; le nom de la tontine en fin de
    commande départage un membre en pause dans plusieurs tontines.
    """
    usage = ("Usage : *cas_resolu [nom_membre] reprend|retire*\n\n"
             "Exemples :\n  cas_resolu Marie reprend\n  cas_resolu Jean retire\n"
             "  cas_resolu Jean retire Njangi Akwa  (si Jean est en pause dans plusieurs tontines)")
    mots = (args_texte or "").split()
    pos = next((i for i, m in enumerate(mots) if i > 0 and m.lower() in ("reprend", "retire")), None)
    if pos is None:
        return usage
    decision = mots[pos].lower()
    nom = " ".join(mots[:pos])
    nom_tontine = " ".join(mots[pos + 1:])
    # Un numéro saisi sans indicatif (699123456) retrouve +237699123456.
    chiffres = "".join(ch for ch in nom if ch.isdigit())
    if len(chiffres) < 8 or len(chiffres) < len(nom.replace(" ", "").lstrip("+")):
        chiffres = ""

    conn = None
    try:
        conn = get_conn()
        if not fetchone(conn, "SELECT 1 AS ok FROM admins_groupe WHERE whatsapp=%s LIMIT 1", (wa_admin,)):
            return "❌ Vous n'êtes pas administrateur d'une tontine."
        cas = fetchall(conn, """
            SELECT c.id, c.membre_id, c.tontine_id, m.nom_complet, m.whatsapp, t.nom AS nom_tontine,
                   (LOWER(m.nom_complet) = LOWER(%s) OR m.whatsapp = %s
                    OR (%s <> '' AND regexp_replace(m.whatsapp, '[^0-9]', '', 'g') LIKE %s)) AS exact
            FROM cas_difficiles c
            JOIN membres m ON m.id = c.membre_id
            JOIN tontines t ON t.id = c.tontine_id
            WHERE c.statut='Actif' AND c.type_cas='Pause'
              AND c.tontine_id IN (SELECT tontine_id FROM admins_groupe WHERE whatsapp=%s)
              AND (LOWER(m.nom_complet) LIKE %s OR m.whatsapp=%s
                   OR (%s <> '' AND regexp_replace(m.whatsapp, '[^0-9]', '', 'g') LIKE %s))
              AND (%s = '' OR LOWER(t.nom) = LOWER(%s))
            ORDER BY m.nom_complet, t.nom, c.id
            FOR UPDATE OF c
        """, (nom, nom, chiffres, "%" + chiffres, wa_admin, _motif_like(nom), nom, chiffres, "%" + chiffres,
              nom_tontine, nom_tontine))
        if any(c["exact"] for c in cas):
            cas = [c for c in cas if c["exact"]]
        if not cas:
            conn.rollback()
            ou = f"dans {nom_tontine}" if nom_tontine else "dans vos tontines"
            return f"❌ Aucun cas ouvert pour '{nom}' {ou}."
        if len({(c["membre_id"], c["tontine_id"]) for c in cas}) > 1:
            conn.rollback()
            vus, liste = set(), []
            homonymes = [(c["nom_complet"].lower(), c["nom_tontine"]) for c in cas]
            for c in cas:
                if (c["membre_id"], c["tontine_id"]) not in vus:
                    vus.add((c["membre_id"], c["tontine_id"]))
                    # Deux homonymes dans la même tontine : seul le numéro les distingue.
                    cle = (c["whatsapp"] if homonymes.count((c["nom_complet"].lower(), c["nom_tontine"])) > 1
                           else c["nom_complet"])
                    liste.append(f"  • cas_resolu {cle} {decision} {c['nom_tontine']}")
            return (f"❓ Plusieurs cas ouverts correspondent à '{nom}'. Tapez l'une de ces commandes :\n"
                    + "\n".join(liste))
        c = cas[0]
        reactive = False
        if decision == "reprend":
            reactive = q(conn, """UPDATE adhesions SET statut='Actif'
                                  WHERE membre_id=%s AND tontine_id=%s AND statut='Pause'""",
                         (c["membre_id"], c["tontine_id"])).rowcount > 0
            if not reactive:
                actuel = fetchone(conn, "SELECT statut FROM adhesions WHERE membre_id=%s AND tontine_id=%s",
                                  (c["membre_id"], c["tontine_id"]))
        else:
            # Comme une cession : refusé tant qu'un tour bloqué ou approuvé attend son
            # règlement (import local : socle importe ce module).
            from katechon.core.socle import bouffage_a_regler, msg_bouffage_a_regler
            a_regler = bouffage_a_regler(conn, c["tontine_id"], c["membre_id"], verrouiller=True)
            if a_regler:
                conn.rollback()
                return msg_bouffage_a_regler(a_regler, "retirer ce membre")
            q(conn, "UPDATE adhesions SET statut='Quitte' WHERE membre_id=%s AND tontine_id=%s",
              (c["membre_id"], c["tontine_id"]))
            # Comme une cession (menu 9 C) : ses tours non servis ne seront pas payés.
            q(conn, """UPDATE liste_passage SET statut='Cede'
                       WHERE membre_id=%s AND tontine_id=%s AND statut='En_attente'""",
              (c["membre_id"], c["tontine_id"]))
        q(conn, "UPDATE cas_difficiles SET statut='Termine' WHERE id = ANY(%s)", ([x["id"] for x in cas],))
        autres = fetchall(conn, "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s AND whatsapp != %s",
                          (c["tontine_id"], wa_admin))
        conn.commit()
        release_conn(conn)
        conn = None

        if decision == "retire":
            titre = f"✅ *Cas résolu — {c['nom_complet']} retiré de {c['nom_tontine']}*"
            au_membre = (f"⏹️ *FIN DE PARTICIPATION — {c['nom_tontine']}*\n"
                         f"Les admins ont décidé de clore votre participation à cette tontine.")
        elif reactive:
            titre = f"✅ *Cas résolu — {c['nom_complet']} réactivé*"
            au_membre = _message_reprise(c["nom_tontine"])
        else:
            statut = actuel["statut"] if actuel else "aucune adhésion"
            titre = (f"ℹ️ *Cas clos — {c['nom_complet']} n'était plus en pause (statut : {statut})*\n"
                     f"Adhésion inchangée.")
            au_membre = None
        aux_admins = (f"🤝 *CAS DIFFICILE CLOS — {c['nom_tontine']}*\n"
                      f"Membre : *{c['nom_complet']}*\nDécision : *{decision}*")
        envois = [(a["whatsapp"], aux_admins) for a in autres]
        if au_membre:
            envois.append((c["whatsapp"], au_membre))
        for dest, texte in envois:
            try:
                wa_prive(dest, texte)
            except Exception as e:
                log.error(f"❌ cas_resolu : notification {dest} : {e}")
        try:
            log_audit("CAS_DIFFICILE_RESOLU",
                      f"Tontine:{c['nom_tontine']} | Membre:{c['nom_complet']} | {decision}", wa_admin)
        except Exception as e:
            log.error(f"❌ cas_resolu : audit : {e}")
        return f"{titre}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{len(autres)} autre(s) admin(s) informé(s).\n\n_BADF Ltd_"
    except Exception as e:
        log.error(f"❌ commande_admin_cas_resolu : {e}")
        return "❌ Erreur lors de la clôture du cas. Réessayez."
    finally:
        if conn:
            release_conn(conn)


# Date de reprise effective : le menu 9 A écrivait le texte JJ/MM/AAAA tel quel
# dans la colonne DATE (lu selon le DateStyle du serveur, donc parfois inversé) ;
# le texte saisi, conservé dans details, fait foi.
_REPRISE_EFFECTIVE = """COALESCE(CASE WHEN {c}.details ~ '^Pause jusqu''au [0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}}$'
                     THEN to_date(right({c}.details, 10), 'DD/MM/YYYY') END, {c}.date_reprise)"""


@healed()
def lever_pauses_echues():
    """
    Job quotidien : lève les pauses (commande 'cas' ou menu admin 9 A) dont la
    date de reprise est atteinte. Les cas échus sont clos ; l'adhésion repasse
    Actif seulement si elle est encore en Pause et qu'aucune autre pause active
    ne court encore. Un seul message par membre et par tontine.
    """
    conn = None
    try:
        conn = get_conn()
        echus = fetchall(conn, f"""
            SELECT c.id, c.membre_id, c.tontine_id, m.whatsapp, m.nom_complet, t.nom AS nom_tontine,
                   EXISTS (SELECT 1 FROM cas_difficiles c2
                           WHERE c2.membre_id = c.membre_id AND c2.tontine_id = c.tontine_id
                             AND c2.statut='Actif' AND c2.type_cas='Pause'
                             AND ({_REPRISE_EFFECTIVE.format(c="c2")} IS NULL
                                  OR {_REPRISE_EFFECTIVE.format(c="c2")} > CURRENT_DATE)) AS autre_pause
            FROM cas_difficiles c
            JOIN membres m ON m.id = c.membre_id
            JOIN tontines t ON t.id = c.tontine_id
            WHERE c.statut='Actif' AND c.type_cas='Pause'
              AND {_REPRISE_EFFECTIVE.format(c="c")} <= CURRENT_DATE
            ORDER BY c.id
            FOR UPDATE OF c
        """)
        repris, vus = [], set()
        for c in echus:
            q(conn, "UPDATE cas_difficiles SET statut='Termine' WHERE id=%s", (c["id"],))
            cle = (c["membre_id"], c["tontine_id"])
            if cle in vus or c["autre_pause"]:
                continue
            vus.add(cle)
            if q(conn, """UPDATE adhesions SET statut='Actif'
                          WHERE membre_id=%s AND tontine_id=%s AND statut='Pause'""", cle).rowcount > 0:
                repris.append(c)
        conn.commit()
        release_conn(conn)
        conn = None
        for c in repris:
            try:
                wa_prive(c["whatsapp"], _message_reprise(c["nom_tontine"]))
            except Exception as e:
                log.error(f"❌ lever_pauses_echues : notification {c['whatsapp']} : {e}")
            try:
                log_audit("PAUSE_LEVEE", f"Tontine:{c['nom_tontine']} | Membre:{c['nom_complet']}", c["whatsapp"])
            except Exception as e:
                log.error(f"❌ lever_pauses_echues : audit : {e}")
        if echus:
            log.info(f"▶️ Pauses échues : {len(echus)} cas clos, {len(repris)} membre(s) réactivé(s)")
    finally:
        if conn:
            release_conn(conn)
