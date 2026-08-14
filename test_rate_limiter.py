# -*- coding: utf-8 -*-
"""
Tests du limiteur de débit (module pur rate_limiter). Aucune infra.
Lancer :  python -m pytest test_rate_limiter.py -v
"""
from rate_limiter import RateLimiter


class _Horloge:
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def avance(self, dt):
        self.t += dt


# ── Limite par clé (comportement historique préservé) ───────────────────────
def test_par_cle_cap_a_max():
    h = _Horloge()
    rl = RateLimiter(max_per_key=10, max_global=10_000, window=60, clock=h)
    for _ in range(10):
        assert rl.autorise("237690") is True
    assert rl.autorise("237690") is False        # 11e dans la fenêtre → drop


def test_par_cle_fenetre_glisse():
    h = _Horloge()
    rl = RateLimiter(max_per_key=10, max_global=10_000, window=60, clock=h)
    for _ in range(10):
        rl.autorise("A")
    assert rl.autorise("A") is False
    h.avance(61)                                  # fenêtre écoulée
    assert rl.autorise("A") is True


# ── Plafond GLOBAL : le burst multi-numéros ne passe plus (finding #4) ──────
def test_plafond_global_bloque_burst_multi_numeros():
    h = _Horloge()
    # per-key permissif (100), mais global à 50 : 200 numéros distincts ×1 msg
    rl = RateLimiter(max_per_key=100, max_global=50, window=60, clock=h)
    acceptes = sum(rl.autorise(f"num_{i}") for i in range(200))
    assert acceptes == 50                         # le global coupe à 50, pas 200×...


def test_global_fenetre_glisse():
    h = _Horloge()
    rl = RateLimiter(max_per_key=100, max_global=50, window=60, clock=h)
    for i in range(50):
        rl.autorise(f"n{i}")
    assert rl.autorise("nouveau") is False        # global saturé
    h.avance(61)
    assert rl.autorise("nouveau") is True          # fenêtre globale écoulée


# ── Mémoire bornée : pas de DoS par accumulation de faux numéros ────────────
def test_memoire_bornee_max_keys():
    h = _Horloge()
    rl = RateLimiter(max_per_key=10, max_global=10_000_000, window=60,
                     max_keys=100, clock=h)
    for i in range(5000):
        rl.autorise(f"faux_{i}")
    assert rl.taille() <= 100                      # jamais 5000 buckets


def test_buckets_vides_purges():
    h = _Horloge()
    rl = RateLimiter(max_per_key=10, max_global=10_000, window=60, clock=h)
    for i in range(50):
        rl.autorise(f"n{i}")
    assert rl.taille() == 50
    h.avance(61)                                   # tout expire
    rl.autorise("reveil")                          # un nouvel appel referme un bucket…
    # le prochain accès à une clé expirée la supprime ; on force un balayage :
    for i in range(50):
        rl.autorise(f"n{i}")                       # recrée, mais les anciens sont partis
    assert rl.taille() <= 51


def test_refus_ne_cree_pas_de_bucket():
    # Un refus par plafond global ne doit pas laisser de trace mémoire pour la clé.
    h = _Horloge()
    rl = RateLimiter(max_per_key=10, max_global=1, window=60, clock=h)
    assert rl.autorise("premier") is True          # consomme le quota global (1)
    assert rl.autorise("jamais_vu") is False       # refusé par le global
    # "jamais_vu" ne doit pas exister comme bucket
    assert "jamais_vu" not in rl._buckets


def test_cles_independantes():
    h = _Horloge()
    rl = RateLimiter(max_per_key=2, max_global=10_000, window=60, clock=h)
    assert rl.autorise("A") and rl.autorise("A")
    assert rl.autorise("A") is False               # A saturé
    assert rl.autorise("B") is True                # B indépendant
