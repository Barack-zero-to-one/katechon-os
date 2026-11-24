"""
Envoi WhatsApp par Green API : outbox persistante, requête Green API,
expéditeur unique (wa_dispatch), diffusion de groupe et fonctions wa_*.

Extrait de barack_corp_v9_18.py. Ce module est le seul lecteur de
GREENAPI_INSTANCE_ID, GREENAPI_TOKEN, GREENAPI_BASE et OWNER_WA ; le bot les
réexporte. Aucune dépendance au bot : la base passe par db.py.
"""
import json
import logging
import os
import threading
import time as time_module
from pathlib import Path

import requests

import wa_dispatch
from db import get_conn, release_conn, fetchone, fetchall

__all__ = [
    "GREENAPI_INSTANCE_ID", "GREENAPI_TOKEN", "GREENAPI_BASE", "OWNER_WA",
    "_outbox_lock", "_outbox_enqueue", "_outbox_drain", "_wa_request", "_chat_id",
    "_wa_send_direct", "WA_ECART_PAR_CHAT", "WA_DEBIT_GLOBAL", "_expediteur",
    "_wa_send_groupe_direct", "_wa_send", "_wa_send_groupe", "_wa_send_group_chatid",
    "wa_kick_membre", "wa_quitter_groupe", "wa_prive", "wa_owner", "wa_admins_tontine",
    "wa_groupe", "wa_admin",
]

log = logging.getLogger("BarackCorp")   # même logger que le bot : sortie inchangée

# ── WhatsApp — Green API (compte perso existant, scan QR) ─────────────────
GREENAPI_INSTANCE_ID    = os.getenv("GREENAPI_INSTANCE_ID",    "")  # Ex: 1234567890
GREENAPI_TOKEN          = os.getenv("GREENAPI_TOKEN",          "")  # API token Green API
GREENAPI_BASE           = os.getenv("GREENAPI_BASE", "https://api.green-api.com")

# ── Propriétaire BADF (destinataire de wa_owner / wa_admin) ────────────────
OWNER_WA     = os.getenv("OWNER_WA", "+237693969773")


# ── Outbox persistante — file de messages en cas de panne Meta ───────────
_outbox_lock = threading.Lock()
_outbox_path = Path("logs/wa_outbox.jsonl")
_outbox_path.parent.mkdir(exist_ok=True)


def _outbox_enqueue(kind: str, payload: dict):
    """Sauvegarde un message non envoyé pour retransmission ultérieure."""
    try:
        with _outbox_lock:
            entry = {"ts": time_module.time(), "kind": kind, "payload": payload}
            with open(_outbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"Outbox enqueue erreur : {e}")


