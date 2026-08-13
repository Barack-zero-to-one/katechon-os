# -*- coding: utf-8 -*-
"""
ira_engine.py — Moteur IRA v2 (Katechon comportemental) + Bonus Ponctualité.

Module PUR : aucune dépendance DB, réseau ou I/O. Tout est fonction déterministe,
100 % testable hors infrastructure. Le câblage (PostgreSQL, WhatsApp, scheduler)
vit dans barack_corp_v9_18.py et appelle ces fonctions.

────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE
────────────────────────────────────────────────────────────────────────────
L'IRA historique était un flat 150 FCFA/jour, décorrélé du montant en jeu.
Théorie des jeux faible : sur une mise de 50 000 FCFA, 3 jours de retard = 450 FCFA
= 0,9 % du cash retenu → garder son cash est rationnel. Le flat protégeait les
petits groupes et laissait fuir les gros — l'inverse du besoin.

Le modèle v2 rend le retard STRICTEMENT irrationnel quelle que soit la taille du
groupe, sans franchir l'usure (optics ANIF) ni perdre un membre récupérable :

    grace = heure_limite + IRA_GRACE_MIN (tolérance réseau MTN/Orange)
    si paiement <= grace : IRA = 0
    sinon :
        brut = M·CLIFF                    # socle Schelling, dès la 6e minute
             + M·DAILY·jours_pleins       # accrual journalier LINÉAIRE
             + M·HOURLY·heures_entamees   # rampe intra-day OPTIONNELLE (défaut 0)
        brut = min(brut, M·CAP)           # plafond anti-usure  (EN PREMIER)
        brut = max(brut, FLOOR)           # plancher petits groupes (EN SECOND)
        IRA  = floor_100(brut)            # arrondi BAS non-prédateur (EN DERNIER)

Propriété dure : IRA(t) est MONOTONE croissante dans le temps. Jamais un retard
plus long ne coûte moins cher. Vérifiée en test.

Éligibilité / mulligan : 1 slip réseau toléré par cycle pour le bonus/position
(la peine IRA s'applique quand même). Le 2e slip éjecte.

Reward : priorité de rotation (moteur Phase 1, float 0). Pool cash = hooks Phase 2
(compte marchand MoMo/OM), invariant Redistribué <= Collecté.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────
# Paramètres par défaut du barème (surchargés par les constantes du bot).
# ──────────────────────────────────────────────────────────────────────────
IRA_CLIFF_PCT   = 0.15   # Socle Schelling : % de la mise dès la 6e minute de retard
IRA_DAILY_PCT   = 0.02   # Accrual journalier LINÉAIRE (convexe abandonné : cap fait le travail)
IRA_HOURLY_PCT  = 0.00   # Rampe intra-day (heures entamées). 0 = falaise plate. 0.01 = urgence horaire.
IRA_CAP_PCT     = 0.50   # Plafond anti-usure : l'IRA ne dépasse jamais 50 % de la mise
IRA_FLOOR       = 200    # Plancher aligné sur la grille de 100 (protège les petits groupes)
IRA_GRACE_MIN   = 5      # Tolérance réseau en minutes après l'heure limite

_SECONDES_PAR_JOUR   = 86_400
_SECONDES_PAR_HEURE  = 3_600


def floor_100(x) -> int:
    """
    Arrondi au multiple de 100 INFÉRIEUR. Choisi (vs 'au plus proche') pour deux raisons :
      1. Non-prédateur : un arrondi toujours-haut sur une pénalité = optics usure (ANIF).
      2. Invariant : sur le dispatch bonus, garantit Σ(bonus) <= pool collecté.
    """
    n = int(x)
    if n < 0:
        n = 0
    return (n // 100) * 100


def calculer_ira(
    montant_mise: int,
    dt_paiement: datetime,
    dt_limite: datetime,
    *,
    cliff: float = IRA_CLIFF_PCT,
    daily: float = IRA_DAILY_PCT,
    hourly: float = IRA_HOURLY_PCT,
    cap: float = IRA_CAP_PCT,
    floor: int = IRA_FLOOR,
    grace_min: int = IRA_GRACE_MIN,
) -> dict:
    """
    Calcule l'IRA d'un paiement, indexée sur la mise.

    Args:
        montant_mise : montant de la cotisation (la "mise"), en FCFA.
        dt_paiement  : datetime du paiement effectif (aware ou naive, cohérent avec dt_limite).
        dt_limite    : datetime de l'heure limite du jour (AVANT ajout de la grâce).
        cliff/daily/hourly/cap/floor/grace_min : voir barème module.

    Returns:
        {
          "en_grace"     : bool,   # True si dans la tolérance -> IRA 0
          "jours_retard" : int,    # jours pleins écoulés depuis la grâce
          "heures_retard": int,    # heures entamées depuis la grâce (pour la rampe)
          "plafonnee"    : bool,   # True si le plafond 50 % a mordu
          "ira"          : int,    # pénalité finale, multiple de 100
        }

    Invariant : monotone croissante en dt_paiement. Bornée [0 .. floor_100(cap·mise)].
    """
    grace = dt_limite + timedelta(minutes=grace_min)

    if dt_paiement <= grace:
        return {
            "en_grace": True,
            "jours_retard": 0,
            "heures_retard": 0,
            "plafonnee": False,
            "ira": 0,
        }

    ecoule = (dt_paiement - grace).total_seconds()
    jours_pleins   = int(ecoule // _SECONDES_PAR_JOUR)
    # "heures entamées" : toute heure commencée compte (ceil), y compris la 1re minute.
    heures_entamees = int((ecoule + _SECONDES_PAR_HEURE - 1) // _SECONDES_PAR_HEURE)

    brut = (
        montant_mise * cliff
        + montant_mise * daily * jours_pleins
        + montant_mise * hourly * heures_entamees
    )

    plafond = montant_mise * cap
    plafonnee = brut >= plafond
    if plafonnee:
        brut = plafond

    if brut < floor:
        brut = floor

    ira = floor_100(brut)

    return {
        "en_grace": False,
        "jours_retard": jours_pleins,
        "heures_retard": heures_entamees,
        "plafonnee": plafonnee,
        "ira": ira,
    }


def eligibilite_apres_slip(slips_avant: int) -> dict:
    """
    Applique la règle du mulligan unique par cycle après un slip (paiement hors grâce).

    La peine IRA s'applique TOUJOURS (ira_due=True) — le mulligan ne concerne QUE
    l'éligibilité au bonus / à la priorité de rotation.

    Args:
        slips_avant : nombre de slips DÉJÀ enregistrés ce cycle AVANT celui-ci.

    Returns:
        {
          "ira_due"        : True,   # la peine tombe à chaque slip
          "slips_apres"    : int,
          "mulligan_utilise": bool,  # True si ce slip consomme le mulligan (1er du cycle)
          "eligible"       : bool,   # éligibilité bonus/position APRÈS ce slip
          "ejecte"         : bool,   # True si ce slip éjecte définitivement (2e+)
        }
    """
    slips_apres = slips_avant + 1
    # 0 slip avant -> ce slip est le 1er (mulligan), reste éligible.
    # 1+ slip avant -> ce slip est le 2e+, éjection.
    eligible = slips_apres <= 1
    mulligan_utilise = slips_apres == 1
    ejecte = slips_apres >= 2
    return {
        "ira_due": True,
        "slips_apres": slips_apres,
        "mulligan_utilise": mulligan_utilise,
        "eligible": eligible,
        "ejecte": ejecte,
    }


def tier_ponctualite(slips: int) -> int:
    """Tier de priorité rotation : 0 = immaculé, 1 = mulligan (éligible), 2 = éjecté."""
    if slips <= 0:
        return 0
    if slips == 1:
        return 1
    return 2


def ordre_priorite_next_cycle(membres_avec_slips: list) -> list:
    """
    Réordonne les membres pour le cycle suivant : les parfaits promus en tête.

    Récompense = priorité de rotation (float 0, zéro API, symétrique à la peine :
    le retardataire est reclassé en dernier, le parfait promu en premier).

    Args:
        membres_avec_slips : liste de dicts contenant au minimum
            {"membre_id": int, "slips": int, "ordre_actuel": int}.
            "ordre_actuel" sert de clé de départage STABLE intra-tier (préserve
            l'ordre relatif d'origine — pas de rebattage arbitraire).

    Returns:
        Nouvelle liste, même dicts, avec "ordre_nouveau" (1-indexé) ajouté,
        triée tier (0>1>2) puis ordre_actuel croissant.
    """
    ordonnee = sorted(
        membres_avec_slips,
        key=lambda m: (tier_ponctualite(m.get("slips", 0)), m.get("ordre_actuel", 0)),
    )
    resultat = []
    for i, m in enumerate(ordonnee, start=1):
        nouveau = dict(m)
        nouveau["ordre_nouveau"] = i
        nouveau["tier"] = tier_ponctualite(m.get("slips", 0))
        resultat.append(nouveau)
    return resultat


# ──────────────────────────────────────────────────────────────────────────
# PHASE 2 — Pool cash floaté (compte marchand MoMo/OM, roadmap Q1 2027).
# Stubs purs, testables, INACTIFS tant que pas d'API de mouvement de fonds.
# Voir mémoire projet : project_merchant_account_rubicon.
# ──────────────────────────────────────────────────────────────────────────
def bonus_par_parfait(
    pool_collecte: int,
    n_parfaits: int,
    *,
    frais_dispatch: int = 0,
) -> dict:
    """
    Répartit le pool d'IRA collectées entre les membres parfaits, en fin de cycle.

    Invariant DUR : Σ(bonus versés) + reliquat_rollover == pool_net, et
    Σ(bonus versés) <= pool_collecte  (jamais l'admin/BADF n'avance de cash).

    Args:
        pool_collecte  : Σ des IRA réellement COLLECTÉES (statut='Prelevee'), pas les 'Due'.
        n_parfaits     : nombre de membres éligibles (0 ou 1 slip) en fin de cycle.
        frais_dispatch : frais totaux de transaction MoMo/OM du dispatch (~1-2 %),
                         nettés AVANT division. Fenêtre settlement anti-reversal gérée
                         en amont par l'appelant (n'inclure ici que du collecté-dur).

    Returns:
        {
          "pool_net"        : int,  # pool_collecte - frais_dispatch (>=0)
          "bonus_tete"      : int,  # part par parfait, floor_100
          "reliquat_rollover": int, # ce qui roule au cycle suivant (pool_net - n·bonus_tete)
        }

    Cas N=0 (personne de parfait) : tout le pool_net roule au cycle suivant
    (dynamique jackpot). Aucune division par zéro.
    """
    pool_net = max(0, int(pool_collecte) - int(frais_dispatch))

    if n_parfaits <= 0:
        return {"pool_net": pool_net, "bonus_tete": 0, "reliquat_rollover": pool_net}

    bonus_tete = floor_100(pool_net / n_parfaits)
    reliquat_rollover = pool_net - bonus_tete * n_parfaits
    return {
        "pool_net": pool_net,
        "bonus_tete": bonus_tete,
        "reliquat_rollover": reliquat_rollover,
    }
