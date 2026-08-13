# -*- coding: utf-8 -*-
"""
Tests du moteur IRA v2 (module pur ira_engine). Aucune DB requise.
Lancer :  python -m pytest test_ira_engine.py -v
"""

from datetime import datetime, timedelta

import pytest

import ira_engine as ie


# Heure limite de référence : 18:00 un jour arbitraire.
LIMITE = datetime(2026, 8, 13, 18, 0, 0)
GRACE_FIN = LIMITE + timedelta(minutes=ie.IRA_GRACE_MIN)  # 18:05


def _at(**delta):
    """Helper : datetime de paiement = fin de grâce + delta."""
    return GRACE_FIN + timedelta(**delta)


# ── floor_100 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("x,attendu", [
    (0, 0), (99, 0), (100, 100), (150, 100), (199, 100),
    (7599, 7500), (25000, 25000), (-50, 0),
])
def test_floor_100(x, attendu):
    assert ie.floor_100(x) == attendu


# ── calculer_ira : grâce ────────────────────────────────────────────────────
def test_grace_avant_limite():
    r = ie.calculer_ira(50_000, LIMITE - timedelta(hours=2), LIMITE)
    assert r["en_grace"] is True and r["ira"] == 0


def test_grace_pile_a_5min():
    # Pile à la fin de la grâce (18:05:00) -> encore en grâce (<=).
    r = ie.calculer_ira(50_000, GRACE_FIN, LIMITE)
    assert r["en_grace"] is True and r["ira"] == 0


# ── calculer_ira : falaise Schelling 15 % dès la 6e minute ─────────────────
def test_falaise_15pct_juste_apres_grace():
    r = ie.calculer_ira(50_000, _at(seconds=1), LIMITE)  # 18:05:01
    assert r["en_grace"] is False
    assert r["jours_retard"] == 0
    assert r["ira"] == 7_500  # 15 % de 50 000


def test_falaise_plate_meme_journee():
    # Sans rampe horaire (défaut hourly=0), 1 min et 11h de retard = même socle 15 %.
    r_min = ie.calculer_ira(50_000, _at(minutes=1), LIMITE)
    r_11h = ie.calculer_ira(50_000, _at(hours=11), LIMITE)
    assert r_min["ira"] == r_11h["ira"] == 7_500


# ── calculer_ira : accrual journalier linéaire ─────────────────────────────
def test_accrual_journalier():
    # Jour 5 : 15 % + 2 %·5 = 25 % de 50 000 = 12 500.
    r = ie.calculer_ira(50_000, _at(days=5, hours=1), LIMITE)
    assert r["jours_retard"] == 5
    assert r["ira"] == 12_500


# ── calculer_ira : plafond anti-usure 50 % ─────────────────────────────────
def test_plafond_50pct():
    r = ie.calculer_ira(50_000, _at(days=60), LIMITE)  # très en retard
    assert r["plafonnee"] is True
    assert r["ira"] == 25_000  # cap 50 % de 50 000, jamais au-delà


# ── calculer_ira : plancher petits groupes ─────────────────────────────────
def test_plancher_petit_groupe():
    # Mise 2 000 : 15 % = 300 -> au-dessus du plancher 200, donc 300.
    r = ie.calculer_ira(2_000, _at(minutes=1), LIMITE)
    assert r["ira"] == 300


def test_plancher_mordant_tres_petite_mise():
    # Mise 1 000 : 15 % = 150 < plancher 200 -> plancher 200.
    r = ie.calculer_ira(1_000, _at(minutes=1), LIMITE)
    assert r["ira"] == 200


# ── calculer_ira : rampe horaire optionnelle ───────────────────────────────
def test_rampe_horaire_activee():
    # hourly=0.01 : socle 15 % + 1 %·(heures entamées).
    r1 = ie.calculer_ira(50_000, _at(minutes=1), LIMITE, hourly=0.01)   # 1 h entamée
    r3 = ie.calculer_ira(50_000, _at(hours=2, minutes=1), LIMITE, hourly=0.01)  # 3 h entamées
    assert r1["ira"] == 8_000    # 15 % + 1 % = 16 % de 50 000 = 8 000
    assert r3["ira"] == 9_000    # 15 % + 3 % = 18 % de 50 000 = 9 000


