"""
File d'envoi WhatsApp : un seul expéditeur, priorités, espacement par destinataire.

Module pur (aucune dépendance au bot, comme rate_limiter.py). L'appelant ne
dort jamais : soumettre() range le message et rend la main. Un thread unique
envoie en respectant deux règles :
  - au moins `ecart_par_chat` secondes entre deux messages au même destinataire
    (anti-détection WhatsApp, l'ancienne règle des 3 s, désormais par chat) ;
  - au plus `debit_global` messages par seconde pour toute l'instance.
Une réponse interactive (PRIORITE_REPONSE) passe avant une diffusion
(PRIORITE_DIFFUSION) ; chaque destinataire garde l'ordre de ses messages.
Un envoi refusé ou en exception est passé au crochet `en_echec`.
"""
import itertools
import logging
import threading
import time
from collections import deque

PRIORITE_REPONSE   = 0
PRIORITE_DIFFUSION = 1

_log = logging.getLogger(__name__)


class Expediteur:
    def __init__(self, envoyer, ecart_par_chat: float = 3.0,
                 debit_global: float = 1.0, en_echec=None,
                 horloge=time.monotonic):
        if ecart_par_chat < 0:
            raise ValueError("ecart_par_chat doit être >= 0")
        if debit_global <= 0:
            raise ValueError("debit_global doit être > 0")
        self._envoyer        = envoyer
        self.ecart_par_chat  = float(ecart_par_chat)
        self.debit_global    = float(debit_global)
        self._en_echec       = en_echec
        self._horloge        = horloge
        self._files          = {}      # chat -> deque[(priorite, seq, texte)]
        self._dernier_chat   = {}      # chat -> instant du dernier envoi
        self._dernier_global = None
        self._seq            = itertools.count()
        self._cond           = threading.Condition()
        self._en_cours       = 0
        self._thread         = None

    # ── API ────────────────────────────────────────────────────────────────
    def soumettre(self, chat_id: str, texte: str,
                  priorite: int = PRIORITE_REPONSE) -> None:
        """Range le message et rend la main immédiatement."""
        with self._cond:
            self._files.setdefault(chat_id, deque()).append(
                (priorite, next(self._seq), texte))
            self._cond.notify_all()

    def en_attente(self) -> int:
        with self._cond:
            return sum(len(f) for f in self._files.values())

    def demarrer(self) -> None:
        """Lance le thread expéditeur (idempotent)."""
        with self._cond:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._boucle, name="WaExpediteur", daemon=True)
            self._thread.start()

    def vider(self, timeout: float) -> bool:
        """Attend que la file soit vide et qu'aucun envoi ne soit en cours.
        Retourne False si `timeout` (secondes réelles) est dépassé."""
        fin = time.monotonic() + timeout
        with self._cond:
            while self._files or self._en_cours:
                reste = fin - time.monotonic()
                if reste <= 0:
                    return False
                self._cond.wait(min(reste, 0.05))
        return True

    def traiter_un(self):
        """Envoie au plus un message prêt. Retourne 0.0 si un message est parti,
        le délai avant le prochain envoi possible, ou None si la file est vide."""
        with self._cond:
            maintenant = self._horloge()
            chat, attente = self._choisir(maintenant)
            if chat is None:
                return attente
            _, _, texte = self._files[chat].popleft()
            if not self._files[chat]:
                del self._files[chat]
            self._dernier_chat[chat] = maintenant
            self._dernier_global = maintenant
            self._en_cours += 1
            self._purger(maintenant)
        try:
            ok = bool(self._envoyer(chat, texte))
        except Exception as e:
            _log.error(f"❌ Envoi WhatsApp vers {chat} : {e}")
            ok = False
        if not ok and self._en_echec:
            try:
                self._en_echec(chat, texte)
            except Exception as e:
                _log.error(f"❌ Crochet d'échec WhatsApp : {e}")
        with self._cond:
            self._en_cours -= 1
            self._cond.notify_all()
        return 0.0

    # ── Interne ────────────────────────────────────────────────────────────
    def _choisir(self, maintenant: float):
        """(chat prêt de plus haute priorité, 0.0) ou (None, délai minimal),
        (None, None) si rien n'est en file. Seule la tête de chaque file par
        destinataire est candidate, ce qui garde l'ordre par destinataire."""
        attente_globale = 0.0
        if self._dernier_global is not None:
            attente_globale = self._dernier_global + 1.0 / self.debit_global - maintenant
        meilleur, attente_min = None, None
        for chat, file in self._files.items():
            dernier = self._dernier_chat.get(chat)
            attente = 0.0 if dernier is None else dernier + self.ecart_par_chat - maintenant
            attente = max(attente, attente_globale, 0.0)
            if attente <= 0.0:
                cle = file[0][:2]
                if meilleur is None or cle < meilleur[0]:
                    meilleur = (cle, chat)
            elif attente_min is None or attente < attente_min:
                attente_min = attente
        if meilleur is not None:
            return meilleur[1], 0.0
        return None, attente_min

    def _purger(self, maintenant: float) -> None:
        """Oublie les destinataires dont l'écart est écoulé (mémoire bornée)."""
        if len(self._dernier_chat) < 10_000:
            return
        limite = maintenant - self.ecart_par_chat
        for chat in [c for c, t in self._dernier_chat.items() if t < limite]:
            del self._dernier_chat[chat]

    def _boucle(self) -> None:
        while True:
            attente = self.traiter_un()
            if attente == 0.0:
                continue
            with self._cond:
                if attente is None:
                    if not self._files:
                        self._cond.wait()
                else:
                    self._cond.wait(timeout=attente)
