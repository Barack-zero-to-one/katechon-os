# -*- coding: utf-8 -*-
"""
Tests du durcissement webhook (module pur webhook_security). Aucune infra.
Lancer :  python -m pytest test_webhook_security.py -v
"""
import webhook_security as ws


class _Horloge:
    """Horloge manuelle pour piloter le TTL du ReplayGuard sans dormir."""
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def avance(self, dt):
        self.t += dt


# ── constant_time_equal ─────────────────────────────────────────────────────
def test_cte_egaux():
    assert ws.constant_time_equal("s3cr3t-token", "s3cr3t-token") is True


def test_cte_differents():
    assert ws.constant_time_equal("s3cr3t-token", "wrong") is False


def test_cte_none_et_vide():
    assert ws.constant_time_equal(None, "x") is False
    assert ws.constant_time_equal("x", None) is False
    assert ws.constant_time_equal("", "") is True


def test_cte_unicode_ne_leve_pas():
    # L'ancien compare_digest sur str non-ASCII levait TypeError → 500.
    assert ws.constant_time_equal("clé-secrète-é", "clé-secrète-é") is True
    assert ws.constant_time_equal("clé-secrète-é", "autre") is False


def test_cte_bytes_et_str_melanges():
    assert ws.constant_time_equal(b"abc", "abc") is True


# ── media_url_autorisee (SSRF whitelist, hostname-based) ────────────────────
def test_ssrf_domaine_legit():
    assert ws.media_url_autorisee("https://media.green-api.com/x.jpg") is True
    assert ws.media_url_autorisee("https://a.b.whatsapp.net/img") is True
    assert ws.media_url_autorisee("https://files.sms.by/y") is True


def test_ssrf_domaine_exact_sans_sous_domaine():
    assert ws.media_url_autorisee("https://green-api.com/x") is True


def test_ssrf_scheme_non_https_rejete():
    assert ws.media_url_autorisee("http://media.green-api.com/x") is False
    assert ws.media_url_autorisee("file:///etc/passwd") is False
    assert ws.media_url_autorisee("gopher://green-api.com/") is False


def test_ssrf_suffixe_leurre_rejete():
    # frontière de label : evilgreen-api.com ne doit PAS matcher green-api.com
    assert ws.media_url_autorisee("https://evilgreen-api.com/x") is False
    assert ws.media_url_autorisee("https://green-api.com.evil.com/x") is False


def test_ssrf_userinfo_at_bypass_rejete():
    # host réel = evil.com, malgré le userinfo qui ressemble à un domaine ok
    assert ws.media_url_autorisee("https://media.green-api.com@evil.com/x") is False


def test_ssrf_ip_decimale_hex_rejetee():
    assert ws.media_url_autorisee("https://2130706433/") is False        # 127.0.0.1
    assert ws.media_url_autorisee("https://0x7f000001/") is False
    assert ws.media_url_autorisee("https://169.254.169.254/latest/meta-data/") is False


def test_ssrf_localhost_rejete():
    assert ws.media_url_autorisee("https://localhost:4040/api/tunnels") is False
    assert ws.media_url_autorisee("https://127.0.0.1/") is False


def test_ssrf_port_sur_domaine_ok_reste_ok():
    # hostname ignore le port → un domaine légit avec port explicite reste autorisé
    assert ws.media_url_autorisee("https://media.green-api.com:8443/x") is True


def test_ssrf_url_vide_ou_pourrie():
    assert ws.media_url_autorisee("") is False
    assert ws.media_url_autorisee("pas une url") is False


# ── ReplayGuard (anti-rejeu borné) ──────────────────────────────────────────
def test_replay_premier_passage_ok_puis_rejeu():
    h = _Horloge()
    g = ws.ReplayGuard(ttl=900, clock=h)
    assert g.est_rejeu("MSG_ABC") is False   # 1re fois
    assert g.est_rejeu("MSG_ABC") is True    # rejeu détecté
    assert g.est_rejeu("MSG_ABC") is True    # toujours rejeu dans la fenêtre


def test_replay_ids_distincts_independants():
    h = _Horloge()
    g = ws.ReplayGuard(ttl=900, clock=h)
    assert g.est_rejeu("A") is False
    assert g.est_rejeu("B") is False
    assert g.est_rejeu("A") is True
    assert g.est_rejeu("B") is True


def test_replay_expire_apres_ttl():
    h = _Horloge()
    g = ws.ReplayGuard(ttl=900, clock=h)
    assert g.est_rejeu("A") is False
    h.avance(901)                            # au-delà du TTL
    assert g.est_rejeu("A") is False         # re-accepté (fenêtre passée)


def test_replay_id_vide_fail_open():
    g = ws.ReplayGuard()
    assert g.est_rejeu("") is False
    assert g.est_rejeu(None) is False
    assert g.est_rejeu("") is False          # jamais dédupliqué faute d'id


def test_replay_borne_memoire():
    # cap dur : la taille ne dépasse jamais max_size malgré un flot d'ids uniques.
    h = _Horloge()
    g = ws.ReplayGuard(ttl=10_000, max_size=100, clock=h)
    for i in range(1000):
        g.est_rejeu(f"ID_{i}")
    assert g.taille() <= 100


def test_replay_purge_temporelle_libere():
    h = _Horloge()
    g = ws.ReplayGuard(ttl=100, max_size=10_000, clock=h)
    for i in range(50):
        g.est_rejeu(f"ID_{i}")
    assert g.taille() == 50
    h.avance(101)                            # tout expire
    g.est_rejeu("NOUVEAU")                   # déclenche la purge paresseuse
    assert g.taille() == 1
