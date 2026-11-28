"""
Démarrage et planification : vérification et création de la base
PostgreSQL, détection de l'URL publique, scheduler APScheduler avec ses 23
jobs (heure de Douala), démarrage unique sous verrou (pool, tables,
sessions, scheduler), service HTTP Waitress et arrêt propre.

Extrait de barack_corp_v9_18.py sans changement de comportement. Ne dépend
jamais du bot ; le point d'entrée appelle demarrer().
"""
import logging
import atexit
import sys
import threading
import time as time_module
from datetime import datetime
from typing import Optional

import psycopg2.extras
import requests
from apscheduler.schedulers import SchedulerNotRunningError
from apscheduler.schedulers.background import BackgroundScheduler
from katechon.core.app import _DASH_TOKEN, _HEALTH_TOKEN, app
from katechon.core.db import fermer_pool, reinitialiser_pool
from katechon.core.etat import (
    WAITRESS_THREADS,
    _download_executor,
    _msg_executor,
    _purger_sessions_expirees,
    _restaurer_sessions,
    _sauvegarder_sessions,
)
from katechon.core.health import _check_greenapi, _self_heal_check, _self_heal_outbox
from katechon.core.schema import init_db
from katechon.core.socle import (
    HEURE_BACKUP,
    NGROK_DOMAIN,
    NUMERO_BADF_MTN,
    PG_DB,
    PG_HOST,
    PG_PASS,
    PG_PORT,
    PG_USER,
    PORT,
)
from katechon.finance.jobs import (
    backup_postgresql,
    detecter_fugitifs,
    notifier_prochain_bouffage,
    rappel_matin,
    rappel_non_cotisants,
    rappel_ouverture,
    traiter_bouffages_suspects_expires,
    verifier_dettes_badf_impayees,
    verifier_suspensions_retard,
)
from katechon.finance.paiement import verifier_et_liberer_cautions
from katechon.finance.supervision import (
    comptabilite_badf_quotidienne,
    surveillance_anomalies_quotidienne,
    zto_supervision_quotidienne,
)
from katechon.finance.trust_graph import alerter_risques_bouffage_imminent, lever_pauses_echues
from katechon.messaging.whatsapp import GREENAPI_INSTANCE_ID, OWNER_WA, _expediteur, _outbox_drain, vider_expediteurs
from katechon.handlers.owner import envoyer_releve_fmp_post_bouffage, rapport_groupes_fin_journee

__all__ = ["PUBLIC_URL", "detecter_url_publique", "tester_connexion_postgresql",
    "creer_base_postgresql", "demarrer_scheduler", "_demarrage", "_demarrage_lock",
    "_ARRET_ATTENTE_JOBS", "_enregistrer_arret", "_arreter_scheduler", "_shutdown_bot", "demarrer",
    "_initialiser"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


PUBLIC_URL    = ""


def detecter_url_publique() -> str:
    """
    Détecte l'URL publique ngrok via l'API locale (port 4040).
    Si NGROK_DOMAIN est configuré → utilise directement le domaine statique.
    Appelé au démarrage. Met à jour PUBLIC_URL global.
    """
    global PUBLIC_URL

    # Domaine statique configuré → URL connue d'avance, pas besoin de détecter
    if NGROK_DOMAIN:
        PUBLIC_URL = f"https://{NGROK_DOMAIN}"
        log.info(f"🌐 URL publique (statique) : {PUBLIC_URL}")
        return PUBLIC_URL

    # Tentative de détection via l'API ngrok locale
    for tentative in range(10):
        try:
            resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=3)
            tunnels = resp.json().get("tunnels", [])
            for t in tunnels:
                if t.get("proto") == "https":
                    PUBLIC_URL = t["public_url"]
                    log.info(f"🌐 URL publique (dynamique) : {PUBLIC_URL}")
                    return PUBLIC_URL
        except Exception:
            pass
        time_module.sleep(2)

    log.warning("⚠️ URL ngrok non détectée après 10 tentatives.")
    return ""


def tester_connexion_postgresql() -> bool:
    """
    Vérifie que PostgreSQL est accessible et que la base existe.
    Retourne True si OK, False sinon.
    Appelé au démarrage avant init_db().
    """
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DB,
            user=PG_USER, password=PG_PASS,
            connect_timeout=5
        )
        conn.close()
        log.info(f"✅ PostgreSQL OK — Base '{PG_DB}' accessible.")
        return True
    except psycopg2.OperationalError as e:
        erreur = str(e).strip()
        if f'database "{PG_DB}" does not exist' in erreur:
            log.warning(f"⚠️ La base '{PG_DB}' n'existe pas. Tentative de création...")
            return creer_base_postgresql()
        else:
            log.error(f"❌ PostgreSQL inaccessible : {erreur}")
            log.error(f"   Vérifiez : PG_HOST={PG_HOST}, PG_PORT={PG_PORT}, "
                      f"PG_USER={PG_USER}, PG_PASS=***")
            return False


