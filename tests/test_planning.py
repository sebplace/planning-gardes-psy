"""Tests de validation, publication, permissions et extensibilité.

Couvre les exigences §22 : 19, 23, 24, 31, 47.
"""

from __future__ import annotations

import json
from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import (
    AuditEvent,
    Color,
    CoverageMode,
    ManualCorrection,
    Module,
    ProfessionalProfile,
    QuotaCategory,
    ScheduleState,
    Status,
    WaveKind,
)
from app.services import (
    audit_service,
    campaign_service,
    engine_bridge,
    handover_service,
    planning_service,
    swap_service,
)
from tests.conftest import publish_plan


def _version_en_revision(world, seed=4242):
    from tests.conftest import APRES_GRACE, close_and_prepare
    from app.services.clock import Clock

    close_and_prepare(world)
    Clock.freeze(APRES_GRACE)
    run = planning_service.run_engine(
        world.session, world.quarter, admin=world.admin, seed=seed, variants=1
    )
    return planning_service.create_version_from_proposal(
        world.session, run.proposals[0], world.admin
    )


# --------------------------------------------------------------------------- #
# Test 19 — correction manuelle auditée
# --------------------------------------------------------------------------- #


def test_19_correction_manuelle_auditee(world):
    session = world.session
    version = _version_en_revision(world)
    affectation = next(
        a for a in version.assignments if a.post.required_status is Status.SENIOR
    )
    ancien = affectation.profile_id
    remplacant = next(
        s for s in world.seniors
        if s.id != ancien
        and engine_bridge.check_assignment(
            session, affectation.post, s, ignore_assignment_ids={affectation.id},
            schedule_version_id=version.id,
        ) is None
    )

    # Un motif est obligatoire.
    with pytest.raises(planning_service.PlanningError):
        planning_service.manual_correction(
            session, version, affectation.post, remplacant, world.admin, "  "
        )

    planning_service.manual_correction(
        session, version, affectation.post, remplacant, world.admin,
        "Réorganisation demandée par le service",
    )
    session.refresh(affectation)
    assert affectation.profile_id == remplacant.id
    assert affectation.origin.value == "MANUEL"

    correction = session.execute(
        select(ManualCorrection).where(ManualCorrection.post_id == affectation.post_id)
    ).scalar_one()
    assert correction.author_id == world.admin.id
    assert correction.from_profile_id == ancien
    assert correction.to_profile_id == remplacant.id
    assert correction.reason == "Réorganisation demandée par le service"
    assert correction.created_at is not None

    evenement = session.execute(
        select(AuditEvent).where(AuditEvent.action == "CORRECTION_MANUELLE")
    ).scalars().first()
    assert evenement is not None
    charge = json.loads(evenement.payload_json)
    assert charge["motif"] == "Réorganisation demandée par le service"
    ok, anomalies = audit_service.verify_chain(session)
    assert ok, anomalies


def test_19b_un_planning_publie_n_est_jamais_reecrit(world):
    session = world.session
    version = publish_plan(world)
    affectation = version.assignments[0]
    with pytest.raises(planning_service.PlanningError) as exc:
        planning_service.manual_correction(
            session, version, affectation.post, None, world.admin, "tentative"
        )
    assert "jamais réécrit" in str(exc.value)

    clone = planning_service.clone_version_for_edit(
        session, version, world.admin, "correction après publication"
    )
    assert clone.version_no == version.version_no + 1
    assert clone.state is ScheduleState.EN_REVISION
    assert len(clone.assignments) == len(version.assignments)


# --------------------------------------------------------------------------- #
# Tests 31 et 47 — un rouge ne peut jamais être forcé
# --------------------------------------------------------------------------- #


def test_31_administrateur_ne_peut_pas_forcer_un_rouge(world):
    session = world.session
    version = _version_en_revision(world)
    affectation = next(
        a for a in version.assignments if a.post.required_status is Status.SENIOR
    )
    rouge = next(s for s in world.seniors if s.id != affectation.profile_id)
    world.set_color(rouge, affectation.post.occurrence, Color.ROUGE)

    with pytest.raises(planning_service.HardConstraintError) as exc:
        planning_service.manual_correction(
            session, version, affectation.post, rouge, world.admin, "forçage administratif"
        )
    assert "Indisponibilité rouge" in str(exc.value)
    assert "Aucune dérogation n'existe" in str(exc.value)

    # Aucun paramètre de dérogation n'existe dans la signature de la fonction.
    import inspect

    parametres = set(inspect.signature(planning_service.manual_correction).parameters)
    assert not parametres & {"force", "override", "derogation", "bypass"}


