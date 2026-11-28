"""
Enregistrement des routes HTTP des handlers sur l'app Flask du bot :
GET et POST /webhook/whatsapp (katechon.handlers.webhook) et /dashboard,
/dashboard/data (katechon.handlers.dashboard). Les routes /health vivent
avec l'app dans katechon/core/app.py.

Idempotent et sûr en parallèle : sous verrou, chaque groupe de routes n'est
enregistré que s'il manque (Flask refuse de remplacer un endpoint, et
register_dashboard crée de nouvelles vues à chaque appel). Appelé à l'import du point d'entrée et par
katechon.core.scheduler.demarrer(), pour qu'un démarrage direct serve aussi
le webhook et le dashboard.
"""
import threading

from katechon.core.app import _BOT_START, _DASH_TOKEN, log
from katechon.core.db import get_conn, release_conn
from katechon.handlers import dashboard
from katechon.handlers.telegram import register_telegram
from katechon.handlers.webhook import register_webhook

__all__ = ["enregistrer_routes"]


_verrou = threading.Lock()


def enregistrer_routes(app) -> None:
    with _verrou:
        if "webhook_whatsapp_greenapi" not in app.view_functions:
            register_webhook(app)   # GET et POST /webhook/whatsapp, endpoints inchangés
        if "webhook_telegram" not in app.view_functions:
            register_telegram(app)  # POST /webhook/telegram, 404 tant que KATECHON_TELEGRAM n'est pas à 1
        if "dashboard_data" not in app.view_functions:
            dashboard.register_dashboard(
                app,
                get_conn=get_conn, release_conn=release_conn,
                bot_start=_BOT_START, dash_token=_DASH_TOKEN, logger=log,
            )
