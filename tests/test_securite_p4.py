"""Tests P4 (tranche 2) : health, avancement de reprise réservé admin, bornes de
génération, neutralisation de l'injection CSV. Données fictives.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import planning_service
from app.web.routers.ui import _csv_safe


def _client():
    return TestClient(create_app())


def _login(client, email, password="demo"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return client


# --- Health --------------------------------------------------------------- #


def test_health_live():
    r = _client().get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_ready(world):
    r = _client().get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


# --- Avancement de reprise réservé aux administrateurs (P4.9) -------------- #


def test_avancement_reprise_refuse_au_medecin(world):
    client = _client()
    _login(client, world.user_of(world.seniors[0]).email)  # médecin non admin
    r = client.post("/api/v1/handover/requests/999999/advance")
    assert r.status_code == 403


def test_avancement_reprise_admin_passe_l_autorisation(world):
    client = _client()
    _login(client, world.admin.email)  # admin
    # 404 (demande inexistante) prouve que l'autorisation admin est franchie.
    r = client.post("/api/v1/handover/requests/999999/advance")
    assert r.status_code == 404


# --- Bornes de génération (P4.8) ------------------------------------------ #


def test_generation_variants_hors_bornes_service(world):
    with pytest.raises(ValueError):
        planning_service.run_engine(world.session, world.quarter, variants=5)
    with pytest.raises(ValueError):
        planning_service.run_engine(world.session, world.quarter, variants=0)


def test_generation_variants_hors_bornes_api(world):
    client = _client()
    _login(client, world.admin.email)
    r = client.post(
        "/api/v1/planning/generate",
        json={"quarter_id": world.quarter.id, "variants": 5},
    )
    assert r.status_code == 422


# --- Injection CSV (P4.12) ------------------------------------------------ #


def test_csv_safe_neutralise_formules():
    assert _csv_safe("=1+2") == "'=1+2"
    assert _csv_safe("+CMD") == "'+CMD"
    assert _csv_safe("-2") == "'-2"
    assert _csv_safe("@x") == "'@x"
    assert _csv_safe("\tsurdel") == "'\tsurdel"
    # Valeur normale inchangée.
    assert _csv_safe("SEN-01") == "SEN-01"
    assert _csv_safe(None) == ""