def creer_base_postgresql() -> bool:
    """
    Crée la base de données barack_corp si elle n'existe pas.
    Se connecte à la base 'postgres' (toujours présente) pour créer.
    """
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname="postgres",
            user=PG_USER, password=PG_PASS,
            connect_timeout=5
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE {PG_DB} ENCODING 'UTF8'")
        conn.close()
        log.info(f"✅ Base '{PG_DB}' créée avec succès.")
        return True
    except Exception as e:
        log.error(f"❌ Impossible de créer la base '{PG_DB}' : {e}")
        log.error(f"   Créez-la manuellement : psql -U postgres -c "
                  f"\"CREATE DATABASE {PG_DB};\"")
        return False


def demarrer_scheduler():
    scheduler = BackgroundScheduler(timezone="Africa/Douala")

    # ── Rappels groupe (minute=3 — fréquence naturelle) ───────────────────
    scheduler.add_job(rappel_ouverture,      "cron", minute=3,  id="rappel_ouverture")
    scheduler.add_job(rappel_matin,          "cron", minute=3,  id="rappel_matin")
    scheduler.add_job(rappel_non_cotisants,  "cron", minute=3,  id="rappel_non_cotisants")
    scheduler.add_job(notifier_prochain_bouffage, "cron", minute=9, id="bouffage")

    # ── Jobs fixes ────────────────────────────────────────────────────────
    scheduler.add_job(rapport_groupes_fin_journee,      "cron", minute=0,           id="rapport_groupes")
    scheduler.add_job(envoyer_releve_fmp_post_bouffage,"cron", minute="*",         id="fmp_post_bouffage")
    scheduler.add_job(verifier_suspensions_retard,  "cron", minute=30, id="suspensions")
    scheduler.add_job(detecter_fugitifs,            "cron", hour=8,  minute=33, id="anti_fugue")
    scheduler.add_job(backup_postgresql,            "cron", hour=HEURE_BACKUP, minute=0, id="backup")
    scheduler.add_job(traiter_bouffages_suspects_expires, "cron", hour=11, minute=6,  id="suspects_expires")
    scheduler.add_job(verifier_et_liberer_cautions, "cron", hour=9,  minute=9,  id="cautions_liberation")
    scheduler.add_job(verifier_dettes_badf_impayees,"cron", hour=10, minute=0,  id="dettes_badf")

    # ── Outbox drain — réessaie les messages WhatsApp non envoyés ────────
    scheduler.add_job(_outbox_drain,             "interval", seconds=30,  id="outbox_drain")
    scheduler.add_job(_purger_sessions_expirees, "interval", minutes=5,   id="purge_sessions")
    scheduler.add_job(_sauvegarder_sessions,     "interval", seconds=60,  id="sessions_backup")

    # ── Auto-healing — supervision silencieuse ────────────────────────────
    scheduler.add_job(_self_heal_check,  "interval", seconds=60,  id="self_heal_check")
    scheduler.add_job(_self_heal_outbox, "interval", seconds=120, id="self_heal_outbox")
    scheduler.add_job(_check_greenapi,   "interval", minutes=10,  id="check_greenapi",
                      next_run_time=datetime.now())

    # ── Zero Touch Ops — supervision quotidienne du owner ────────────────
    scheduler.add_job(zto_supervision_quotidienne,    "cron", hour=6, minute=33, id="zto_supervision")

    # ── Surveillance proactive anti-fraude (avant la compta) ─────────────
    scheduler.add_job(surveillance_anomalies_quotidienne, "cron", hour=6, minute=36, id="surveillance_anomalies")

    # ── Comptabilité BADF auto (envoyée juste après ouverture) ───────────
    scheduler.add_job(comptabilite_badf_quotidienne,  "cron", hour=7, minute=33, id="compta_badf")

    # ── Finance comportementale prédictive — risque fugue J-7 ────────────
    scheduler.add_job(alerter_risques_bouffage_imminent, "cron", hour=5, minute=33, id="risque_fugue_predictif")
    # Reprise automatique des pauses (commande 'cas' et menu admin 9 A) à date_reprise.
    scheduler.add_job(lever_pauses_echues, "cron", hour=6, minute=5, id="pauses_echues")

    scheduler.start()
    log.info("✅ Scheduler TontineBot Pro v9.18 démarré.")
    return scheduler


