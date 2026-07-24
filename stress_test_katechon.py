"""
stress_test_katechon.py — suite pytest pour le moteur de risque Trust Graph
(calculer_score_risque_fugue / _agreger_score_risque_fugue / alerter_risques_bouffage_imminent
dans barack_corp_v9_18.py).

Trois groupes de tests, séparés par ce qui nécessite réellement une base
PostgreSQL et ce qui n'en a pas besoin :

- TestNormalizationBoundaries : pur, ZÉRO DB. Exerce directement
  _agreger_score_risque_fugue() avec des score_brut adversariaux (négatif,
  NaN, infini, overflow) — la seule chose "combinatoire" qui peut être
  adversariale ici, puisque chacune des 10 features est déjà bornée
  individuellement par sa propre logique SQL/Python avant d'arriver ici.

- TestTrustGraphIntegration (@pytest.mark.integration) : seed des membres/
  tontines réalistes dans barack_corp_test (fixtures de conftest.py) et
  vérifie le scoring de bout en bout + le pipeline d'alerte.

- TestConcurrencyStress (@pytest.mark.stress, exclu par défaut — voir
  pytest.ini `addopts = -m "not stress"`) : 88 appelants concurrents contre
  le pool DB (maxconn=80, dépassement volontaire), style ThreadPoolExecutor
  identique à stress_test.py.

Lancer :
    pytest stress_test_katechon.py                  # pur + intégration (DB requise)
    pytest stress_test_katechon.py -m "not integration and not stress"  # pur seulement
    pytest stress_test_katechon.py -m stress         # scénario de charge, manuel/local
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import pytest

import barack_corp_v9_18 as bc
from barack_corp_v9_18 import POIDS_MAX_TRUST_GRAPH, _agreger_score_risque_fugue


# ═══════════════════════════════════════════════════════════════════════════
# TestNormalizationBoundaries — pur, aucune DB
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizationBoundaries:
    """Exerce _agreger_score_risque_fugue(score_brut, features, signaux) directement.
    score_brut est la somme déjà accumulée des 10 features (échelle 0-145 en
    fonctionnement normal) ; features/signaux ne participent à aucun calcul
    ici, ce sont des métadonnées passives recopiées dans le retour."""

    @pytest.mark.parametrize("normalise_cible,niveau_attendu", [
        (0,   "vert"),
        (30,  "vert"),
        (31,  "jaune"),
        (55,  "jaune"),
        (56,  "orange"),
        (75,  "orange"),
        (76,  "rouge"),
        (100, "rouge"),
    ])
    def test_seuils_niveau(self, normalise_cible, niveau_attendu):
        score_brut = normalise_cible * POIDS_MAX_TRUST_GRAPH / 100.0
        result = _agreger_score_risque_fugue(score_brut, {}, [])
        assert result["score"] == normalise_cible
        assert result["niveau"] == niveau_attendu

    def test_score_brut_zero(self):
        result = _agreger_score_risque_fugue(0.0, {}, [])
        assert result["score"] == 0
        assert result["niveau"] == "vert"

    def test_score_brut_max_theorique_145_donne_100(self):
        result = _agreger_score_risque_fugue(float(POIDS_MAX_TRUST_GRAPH), {}, [])
        assert result["score"] == 100
        assert result["niveau"] == "rouge"

    @pytest.mark.parametrize("score_brut_adversarial", [-999999, -1, -0.0001])
    def test_score_brut_negatif_clampe_a_zero(self, score_brut_adversarial):
        result = _agreger_score_risque_fugue(score_brut_adversarial, {}, [])
        assert result["score"] == 0
        assert result["niveau"] == "vert"

    @pytest.mark.parametrize("score_brut_adversarial", [250000, 1e300, 999999999])
    def test_score_brut_overflow_positif_clampe_a_100(self, score_brut_adversarial):
        result = _agreger_score_risque_fugue(score_brut_adversarial, {}, [])
        assert result["score"] == 100
        assert result["niveau"] == "rouge"

    def test_score_brut_nan_leve_valueerror(self):
        """Documente le comportement RÉEL (Python natif) : round(nan) lève
        ValueError. Dans calculer_score_risque_fugue, cette exception est
        attrapée par le try/except englobant -> niveau "erreur" (cf.
        TestTrustGraphIntegration.test_erreur_devrait_etre_signalee_pas_ignoree
        pour le gap que ça documente côté appelants)."""
        with pytest.raises(ValueError):
            _agreger_score_risque_fugue(float("nan"), {}, [])

    @pytest.mark.parametrize("inf_val", [float("inf"), float("-inf")])
    def test_score_brut_infini_leve_overflowerror(self, inf_val):
        """round(inf) lève OverflowError en Python natif — même remarque que
        le cas NaN ci-dessus : attrapé par calculer_score_risque_fugue, pas ici."""
        with pytest.raises(OverflowError):
            _agreger_score_risque_fugue(inf_val, {}, [])

    def test_features_et_signaux_sont_de_purs_passthrough(self):
        features_exotiques = {"une_feature": float("nan"), "autre": None, "x": "texte"}
        signaux_exotiques = ["a", None, 42]
        result = _agreger_score_risque_fugue(50.0, features_exotiques, signaux_exotiques)
        assert result["features"] is features_exotiques
        assert result["signaux"] is signaux_exotiques
        assert result["niveau"] in ("vert", "jaune", "orange", "rouge")


# ═══════════════════════════════════════════════════════════════════════════
# Helper partagé — seed d'un membre "fugitif évident" (utilisé par les deux
# groupes de tests DB-dépendants)
# ═══════════════════════════════════════════════════════════════════════════

def _seed_membre_fugitif(conn, member_factory, tontine_factory):
    """Seed un membre dont TOUS les signaux de fugue sont réunis :
    - score_confiance au plancher + 2 tentatives de fraude passées
    - 3 suspensions passées (signaux faibles)
    - 200 000 FCFA de dette IRA sur une capacité de 150 000 (ratio clampé à 1.0)
    - dernier bouffage il y a 95 jours, silence total dans les 30j qui ont
      suivi (feature 8 au max) — volontairement loin dans le passé pour ne
      PAS chevaucher la fenêtre 30-60j utilisée par les features 1 et 2
      ci-dessous (fenêtre post-bouffage = [65,95]j, fenêtre irrégularité =
      [31,60]j : aucun recouvrement)
    - position tardive (8/10) dans le cycle courant, bouffage prévu dans 3
      jours -> même ligne utilisée par alerter_risques_bouffage_imminent()
      pour le test du pipeline d'alerte
    - 6 cotisations irrégulières entre 31 et 60 jours, RIEN depuis 30 jours
      (déclenche feature 1 - régularité - ET feature 2 - tendance récente)
    - une chute de score de confiance de -30 pts (feature 9)

    Vise un score normalisé net au-delà de 75 (rouge) avec de la marge, même
    en comptant une régularité réaliste (~15-20/25, pas le max théorique).
    Retourne (membre_id, tontine_id).
    """
    membre_id = member_factory(score_confiance=0, tentatives_fraude=2)
    tontine_id = tontine_factory(cycle_actuel=2, montant_place=5000)

    cur = conn.cursor()

    cur.execute(
        "INSERT INTO liste_passage (tontine_id, membre_id, cycle, ordre, statut, date_bouffage) "
        "VALUES (%s,%s,1,1,'Paye',%s)",
        (tontine_id, membre_id, date.today() - timedelta(days=95)),
    )

    for ordre in range(1, 11):
        if ordre == 8:
            cur.execute(
                "INSERT INTO liste_passage (tontine_id, membre_id, cycle, ordre, statut, date_bouffage) "
                "VALUES (%s,%s,2,%s,'En_attente',%s)",
                (tontine_id, membre_id, ordre, date.today() + timedelta(days=3)),
            )
        else:
            cur.execute(
                "INSERT INTO liste_passage (tontine_id, nickname, cycle, ordre, statut) "
                "VALUES (%s,%s,2,%s,'En_attente')",
                (tontine_id, f"Autre {ordre}", ordre),
            )

    for jours in (60, 54, 40, 38, 37, 31):
        cur.execute(
            "INSERT INTO transactions (membre_id, tontine_id, montant_brut, montant_net, "
            "type_transaction, statut, date_heure) VALUES (%s,%s,5000,5000,'Cotisation','Confirmee',%s)",
            (membre_id, tontine_id, datetime.now(timezone.utc) - timedelta(days=jours)),
        )

    cur.execute(
        "INSERT INTO dettes_ira (membre_id, tontine_id, montant, statut) VALUES (%s,%s,200000,'Due')",
        (membre_id, tontine_id),
    )

    for _ in range(3):
        cur.execute(
            "INSERT INTO sanctions (membre_id, tontine_id, type_sanction) VALUES (%s,%s,'Suspension_72h')",
            (membre_id, tontine_id),
        )

    cur.execute(
        "INSERT INTO historique_score_confiance (membre_id, score_av, score_ap, delta, raison) "
        "VALUES (%s,30,0,-30,'seed test fugitif')",
        (membre_id,),
    )

    conn.commit()
    return membre_id, tontine_id


# ═══════════════════════════════════════════════════════════════════════════
# TestTrustGraphIntegration — DB de test requise (barack_corp_test)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestTrustGraphIntegration:

    def test_membre_neuf_sans_historique_est_vert(self, conn, member_factory, tontine_factory):
        membre_id = member_factory()
        tontine_id = tontine_factory()
        result = bc.calculer_score_risque_fugue(conn, membre_id, tontine_id)
        assert result["niveau"] == "vert"
        assert result["score"] < 30

    def test_membre_fugitif_evident_declenche_rouge(self, conn, member_factory, tontine_factory):
        membre_id, tontine_id = _seed_membre_fugitif(conn, member_factory, tontine_factory)
        result = bc.calculer_score_risque_fugue(conn, membre_id, tontine_id)
        assert result["score"] > 75, (
            f"score={result['score']} niveau={result['niveau']} features={result['features']}"
        )
        assert result["niveau"] == "rouge"
        assert result["features"]["post_bouffage"] == 20
        assert result["features"]["position_cycle"] == 15

    def test_pipeline_alerte_declenche_dm_admin_pour_membre_rouge(
        self, conn, member_factory, tontine_factory, mock_wa_send
    ):
        membre_id, tontine_id = _seed_membre_fugitif(conn, member_factory, tontine_factory)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO admins_groupe (tontine_id, whatsapp, nom) VALUES (%s,%s,%s)",
            (tontine_id, "+237600000000", "Admin Test"),
        )
        conn.commit()

        bc.alerter_risques_bouffage_imminent()

        assert mock_wa_send.called, "le DM admin n'a jamais été envoyé pour un membre au score rouge"
        # Le message doit référencer le score de risque, pas juste être un envoi générique
        appel_args = mock_wa_send.call_args
        assert "risque" in appel_args.args[1].lower() or "score" in appel_args.args[1].lower()

    @pytest.mark.xfail(
        reason=(
            "Gap connu et documenté, non corrigé ici par décision produit explicite : "
            "niveau='erreur' (exception interne quelconque) est aujourd'hui traité "
            "EXACTEMENT comme 'vert' par tous les appelants (alerter_risques_bouffage_imminent "
            "et 2 autres sites d'appel) — un bug de scoring devient silencieusement "
            "zéro-risque au lieu d'être signalé pour revue manuelle. Ce test encode le "
            "comportement DÉSIRÉ (niveau erreur traité comme orange/rouge) et échoue "
            "tant que ce n'est pas corrigé — voir la note de suivi du plan de session."
        ),
        strict=False,
    )
    def test_erreur_devrait_etre_signalee_pas_ignoree(self, conn, member_factory, tontine_factory):
        membre_id = member_factory()
        tontine_id = tontine_factory()
        conn.close()  # force la première requête SQL à lever une exception
        result = bc.calculer_score_risque_fugue(conn, membre_id, tontine_id)
        assert result["niveau"] == "erreur"  # comportement réel actuel
        assert result["niveau"] in ("orange", "rouge"), (
            "comportement désiré : une panne interne du scoring doit être signalée "
            "pour revue manuelle, pas silencieusement traitée comme un membre sûr"
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestConcurrencyStress — DB requise, exclu par défaut (pytest.ini -m "not stress")
# ═══════════════════════════════════════════════════════════════════════════

def _worker_calculer_score(membre_id: int, tontine_id: int) -> dict:
    """Chaque worker prend SA PROPRE connexion du pool, comme en production."""
    worker_conn = bc.get_conn()
    try:
        return bc.calculer_score_risque_fugue(worker_conn, membre_id, tontine_id)
    finally:
        bc.release_conn(worker_conn)


@pytest.mark.stress
class TestConcurrencyStress:
    N_MEMBRES = 88  # dépasse volontairement maxconn=80 du pool — probe de saturation

    def test_88_membres_concurrents_sans_erreur_ni_deadlock(self, tontine_factory, member_factory):
        tontine_id = tontine_factory()
        membre_ids = [
            member_factory(score_confiance=max(0, 100 - i)) for i in range(self.N_MEMBRES)
        ]

        t0 = time.monotonic()
        resultats, erreurs = [], []
        with ThreadPoolExecutor(max_workers=self.N_MEMBRES) as ex:
            futures = {
                ex.submit(_worker_calculer_score, mid, tontine_id): mid for mid in membre_ids
            }
            for fut in as_completed(futures):
                try:
                    resultats.append(fut.result(timeout=30))
                except Exception as e:
                    erreurs.append(e)
        duree = time.monotonic() - t0

        assert not erreurs, (
            f"{len(erreurs)} erreur(s) non gérée(s) sous charge concurrente "
            f"(pool maxconn=80, {self.N_MEMBRES} appelants) : {erreurs[:3]}"
        )
        assert len(resultats) == self.N_MEMBRES
        for r in resultats:
            assert isinstance(r["score"], int)
            assert 0 <= r["score"] <= 100
            assert r["niveau"] in ("vert", "jaune", "orange", "rouge", "erreur")

        print(
            f"\n[stress] {self.N_MEMBRES} scorings concurrents en {duree:.2f}s "
            f"({self.N_MEMBRES / duree:.1f} req/s) — pool maxconn=80"
        )

    def test_membre_a_risque_declenche_alerte_meme_apres_charge(
        self, conn, member_factory, tontine_factory, mock_wa_send
    ):
        """Vérifie que le pipeline d'alerte fonctionne toujours normalement
        après un passage en charge — pas de connexion corrompue laissée
        derrière par les workers concurrents du test précédent."""
        membre_id, tontine_id = _seed_membre_fugitif(conn, member_factory, tontine_factory)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO admins_groupe (tontine_id, whatsapp, nom) VALUES (%s,%s,%s)",
            (tontine_id, "+237600000000", "Admin Test"),
        )
        conn.commit()

        bc.alerter_risques_bouffage_imminent()

        assert mock_wa_send.called
