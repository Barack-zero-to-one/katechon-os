"""
Canal Telegram (Bot API), d'abord pour l'instance de staging.

Possède tous les identifiants « tg: » : un membre est « tg:<user_id> », un
groupe « tg:<chat_id> » (négatif). Il est enregistré dès l'import, même
désactivé, pour qu'un identifiant Telegram ne parte jamais vers Green API ;
désactivé, il journalise et n'envoie rien.

Envoi : sendMessage en HTML (le *gras* et l'_italique_ de WhatsApp sont
convertis, le reste échappé), découpé sous 4096 caractères. Un 400/403
(membre qui n'a jamais ouvert le bot, chat introuvable) est définitif : il est
journalisé et abandonné, jamais remis dans l'outbox, qui le rejouerait sans
fin. Une erreur réseau ou 5xx rend False : l'outbox réessaie.

Média : getFile puis téléchargement depuis le même hôte que l'API, sans
redirection, plafonné à MEDIA_MAX_BYTES.

Invitations : un lien t.me/<bot>?start=<code> où code = <tontine_id>_<hmac>,
signé avec TELEGRAM_WEBHOOK_SECRET : impossible de deviner le code d'une
autre tontine.

Variables (lues à chaque appel) : KATECHON_TELEGRAM=1, TELEGRAM_BOT_TOKEN,
TELEGRAM_WEBHOOK_SECRET, TELEGRAM_API_BASE (défaut https://api.telegram.org).
"""
import hashlib
import hmac
import html
import logging
import os
import re
from urllib.parse import urlsplit

import requests

from katechon.messaging import canal

__all__ = ["TelegramCanal", "actif", "telecharger", "code_invitation", "lire_code"]

log = logging.getLogger("BarackCorp")

LIMITE_TEXTE = 4000           # marge sous les 4096 de Telegram après conversion HTML
MEDIA_MAX_BYTES = int(os.getenv("MEDIA_MAX_BYTES", str(10 * 1024 * 1024)))


def actif() -> bool:
    return os.getenv("KATECHON_TELEGRAM") == "1" and bool(os.getenv("TELEGRAM_BOT_TOKEN"))