# ── PROPRIÉTÉ DURE : monotonie temporelle ──────────────────────────────────
def test_monotonie_stricte_dans_le_temps():
    mise = 50_000
    dernier = -1
    for h in range(0, 24 * 40, 6):  # sur 40 jours, pas de 6 h
        r = ie.calculer_ira(mise, _at(hours=h) + timedelta(seconds=1), LIMITE, hourly=0.01)
        assert r["ira"] >= dernier, f"non-monotone à h={h}"
        dernier = r["ira"]
    # Et borné au cap.
    assert dernier == 25_000


def test_arrondi_bas_grille_100():
    # 33 333 de mise, 15 % = 4 999.95 -> floor_100 = 4 900.
    r = ie.calculer_ira(33_333, _at(minutes=1), LIMITE)
    assert r["ira"] % 100 == 0
    assert r["ira"] == 4_900


# ── eligibilite_apres_slip : mulligan unique ───────────────────────────────
def test_mulligan_premier_slip():
    r = ie.eligibilite_apres_slip(0)
    assert r["ira_due"] is True
    assert r["mulligan_utilise"] is True
    assert r["eligible"] is True
    assert r["ejecte"] is False


def test_ejection_deuxieme_slip():
    r = ie.eligibilite_apres_slip(1)
    assert r["ira_due"] is True
    assert r["eligible"] is False
    assert r["ejecte"] is True


def test_slips_ulterieurs_restent_ejectes():
    for avant in (2, 3, 10):
        r = ie.eligibilite_apres_slip(avant)
        assert r["ira_due"] is True and r["ejecte"] is True and r["eligible"] is False


# ── ordre_priorite_next_cycle : tri par tier ───────────────────────────────
def test_priorite_rotation_tri_par_tier():
    membres = [
        {"membre_id": 1, "slips": 2, "ordre_actuel": 1},   # éjecté
        {"membre_id": 2, "slips": 0, "ordre_actuel": 5},   # immaculé
        {"membre_id": 3, "slips": 1, "ordre_actuel": 3},   # mulligan
        {"membre_id": 4, "slips": 0, "ordre_actuel": 2},   # immaculé
    ]
    out = ie.ordre_priorite_next_cycle(membres)
    ids = [m["membre_id"] for m in out]
    # Immaculés d'abord (départage par ordre_actuel : 4 avant 2), puis mulligan, puis éjecté.
    assert ids == [4, 2, 3, 1]
    assert [m["ordre_nouveau"] for m in out] == [1, 2, 3, 4]
    assert out[0]["tier"] == 0 and out[-1]["tier"] == 2


# ── bonus_par_parfait : invariant Redistribué <= Collecté ──────────────────
def test_bonus_invariant_somme_bornee():
    r = ie.bonus_par_parfait(8_500, 6)
    total = r["bonus_tete"] * 6 + r["reliquat_rollover"]
    assert total == r["pool_net"] == 8_500
    assert r["bonus_tete"] * 6 <= 8_500  # jamais plus que collecté


def test_bonus_netting_frais():
    r = ie.bonus_par_parfait(10_000, 4, frais_dispatch=200)
    assert r["pool_net"] == 9_800
    assert r["bonus_tete"] == 2_400  # floor_100(9800/4 = 2450) = 2400
    assert r["reliquat_rollover"] == 9_800 - 2_400 * 4  # 200


def test_bonus_zero_parfait_rollover_total():
    r = ie.bonus_par_parfait(40_000, 0)
    assert r["bonus_tete"] == 0
    assert r["reliquat_rollover"] == 40_000  # jackpot roule


def test_bonus_frais_superieurs_pool():
    r = ie.bonus_par_parfait(100, 3, frais_dispatch=500)
    assert r["pool_net"] == 0 and r["bonus_tete"] == 0 and r["reliquat_rollover"] == 0
