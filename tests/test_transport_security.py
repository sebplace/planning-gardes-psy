"""Tests P0 : transport (HTTPS, cookie Secure, en-têtes) et séparation des
environnements (garde-fous de démarrage, verrou du seed destructif).

Données fictives uniquement. On reconstruit l'application avec un environnement
simulé (staging/production) via monkeypatch de la configuration.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services import environment as envsvc

STRONG_SECRET = "s" * 48


def _staging(monkeypatch):
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "secret_key", STRONG_SECRET)


# --- Démonstration : HTTP servi, Swagger public, en-têtes de base --------- #


def test_demo_sert_http_et_entetes():
    app = create_app()  # environnement 'demonstration' par défaut en test
    client = TestClient(app)
    r = client.get("/api/openapi.json")  # Swagger actif en démo
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "no-referrer"


def test_demo_pas_de_redirection_sans_proxy():
    app = create_app()
    client = TestClient(app)
    r = client.get("/connexion", follow_redirects=False)
    # En démo locale, HTTP est servi (pas de redirection).
    assert r.status_code == 200


# --- Déployé (staging/production) : redirection, cookie Secure, HSTS ------- #


def test_deploye_redirige_http_vers_https(monkeypatch):
    _staging(monkeypatch)
    app = create_app()
    client = TestClient(app)
    r = client.get(
        "/connexion", headers={"x-forwarded-proto": "http"}, follow_redirects=False
    )
    assert r.status_code == 308
    assert r.headers["location"].startswith("https://")


def test_deploye_hsts_present(monkeypatch):
    _staging(monkeypatch)
    app = create_app()
    client = TestClient(app)
    r = client.get("/connexion", follow_redirects=False)
    assert "max-age=" in (r.headers.get("Strict-Transport-Security") or "")


def test_deploye_cookie_session_secure(world, monkeypatch):
    _staging(monkeypatch)
    app = create_app()
    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    assert r.status_code == 200
    set_cookie = (r.headers.get("set-cookie") or "").lower()
    assert "session=" in set_cookie
    assert "secure" in set_cookie
    assert "httponly" in set_cookie


def test_deploye_swagger_desactive(monkeypatch):
    _staging(monkeypatch)
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_api_no_store(monkeypatch):
    _staging(monkeypatch)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/v1/auth/me", follow_redirects=False)
    # 401 sans session, mais l'en-tête no-store doit être posé sur /api.
    assert "no-store" in (r.headers.get("Cache-Control") or "")


# --- Séparation des environnements : garde-fous de démarrage / seed -------- #


def test_production_refuse_comptes_invalid(world, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", STRONG_SECRET)
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_startup_safe()
    assert "invalid" in str(exc.value).lower()


def test_production_refuse_secret_faible(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", envsvc.DEFAULT_SECRET)
    with pytest.raises(SystemExit) as exc:
        envsvc.assert_startup_safe()
    assert "secret" in str(exc.value).lower()


def test_seed_destructif_bloque_en_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(SystemExit):
        envsvc.assert_destructive_seed_allowed()


def test_demo_autorise_seed(world, monkeypatch):
    monkeypatch.setattr(settings, "environment", "demonstration")
    # Base de démonstration (comptes .invalid présents) : autorisé.
    envsvc.assert_destructive_seed_allowed()


def test_staging_autorise_seed(world, monkeypatch):
    monkeypatch.setattr(settings, "environment", "staging")
    # Staging fictif (comptes .invalid présents) : autorisé.
    envsvc.assert_destructive_seed_allowed()
