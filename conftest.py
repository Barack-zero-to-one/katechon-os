"""
Fixtures pytest pour stress_test_katechon.py.

Pointe EXCLUSIVEMENT sur une base de données de test jetable (TEST_PG_*,
via .env.test — jamais ENV). Garde-fou explicite dans _pg_test_schema() :
refuse d'opérer si la DB résolue par barack_corp_v9_18 ne ressemble pas à
une DB de test (doit contenir "_test" dans son nom).

Les tests purs (TestNormalizationBoundaries dans stress_test_katechon.py)
ne dépendent d'aucune fixture ci-dessous et tournent sans DB, partout.
"""
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Charge .env.test AVANT d'importer barack_corp_v9_18 ────────────────────
# barack_corp_v9_18 lit PG_HOST/PG_PORT/PG_DB/PG_USER/PG_PASS via os.getenv()
# en tant que CONSTANTES DE MODULE, résolues une seule fois à l'import.
# Donc les variables d'environnement doivent être posées avant le premier
# `import barack_corp_v9_18` — ce qui doit arriver ici, au niveau module de
# ce conftest.py, puisque pytest charge conftest.py avant de collecter les
# fichiers de test du même dossier.
_ENV_TEST_FILE = Path(__file__).parent / ".env.test"
if _ENV_TEST_FILE.exists():
    for _line in _ENV_TEST_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _val = _line.partition("=")
        os.environ.setdefault(_key.strip(), _val.strip())

for _suffix in ("HOST", "PORT", "DB", "USER", "PASS"):
    _test_key = f"TEST_PG_{_suffix}"
    if _test_key in os.environ:
        os.environ[f"PG_{_suffix}"] = os.environ[_test_key]

import barack_corp_v9_18 as bc  # noqa: E402 — import différé volontairement, après les env vars


def _assert_safe_test_db():
    """Garde-fou dur : jamais exécuter les tests contre une DB qui pourrait être la prod."""
    resolved = getattr(bc, "PG_DB", "")
    if "_test" not in resolved:
        raise RuntimeError(
            f"GARDE-FOU: PG_DB résolu par barack_corp_v9_18 = '{resolved}' ne contient "
            "pas '_test'. Refus d'opérer — risque de toucher la DB de production. "
            "Vérifie .env.test / TEST_PG_DB (doit valoir un nom du style barack_corp_test)."
        )


# Tables vidées entre chaque test d'intégration — TRUNCATE ... CASCADE gère
# les foreign keys, l'ordre de la liste n'a pas besoin d'être topologique.
_TABLES_A_VIDER = [
    "alertes_fugue", "cautions_garantie", "dettes_ira",
    "historique_score_confiance", "sanctions", "cotisations_manuelles",
    "bouffages_manuels", "dettes_badf", "transactions", "liste_passage",
    "adhesions", "admins_groupe", "tontines", "membres",
]


@pytest.fixture(scope="session")
def _pg_test_schema():
    """Initialise le schéma une fois par session, sur la DB de test.
    Skip (pas d'échec dur) si la DB de test n'est pas joignable — un
    développeur sans Postgres local voit un skip explicite, jamais un
    passage silencieux."""
    _assert_safe_test_db()
    try:
        bc.init_db()
    except Exception as e:
        pytest.skip(f"DB de test injoignable ({bc.PG_HOST}:{bc.PG_PORT}/{bc.PG_DB}) : {e}")
    yield


@pytest.fixture
def clean_db(_pg_test_schema):
    """Vide toutes les tables avant ET après le test — isolation totale.
    TRUNCATE (pas rollback par transaction) car alerter_risques_bouffage_imminent()
    commit lui-même sur SA PROPRE connexion (obtenue via bc.get_conn() en interne) —
    un simple rollback autour d'une connexion différente ne défait pas ces commits."""
    def _truncate():
        c = bc.get_conn()
        try:
            c.cursor().execute(
                "TRUNCATE " + ", ".join(_TABLES_A_VIDER) + " RESTART IDENTITY CASCADE"
            )
            c.commit()
        finally:
            bc.release_conn(c)
    _truncate()
    yield
    _truncate()


@pytest.fixture
def conn(clean_db):
    """Connexion DB pour un test. release_conn() fait un rollback auto au retour au pool."""
    c = bc.get_conn()
    yield c
    bc.release_conn(c)


@pytest.fixture
def member_factory(conn):
    """Factory : insère un membre minimal valide, retourne son id.
    Commit explicite — nécessaire pour que d'autres connexions du pool
    (ex: celle ouverte en interne par alerter_risques_bouffage_imminent)
    voient la ligne."""
    def _make(**overrides):
        uid = uuid.uuid4().hex[:10]
        row = {
            "nom_complet":    overrides.pop("nom_complet", f"Membre {uid}"),
            "kyc_hash":       overrides.pop("kyc_hash", f"hash_{uid}"),
            "whatsapp":       overrides.pop("whatsapp", f"+2376{uid[:8]}"),
            "statut_global":  overrides.pop("statut_global", "Actif"),
            "score_confiance": overrides.pop("score_confiance", 100),
        }
        row.update(overrides)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["%s"] * len(row))
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO membres ({cols}) VALUES ({placeholders}) RETURNING id",
            list(row.values()),
        )
        membre_id = cur.fetchone()[0]
        conn.commit()
        return membre_id
    return _make


@pytest.fixture
def tontine_factory(conn):
    """Factory : insère une tontine minimale valide, retourne son id."""
    def _make(**overrides):
        uid = uuid.uuid4().hex[:10]
        row = {
            "nom":             overrides.pop("nom", f"Tontine {uid}"),
            "type_tontine":    overrides.pop("type_tontine", "Journaliere"),
            "montant_place":   overrides.pop("montant_place", 5000),
            "cycle_actuel":    overrides.pop("cycle_actuel", 1),
            "heure_ouverture": overrides.pop("heure_ouverture", "05:00"),
        }
        row.update(overrides)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["%s"] * len(row))
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO tontines ({cols}) VALUES ({placeholders}) RETURNING id",
            list(row.values()),
        )
        tontine_id = cur.fetchone()[0]
        conn.commit()
        return tontine_id
    return _make


@pytest.fixture
def mock_wa_send(monkeypatch):
    """Neutralise tout envoi WhatsApp réel — enregistre les appels à la place."""
    mock = MagicMock(return_value=True)
    monkeypatch.setattr(bc, "_wa_send", mock)
    return mock