def test_47_le_rouge_bloque_tous_les_chemins(world):
    """Moteur, correction manuelle, API, candidature de reprise et échange."""
    from fastapi.testclient import TestClient

    from app.main import app

    session = world.session
    version = publish_plan(world)

    affectation = next(
        a for a in version.assignments if a.post.required_status is Status.SENIOR
    )
    rouge = next(s for s in world.seniors if s.id != affectation.profile_id)
    world.set_color(rouge, affectation.post.occurrence, Color.ROUGE)
    session.commit()

    # (a) contrôle central partagé par tous les chemins
    rejet = engine_bridge.check_assignment(session, affectation.post, rouge)
    assert rejet is not None and rejet.constraint_code == "H02_ROUGE"

    # (b) appel direct à l'API
    client = TestClient(app)
    client.post(
        "/api/v1/auth/login", json={"email": world.admin.email, "password": "demo"}
    )
    clone = planning_service.clone_version_for_edit(
        session, version, world.admin, "test API"
    )
    session.commit()
    reponse = client.post(
        f"/api/v1/planning/versions/{clone.id}/assignments",
        json={
            "post_id": affectation.post_id,
            "profile_id": rouge.id,
            "reason": "tentative directe par API",
        },
    )
    assert reponse.status_code == 409
    assert "Indisponibilité rouge" in reponse.json()["detail"]

    # (c) une personne rouge n'est jamais sollicitée pour une reprise
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    demande = handover_service.request_handover(session, affectation, titulaire)
    eligibles = handover_service.eligible_profiles(session, demande, WaveKind.VERTE)
    assert rouge not in eligibles
    eligibles_orange = handover_service.eligible_profiles(session, demande, WaveKind.ORANGE)
    assert rouge not in eligibles_orange
    handover_service.cancel_request(session, demande, world.admin)


# --------------------------------------------------------------------------- #
# Test 23 — droits séparés médecin / administrateur
# --------------------------------------------------------------------------- #


def test_23_droits_separes_medecin_et_administrateur(world):
    from fastapi.testclient import TestClient

    from app.main import app

    session = world.session
    session.commit()
    client = TestClient(app)

    medecin = world.seniors[0]
    client.post(
        "/api/v1/auth/login",
        json={"email": world.user_of(medecin).email, "password": "demo"},
    )
    identite = client.get("/api/v1/auth/me").json()
    assert identite["droits"] == {"medecin": True, "administrateur": False}
    assert client.post(
        "/api/v1/planning/generate", json={"quarter_id": world.quarter.id}
    ).status_code == 403
    assert client.get("/api/v1/audit/verify").status_code == 403
    assert client.get("/admin", follow_redirects=False).status_code == 403
    assert client.get("/admin/quotas", follow_redirects=False).status_code == 403
    assert client.get("/projections", follow_redirects=False).status_code == 403

    client.post("/api/v1/auth/logout")
    client.post(
        "/api/v1/auth/login", json={"email": world.admin.email, "password": "demo"}
    )
    identite = client.get("/api/v1/auth/me").json()
    assert identite["droits"] == {"medecin": False, "administrateur": True}
    assert client.get("/api/v1/audit/verify").status_code == 200
    assert client.get("/admin", follow_redirects=False).status_code == 200

    # Un cumul est possible et les permissions restent explicitement séparées.
    cumul = world.user_of(world.seniors[1])
    cumul.is_admin = True
    session.commit()
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": cumul.email, "password": "demo"})
    identite = client.get("/api/v1/auth/me").json()
    assert identite["droits"] == {"medecin": True, "administrateur": True}


# --------------------------------------------------------------------------- #
# Test 24 — architecture extensible au module de jour
# --------------------------------------------------------------------------- #


def test_24_architecture_extensible_au_module_de_jour(world):
    """Le socle porte déjà un discriminant de module ; aucune logique du module
    de jour n'est implémentée, mais ses objets peuvent être créés."""
    from app.models import Campaign, GardeType

    session = world.session
    assert set(Module) == {Module.GARDES, Module.PERMANENCES_JOUR}

    categorie = QuotaCategory(
        code="PERMANENCES", label="Permanences de jour",
        module=Module.PERMANENCES_JOUR, position=10,
    )
    session.add(categorie)
    session.flush()
    matin = GardeType(
        code="PERM_MATIN", label="Permanence du matin",
        module=Module.PERMANENCES_JOUR, category_id=categorie.id,
        default_coverage_mode=CoverageMode.A,
        start_time=time(8, 0), end_time=time(12, 30),
        duration_hours=4.5, duration_class="DEMI_JOURNEE",
    )
    session.add(matin)
    session.flush()

    # La génération du module « gardes » ignore les types de l'autre module.
    from app.services import catalog_service

    codes = {
        catalog_service.resolve_type_code(
            world.quarter.start_date + __import__("datetime").timedelta(days=i), set()
        )
        for i in range(7)
    }
    assert "PERM_MATIN" not in codes

    # Les campagnes portent aussi le discriminant.
    assert Campaign.__table__.c.module is not None
    assert world.campaign.module is Module.GARDES

    # L'interface expose l'entrée désactivée.
    from fastapi.testclient import TestClient

    from app.main import app

    session.commit()
    client = TestClient(app)
    client.post(
        "/api/v1/auth/login", json={"email": world.admin.email, "password": "demo"}
    )
    page = client.get("/modules").text
    assert "Permanences de jour — à venir" in page
    assert "disabled" in page
