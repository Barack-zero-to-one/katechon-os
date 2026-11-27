"""
Webhook Telegram : POST /webhook/telegram.

Même chaîne de défense que le webhook WhatsApp : drapeau (404 sans
KATECHON_TELEGRAM=1), secret partagé dans l'en-tête
X-Telegram-Bot-Api-Secret-Token comparé en temps constant (503 sans secret
configuré, 403 s'il est faux), anti-rejeu sur update_id, rate limit par
identifiant. Chaque message devient un MessageEntrant routé par
katechon.handlers.webhook.traiter_message, comme un message WhatsApp.

Deux parcours sont propres à Telegram :
  - /start <code> : inscription par lien d'invitation. Le membre est créé,
    mais n'entre dans le calendrier de bouffage qu'après avoir donné un numéro
    Mobile Money valide avec son opérateur (règle : aucun bouffage ne doit
    bloquer faute de numéro de retrait) ;
  - my_chat_member : le bot ajouté à un groupe relie ce groupe à la tontine
    du même nom.
"""
import logging
import os
import re
import time as time_module

from flask import jsonify, request

from katechon.core.audit import log_audit
from katechon.core.db import fetchone, get_conn, q, release_conn
from katechon.core.etat import _msg_executor, rate_limit_ok
from katechon.core.socle import (_replay_guard, est_telegram, inscrire_dans_tontine,
                                 normaliser_numero, valider_numero_cameroun)
from katechon.handlers.groupe import _est_nom_valide
from katechon.handlers.webhook import traiter_message
from katechon.messaging.canal import MessageEntrant
from katechon.messaging.telegram import lire_code, telecharger
from katechon.messaging.whatsapp import wa_prive
from katechon.security import webhook_security as ws

__all__ = ["webhook_telegram", "register_telegram"]

log = logging.getLogger("BarackCorp")

DELAI_NUMERO = 3600          # s : au-delà, le lien doit être recliqué
_attente_momo = {}           # tg:<id> → {"tontine_id": int, "ts": float}

MSG_NUMERO = (
    "📱 *Dernière étape : votre numéro Mobile Money*\n\n"
    "C'est le numéro qui recevra votre bouffage. Sans lui, vous ne pouvez pas "
    "entrer dans le calendrier.\n\n"
    "Envoyez l'opérateur et le numéro, par exemple :\n"
    "• *MTN +237690123456*\n• *ORANGE +237699123456*"
)


def _demarrer(wa: str, code: str, prenom: str):
    tid = lire_code(code)
    conn = get_conn()
    try:
        tontine = fetchone(conn, "SELECT id, nom, statut FROM tontines WHERE id=%s", (tid,)) if tid else None
        if not tontine or tontine.get("statut") not in (None, "Active"):
            wa_prive(wa, "❌ Lien d'invitation invalide ou expiré. Demandez un nouveau lien à votre admin.")
            return
        nom = prenom.strip() if _est_nom_valide(prenom or "") else f"Membre_{wa[-4:]}"
        q(conn, """INSERT INTO membres (nom_complet, kyc_hash, whatsapp, adhesion_payee, statut_global, kyc_etape)
                   VALUES (%s, md5(%s), %s, 1, 'Actif', 0) ON CONFLICT (whatsapp) DO NOTHING""",
          (nom, f"TG{wa}", wa))
        conn.commit()
        membre = fetchone(conn, "SELECT id, blackliste, numero_momo FROM membres WHERE whatsapp=%s", (wa,))
    finally:
        release_conn(conn)
    if not membre or membre.get("blackliste"):
        wa_prive(wa, "❌ Inscription impossible. Contactez votre admin.")
        return
    log_audit("TELEGRAM_INVITATION", f"Membre#{membre['id']} → Tontine#{tid}", wa)
    if membre.get("numero_momo"):
        _inscrire(wa, membre["id"], tontine)
        return
    _attente_momo[wa] = {"tontine_id": tid, "ts": time_module.time()}
    wa_prive(wa, f"👋 Bienvenue dans *{tontine['nom']}* !\n\n" + MSG_NUMERO)


def _lire_numero(texte: str):
    m = re.search(r"(\+?237[26789]\d{8}|\b[26789]\d{8}\b)", texte or "")
    tu = (texte or "").upper()
    operateur = "cm.mtn" if "MTN" in tu else "cm.orange" if "ORANGE" in tu else None
    if not m or not operateur:
        return None
    n = m.group(1)
    numero = normaliser_numero(n if "237" in n else "237" + n)
    return (numero, operateur) if valider_numero_cameroun(numero) else None


def _recevoir_numero(wa: str, texte: str):
    attente = _attente_momo.get(wa)
    if not attente or time_module.time() - attente["ts"] > DELAI_NUMERO:
        _attente_momo.pop(wa, None)
        wa_prive(wa, "⏱ Délai dépassé. Recliquez le lien d'invitation de votre tontine.")
        return
    lu = _lire_numero(texte)
    if not lu:
        wa_prive(wa, "❌ Numéro ou opérateur non reconnu.\n\n" + MSG_NUMERO)
        return
    numero, operateur = lu
    conn = get_conn()
    try:
        q(conn, "UPDATE membres SET numero_momo=%s, operateur_momo=%s WHERE whatsapp=%s",
          (numero, operateur, wa))
        conn.commit()
        membre  = fetchone(conn, "SELECT id FROM membres WHERE whatsapp=%s", (wa,))
        tontine = fetchone(conn, "SELECT id, nom FROM tontines WHERE id=%s", (attente["tontine_id"],))
    finally:
        release_conn(conn)
    _attente_momo.pop(wa, None)
    log_audit("TELEGRAM_NUMERO_MOMO", f"Membre#{membre['id']} | {numero} ({operateur})", wa)
    _inscrire(wa, membre["id"], tontine)


