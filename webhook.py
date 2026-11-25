"""
Webhook WhatsApp (Green API) : routes GET et POST /webhook/whatsapp
(authentification par jeton, anti-rejeu, limite de débit, routage par type
d'événement, préchargement des médias), téléchargement sécurisé des
médias, aiguillage des messages vers les menus owner, admin, membre, la
configuration de groupe et les commandes de groupe, et traitement des
captures de cotisation.

Extrait de barack_corp_v9_18.py sans changement de comportement. Ne dépend
jamais du bot ; le bot enregistre les deux routes sur son app Flask avec
register_webhook(app), comme dashboard.register_dashboard.
"""
import logging
import concurrent.futures
import time as time_module

import requests
from katechon.security import screenshot_integrity as si
from katechon.security import webhook_security as ws
from katechon.core.audit import log_audit
from katechon.core.db import fetchone, get_conn, q, release_conn
from katechon.core.etat import _download_executor, _msg_executor, _sessions_config, rate_limit_ok
from flask import jsonify, request
from groupe import (
    _bot_ajoute_groupe,
    _est_nom_valide,
    _greenapi_get_group_members,
    _traiter_commande_groupe,
    traiter_config_tontine,
)
from menu_admin import traiter_menu_admin
from menu_membre import traiter_menu_membre
from ocr import lire_screenshot_mobile_money
from owner import traiter_menu_owner
from paiement import (
    enregistrer_cotisation_manuelle,
    hash_screenshot,
    reference_deja_utilisee,
    screenshot_deja_utilise,
    screenshot_near_duplicate,
)
from katechon.core.socle import (
    GREENAPI_WEBHOOK_SECRET,
    MEDIA_MAX_BYTES,
    _replay_guard,
    est_admin,
    est_owner,
    get_membre_by_wa,
    incrementer_tentatives_fraude,
    normaliser_numero,
)
from katechon.messaging.whatsapp import OWNER_WA, wa_prive

__all__ = ["webhook_whatsapp_verify", "_traiter_groupe_participants", "webhook_whatsapp_greenapi",
    "_greenapi_telecharger_media", "_traiter_message_greenapi",
    "_traiter_screenshot_cotisation_bytes", "register_webhook"]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée


def webhook_whatsapp_verify():
    """Health check — Green API n'utilise pas de challenge GET."""
    return jsonify({"status": "ok", "bot": "TontineBot Pro"}), 200


def _traiter_groupe_participants(payload: dict):
    """
    Dispatch incomingGroupParticipantsUpdate.
    Seul cas traité : ajout du bot → _bot_ajoute_groupe().
    """
    if payload.get("typeParticipantsUpdate") != "add":
        return

    instance_data = payload.get("instanceData") or {}
    bot_wid       = instance_data.get("wid", "")        # ex: 237XXXXXXXXXX@c.us
    group_data    = payload.get("groupData") or {}
    group_id      = group_data.get("groupId", "")        # ex: 120363XXXX@g.us
    group_name    = group_data.get("groupName", "")

    if not group_id or not bot_wid:
        return

    raw_parts = group_data.get("participants", [])
    added_ids = [
        (p.get("id", "") if isinstance(p, dict) else str(p))
        for p in raw_parts
    ]

    if bot_wid not in added_ids:
        return  # C'est quelqu'un d'autre qui a été ajouté — pas le bot

    members = _greenapi_get_group_members(group_id, exclude=bot_wid)
    log.info(f"[GROUPE] Bot ajouté → {group_name!r} ({group_id}) — {len(members)} membres")

    try:
        _msg_executor.submit(_bot_ajoute_groupe, group_id, group_name, members)
    except Exception as e:
        log.error(f"_bot_ajoute_groupe dispatch : {e}")


