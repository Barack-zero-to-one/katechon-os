# -*- coding: utf-8 -*-
"""
dashboard.py — Dashboard local temps réel (Bloomberg terminal UI).

Sous-système périphérique EN LECTURE SEULE, extrait de barack_corp_v9_18.py : il
ne touche aucun chemin de mouvement d'argent, uniquement des SELECT. Séparé de
l'orchestrateur métier par choix d'architecture (le HTML vivait déjà dans
dashboard.html ; la logique Python le rejoint ici).

Câblage par INJECTION DE DÉPENDANCES (pas d'import de barack_corp → zéro import
circulaire) :

    import dashboard
    dashboard.register_dashboard(
        app,
        get_conn=get_conn, release_conn=release_conn,
        bot_start=_BOT_START, dash_token=_DASH_TOKEN, logger=log,
    )

Comportement identique à l'inline d'origine : mêmes routes (/dashboard,
/dashboard/data), même auth par token + session, mêmes requêtes SQL.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime

from flask import request, jsonify, session, Response

_DASHBOARD_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "dashboard.html")


def register_dashboard(app, *, get_conn, release_conn, bot_start, dash_token,
                       logger, html_path: str = _DASHBOARD_HTML_PATH):
    """Enregistre les routes du dashboard sur l'app Flask fournie.

    Args:
        app          : l'application Flask.
        get_conn     : callable -> connexion DB (pool).
        release_conn : callable(conn) -> rend la connexion au pool.
        bot_start    : datetime de démarrage du bot (pour l'uptime).
        dash_token   : token secret d'accès (str) ; vide => accès refusé.
        logger       : logger pour les erreurs.
        html_path    : chemin du dashboard.html (défaut : sibling).
    """
    _dashboard_html = open(html_path, encoding="utf-8").read()

    @app.route("/dashboard/data", methods=["GET"])
    def dashboard_data():
        """Données temps réel pour le dashboard — JSON."""
        if not session.get("dash_ok"):
            return jsonify({"error": "unauthorized"}), 401
        try:
            conn = get_conn()
            try:
                delta = datetime.now() - bot_start
                h, rem = divmod(int(delta.total_seconds()), 3600)
                m, s   = divmod(rem, 60)
                uptime = f"{h}h {m:02d}m {s:02d}s"

                cur = conn.cursor()
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

                cur.execute("""
                    SELECT t.nom, t.montant_place,
                           t.cycle_actuel, t.heure_bouffage,
                           COUNT(a.id) FILTER (WHERE a.statut='Actif') AS nb_membres
                    FROM tontines t
                    LEFT JOIN adhesions a ON a.tontine_id = t.id
                    WHERE t.statut = 'Active'
                    GROUP BY t.id
                    ORDER BY t.nom
                """)
                cols     = [d[0] for d in cur.description]
                tontines = [dict(zip(cols, row)) for row in cur.fetchall()]

                cur.execute("""
                    SELECT
                        t.nom,
                        t.montant_place,
                        t.capacite_max,
                        (SELECT COUNT(*)
                         FROM adhesions a WHERE a.tontine_id=t.id AND a.statut='Actif') AS nb_membres,
                        (SELECT COALESCE(SUM(a2.nombre_places),0)
                         FROM adhesions a2 WHERE a2.tontine_id=t.id AND a2.statut='Actif') AS nb_places,
                        (SELECT COALESCE(SUM(tx.frais_fmp),0)
                         FROM transactions tx WHERE tx.tontine_id=t.id AND tx.statut='Confirmee') AS fmp_reel,
                        (SELECT COALESCE(SUM(di.montant),0)
                         FROM dettes_ira di WHERE di.tontine_id=t.id AND di.statut='Due') AS ira_du
                    FROM tontines t
                    WHERE t.statut='Active'
                    ORDER BY t.nom
                """)
                _proj_rows = cur.fetchall()
                _proj_list, _fmp_reel_tot, _fmp_jour_tot, _ira_du_tot = [], 0.0, 0.0, 0.0
                for _r in _proj_rows:
                    _nom, _mp, _cap, _nb_m, _nb_p, _fmp_r, _ira = _r
                    _fj = float(_nb_p) * float(_mp) * 0.02
                    _fmp_reel_tot += float(_fmp_r)
                    _fmp_jour_tot += _fj
                    _ira_du_tot   += float(_ira)
                    _proj_list.append({"nom": _nom, "nb_membres": int(_nb_m),
                        "capacite_max": int(_cap), "fmp_jour": _fj,
                        "fmp_reel": float(_fmp_r), "ira_du": float(_ira)})
                projection = {"par_tontine": _proj_list, "fmp_reel": _fmp_reel_tot,
                              "fmp_jour": _fmp_jour_tot, "fmp_30j": _fmp_jour_tot * 30,
                              "ira_du": _ira_du_tot}

                cur.execute("""
                    SELECT
                        COALESCE(SUM(frais_fmp)    FILTER (WHERE date_heure::date = CURRENT_DATE), 0),
                        COALESCE(SUM(montant_brut) FILTER (WHERE type_transaction = 'Adhesion'
                                                           AND date_heure::date = CURRENT_DATE), 0),
                        COALESCE(SUM(frais_ira)    FILTER (WHERE date_heure::date = CURRENT_DATE), 0),
                        COALESCE(SUM(montant_brut) FILTER (WHERE date_heure::date = CURRENT_DATE), 0),
                        COALESCE(SUM(montant_brut), 0)
                    FROM transactions
                    WHERE statut = 'Confirmee'
                """)
                r         = cur.fetchone()
                revenus   = {"fmp": r[0], "adhesions": r[1], "ira": r[2], "total": r[0]+r[1]+r[2]}
                gmv_jour  = r[3]
                gmv_total = r[4]

                cur.execute("SELECT COUNT(*) FROM cotisations_manuelles WHERE statut='En_attente'")
                cotis_attente = cur.fetchone()[0]

                cur.execute("""
                    SELECT m.nom_complet, bm.montant_net, t.nom AS tontine
                    FROM bouffages_manuels bm
                    JOIN membres m  ON m.id  = bm.membre_id
                    JOIN tontines t ON t.id  = bm.tontine_id
                    WHERE bm.statut = 'En_attente'
                    ORDER BY bm.date_declenchement DESC
                    LIMIT 5
                """)
                bouffages = [{"nom": row[0], "montant": row[1], "tontine": row[2]}
                             for row in cur.fetchall()]

                cur.execute("SELECT COALESCE(SUM(montant), 0) FROM dettes_ira WHERE statut='Due'")
                ira_total = cur.fetchone()[0]

                cur.execute("""
                    SELECT type_event, details, date_heure
                    FROM audit_log
                    ORDER BY date_heure DESC
                    LIMIT 15
                """)
                activite = [
                    {"type": row[0],
                     "details": (row[1] or "")[:60],
                     "ts": row[2].strftime("%H:%M:%S") if row[2] else ""}
                    for row in cur.fetchall()
                ]

                resp = jsonify({
                    "uptime":       uptime,
                    "ts":           datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "tontines":     tontines,
                    "projection":   projection,
                    "revenus":      revenus,
                    "cotis_attente": cotis_attente,
                    "bouffages":    bouffages,
                    "ira_total":    ira_total,
                    "gmv_jour":     gmv_jour,
                    "gmv_total":    gmv_total,
                    "activite":     activite,
                })
                resp.headers["Cache-Control"] = "no-store"
                return resp
            finally:
                release_conn(conn)
        except Exception as e:
            logger.error(f"Dashboard data error: {e}")
            return jsonify({"error": "internal_error"}), 500

    @app.route("/dashboard", methods=["GET"])
    def dashboard():
        """Dashboard local temps réel — Bloomberg terminal UI."""
        tok = request.args.get("token", "")
        if not session.get("dash_ok"):
            if not dash_token or not hmac.compare_digest(tok, dash_token):
                return Response(
                    '<html><body style="background:#060606;color:#ff4444;'
                    'font-family:monospace;padding:40px">'
                    '<h2>&#9679; ACC&#200;S REFUS&#201;</h2>'
                    '<p>Ajoutez <code>?token=DASHBOARD_TOKEN</code> '
                    'à l\'URL.</p></body></html>',
                    content_type="text/html; charset=utf-8",
                    status=401
                )
            session["dash_ok"] = True
        return Response(_dashboard_html, content_type="text/html; charset=utf-8")
