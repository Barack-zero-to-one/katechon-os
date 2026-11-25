"""
Santé du bot : décorateur @healed (retry et fallback des jobs), état de santé
partagé, auto-réparation du pool PostgreSQL, supervision de la base et de
Green API, drain de l'outbox au rétablissement.

Extrait de barack_corp_v9_18.py sans changement de comportement. Dépend de
db.py et whatsapp.py, jamais du bot. Les routes /health et /health/detail
restent dans le bot avec l'application Flask et lisent l'état réexporté.
"""
import functools
import logging
import threading
import time as time_module

import psycopg2
import requests

from katechon.core.db import get_conn, fetchone, release_conn, reinitialiser_pool
from katechon.messaging.whatsapp import (GREENAPI_BASE, GREENAPI_INSTANCE_ID, GREENAPI_TOKEN, OWNER_WA,
                      _outbox_drain, _outbox_enqueue, _wa_send)

__all__ = ["healed", "_health_state", "_health_lock", "_self_heal_db", "_self_heal_check",
           "_self_heal_outbox", "_check_greenapi"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


def healed(critical: bool = False, fallback=None):
    """
    Décorateur auto-healing : si la fonction échoue, log silencieux + retry +
    fallback. Aucune alerte au owner si la fonction se rétablit seule.
    Usage :
        @healed(critical=True)
        def ma_fonction(): ...
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(3):
                try:
                    result = fn(*args, **kwargs)
                    if attempt > 0:
                        log.info(f"✅ Auto-heal OK : {fn.__name__} après {attempt+1} tentative(s)")
                    return result
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                    log.warning(f"⚠️ DB error dans {fn.__name__} (tentative {attempt+1}) : {str(e)[:80]}")
                    with _health_lock:
                        _health_state["consecutive_db_failures"] += 1
                        _health_state["last_db_failure"] = time_module.time()
                    if attempt < 2:
                        _self_heal_db()
                        time_module.sleep(1 + attempt)
                    elif critical:
                        raise
                except (requests.ConnectionError, requests.Timeout) as e:
                    log.warning(f"⚠️ Network error dans {fn.__name__} : {str(e)[:80]}")
                    if attempt < 2:
                        time_module.sleep(2 + attempt * 2)
                except Exception as e:
                    log.error(f"❌ Exception dans {fn.__name__} (tentative {attempt+1}/3) : {e}")
                    if attempt < 2 and not critical:
                        time_module.sleep(1)
                    elif critical:
                        raise
                    else:
                        break
            return fallback
        return wrapper
    return deco


# État de santé global du bot
_health_state = {
    "db_ok":            True,
    "wa_ok":          True,
    "scheduler_ok":     True,
    "last_db_failure":  0,
    "last_wa_failure":0,
    "consecutive_db_failures":   0,
    "consecutive_wa_failures": 0,
    "self_heal_attempts":        0,
    "started_at":                time_module.time(),
}
_health_lock = threading.Lock()


def _self_heal_db():
    """Tente de réparer le pool DB silencieusement."""
    with _health_lock:
        _health_state["self_heal_attempts"] += 1
    try:
        reinitialiser_pool()             # même verrou que get_conn() — un seul pool à la fois
        # Ne pas reset consecutive_db_failures ici : le reset appartient à _self_heal_check()
        # après confirmation par SELECT 1. Resetter ici masquerait les DB flapping.
        log.info("✅ Auto-heal DB : pool réinitialisé")
        return True
    except Exception as e:
        log.error(f"❌ Auto-heal DB échec : {e}")
        return False


def _self_heal_check():
    """
    Job périodique de supervision. S'exécute toutes les 60s.
    Détecte les pannes DB silencieusement et tente la réparation.
    Alertes graduées : chaque multiple de 5 cycles en échec (5 min, 10 min, ...).
    Notification de recovery quand la DB revient après une alerte.
    """
    try:
        conn = get_conn(retries=1)
        fetchone(conn, "SELECT 1 AS ok")
        release_conn(conn)
        with _health_lock:
            prev_failures = _health_state["consecutive_db_failures"]
            _health_state["db_ok"] = True
            _health_state["consecutive_db_failures"] = 0
        # Recovery : notifier le owner si on était en état d'alerte
        if prev_failures >= 5:
            try:
                _wa_send(OWNER_WA,
                    f"✅ *DB RÉTABLIE*\n\n"
                    f"Base de données opérationnelle après {prev_failures} cycles en échec ({prev_failures} min).\n"
                    f"Bot 100% fonctionnel.")
            except Exception:
                pass
    except Exception as e:
        with _health_lock:
            _health_state["db_ok"] = False
            _health_state["consecutive_db_failures"] += 1
            failures = _health_state["consecutive_db_failures"]
            heal_attempts = _health_state["self_heal_attempts"]
        log.warning(f"⚠️ Health check DB échec #{failures} : {str(e)[:80]}")
        _self_heal_db()
        # Alerte graduée : à 5 cycles, puis tous les 5 cycles (évite spam + évite miss)
        if failures >= 5 and failures % 5 == 0:
            try:
                _wa_send(OWNER_WA,
                    f"🔴 *ALERTE DB #{failures // 5}*\n\n"
                    f"Base de données injoignable depuis {failures} cycles ({failures} min).\n"
                    f"Tentatives auto-réparation : {heal_attempts}\n\n"
                    f"Vérifier PostgreSQL.")
            except Exception:
                pass


def _self_heal_outbox():
    """Force le drain de l'outbox quand Green API revient."""
    with _health_lock:
        wa_ok = _health_state["wa_ok"]
    if wa_ok:
        _outbox_drain()


def _check_greenapi():
    """Ping Green API toutes les 10 min — détecte les pannes et notifie le recovery."""
    if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
        return
    try:
        url = f"{GREENAPI_BASE}/waInstance{GREENAPI_INSTANCE_ID}/getStateInstance/{GREENAPI_TOKEN}"
        r = requests.get(url, timeout=10)
        state = (r.json().get("stateInstance", "") if r.status_code == 200 else "")
        ok = (state == "authorized")
        with _health_lock:
            prev_ok = _health_state["wa_ok"]
            _health_state["wa_ok"] = ok
            if ok:
                _health_state["consecutive_wa_failures"] = 0
            else:
                _health_state["consecutive_wa_failures"] += 1
                _health_state["last_wa_failure"] = time_module.time()
            failures = _health_state["consecutive_wa_failures"]
        if not ok and failures >= 3 and failures % 3 == 0:
            _outbox_enqueue("send", {
                "to":   OWNER_WA,
                "body": (f"🟠 *ALERTE GREEN API #{failures // 3}*\n\n"
                         f"WhatsApp injoignable depuis {failures * 10} min.\n"
                         f"État : {state or 'inconnu'}. Messages en file d'attente outbox."),
            })
        if ok and not prev_ok:
            try:
                _wa_send(OWNER_WA,
                    "✅ *GREEN API RÉTABLIE*\n\nMessages en file d'attente en cours d'envoi.")
            except Exception:
                pass
            _outbox_drain()
    except Exception:
        with _health_lock:
            _health_state["wa_ok"] = False
            _health_state["consecutive_wa_failures"] += 1