def webhook_whatsapp_greenapi():
    """
    Reçoit les événements WhatsApp depuis Green API.
    Format Green API : { typeWebhook, instanceData, senderData, messageData }
    """
    # ── 1) Authentification cryptographique — token secret dans le query string ──
    # GREENAPI_WEBHOOK_SECRET est configuré dans l'URL webhook du dashboard Green API :
    # https://<ngrok>/webhook/whatsapp?token=<SECRET>
    # Green API transmet ce token en query param à chaque appel entrant.
    if not GREENAPI_WEBHOOK_SECRET:
        log.error("🔴 GREENAPI_WEBHOOK_SECRET non configuré — webhook refusé")
        return jsonify({"status": "misconfigured"}), 503
    # Token accepté en query (mode Green API) OU en header Authorization: Bearer
    # (le header ne fuite pas dans les access logs comme le query string).
    incoming_token = request.args.get("token", "")
    if not incoming_token:
        _auth = request.headers.get("Authorization", "")
        if _auth.startswith("Bearer "):
            incoming_token = _auth[7:]
    if not ws.constant_time_equal(incoming_token, GREENAPI_WEBHOOK_SECRET):
        log_audit("GREENAPI_TOKEN_INVALIDE", "Webhook token mismatch", request.remote_addr)
        return jsonify({"status": "forbidden"}), 403

    # ── 2) Parsing payload ────────────────────────────────────────────────
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"status": "bad_json"}), 400

    # ── 2b) Anti-rejeu : un idMessage déjà vu = requête rejouée → drop ─────
    #        Tue le replay d'une requête capturée (le token seul ne l'empêche pas).
    if _replay_guard.est_rejeu(payload.get("idMessage", "")):
        log_audit("WEBHOOK_REPLAY",
                  f"idMessage rejoué: {payload.get('idMessage', '')}", request.remote_addr)
        return jsonify({"status": "ok"}), 200

    # ── 3) Router par type d'événement ──────────────────────────────────
    type_webhook = payload.get("typeWebhook", "")

    if type_webhook == "incomingGroupParticipantsUpdate":
        _msg_executor.submit(_traiter_groupe_participants, payload)
        return jsonify({"status": "ok"}), 200

    if type_webhook not in ("incomingMessageReceived", "incomingAPIMessageReceived"):
        return jsonify({"status": "ignored"}), 200

    # ── 4) Extraire l'émetteur ────────────────────────────────────────────
    sender_data  = payload.get("senderData") or {}
    chat_id      = sender_data.get("chatId", "")
    sender       = sender_data.get("sender", "")
    is_group     = "@g.us" in chat_id
    wa_brut      = (sender if is_group else chat_id).split("@")[0]
    group_id     = chat_id if is_group else ""
    wa           = normaliser_numero(wa_brut)
    if not wa:
        if is_group and not sender:
            log.warning(f"Green API groupe sans sender : chatId={chat_id!r}")
        return jsonify({"status": "ok"}), 200

    # ── 4b) Vérification rate limit avant download ────────────────────────
    if not rate_limit_ok(wa):
        return jsonify({"status": "ok"}), 200

    # ── 5) Précharger l'image immédiatement (avant que l'URL expire) ──────
    img_future = None
    _type_msg = (payload.get("messageData") or {}).get("typeMessage", "")
    if _type_msg in ("imageMessage", "documentMessage"):
        _data_key  = "imageData" if _type_msg == "imageMessage" else "documentData"
        _url_media = ((payload.get("messageData") or {}).get(_data_key) or {}).get("downloadUrl", "")
        if _url_media:
            try:
                img_future = _download_executor.submit(_greenapi_telecharger_media, _url_media)
            except Exception as e:
                log.error(f"Download executor submit : {e}")
                # Future pre-resolu b"" : evite le fallback sync 31s dans le MsgWorker
                _f = concurrent.futures.Future()
                _f.set_result(b"")
                img_future = _f

    # ── 6) Soumettre le traitement au thread pool ─────────────────────────
    try:
        _msg_executor.submit(_traiter_message_greenapi, payload, wa, img_future, group_id)
    except Exception as e:
        log.error(f"Webhook Green API submit : {e}")
        if img_future is not None:
            img_future.cancel()

    return jsonify({"status": "ok"}), 200