def _inscrire(wa: str, membre_id: int, tontine: dict):
    try:
        inscrire_dans_tontine(membre_id, tontine["id"], 1)
    except ValueError as e:
        wa_prive(wa, f"❌ {e}")
        return
    wa_prive(wa, f"✅ Vous êtes inscrit dans *{tontine['nom']}*.\n_Tapez *menu*_")


def _bot_ajoute_groupe(evenement: dict):
    chat = evenement.get("chat") or {}
    nouveau = evenement.get("new_chat_member") or {}
    if chat.get("type") not in ("group", "supergroup") or nouveau.get("status") not in ("member", "administrator"):
        return
    group_id = normaliser_numero(f"tg:{chat.get('id')}")
    titre = (chat.get("title") or "").strip()
    if not est_telegram(group_id) or not titre:
        return
    conn = get_conn()
    try:
        cur = q(conn, """UPDATE tontines SET whatsapp_groupe=%s
                         WHERE nom=%s AND (whatsapp_groupe IS NULL OR whatsapp_groupe='')""", (group_id, titre))
        conn.commit()
        relie = cur.rowcount
    finally:
        release_conn(conn)
    if relie:
        log_audit("TELEGRAM_GROUPE_RELIE", f"{titre} → {group_id}")


def _plus_grande_photo(photos):
    photos = [p for p in (photos or []) if p.get("file_id")]
    if not photos:
        return ""
    return max(photos, key=lambda p: (p.get("file_size") or 0, (p.get("width") or 0) * (p.get("height") or 0),
                                      p.get("width") or 0))["file_id"]


def webhook_telegram():
    if os.getenv("KATECHON_TELEGRAM") != "1":
        return jsonify({"status": "off"}), 404
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET") or ""
    if not secret:
        log.error("❌ TELEGRAM_WEBHOOK_SECRET manquant — webhook Telegram refusé")
        return jsonify({"error": "Webhook non configuré"}), 503
    if not ws.constant_time_equal(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), secret):
        log_audit("TELEGRAM_WEBHOOK_REFUSE", "secret invalide", request.remote_addr)
        return jsonify({"error": "Non autorisé"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON invalide"}), 400
    if _replay_guard.est_rejeu(f"tg-{payload.get('update_id', '')}"):
        return jsonify({"status": "ok"}), 200

    if "my_chat_member" in payload:
        _msg_executor.submit(_bot_ajoute_groupe, payload["my_chat_member"])
        return jsonify({"status": "ok"}), 200

    msg = payload.get("message")
    if not isinstance(msg, dict):
        return jsonify({"status": "ignored"}), 200
    expediteur = msg.get("from") or {}
    if expediteur.get("is_bot"):
        return jsonify({"status": "ignored"}), 200
    wa = normaliser_numero(f"tg:{expediteur.get('id', '')}")
    if not est_telegram(wa) or not rate_limit_ok(wa):
        return jsonify({"status": "ok"}), 200

    chat = msg.get("chat") or {}
    est_groupe = chat.get("type") in ("group", "supergroup")
    group_id = normaliser_numero(f"tg:{chat.get('id', '')}") if est_groupe else ""
    texte = (msg.get("text") or "").strip()

    if not est_groupe:
        if texte.split(" ", 1)[0] == "/start":
            code = texte[len("/start"):].strip()
            if code:
                _msg_executor.submit(_demarrer, wa, code, expediteur.get("first_name", ""))
                return jsonify({"status": "ok"}), 200
            texte = "start"
        elif wa in _attente_momo:
            _msg_executor.submit(_recevoir_numero, wa, texte)
            return jsonify({"status": "ok"}), 200

    type_media, media_ref = "", ""
    if msg.get("photo"):
        type_media, media_ref = "image", _plus_grande_photo(msg["photo"])
    elif (msg.get("document") or {}).get("file_id"):
        type_media, media_ref = "document", msg["document"]["file_id"]

    m = MessageEntrant(
        canal="telegram", expediteur=wa, group_id=group_id, texte=texte,
        type_media=type_media, media_url=media_ref, media_future=None,
        caption=(msg.get("caption") or "").strip(), nom_affiche=(expediteur.get("first_name") or "").strip(),
        nom_groupe=(chat.get("title") or "") if est_groupe else "",
        id_message=f"tg-{payload.get('update_id', '')}", telecharger=telecharger,
    )
    _msg_executor.submit(traiter_message, m)
    return jsonify({"status": "ok"}), 200


def register_telegram(app):
    app.add_url_rule("/webhook/telegram", "webhook_telegram", webhook_telegram, methods=["POST"])
