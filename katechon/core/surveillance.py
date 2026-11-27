"""
Dead man's switch : un ping HTTP vers un moniteur externe (Healthchecks.io)
après chaque cycle de santé réussi. Si les pings cessent, le moniteur alerte
le owner par un canal qui ne dépend ni de cette machine ni de WhatsApp.

Le ping part dans un thread dédié, avec un délai de 3 s, et ne lève jamais :
un moniteur lent, muet ou injoignable ne ralentit pas le bot. Un seul ping
peut être en vol ; tant qu'il n'est pas terminé, les suivants sont sautés.

Activé seulement si KATECHON_HEALTHCHECK=1 et HEALTHCHECK_PING_URL est
défini, lus à chaque appel : sans ces variables le bot se comporte comme avant.
"""
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, wait

import requests

__all__ = ["TIMEOUT_PING", "ping_healthcheck", "signaler_arret", "attendre_pings"]

log = logging.getLogger("BarackCorp")

TIMEOUT_PING = 3

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="HealthcheckPing")
_verrou = threading.Lock()
_en_vol = None


def _url():
    if os.getenv("KATECHON_HEALTHCHECK") != "1":
        return None
    return (os.getenv("HEALTHCHECK_PING_URL") or "").strip() or None


def _envoyer(url):
    try:
        requests.get(url, timeout=TIMEOUT_PING)
    except Exception as e:   # moniteur injoignable : jamais une erreur du bot
        log.debug(f"Ping healthcheck non envoyé : {type(e).__name__}")


def ping_healthcheck(suffixe=""):
    """Soumet un ping sans attendre. Retourne True s'il est parti, False s'il
    est désactivé ou si le ping précédent est encore en vol."""
    global _en_vol
    url = _url()
    if url is None:
        return False
    with _verrou:
        if _en_vol is not None and not _en_vol.done():
            return False
        try:
            _en_vol = _executor.submit(_envoyer, url.rstrip("/") + suffixe if suffixe else url)
        except RuntimeError:   # exécuteur arrêté (fin du processus)
            return False
    return True


def attendre_pings(timeout):
    """Attend la fin du ping en vol. Retourne True s'il n'en reste aucun."""
    with _verrou:
        f = _en_vol
    if f is None:
        return True
    wait([f], timeout=timeout)
    return f.done()


def signaler_arret():
    """Arrêt propre : signale l'échec au moniteur, qui alerte sans attendre la
    fin du délai de grâce. Attend au plus TIMEOUT_PING + 1 s."""
    attendre_pings(TIMEOUT_PING + 1)
    if ping_healthcheck("/fail"):
        attendre_pings(TIMEOUT_PING + 1)
