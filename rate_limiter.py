# -*- coding: utf-8 -*-
"""
rate_limiter.py — Limiteur de débit robuste (module pur, thread-safe).

Corrige les faiblesses du limiteur historique (finding #4) :
  • per-numéro SEUL → un burst multi-numéros (ou des `sender` forgés si le token
    fuite) passait intégralement. On ajoute un plafond GLOBAL (garde-fou).
  • mémoire NON bornée (defaultdict jamais purgé) → DoS par accumulation de faux
    numéros. On borne le nombre de clés + on purge les buckets vides.
  • lecture-modification non atomique sous Flask threaded=True. On protège par un
    lock.

Design : deux fenêtres glissantes (deque de timestamps), l'une par clé, l'autre
globale. `clock` injectable pour les tests. Le plafond global doit rester AU-DESSUS
du pic légitime (ex. ouverture simultanée de nombreuses tontines) — il est donc
généreux par défaut et réglable ; la vraie protection universelle est la borne
mémoire.
"""
from __future__ import annotations

import threading
import time as _time
from collections import deque, defaultdict


class RateLimiter:
    def __init__(self, max_per_key: int = 10, max_global: int = 3000,
                 window: float = 60.0, max_keys: int = 100_000,
                 clock=_time.monotonic):
        self.max_per_key = int(max_per_key)
        self.max_global = int(max_global)
        self.window = float(window)
        self.max_keys = int(max_keys)
        self._clock = clock
        self._buckets = defaultdict(deque)   # clé -> deque[timestamps]
        self._global: deque = deque()
        self._lock = threading.Lock()

    def autorise(self, key) -> bool:
        """
        True si le message est autorisé (et le comptabilise), False s'il dépasse
        la limite par clé OU la limite globale. N' AUTO-VIVIFIE PAS de bucket sur
        un refus (pas de fuite mémoire par clés forgées).
        """
        now = self._clock()
        cutoff = now - self.window
        with self._lock:
            g = self._global
            while g and g[0] <= cutoff:
                g.popleft()

            b = self._buckets.get(key)
            if b is not None:
                while b and b[0] <= cutoff:
                    b.popleft()
                if not b:
                    del self._buckets[key]
                    b = None

            n_key = len(b) if b is not None else 0
            if n_key >= self.max_per_key or len(g) >= self.max_global:
                return False

            if b is None:
                if len(self._buckets) >= self.max_keys:
                    self._evincer_locked(cutoff)
                b = self._buckets[key]   # crée le bucket seulement à l'acceptation
            b.append(now)
            g.append(now)
            return True

    def _evincer_locked(self, cutoff: float) -> None:
        # Purge d'abord tout bucket devenu vide/expiré ; en dernier recours, vire
        # le bucket dont l'activité la plus récente est la plus ancienne.
        for k in list(self._buckets.keys()):
            dq = self._buckets[k]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if not dq:
                del self._buckets[k]
        if len(self._buckets) >= self.max_keys and self._buckets:
            plus_vieux = min(self._buckets, key=lambda k: self._buckets[k][-1])
            del self._buckets[plus_vieux]

    def taille(self) -> int:
        with self._lock:
            return len(self._buckets)
