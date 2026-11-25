"""
Enregistrement des routes HTTP des handlers sur l'app Flask du bot :
GET et POST /webhook/whatsapp (katechon.handlers.webhook) et /dashboard,
/dashboard/data (katechon.handlers.dashboard). Les routes /health vivent
avec l'app dans katechon/core/app.py.

Idempotent : un second appel ne réenregistre rien (Flask refuse d'ajouter une
route une fois l'app en service). Appelé à l'import du point d'entrée et par
katechon.core.scheduler.demarrer(), pour qu'un démarrage direct serve aussi
le webhook et le dashboard.
"""
from katechon.core.app import _BOT_START, _DASH_TOKEN, log
from katechon.core.db import get_conn, release_conn
from katechon.handlers import dashboard
from katechon.handlers.webhook import register_webhook

__all__ = ["enregistrer_routes"]


def enregistrer_routes(app) -> None:
    if "webhook_whatsapp_greenapi" in app.view_functions:
        return
    register_webhook(app)   # GET et POST /webhook/whatsapp, endpoints inchangés
    dashboard.register_dashboard(
        app,
        get_conn=get_conn, release_conn=release_conn,
        bot_start=_BOT_START, dash_token=_DASH_TOKEN, logger=log,
    )
