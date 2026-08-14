# -*- coding: utf-8 -*-
"""
Tests du dashboard extrait (module dashboard). Flask test client + fausse DB.
Vérifie l'auth par token/session et le flux /dashboard/data de bout en bout,
sans PostgreSQL. Lancer :  python -m pytest test_dashboard.py -v
"""
from datetime import datetime

import pytest
from flask import Flask

import dashboard


class _FakeCursor:
    """Curseur minimal : ignore le SQL, renvoie des résultats neutres pour que
    dashboard_data se déroule intégralement et retourne 200."""
    description = [("c",)] * 8

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,) * 8


class _FakeConn:
    def cursor(self):
        return _FakeCursor()


def _make_app(token="S3CR3T"):
    app = Flask(__name__)
    app.secret_key = "test-key"
    dashboard.register_dashboard(
        app,
        get_conn=lambda: _FakeConn(),
        release_conn=lambda c: None,
        bot_start=datetime.now(),
        dash_token=token,
        logger=__import__("logging").getLogger("test"),
    )
    return app


def test_routes_enregistrees():
    app = _make_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/dashboard" in rules and "/dashboard/data" in rules


def test_dashboard_refuse_sans_token():
    c = _make_app().test_client()
    r = c.get("/dashboard")
    assert r.status_code == 401
    assert b"REFUS" in r.data


def test_dashboard_mauvais_token():
    c = _make_app(token="S3CR3T").test_client()
    r = c.get("/dashboard?token=WRONG")
    assert r.status_code == 401


def test_dashboard_bon_token_ouvre_session():
    c = _make_app(token="S3CR3T").test_client()
    r = c.get("/dashboard?token=S3CR3T")
    assert r.status_code == 200
    assert b"<html" in r.data.lower() or len(r.data) > 0


def test_dashboard_token_vide_refuse_tout():
    # dash_token vide → aucun accès possible même avec ?token=
    c = _make_app(token="").test_client()
    assert c.get("/dashboard?token=").status_code == 401


def test_data_refuse_sans_session():
    c = _make_app().test_client()
    assert c.get("/dashboard/data").status_code == 401


def test_data_ok_apres_auth():
    # Flux complet : /dashboard avec token ouvre la session, puis /dashboard/data passe.
    c = _make_app(token="S3CR3T").test_client()
    assert c.get("/dashboard?token=S3CR3T").status_code == 200
    r = c.get("/dashboard/data")
    assert r.status_code == 200
    body = r.get_json()
    for k in ("uptime", "tontines", "projection", "revenus", "cotis_attente",
              "bouffages", "ira_total", "gmv_jour", "gmv_total", "activite"):
        assert k in body
    assert r.headers.get("Cache-Control") == "no-store"