# Un seul démarrage par processus : l'initialisation (pool, tables, sessions,
# scheduler) ne s'exécute qu'une fois, sous verrou ; un appel ultérieur peut
# encore servir HTTP. L'arrêt propre est enregistré une seule fois.
_demarrage = {"scheduler": None, "arret_enregistre": False, "initialise": False,
              "sessions_restaurees": False, "adresses_servies": set()}
_demarrage_lock = threading.Lock()
# Attente maximale des jobs en cours quand _shutdown_bot est appelé directement.
# À la sortie normale de l'interpréteur, concurrent.futures a déjà attendu les
# workers (sans limite) avant les handlers atexit : la limite ne joue pas là.
_ARRET_ATTENTE_JOBS = 10


def _enregistrer_arret():
    if not _demarrage["arret_enregistre"]:
        atexit.register(_shutdown_bot)
        _demarrage["arret_enregistre"] = True


def _arreter_scheduler(scheduler):
    """Arrête le scheduler en attendant ses jobs en cours, au plus
    _ARRET_ATTENTE_JOBS secondes ; tout échec est journalisé."""
    erreurs = []

    def _arret():
        try:
            scheduler.shutdown(wait=True)
        except SchedulerNotRunningError:
            pass                      # jamais démarré ou déjà arrêté : rien à signaler
        except Exception as e:
            erreurs.append(e)

    fil = threading.Thread(target=_arret, name="ArretScheduler", daemon=True)
    try:
        fil.start()
    except RuntimeError:
        # Python 3.12+ : aucun thread ne peut démarrer pendant atexit. Les jobs
        # sont déjà terminés à ce stade ; on arrête directement.
        _arret()
        if erreurs:
            log.warning(f"⚠️ Arrêt du scheduler en échec : {erreurs[0]}")
        return
    fil.join(_ARRET_ATTENTE_JOBS)
    if fil.is_alive():
        log.warning(f"⚠️ Jobs encore en cours après {_ARRET_ATTENTE_JOBS}s — fermeture du pool quand même")
    elif erreurs:
        log.warning(f"⚠️ Arrêt du scheduler en échec : {erreurs[0]}")


def _shutdown_bot():
    """Appelé par atexit à chaque arrêt — stoppe le scheduler (jobs en cours
    attendus, délai borné), puis ferme le pool DB."""
    scheduler = _demarrage["scheduler"]
    if scheduler is not None:
        _arreter_scheduler(scheduler)
    vider_expediteurs(timeout=10)   # laisse partir les messages déjà soumis (WhatsApp et Telegram)
    if _demarrage.get("sessions_restaurees"):
        _sauvegarder_sessions()     # jamais avant la restauration : la sauvegarde serait écrasée à vide
    fermer_pool()
    _msg_executor.shutdown(wait=False)
    _download_executor.shutdown(wait=False)
    log.info("Bot arrêté proprement — pool DB fermé.")


def demarrer(servir: bool = True, hote: str = "127.0.0.1", port: Optional[int] = None):
    """Démarre le bot : secrets, PostgreSQL, pool, tables, sessions, URL
    publique, scheduler, arrêt propre, puis Waitress sur hote:port (PORT par
    défaut). Écoute sur la boucle locale par défaut : ngrok et le watchdog s'y
    connectent, rien d'autre n'a à joindre le bot depuis le réseau local. Retourne le scheduler ; servir=False s'arrête avant Waitress.
    L'initialisation ne s'exécute qu'une fois par processus, même sous appels
    simultanés ; un appel ultérieur avec servir=True sert quand même HTTP."""
    port = PORT if port is None else port
    from katechon.routes import enregistrer_routes   # import tardif : scheduler reste léger
    with _demarrage_lock:
        enregistrer_routes(app)   # idempotent : webhook et dashboard même sans le point d'entrée
        if not _demarrage["initialise"]:
            _initialiser(hote, port)
        scheduler = _demarrage["scheduler"]

    if not servir:
        return scheduler
    adresses = _demarrage.setdefault("adresses_servies", set())
    with _demarrage_lock:
        if (hote, port) in adresses:
            log.warning(f"⚠️ {hote}:{port} est déjà servi par ce processus — pas de second serveur")
            return scheduler
        adresses.add((hote, port))
    from waitress import serve
    log.info(f"🚀 Waitress WSGI — {WAITRESS_THREADS} threads — port {port}")
    serve(app, host=hote, port=port, threads=WAITRESS_THREADS,
          channel_timeout=120, cleanup_interval=30)
    return scheduler


