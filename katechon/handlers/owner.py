"""
Menu owner (tableau de bord financier en DM, accès réservé et alerte en
cas d'usurpation, commande CREDIT_VERSE), rapports jour, semaine, mois et
historique, état des tontines, membres, fugitifs et dettes BADF, plus les
deux jobs de fin de journée : bilan dans chaque groupe et relevé des
dettes BADF aux admins après le bouffage.

Extrait de barack_corp_v9_18.py sans changement de comportement. Ne dépend
jamais du bot ; le webhook l'appelle et le scheduler du bot planifie ses
deux jobs.
"""
from datetime import datetime, timedelta

from katechon.core.audit import log_audit
from katechon.core.db import fetchall, fetchone, get_conn, q, release_conn
from katechon.core.etat import _sessions_owner
from katechon.finance.paiement import rapport_dettes_badf_admin
from katechon.core.socle import _est_jour_cotisation, est_owner
from katechon.messaging.whatsapp import OWNER_WA, wa_groupe, wa_owner, wa_prive

__all__ = ["traiter_menu_owner", "_owner_menu_principal", "_bloc_ira_membres",
    "_owner_rapport_jour", "_owner_rapport_semaine", "_owner_rapport_mois",
    "_owner_historique_n_jours", "_owner_tontines_actives", "_owner_reserve_badf",
    "_owner_membres_stats", "_owner_fugitifs_stats", "envoyer_releve_fmp_post_bouffage",
    "rapport_groupes_fin_journee"]


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


def _bloc_ira_membres(ira: int) -> str:
    """Pénalités IRA prélevées : elles reviennent aux membres ponctuels du
    groupe (dissuasion, pas revenu), donc hors de tout total BADF."""
    return (
        f"🤝 *PÉNALITÉS IRA — fonds des membres ponctuels*\n"
        f"  Prélevées (hors revenu BADF) : *{ira:,} FCFA*\n\n"
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

        total_badf = fmp + cautions_saisies["v"]

        return (
            f"📊 *RAPPORT DU JOUR — {datetime.now().strftime('%d/%m/%Y')}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP (2%)     : *{fmp:,} FCFA*\n"
            f"  Cautions saisies        : *{cautions_saisies['v']:,} FCFA* ({cautions_saisies['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL BADF AUJOURD'HUI : {total_badf:,} FCFA*\n\n"
            f"{_bloc_ira_membres(ira)}"
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
        cautions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie
            WHERE statut='Saisie' AND date_liberation >= %s
        """, (debut_mois,))
        bouffages = fetchone(conn, """
            SELECT COUNT(*) n FROM liste_passage
            WHERE statut='Paye' AND date_paiement >= %s
        """, (debut_mois,))
        total = fmp + cautions["v"]
        mois_nom = datetime.now().strftime("%B %Y").upper()
        return (
            f"📆 *RAPPORT {mois_nom}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP          : *{fmp:,} FCFA*\n"
            f"  Cautions saisies        : *{cautions['v']:,} FCFA* ({cautions['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL MOIS : {total:,} FCFA*\n\n"
            f"{_bloc_ira_membres(ira)}"
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
        cautions = fetchone(conn, """
            SELECT COUNT(*) n, COALESCE(SUM(montant), 0) v
            FROM cautions_garantie WHERE statut='Saisie' AND date_liberation >= %s
        """, (debut,))

        # Top 5 jours les plus rentables
        top_jours = fetchall(conn, """
            SELECT date_heure::date AS jour,
                   COALESCE(SUM(frais_fmp), 0) AS total
            FROM transactions
            WHERE statut='Confirmee' AND date_heure >= %s
            GROUP BY jour ORDER BY total DESC LIMIT 5
        """, (debut,))

        lignes_top = "\n".join(
            f"  {r['jour'].strftime('%d/%m/%Y')} → {r['total']:,} FCFA"
            for r in top_jours
        ) or "  Aucune donnée."

        total = fmp + cautions["v"]
        return (
            f"📈 *{titre}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 *REVENUS BADF Ltd*\n"
            f"  Commission FMP          : *{fmp:,} FCFA*\n"
            f"  Cautions saisies        : *{cautions['v']:,} FCFA* ({cautions['n']} fugitifs)\n"
            f"  ──────────────────────────────\n"
            f"  *TOTAL PÉRIODE : {total:,} FCFA*\n\n"
            f"{_bloc_ira_membres(ira)}"
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
        suspendus = fetchone(conn, "SELECT COUNT(*) n FROM membres WHERE statut_global='Suspendu_global'")["n"]
        bannis  = fetchone(conn, "SELECT COUNT(*) n FROM membres WHERE statut_global='Banni'")["n"]
        attente_kyc = fetchone(conn, "SELECT COUNT(*) n FROM membres WHERE statut_global='En_attente_kyc'")["n"]
        nouveaux_j = fetchone(conn, """
            SELECT COUNT(*) n FROM membres WHERE date_adhesion::date = CURRENT_DATE
        """)["n"]
        nouveaux_m = fetchone(conn, """
            SELECT COUNT(*) n FROM membres
            WHERE date_adhesion >= date_trunc('month', CURRENT_DATE)
        """)["n"]
        return (
            f"👥 *STATISTIQUES MEMBRES — BADF Ltd*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"  Total inscrits   : *{total}*\n"
            f"  Actifs           : {actifs}\n"
            f"  Suspendus        : {suspendus}\n"
            f"  Bannis           : {bannis}\n"
            f"  En attente KYC   : {attente_kyc}\n\n"
            f"  Nouveaux aujourd'hui : {nouveaux_j}\n"
            f"  Nouveaux ce mois     : {nouveaux_m}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Tapez *0* pour le menu principal._"
        )
    finally:
        release_conn(conn)


def _owner_fugitifs_stats() -> str:
    conn = get_conn()
    try:
        # Seules les vraies fugues comptent : la table porte aussi les avertissements
        # de retard (Avertissement_1/2) et les signaux prédictifs du Trust Graph.
        total_fugitifs = fetchone(conn, """
            SELECT COUNT(DISTINCT membre_id) n FROM alertes_fugue
            WHERE type_alerte IN ('Blocage','Interception_caution')
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
            SELECT * FROM (
                SELECT DISTINCT ON (af.membre_id)
                       m.nom_complet, m.whatsapp, t.nom AS tontine, af.created_at
                FROM alertes_fugue af
                JOIN membres m ON m.id = af.membre_id
                JOIN tontines t ON t.id = af.tontine_id
                WHERE af.type_alerte IN ('Blocage','Interception_caution')
                ORDER BY af.membre_id, af.created_at DESC
            ) dernieres_fugues
            ORDER BY created_at DESC LIMIT 5
        """)
        lignes = []
        for f in derniers:
            date = f["created_at"].strftime("%d/%m/%Y") if f["created_at"] else "?"
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


def rapport_groupes_fin_journee():
    """Rapport de fin de journée dans chaque groupe, à heure_limite+1h de chaque tontine."""
    conn     = get_conn()
    tontines = fetchall(conn, "SELECT * FROM tontines WHERE statut='Active'")
    heure_now = datetime.now().hour
    for t in tontines:
        if not _est_jour_cotisation(t):
            continue
        h_limite = int((t["heure_limite"] or "18:00").split(":")[0])
        if (h_limite + 1) % 24 != heure_now:
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
