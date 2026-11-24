"""
Jobs quotidiens du propriétaire : comptabilité BADF (07h33), surveillance
des anomalies financières (06h36) et supervision « zero touch » (06h33).

Extrait de barack_corp_v9_18.py sans changement de comportement. Dépend de
db.py, health.py, audit.py et whatsapp.py, jamais du bot ; le scheduler du
bot planifie ces trois jobs.
"""
import logging
import time as time_module
from datetime import datetime, timedelta, timezone

from audit import log_audit
from db import get_conn, release_conn, fetchone, fetchall
from health import healed, _health_lock, _health_state
from whatsapp import wa_owner, _outbox_path

__all__ = ["comptabilite_badf_quotidienne", "surveillance_anomalies_quotidienne", "_zto_state",
           "zto_supervision_quotidienne"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


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
    conn = None
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
              AND date_adhesion::date BETWEEN %s AND %s
        """, (debut_mois, today))["nb"]
        revenu_kyc_mois = kyc_mois * 2000  # FRAIS_KYC

        # ── 4. État des dettes BADF ───────────────────────────────────────
        dettes = fetchone(conn, """
            SELECT
                COALESCE(SUM(CASE WHEN statut='Due'   THEN montant ELSE 0 END), 0) AS du,
                COALESCE(SUM(CASE WHEN statut='Payee' THEN montant ELSE 0 END), 0) AS encaisse,
                COUNT(CASE WHEN statut='Due' AND montant > 0
                                AND date_creation < NOW() - INTERVAL '24 hours'
                           THEN 1 END) AS en_retard
            FROM dettes_badf
            WHERE date_creation::date BETWEEN %s AND %s
        """, (debut_mois, today))

        # ── 5. Top 3 admins par dette en retard (à appeler) ───────────────
        mauvais_payeurs = fetchall(conn, """
            SELECT admin_wa, SUM(montant) AS du, COUNT(*) AS nb_dettes,
                   MIN(date_creation) AS plus_ancienne
            FROM dettes_badf
            WHERE statut='Due' AND montant > 0
              AND date_creation < NOW() - INTERVAL '24 hours'
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
               AND COALESCE(SUM(CASE WHEN statut='Payee' THEN montant ELSE 0 END), 0) > 0
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
        # IRA exclue : elle revient aux membres ponctuels, pas à BADF.
        projection_total = projection_fmp + (kyc_mois * 2000)

        # ── 9. Variations vs mois précédent ───────────────────────────────
        croissance = 0
        if ca_mois_prec > 0:
            croissance = int(((projection_fmp - ca_mois_prec) / ca_mois_prec) * 100)

        release_conn(conn)
        conn = None  # relâchée avant la construction du message : pas de double libération

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
            f"   IRA (membres ponctuels) : {ca_jour['ira_du']:,} FCFA\n"
            f"   Membres actifs    : {ca_jour['membres_actifs']}\n\n"

            f"📅 *CE MOIS ({debut_mois.strftime('%b %Y')})*\n"
            f"   FMP cumulé        : {ca_mois['fmp_du']:,} FCFA\n"
            f"   IRA (membres ponctuels) : {ca_mois['ira_du']:,} FCFA\n"
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
                jours = (datetime.now(timezone.utc) - mp["plus_ancienne"]).days   # TIMESTAMPTZ
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
    finally:
        if conn:
            release_conn(conn)


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
    conn = None
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

        # ── 2. Même image soumise dans des tontines différentes ───────────
        # hash (octets) est UNIQUE : une copie exacte est refusée à l'entrée et
        # ne laisse qu'une ligne. La même image, réencodée, garde son dhash
        # (empreinte perceptuelle) : c'est lui qu'on regroupe.
        hash_partages = fetchall(conn, """
            SELECT dhash AS hash, COUNT(DISTINCT tontine_id) AS nb_tontines,
                   array_agg(DISTINCT tontine_id) AS tontines
            FROM screenshots_hash
            WHERE created_at >= NOW() - INTERVAL '7 days' AND dhash IS NOT NULL
            GROUP BY dhash
            HAVING COUNT(DISTINCT tontine_id) >= 2
            LIMIT 5
        """)
        for h in hash_partages:
            alertes.append(
                f"🔄 HASH PARTAGÉ — fraude inter-groupes\n"
                f"   Screenshot {h['hash'][:12]}... soumis dans "
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
        conn = None

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
    finally:
        if conn:
            release_conn(conn)


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
    conn = None
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
                COUNT(CASE WHEN type_event LIKE '%%ERREUR%%' THEN 1 END) AS erreurs,
                COUNT(CASE WHEN type_event LIKE '%%SUSPENSION%%' THEN 1 END) AS suspensions,
                COUNT(CASE WHEN type_event LIKE '%%FRAUDE%%' THEN 1 END) AS fraudes
            FROM audit_log
            WHERE date_heure >= NOW() - INTERVAL '24 hours'
        """)
        release_conn(conn)
        conn = None

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
    finally:
        if conn:
            release_conn(conn)