def _outbox_drain():
    """Tente de vider la file d'attente. Appelé périodiquement."""
    if not _outbox_path.exists():
        return
    try:
        # Lire sous lock — rapide
        with _outbox_lock:
            with open(_outbox_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return
            # Vider le fichier immédiatement — les nouveaux messages passeront par append
            with open(_outbox_path, "w", encoding="utf-8") as f:
                f.write("")

        # Traiter les messages HORS lock — les appels réseau Meta peuvent prendre des secondes
        restants = []
        envoyes  = 0
        for line in lines:
            try:
                entry = json.loads(line)
                if time_module.time() - entry["ts"] > 86400:
                    continue
                if entry["kind"] == "send":
                    ok = _wa_send(**entry["payload"])
                elif entry["kind"] == "send_group":
                    ok = _wa_send_groupe_direct(**entry["payload"])
                else:
                    ok = False
                if ok:
                    envoyes += 1
                else:
                    restants.append(line)
            except Exception:
                restants.append(line)

        # Réécrire les restants sous lock
        if restants:
            with _outbox_lock:
                with open(_outbox_path, "a", encoding="utf-8") as f:
                    f.writelines(restants)

        if envoyes > 0:
            log.info(f"📤 Outbox drain : {envoyes} message(s) renvoyés, {len(restants)} en attente")
    except Exception as e:
        log.error(f"Outbox drain erreur : {e}")


def _wa_request(method: str, payload: dict, max_retries: int = 3) -> bool:
    """
    Wrapper résilient Green API avec retry exponentiel.
    method : 'sendMessage', 'sendFileByUrl', etc.
    Retourne True si envoyé, False sinon (la queue rattrape).
    """
    if not GREENAPI_INSTANCE_ID or not GREENAPI_TOKEN:
        log.warning("⚠️ Green API non configurée (GREENAPI_INSTANCE_ID / GREENAPI_TOKEN manquants)")
        return False

    url = f"{GREENAPI_BASE}/waInstance{GREENAPI_INSTANCE_ID}/{method}/{GREENAPI_TOKEN}"
    headers = {"Content-Type": "application/json"}
    delays = [2, 5, 10]
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if 200 <= r.status_code < 300:
                return True
            if r.status_code in (401, 403):
                log.error(f"🔴 Green API token invalide ({r.status_code}). Vérifier GREENAPI_TOKEN.")
                return False
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                log.warning(f"⚠️ Green API rate limit. Attente {wait}s")
                time_module.sleep(min(wait, 60))
                continue
            if r.status_code >= 500:
                if attempt < max_retries - 1:
                    time_module.sleep(delays[attempt])
                    continue
            log.warning(f"⚠️ Green API {method} → {r.status_code} : {r.text[:120]}")
            return False
        except (requests.ConnectionError, requests.Timeout):
            if attempt < max_retries - 1:
                time_module.sleep(delays[attempt])
                continue
            return False
        except Exception as e:
            log.error(f"❌ Green API {method} : {e}")
            return False
    return False


def _chat_id(to: str) -> str:
    """+237XXXXXXXXX → JID @c.us ; un JID @c.us ou @g.us est rendu tel quel."""
    if "@g.us" in to or "@c.us" in to:
        return to
    return f"{to.lstrip('+').replace(' ', '')}@c.us"


def _wa_send_direct(to: str, body: str) -> bool:
    """Un envoi Green API immédiat, sans file ni espacement. Seul le thread
    expéditeur (_expediteur) l'appelle : il porte le rythme d'envoi."""
    return _wa_request("sendMessage", {"chatId": _chat_id(to), "message": body[:4096]})


# ── Expéditeur WhatsApp unique ─────────────────────────────────────────────
# Remplace l'ancien écart global de 3 s par message (qui faisait attendre
# chaque appelant) : les appelants soumettent et rendent la main, un seul
# thread envoie, 3 s entre deux messages au MÊME destinataire, débit global
# borné. Un envoi en échec part dans l'outbox (wa_outbox.jsonl).
WA_ECART_PAR_CHAT = float(os.getenv("WA_ECART_PAR_CHAT", "3"))
WA_DEBIT_GLOBAL   = float(os.getenv("WA_DEBIT_GLOBAL",   "1"))   # msg/s, à ajuster aux limites Green API
_expediteur = wa_dispatch.Expediteur(
    envoyer        = lambda chat, texte: _wa_send_direct(chat, texte),
    ecart_par_chat = WA_ECART_PAR_CHAT,
    debit_global   = WA_DEBIT_GLOBAL,
    en_echec       = lambda chat, texte: _outbox_enqueue("send", {"to": chat, "body": texte}),
)


def _wa_send_groupe_direct(group_id: str, body: str) -> bool:
    """
    Broadcast : envoie un DM individuel à chaque membre actif de la tontine.
    `group_id` est l'identifiant interne de la tontine (colonne whatsapp_groupe).
    """
    try:
        conn    = get_conn()
        # group_id peut être l'identifiant interne de la tontine ou nom de groupe
        tontine = fetchone(conn,
            "SELECT id, nom FROM tontines WHERE whatsapp_groupe=%s OR nom=%s LIMIT 1",
            (group_id, group_id))
        if not tontine:
            release_conn(conn)
            log.warning(f"⚠️ Tontine introuvable pour group_id={group_id}")
            return False
        membres = fetchall(conn, """
            SELECT m.whatsapp FROM membres m
            JOIN adhesions a ON a.membre_id = m.id
            WHERE a.tontine_id=%s AND a.statut='Actif'
              AND m.statut_global='Actif'
        """, (tontine["id"],))
        release_conn(conn)
        if not membres:
            return False
        for m in membres:
            _wa_send(m["whatsapp"], body, wa_dispatch.PRIORITE_DIFFUSION)
        return True
    except Exception as e:
        log.error(f"❌ Broadcast groupe {group_id} : {e}")
        return False


def _wa_send(to: str, body: str, priorite: int = wa_dispatch.PRIORITE_REPONSE) -> bool:
    """Soumet un message à l'expéditeur et rend la main aussitôt ; un échec
    d'envoi ultérieur part dans l'outbox. Retourne True (message accepté)."""
    _expediteur.demarrer()
    _expediteur.soumettre(_chat_id(to), body, priorite)
    return True


def _wa_send_groupe(group_id: str, body: str) -> bool:
    """Envoie à tous les membres d'une tontine, fallback outbox si Green API down."""
    if _wa_send_groupe_direct(group_id, body):
        return True
    _outbox_enqueue("send_group", {"group_id": group_id, "body": body})
    return False

def _wa_send_group_chatid(group_chatid: str, body: str) -> bool:
    """Poste dans le canal WhatsApp groupe via JID @g.us.
    Distinct de _wa_send_groupe (broadcast DM individuel)."""
    return _wa_send(group_chatid, body)


def wa_kick_membre(group_id: str, wa_membre: str) -> bool:
    """
    Bannissement signalé — l'admin humain doit retirer manuellement le membre du groupe WhatsApp.
    """
    log.warning(f"⚠️ KICK manuel requis : {wa_membre} doit être retiré de {group_id} par l'admin")
    return False


def wa_quitter_groupe(group_id: str) -> bool:
    """Envoie un message d'au revoir à tous les membres et marque la tontine clôturée."""
    _wa_send_groupe_direct(group_id,
        "🏁 *Cycle terminé.*\nLe service TontineBot Pro se retire de cette tontine. "
        "Merci pour votre confiance.\n\n_BADF Ltd_")
    return True


def wa_prive(numero: str, message: str, priorite: int = wa_dispatch.PRIORITE_REPONSE):
    """Envoie un DM à un membre (sans attendre : l'expéditeur rythme l'envoi)."""
    _wa_send(numero, message, priorite)


def wa_owner(message: str):
    """Envoie un DM au owner BADF."""
    wa_prive(OWNER_WA, message)


def wa_admins_tontine(tontine_id: int, message: str):
    """Envoie un DM à tous les admins d'une tontine."""
    conn   = get_conn()
    admins = fetchall(conn,
        "SELECT whatsapp FROM admins_groupe WHERE tontine_id=%s", (tontine_id,))
    release_conn(conn)
    for a in admins:
        wa_prive(a["whatsapp"], message, wa_dispatch.PRIORITE_DIFFUSION)


def wa_groupe(group_id: str, message: str):
    """
    Envoie un message dans le canal WhatsApp du groupe de la tontine.
    group_id = JID @g.us du groupe (ex: 120363XXXXXXXX@g.us)
    ou nom de la tontine si on a le whatsapp_groupe en base.
    Fallback broadcast DM aux membres actifs si aucun JID @g.us disponible.
    """
    if group_id and "@g.us" in group_id:
        _wa_send_group_chatid(group_id, message)
    elif group_id:
        conn = get_conn()
        tontine = fetchone(conn,
            "SELECT whatsapp_groupe FROM tontines WHERE nom=%s OR whatsapp_groupe=%s",
            (group_id, group_id))
        release_conn(conn)
        if tontine and tontine.get("whatsapp_groupe"):
            jid = tontine["whatsapp_groupe"]
            if "@g.us" in jid:
                _wa_send_group_chatid(jid, message)
            else:
                _wa_send_groupe(jid, message)


def wa_admin(message: str):
    """Alerte ops (auto-ban, fraude, cas critiques) → DM direct à OWNER_WA.

    NE PAS router via wa_groupe(GROUPE_ADMIN, ...) : GROUPE_ADMIN="Admin Barack
    Corp" n'est pas un JID @g.us, et aucune tontine n'est jamais seedée sous ce
    nom — wa_groupe() ne trouve alors rien et retourne silencieusement, sans
    log ni erreur. Le message n'est simplement jamais livré. wa_prive() sur un
    numéro réel est le seul canal qui fonctionne réellement ici.
    """
    wa_prive(OWNER_WA, message)
