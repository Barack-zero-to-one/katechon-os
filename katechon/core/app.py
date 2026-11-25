"""
Coquille de l'application : configuration du journal (barack_corp.log et
journal de sécurité audit_securite.log), app Flask avec sa clé de session,
jetons du dashboard et de la santé, horodatage de démarrage .bot_start,
en-têtes de sécurité, et routes /health et /health/detail.

Extrait de barack_corp_v9_18.py sans changement de comportement. Le
webhook et le dashboard enregistrent leurs routes sur cette app ; le
module ne dépend jamais du bot.
"""
import hmac
import logging
import os
import pathlib
import time as time_module
from datetime import datetime

from flask import Flask, jsonify, request

from katechon.core.db import fetchone, get_conn, release_conn
from katechon.core.health import _health_lock, _health_state

__all__ = ["RACINE", "log", "audit", "app", "_DASH_TOKEN", "_HEALTH_TOKEN", "_BOT_START_FILE", "_BOT_START",
           "_add_security_headers", "health", "health_detail"]

RACINE = pathlib.Path(__file__).resolve().parents[2]   # racine du dépôt

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


# Nom fixe et dossier racine du dépôt. Importé, le bot nommait l'app
# « barack_corp_v9_18 » ; lancé en script par le watchdog, elle s'appelait
# « __main__ ». Le nom est désormais toujours « barack_corp_v9_18 » : seul le
# nom du logger Flask (app.logger) change sous le watchdog, aucun réglage n'en dépend.
app = Flask("barack_corp_v9_18", root_path=str(RACINE))
# Pas de fallback hardcodé : ce fichier est sur un repo public GitHub, donc
# toute valeur par défaut ici serait un secret public. Si la variable est
# absente, secret_key vaut "" — Flask lève à la première utilisation de la
# session, et le check fail-closed dans __main__ empêche même de démarrer.
app.secret_key  = os.getenv("FLASK_SECRET_KEY", "")
_DASH_TOKEN     = os.getenv("DASHBOARD_TOKEN",  "")
_HEALTH_TOKEN   = os.getenv("HEALTH_TOKEN",     "")
_BOT_START_FILE = os.path.join(str(RACINE), ".bot_start")   # à la racine du dépôt, comme avant
try:
    with open(_BOT_START_FILE) as _f:
        _BOT_START = datetime.fromisoformat(_f.read().strip())
except Exception:
    _BOT_START = datetime.now()
    try:
        with open(_BOT_START_FILE, "w") as _f:
            _f.write(_BOT_START.isoformat())
    except Exception:
        pass

@app.after_request
def _add_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# ══════════════════════════════════════════════════════════════════════════
# ROUTE DE SANTÉ
# ══════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    if not _HEALTH_TOKEN or not hmac.compare_digest(request.args.get("token", ""), _HEALTH_TOKEN):
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
        "version":   "9.18",
        "timestamp": datetime.now().isoformat(),
        "db":        "ok" if db_ok else "error",
    }), 200 if db_ok else 503


# ══════════════════════════════════════════════════════════════════════════
# AUTO-HEALING — logique dans health.py ; ici la route de détail (app Flask)
# ══════════════════════════════════════════════════════════════════════════


@app.route("/health/detail", methods=["GET"])
def health_detail():
    """Endpoint santé détaillé pour monitoring externe."""
    if not _HEALTH_TOKEN or not hmac.compare_digest(request.args.get("token", ""), _HEALTH_TOKEN):
        return jsonify({"status": "unauthorized"}), 401
    with _health_lock:
        state = dict(_health_state)
    state["uptime_sec"] = int(time_module.time() - state["started_at"])
    state["timestamp"]  = datetime.now().isoformat()
    overall = state["db_ok"] and state["wa_ok"]
    return jsonify(state), 200 if overall else 503
