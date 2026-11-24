# -*- coding: utf-8 -*-
"""
webhook_security.py — Durcissement du webhook Green API.

Module PUR (pas de Flask, DB, réseau) : logique 100 % testable hors infra.
Le câblage HTTP vit dans barack_corp_v9_18.py et appelle ces primitives.

Résout trois faiblesses du pentest sur la surface #1 :
  • Rejeu (replay) : le token statique dans l'URL n'empêche PAS de rejouer une
    requête capturée. ReplayGuard déduplique les `idMessage` sur fenêtre glissante,
    borné en mémoire (aucun DoS par accumulation).
  • Comparaison de token : constant_time_equal encapsule hmac.compare_digest en
    bytes + garde None/non-ASCII (l'ancien code levait TypeError → 500, distinguable
    du 403).
  • SSRF : media_url_autorisee valide sur le HOSTNAME (hors userinfo/port) avec
    frontière de label exacte — utilisé conjointement à allow_redirects=False côté
    téléchargement.
"""
from __future__ import annotations

import hmac
import threading
import time as _time
from urllib.parse import urlparse

# Domaines autorisés pour le téléchargement de média Green API (surface SSRF #2).
DOMAINES_MEDIA_OK = ("green-api.com", "sms.by", "whatsapp.net")


def constant_time_equal(a, b) -> bool:
    """
    Égalité en temps constant, robuste. Encode str→bytes (surrogatepass pour ne
    jamais lever sur unicode), renvoie False sur None. Constant-time pour deux
    entrées de même longueur (hmac.compare_digest).
    """
    if a is None or b is None:
        return False
    if isinstance(a, str):
        a = a.encode("utf-8", "surrogatepass")
    if isinstance(b, str):
        b = b.encode("utf-8", "surrogatepass")
    try:
        return hmac.compare_digest(a, b)
    except TypeError:
        return False


def media_url_autorisee(url: str, domaines_ok=DOMAINES_MEDIA_OK) -> bool:
    """
    True si url est https ET son HOSTNAME se termine EXACTEMENT sur un domaine
    whitelisté (frontière de label). Corrige les défauts du check historique :

      - basé sur `hostname` (urlparse) → ignore userinfo (`user@host`) et port,
        que l'ancien `netloc.endswith` mélangeait ;
      - frontière de label stricte (`== d` ou `.d`) → `evilgreen-api.com` rejeté ;
      - IP littérales (décimale/hex/octale) rejetées (aucun domaine ne matche) ;
      - scheme non-https rejeté.

    NB : ne protège que le PREMIER hop. Le téléchargement DOIT poser
    allow_redirects=False et re-valider toute redirection (cf. barack_corp).
    """
    if not url:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme != "https":
        return False
    try:
        host = (p.hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domaines_ok)


class ReplayGuard:
    """
    Anti-rejeu borné en mémoire. Mémorise les `idMessage` vus pendant `ttl`
    secondes ; un id revu dans la fenêtre = rejeu → à rejeter par l'appelant.

    Bornes DURES (anti-DoS mémoire) :
      - purge paresseuse des entrées expirées à chaque appel ;
      - cap `max_size` : au-delà, éviction des plus anciennes.

    Thread-safe (Flask threaded=True). `clock` injectable pour les tests.

    Fail-open sur idMessage absent : sans identifiant on ne peut pas dédupliquer,
    on laisse passer (comportement au pire égal à l'existant, jamais pire).
    """

    def __init__(self, ttl: float = 900.0, max_size: int = 50_000,
                 clock=_time.monotonic):
        self.ttl = float(ttl)
        self.max_size = int(max_size)
        self._clock = clock
        self._seen: dict = {}          # id_message -> timestamp d'expiration
        self._lock = threading.Lock()

    def _purge_locked(self, now: float) -> None:
        morts = [k for k, exp in self._seen.items() if exp <= now]
        for k in morts:
            self._seen.pop(k, None)

    def _evincer_pour_inserer_locked(self) -> None:
        # Laisse la place à UNE insertion : garde len <= max_size après ajout.
        surplus = len(self._seen) - (self.max_size - 1)
        if surplus > 0:
            for k in sorted(self._seen, key=self._seen.get)[:surplus]:
                self._seen.pop(k, None)

    def est_rejeu(self, id_message) -> bool:
        """
        True si `id_message` a déjà été vu dans la fenêtre (→ rejeu à rejeter).
        Enregistre l'id si nouveau. id vide/None → False (fail-open, non dédupliquable).
        """
        if not id_message:
            return False
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            exp = self._seen.get(id_message)
            if exp is not None and exp > now:
                return True
            if len(self._seen) >= self.max_size:
                self._evincer_pour_inserer_locked()
            self._seen[id_message] = now + self.ttl
            return False

    def taille(self) -> int:
        with self._lock:
            return len(self._seen)