def _greenapi_telecharger_media(url_media: str) -> bytes:
    """Télécharge un média depuis l'URL Green API et retourne le contenu en bytes.
    2 tentatives max. Timeout 15s par tentative. Détecte 403/404 = lien expiré.

    Durcissement SSRF (finding #2) :
      - whitelist sur le HOSTNAME avec frontière de label (ws.media_url_autorisee) ;
      - allow_redirects=False + rejet explicite de tout 3xx : la whitelist ne
        valide que le 1er hop ; une redirection mènerait vers 169.254.169.254,
        127.0.0.1:4040 (API ngrok locale), Postgres, etc. ;
      - streaming + plafond MEDIA_MAX_BYTES : pas de chargement mémoire illimité.
    """
    if not url_media:
        return b""
    if not ws.media_url_autorisee(url_media):
        log.warning(f"⚠️ URL média rejetée (hors whitelist/scheme) : {url_media[:80]}")
        return b""
    try:
        for attempt in range(2):
            try:
                r = requests.get(url_media, timeout=15,
                                  allow_redirects=False, stream=True)
                try:
                    if r.status_code in (301, 302, 303, 307, 308):
                        log.warning(f"⚠️ Média Green API redirige ({r.status_code}) "
                                    f"→ rejeté (anti-SSRF)")
                        return b""
                    if r.status_code == 200:
                        buf = bytearray()
                        for chunk in r.iter_content(65536):
                            if not chunk:
                                continue
                            buf.extend(chunk)
                            if len(buf) > MEDIA_MAX_BYTES:
                                log.warning("⚠️ Média Green API dépasse la limite "
                                            "de taille → rejeté")
                                return b""
                        return bytes(buf)
                    if r.status_code in (403, 404):
                        log.warning(f"⚠️ Média Green API {r.status_code} — lien expiré ou introuvable")
                        return b""
                finally:
                    r.close()
                if attempt == 0:
                    time_module.sleep(1)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 0:
                    time_module.sleep(1)
                continue
    except Exception as e:
        log.error(f"Téléchargement média Green API : {e}")
    return b""


def _traiter_message_greenapi(payload: dict, wa: str, img_future=None, group_id: str = ""):
    """Parse un événement Green API et route vers la logique métier."""
    if not group_id and not est_owner(wa):
        if not get_membre_by_wa(wa) and not est_admin(wa) and wa not in _sessions_config:
            return

    # ── Capture passive du pushname ──────────────────────────────────────────
    sender_name = (payload.get("senderData") or {}).get("senderName", "").strip()
    if _est_nom_valide(sender_name):
        m_pn = get_membre_by_wa(wa)
        if m_pn and (m_pn.get("nom_complet") or "").startswith("Membre_"):
            try:
                conn_pn = get_conn()
                try:
                    q(conn_pn,
                      "UPDATE membres SET nom_complet=%s, kyc_nom=%s "
                      "WHERE whatsapp=%s AND nom_complet LIKE 'Membre\\_%%'",
                      (sender_name, sender_name, wa))
                    conn_pn.commit()
                finally:
                    release_conn(conn_pn)
            except Exception:
                pass

    message_data = payload.get("messageData") or {}
    type_message = message_data.get("typeMessage", "")

    texte     = ""
    est_image = False
    img_bytes = b""

    if type_message == "textMessage":
        texte = (message_data.get("textMessageData") or {}).get("textMessage", "").strip()
    elif type_message in ("imageMessage", "documentMessage"):
        est_image  = True
        data_key   = "imageData" if type_message == "imageMessage" else "documentData"
        media_data = message_data.get(data_key) or {}
        url_media  = media_data.get("downloadUrl", "")
        caption    = media_data.get("caption", "")
        if img_future is not None:
            try:
                img_bytes = img_future.result(timeout=25)
            except Exception as e:
                log.error(f"img_future.result() : {e}")
                img_bytes = b""
        elif url_media:
            img_bytes = _greenapi_telecharger_media(url_media)
        if not img_bytes and caption:
            texte = caption
            est_image = False
        elif not img_bytes:
            wa_prive(wa, "❌ Impossible de télécharger le reçu. Renvoyez le screenshot.")
            return
    elif type_message == "extendedTextMessage":
        texte = (message_data.get("extendedTextMessageData") or {}).get("text", "").strip()

    log.info(f"WA ← {wa} : {texte[:60]!r}" + (" [IMG]" if est_image else ""))

    if est_image and img_bytes:
        try:
            # _traiter_screenshot_cotisation_bytes gère déjà group_id="" (DM) via
            # sa propre branche de lookup d'adhésion — la garde `if group_id:` ici
            # jetait silencieusement tout screenshot envoyé en DM, sans aucun
            # feedback au membre.
            _traiter_screenshot_cotisation_bytes(wa, img_bytes, caption, group_id or "")
        except Exception as e:
            log.error(f"Erreur image Green API : {e}")
        return

    if group_id:
        try:
            group_name = (payload.get("senderData") or {}).get("chatName", "")
            _traiter_commande_groupe(wa, texte, group_id, group_name)
        except Exception as e:
            log.error(f"Erreur commande groupe {group_id} : {e}")
        return

    # ── Config tontine via DM — intercept avant menu normal ──────────────────
    config_rep = traiter_config_tontine(wa, texte)
    if config_rep:
        wa_prive(wa, config_rep)
        return

    if est_owner(wa):
        rep = traiter_menu_owner(wa, texte)
        if rep:
            wa_prive(wa, rep)
            return

    rep_admin = traiter_menu_admin(wa, texte)
    if rep_admin:
        wa_prive(wa, rep_admin)
        return

    rep_membre = traiter_menu_membre(wa, texte, est_image)
    if rep_membre:
        wa_prive(wa, rep_membre)