def _initialiser(hote: str, port: int):
    """Étapes de démarrage, dans l'ordre : secrets, PostgreSQL, pool (arrêt
    propre enregistré aussitôt), tables, sessions, URL publique, scheduler."""
    log.info("━" * 60)
    log.info("🚀 TontineBot Pro v9.18 — Barack & AI Development Facilities Ltd")
    log.info("   BADF Ltd — Cameroun 🇨🇲")
    log.info("━" * 60)
    log.info(f"   Owner           : {OWNER_WA}")
    log.info(f"   WhatsApp       : Green API — Instance {GREENAPI_INSTANCE_ID or '(non configurée)'}")
    log.info(f"   Base de données : {PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}")
    log.info(f"   Écoute HTTP     : {hote}:{port}")
    log.info(f"   Numéro BADF MTN : {NUMERO_BADF_MTN}")
    log.info("━" * 60)
    log.info("💡 Mode paiement : MANUEL (screenshot → admin confirme)")
    log.info("   • Membres envoient screenshot dans le groupe")
    log.info("   • Bot détecte et enregistre automatiquement")
    log.info("   • Admin confirme en 1 tap")
    log.info("   • Bot notifie le membre")
    log.info("━" * 60)

    # ── 0. Validation fail-closed des secrets critiques ─────────────────────
    # Ce fichier est sur un repo GitHub public : aucune valeur par défaut
    # hardcodée n'est acceptable pour ces secrets. Si l'un d'eux est absent
    # ou trop faible, on refuse de démarrer plutôt que de tourner avec un
    # secret devinable par quiconque lit le code source.
    _SECRETS_CRITIQUES = {
        "FLASK_SECRET_KEY": app.secret_key,
        "DASHBOARD_TOKEN":  _DASH_TOKEN,
        "HEALTH_TOKEN":     _HEALTH_TOKEN,
    }
    _secrets_manquants = [nom for nom, val in _SECRETS_CRITIQUES.items() if not val]
    _secrets_faibles   = [nom for nom, val in _SECRETS_CRITIQUES.items() if val and len(val) < 16]
    if _secrets_manquants or _secrets_faibles:
        if _secrets_manquants:
            log.error(f"❌ ARRÊT — variable(s) d'environnement manquante(s) : {', '.join(_secrets_manquants)}")
        if _secrets_faibles:
            log.error(f"❌ ARRÊT — secret(s) trop court(s) (min 16 caractères) : {', '.join(_secrets_faibles)}")
        log.error("   Configurez-les dans le fichier ENV avant de redémarrer. Aucun fallback par défaut n'existe (repo public).")
        sys.exit(1)
    log.info("✅ Secrets critiques validés (présents, ≥16 caractères).")

    # ── 1. Vérification PostgreSQL ─────────────────────────────────────────
    log.info("🔍 Vérification PostgreSQL...")
    if not tester_connexion_postgresql():
        log.error("❌ ARRÊT — PostgreSQL inaccessible.")
        sys.exit(1)

    # ── 2. Init pool + tables ──────────────────────────────────────────────
    reinitialiser_pool()              # ouvre le pool sous verrou (ferme un éventuel ancien pool)
    _enregistrer_arret()              # le pool ouvert sera fermé même si la suite échoue
    init_db()
    _restaurer_sessions()
    _demarrage["sessions_restaurees"] = True
    log.info("✅ Base de données v9.18 initialisée.")

    # ── 3. Détecter l'URL publique ngrok ──────────────────────────────────
    log.info("🌐 Détection URL publique ngrok...")
    url_pub = detecter_url_publique()
    if url_pub:
        log.info(f"   ✅ URL publique : {url_pub}")
        log.info(f"   ✅ Webhook WhatsApp : {url_pub}/webhook/whatsapp")
        log.info(f"   ℹ️ Configurez ce webhook dans Meta developers.")
    else:
        log.warning("   ⚠️ URL ngrok non détectée — configurez NGROK_DOMAIN dans .env")

    # ── 4. Scheduler ───────────────────────────────────────────────────────
    _demarrage["scheduler"] = demarrer_scheduler()
    _demarrage["initialise"] = True
    log.info("✅ Scheduler démarré.")
    log.info("━" * 60)
    log.info("🟢 TontineBot Pro v9.18 — Prêt à recevoir des messages.")
    log.info("━" * 60)