def _base():
    return (os.getenv("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")


def _appel(methode, corps, delai=15):
    url = f"{_base()}/bot{os.getenv('TELEGRAM_BOT_TOKEN', '')}/{methode}"
    return requests.post(url, json=corps, timeout=delai)


def _vers_html(texte: str) -> str:
    t = html.escape(texte, quote=False)
    t = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<i>\1</i>", t)
    return t


def _decouper(texte: str):
    """Coupe aux fins de ligne sous LIMITE_TEXTE (une ligne trop longue est coupée net)."""
    morceaux, courant = [], ""
    for ligne in texte.split("\n"):
        while len(ligne) > LIMITE_TEXTE:
            if courant:
                morceaux.append(courant); courant = ""
            morceaux.append(ligne[:LIMITE_TEXTE]); ligne = ligne[LIMITE_TEXTE:]
        candidat = f"{courant}\n{ligne}" if courant else ligne
        if len(candidat) > LIMITE_TEXTE:
            morceaux.append(courant); courant = ligne
        else:
            courant = candidat
    if courant or not morceaux:
        morceaux.append(courant)
    return morceaux


class TelegramCanal(canal.Canal):
    nom = "telegram"

    def possede(self, identifiant: str) -> bool:
        return str(identifiant).lower().startswith("tg:")

    def adresse(self, identifiant: str) -> str:
        return "tg:" + str(identifiant)[3:].strip()

    def est_groupe(self, adresse: str) -> bool:
        return str(adresse).startswith("tg:-")

    def envoyer(self, adresse: str, texte: str) -> bool:
        if not actif():
            log.warning(f"⚠️ Telegram désactivé : message vers {adresse} abandonné")
            return True
        try:
            chat_id = int(adresse[3:])
        except ValueError:
            log.warning(f"⚠️ Telegram : adresse invalide {adresse!r}, message abandonné")
            return True
        morceaux = _decouper(texte)
        for i, morceau in enumerate(morceaux):
            ok = self._envoyer_morceau(adresse, chat_id, morceau, "\n".join(morceaux[i:]))
            if ok is not None:
                if ok is False and i > 0:
                    # Les morceaux déjà partis ne sont jamais renvoyés : seul le
                    # reste attend dans l'outbox.
                    _vers_outbox(adresse, "\n".join(morceaux[i:]))
                    return True
                return ok
        return True

    def _envoyer_morceau(self, adresse, chat_id, morceau, reste):
        """None si le morceau est parti ; sinon le résultat final de envoyer()."""
        try:
            r = _appel("sendMessage", {"chat_id": chat_id, "text": _vers_html(morceau),
                                       "parse_mode": "HTML", "disable_web_page_preview": True})
            if r.status_code == 400 and "parse entities" in _description(r):
                # HTML refusé (balises mal imbriquées) : le texte brut passe toujours.
                r = _appel("sendMessage", {"chat_id": chat_id, "text": morceau,
                                           "disable_web_page_preview": True})
        except (requests.ConnectionError, requests.Timeout):
            return False
        parametres = _parametres(r)
        nouveau = parametres.get("migrate_to_chat_id")
        if r.status_code == 400 and nouveau:
            # Groupe devenu supergroupe : son identifiant a changé.
            nouvelle = f"tg:{int(nouveau)}"
            _migrer_groupe(adresse, nouvelle)
            return self.envoyer(nouvelle, reste) if nouvelle != adresse else True
        if r.status_code in (400, 403):
            log.warning(f"⚠️ Telegram {r.status_code} vers {adresse} : abandonné (membre sans /start "
                        f"ou chat introuvable)")
            return True
        if r.status_code == 429 or r.status_code >= 500:
            if parametres.get("retry_after"):
                log.warning(f"⚠️ Telegram 429 : réessayer dans {parametres['retry_after']} s (outbox)")
            return False
        if not 200 <= r.status_code < 300:
            log.warning(f"⚠️ Telegram sendMessage → {r.status_code}, abandonné")
            return True
        return None


def _description(r) -> str:
    try:
        corps = r.json()
    except ValueError:
        return ""
    return str(corps.get("description", "")) if isinstance(corps, dict) else ""


def _vers_outbox(adresse: str, texte: str):
    from katechon.messaging.whatsapp import _outbox_enqueue   # import tardif : whatsapp importe ce module
    _outbox_enqueue("send", {"to": adresse, "body": texte})


def _parametres(r) -> dict:
    try:
        corps = r.json()
    except ValueError:
        return {}
    return (corps.get("parameters") or {}) if isinstance(corps, dict) else {}


def _migrer_groupe(ancienne: str, nouvelle: str):
    """Reporte le nouvel identifiant d'un supergroupe sur la tontine liée."""
    from katechon.core.db import get_conn, q, release_conn   # import tardif : pas de cycle à l'import
    try:
        conn = get_conn()
        try:
            q(conn, "UPDATE tontines SET whatsapp_groupe=%s WHERE whatsapp_groupe=%s", (nouvelle, ancienne))
            conn.commit()
        finally:
            release_conn(conn)
        log.warning(f"⚠️ Groupe Telegram migré : {ancienne} → {nouvelle}")
    except Exception as e:
        log.error(f"❌ Migration du groupe Telegram {ancienne} non enregistrée : {type(e).__name__}")


def _chemin_fichier(file_id: str) -> str:
    r = _appel("getFile", {"file_id": file_id})
    if r.status_code != 200:
        return ""
    return ((r.json() or {}).get("result") or {}).get("file_path", "") or ""


def telecharger(file_id: str) -> bytes:
    """Octets d'un média Telegram, ou b"" (média absent, hôte ou chemin refusé, trop gros)."""
    if not actif() or not file_id:
        return b""
    try:
        chemin = _chemin_fichier(file_id)
        if not chemin or ".." in chemin or chemin.startswith("/") or "://" in chemin:
            log.warning("⚠️ Telegram : chemin de fichier refusé")
            return b""
        url = f"{_base()}/file/bot{os.getenv('TELEGRAM_BOT_TOKEN', '')}/{chemin}"
        if urlsplit(url).netloc != urlsplit(_base()).netloc:
            return b""
        r = requests.get(url, timeout=15, allow_redirects=False, stream=True)
        try:
            if r.status_code != 200:
                return b""
            buf = bytearray()
            for bloc in r.iter_content(65536):
                buf.extend(bloc)
                if len(buf) > MEDIA_MAX_BYTES:
                    log.warning("⚠️ Média Telegram trop gros, rejeté")
                    return b""
            return bytes(buf)
        finally:
            r.close()
    except Exception as e:
        log.error(f"Téléchargement média Telegram : {type(e).__name__}")
        return b""


def _signature(tontine_id: int) -> str:
    cle = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").encode()
    return hmac.new(cle, f"tontine:{tontine_id}".encode(), hashlib.sha256).hexdigest()[:16]


def code_invitation(tontine_id: int) -> str:
    return f"{int(tontine_id)}_{_signature(int(tontine_id))}"


def lire_code(code: str):
    """Retourne l'id de tontine d'un code d'invitation valide, sinon None."""
    m = re.fullmatch(r"([0-9]{1,9})_([0-9a-f]{16})", (code or "").strip())
    if not m or not os.getenv("TELEGRAM_WEBHOOK_SECRET"):
        return None
    tid = int(m.group(1))
    return tid if hmac.compare_digest(m.group(2), _signature(tid)) else None


canal.enregistrer(TelegramCanal())