def _traiter_screenshot_cotisation_bytes(wa: str, image_bytes: bytes,
                                          caption: str, group_id: str = ""):
    """
    Reçoit les bytes de l'image téléchargés depuis Green API.
    Hash SHA-256 anti-recyclage + enregistrement cotisation.
    """
    conn   = get_conn()
    try:
        membre = fetchone(conn,
            "SELECT id, nom_complet, statut_global FROM membres WHERE whatsapp=%s", (wa,))

        if not membre:
            wa_prive(wa,
                f"❓ Vous n'êtes pas encore enregistré.\n\n"
                f"Tapez *menu* pour commencer votre inscription.\n\n"
                f"_TontineBot Pro — BADF Ltd_")
            return

        if membre["statut_global"] in ("Suspendu_global", "Banni"):
            wa_prive(wa, f"🚫 Compte *{membre['statut_global']}*. Contactez votre admin.")
            return

        # Anti-recyclage SHA-256 — avant le lookup adhesion (sécurité couche 1)
        img_hash = hash_screenshot(image_bytes)
        if screenshot_deja_utilise(conn, img_hash):
            log_audit("SCREENSHOT_RECYCLE",
                      f"Membre:{membre['id']} Hash:{img_hash[:16]}", wa)
            incrementer_tentatives_fraude(membre["id"], "Screenshot recyclé")
            return

        # Couche 2 — perceptual hash (dHash), signal SOUPLE : détecte le recyclage
        # 1px/re-compression que le byte-hash laisse passer. Ne bloque jamais seul.
        dh       = si.dhash(image_bytes)
        dh_hex   = "" if dh is None else format(dh, "016x")
        near_dup = screenshot_near_duplicate(conn, membre["id"], dh)
        ref_norm = ""   # référence de transaction normalisée (renseignée par l'OCR)

        # Récupérer l'adhesion avec nombre_places
        if group_id:
            adhesion = fetchone(conn, """
                SELECT a.tontine_id, a.nombre_places, t.nom, t.montant_place
                FROM adhesions a
                JOIN tontines t ON t.id = a.tontine_id
                WHERE a.membre_id=%s AND a.statut='Actif' AND t.statut='Active'
                  AND t.whatsapp_groupe=%s
                LIMIT 1
            """, (membre["id"], group_id))
        else:
            adhesion = fetchone(conn, """
                SELECT a.tontine_id, a.nombre_places, t.nom, t.montant_place
                FROM adhesions a
                JOIN tontines t ON t.id = a.tontine_id
                WHERE a.membre_id=%s AND a.statut='Actif' AND t.statut='Active'
                LIMIT 1
            """, (membre["id"],))

        if not adhesion:
            if group_id:
                wa_prive(wa,
                    "⚠️ *Reçu reçu — tontine non configurée*\n\n"
                    "Votre groupe n'est pas encore lié à une tontine active.\n"
                    "Contactez votre admin.\n\n"
                    "_TontineBot Pro — BADF Ltd_")
            return

        # Calcul du montant attendu selon les places
        nb_places       = adhesion["nombre_places"] or 1
        montant_attendu = adhesion["montant_place"] * nb_places

        # Signal SOUPLE near-dup (dHash) → alerte admin, jamais un blocage auto
        # (deux reçus MoMo distincts partagent le même template).
        if near_dup:
            log_audit("SCREENSHOT_NEAR_DUP",
                      f"Membre:{membre['id']} dHash proche d'un screenshot antérieur", wa)
            admin_nd = fetchone(conn,
                "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                (adhesion["tontine_id"],))
            if admin_nd:
                wa_prive(admin_nd["whatsapp"],
                    f"⚠️ *SCREENSHOT QUASI-IDENTIQUE — {adhesion['nom']}*\n\n"
                    f"Membre : *{membre['nom_complet']}*\n"
                    f"L'image ressemble fortement à un reçu déjà soumis "
                    f"(possible re-compression d'un ancien). Vérifiez la référence "
                    f"de transaction avant de confirmer.\n\n"
                    f"_TontineBot Pro — BADF Ltd_")

        # ── Lecture automatique du screenshot (OCR local Tesseract) ───────────
        lecture = lire_screenshot_mobile_money(image_bytes)
        fraude_visuelle = False

        if lecture.get("ok"):
            montant_lu  = lecture.get("montant")
            confiance   = lecture.get("confiance", "faible")
            operateur   = lecture.get("operateur", "Inconnu")
            ref_lu      = lecture.get("reference", "")
            ref_norm    = si.normaliser_reference(ref_lu)

            # Couche 3 (FORTE) — la référence de transaction est unique par paiement
            # réel : insensible au 1px/re-encodage qui défait le byte-hash. Blocage dur.
            if ref_norm and reference_deja_utilisee(conn, adhesion["tontine_id"], ref_norm):
                log_audit("SCREENSHOT_RECYCLE",
                          f"Membre:{membre['id']} Ref:{ref_norm} — transaction déjà utilisée", wa)
                incrementer_tentatives_fraude(membre["id"],
                                              f"Référence transaction recyclée: {ref_norm}")
                admin_r = fetchone(conn,
                    "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                    (adhesion["tontine_id"],))
                if admin_r:
                    wa_prive(admin_r["whatsapp"],
                        f"🚨 *RECYCLAGE BLOQUÉ — {adhesion['nom']}*\n\n"
                        f"Membre : *{membre['nom_complet']}*\n"
                        f"Référence de transaction *{ref_norm}* déjà utilisée — "
                        f"reçu recyclé rejeté automatiquement.\n\n"
                        f"_TontineBot Pro — BADF Ltd_")
                return

            # Vérification montant : comparaison avec montant attendu
            if montant_lu and montant_attendu:
                ecart = abs(montant_lu - montant_attendu)
                ecart_pct = ecart / montant_attendu * 100

                if ecart_pct > 10:
                    # Montant ne correspond pas — suspect
                    fraude_visuelle = True
                    incrementer_tentatives_fraude(
                        membre["id"],
                        f"Montant lu {montant_lu:,} FCFA ≠ attendu {montant_attendu:,} FCFA"
                    )
                    admin_alerte = fetchone(conn,
                        "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                        (adhesion["tontine_id"],))
                    if admin_alerte:
                        wa_prive(admin_alerte["whatsapp"],
                            f"🚨 *ALERTE MONTANT SUSPECT — {adhesion['nom']}*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"Membre    : *{membre['nom_complet']}*\n"
                            f"Lu sur image : *{montant_lu:,} FCFA* ({operateur})\n"
                            f"Attendu      : *{montant_attendu:,} FCFA*\n"
                            f"Écart        : *{ecart_pct:.0f}%*\n\n"
                            f"⚠️ Le montant du screenshot ne correspond pas.\n"
                            f"Vérifiez avant de confirmer — possible screenshot modifié.\n\n"
                            f"_TontineBot Pro — BADF Ltd_"
                        )
                    log_audit("MONTANT_SUSPECT",
                              f"Membre:{membre['id']} Lu:{montant_lu} Attendu:{montant_attendu}",
                              wa)

            # Confiance faible → alerte admin
            if confiance == "faible" and not fraude_visuelle:
                admin_alerte = fetchone(conn,
                    "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                    (adhesion["tontine_id"],))
                if admin_alerte:
                    wa_prive(admin_alerte["whatsapp"],
                        f"⚠️ *SCREENSHOT DOUTEUX — {adhesion['nom']}*\n\n"
                        f"Membre : *{membre['nom_complet']}*\n"
                        f"L'image soumise est *floue ou tronquée*.\n"
                        f"Vérifiez attentivement avant de confirmer.\n\n"
                        f"_TontineBot Pro — BADF Ltd_"
                    )
                log_audit("SCREENSHOT_DOUTEUX",
                          f"Membre:{membre['id']} Confiance:{confiance}", wa)
        else:
            # OCR a échoué entièrement → on demande à l'admin de confirmer le montant
            operateur = "Inconnu"
            ref_lu    = ""
            # Alerter l'admin pour confirmation manuelle du montant
            admin_alerte = fetchone(conn,
                "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                (adhesion["tontine_id"],))
            if admin_alerte:
                wa_prive(admin_alerte["whatsapp"],
                    f"📸 *SCREENSHOT REÇU — confirmation manuelle*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Tontine    : *{adhesion['nom']}*\n"
                    f"Membre     : *{membre['nom_complet']}*\n"
                    f"Montant attendu : *{montant_attendu:,} FCFA*\n\n"
                    f"⚠️ Le bot n'a pas pu lire automatiquement ce reçu.\n"
                    f"Vérifiez sur votre téléphone que vous avez bien reçu le paiement, "
                    f"puis traitez via le menu admin :\n\n"
                    f"  1. Tapez *admin {adhesion['nom']}*\n"
                    f"  2. Tapez *15* (Cotisations en attente)\n"
                    f"  3. Répondez *OUI* si reçu, *NON [raison]* sinon\n\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )
            log_audit("OCR_ECHEC_MANUAL",
                      f"Membre:{membre['id']} Tontine:{adhesion['tontine_id']}", wa)

        # ── Limite : 1 screenshot par membre par période ─────────────────────
        # Détecte les soumissions multiples le même jour (Photoshop suspect)
        nb_aujourd_hui = fetchone(conn, """
            SELECT COUNT(*) n FROM cotisations_manuelles
            WHERE membre_id=%s AND tontine_id=%s
              AND date_soumission::date = CURRENT_DATE
        """, (membre["id"], adhesion["tontine_id"]))["n"]

        if nb_aujourd_hui >= 1:
            # Déjà soumis aujourd'hui — suspect
            incrementer_tentatives_fraude(
                membre["id"],
                f"Soumission multiple screenshot — {nb_aujourd_hui+1}ème tentative aujourd'hui"
            )
            # Alerte immédiate à l'admin
            admin_alerte = fetchone(conn,
                "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
                (adhesion["tontine_id"],))
            if admin_alerte:
                wa_prive(admin_alerte["whatsapp"],
                    f"⚠️ *ALERTE FRAUDE POSSIBLE — {adhesion['nom']}*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"*{membre['nom_complet']}* vient de soumettre un *{nb_aujourd_hui+1}ème screenshot "
                    f"aujourd'hui*.\n\n"
                    f"Ses soumissions du jour :\n"
                    f"  Déjà soumis  : *{nb_aujourd_hui}* screenshot(s)\n"
                    f"  Nouveau      : *1* (en attente)\n\n"
                    f"⚠️ Vérifiez attentivement avant de confirmer.\n"
                    f"Un screenshot modifié (Photoshop) peut ressembler à un vrai.\n\n"
                    f"_TontineBot Pro — BADF Ltd_"
                )
            log_audit("SCREENSHOT_MULTIPLE",
                      f"Membre:{membre['id']} | {nb_aujourd_hui+1}ème soumission aujourd'hui", wa)

            # Ne pas bloquer — l'admin décide — mais informer le membre
            wa_prive(wa,
                f"⚠️ *Attention — {adhesion['nom']}*\n\n"
                f"Vous avez déjà soumis un screenshot aujourd'hui.\n"
                f"Votre nouveau reçu a été transmis à l'admin pour vérification.\n\n"
                f"_TontineBot Pro — BADF Ltd_"
            )

        # Récupérer l'admin
        admin = fetchone(conn,
            "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s LIMIT 1",
            (adhesion["tontine_id"],))
        admin_wa = admin["whatsapp"] if admin else OWNER_WA

        # Enregistrer la cotisation avec le bon montant
        try:
            cotis_id = enregistrer_cotisation_manuelle(
                conn,
                membre_id       = membre["id"],
                tontine_id      = adhesion["tontine_id"],
                montant         = montant_attendu,
                screenshot_hash = img_hash,
                admin_wa        = admin_wa,
                dhash_hex       = dh_hex,
                ref_norm        = ref_norm,
            )
        except Exception as e:
            log.error(f"❌ enregistrer_cotisation_manuelle échec pour {wa}: {e}")
            wa_prive(wa,
                f"⚠️ *Erreur technique — {adhesion['nom']}*\n\n"
                f"Votre reçu a été reçu mais n'a pas pu être enregistré.\n"
                f"Contactez votre admin.\n\n"
                f"_TontineBot Pro — BADF Ltd_")
            return


        # Message places multiples si applicable
        places_info = ""
        if nb_places > 1:
            places_info = (
                f"\n📋 *{nb_places} places* × {adhesion['montant_place']:,} FCFA "
                f"= *{montant_attendu:,} FCFA*"
            )

        # Accusé au membre
        wa_prive(wa,
            f"📸 *Reçu enregistré — {adhesion['nom']}*\n\n"
            f"Bonjour *{membre['nom_complet']}*,{places_info}\n\n"
            f"Votre reçu de *{montant_attendu:,} FCFA* a été transmis "
            f"à votre admin.\n\nVous serez notifié dès validation.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )

        # Notifier l'admin
        places_txt  = f" ({nb_places} places)" if nb_places > 1 else ""
        vision_txt  = ""
        if lecture.get("ok") and lecture.get("montant"):
            vision_txt = (
                f"📱 Lu sur image : *{lecture['montant']:,} FCFA* "
                f"({lecture.get('operateur','?')}) "
                f"— confiance *{lecture.get('confiance','?')}*\n"
            )
        alerte_txt = "🚨 *MONTANT SUSPECT — vérifiez avant de confirmer*\n\n" if fraude_visuelle else ""

        wa_prive(admin_wa,
            f"🔔 *COTISATION — {adhesion['nom']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{alerte_txt}"
            f"Membre  : *{membre['nom_complet']}*{places_txt}\n"
            f"Attendu : *{montant_attendu:,} FCFA*\n"
            f"{vision_txt}"
            f"Réf     : *#{cotis_id}*\n\n"
            f"Menu admin → option *15* pour confirmer.\n\n"
            f"_TontineBot Pro — BADF Ltd_"
        )
        log_audit("SCREENSHOT_RECU",
                  f"Membre:{membre['id']} Places:{nb_places} "
                  f"Montant:{montant_attendu} Cotis#{cotis_id}", wa)
    finally:
        release_conn(conn)


def register_webhook(app) -> None:
    """Enregistre les deux routes /webhook/whatsapp sur l'app Flask du bot.

    Mêmes règles et mêmes noms d'endpoint que les anciens @app.route du bot ;
    appelé une fois, à l'endroit où ces décorateurs s'exécutaient.
    """
    app.add_url_rule("/webhook/whatsapp", "webhook_whatsapp_verify",
                     webhook_whatsapp_verify, methods=["GET"])
    app.add_url_rule("/webhook/whatsapp", "webhook_whatsapp_greenapi",
                     webhook_whatsapp_greenapi, methods=["POST"])
