"""Lot 1.5 — immuabilité d'une version publiée.

Contre-audit du 04/09/2026 : ``validate_version`` ne contrôlait pas l'état de
départ, donc la transition PUBLIE → VALIDE était possible, et ``set_lock``
acceptait d'écrire sur une version publiée.

Données fictives.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ScheduleState, permissions
from app.services import permission_service, planning_service
from tests.conftest import publish_plan


def _version_publiee(world):
    version = publish_plan(world)
    world.session.refresh(version)
    assert version.state is ScheduleState.PUBLIE
    return version


# --------------------------------------------------------------------------- #
# Transitions interdites, au niveau service
# --------------------------------------------------------------------------- #


def test_publie_ne_peut_pas_revenir_a_valide(world):
    version = _version_publiee(world)
    with pytest.raises(planning_service.ImmutableVersionError) as exc:
        planning_service.validate_version(world.session, version, world.admin)
    assert "PUBLIE" in str(exc.value)
    # Aucune écriture n'a eu lieu : l'état est inchangé en base.
    world.session.expire(version)
    assert version.state is ScheduleState.PUBLIE


def test_publie_ne_peut_pas_etre_republiee(world):
    version = _version_publiee(world)
    with pytest.raises(planning_service.PlanningError):
        planning_service.publish_version(world.session, version, world.admin)


def test_remplace_est_definitivement_fige(world):
    version = _version_publiee(world)
    version.state = ScheduleState.REMPLACE
    world.session.flush()
    for cible in ScheduleState:
        with pytest.raises(planning_service.ImmutableVersionError):
            planning_service.assert_transition_allowed(version, cible)


@pytest.mark.parametrize("etat", [ScheduleState.PUBLIE, ScheduleState.REMPLACE])
def test_aucune_ecriture_sur_une_version_figee(world, etat):
    version = _version_publiee(world)
    version.state = etat
    world.session.flush()
    poste_id = version.assignments[0].post_id

    with pytest.raises(planning_service.ImmutableVersionError):
        planning_service.set_lock(
            world.session, version, poste_id, True, world.admin
        )
    with pytest.raises(planning_service.ImmutableVersionError):
        planning_service.assert_version_mutable(version, "test")


def test_la_table_des_transitions_est_exhaustive():
    """Chaque état connu déclare explicitement ses transitions autorisées."""
    assert set(planning_service.TRANSITIONS_AUTORISEES) == set(ScheduleState)
    assert planning_service.TRANSITIONS_AUTORISEES[ScheduleState.PUBLIE] == (
        ScheduleState.REMPLACE,
    )
    assert planning_service.TRANSITIONS_AUTORISEES[ScheduleState.REMPLACE] == ()


def test_une_correction_passe_par_une_nouvelle_version(world):
    """Le chemin légitime : cloner, corriger, revalider, republier."""
    version = _version_publiee(world)
    clone = planning_service.clone_version_for_edit(
        world.session, version, world.admin, "correction apres publication"
    )
    assert clone.id != version.id
    assert clone.state is ScheduleState.EN_REVISION
    planning_service.assert_version_mutable(clone, "correction manuelle")
    # L'originale reste intacte.
    world.session.refresh(version)
    assert version.state is ScheduleState.PUBLIE


# --------------------------------------------------------------------------- #
# Mêmes refus en interface et en API
# --------------------------------------------------------------------------- #


def _client_admin(world):
    world.session.commit()
    client = TestClient(app)
    client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    return client


def test_api_refuse_une_correction_sur_version_publiee(world):
    version = _version_publiee(world)
    affectation = version.assignments[0]
    autre = next(
        p
        for p in world.seniors + world.assistants
        if p.id != affectation.profile_id
    )
    client = _client_admin(world)
    reponse = client.post(
        f"/api/v1/planning/versions/{version.id}/assignments",
        json={
            "post_id": affectation.post_id,
            "profile_id": autre.id,
            "reason": "tentative sur version publiee",
        },
    )
    assert reponse.status_code == 409
    assert "nouvelle version" in reponse.json()["detail"]


def test_interface_refuse_de_valider_une_version_publiee(world):
    version = _version_publiee(world)
    permission_service.grant(
        world.session, world.admin, permissions.PUBLICATION, world.admin
    )
    client = _client_admin(world)
    reponse = client.post(
        f"/admin/version/{version.id}/action",
        data={"action": "valider"},
        follow_redirects=True,
    )
    assert reponse.status_code == 200
    assert "interdite" in reponse.text or "n'est plus modifiable" in reponse.text
    world.session.expire_all()
    version = world.session.get(type(version), version.id)
    assert version.state is ScheduleState.PUBLIE


def test_interface_refuse_de_verrouiller_sur_version_publiee(world):
    version = _version_publiee(world)
    poste_id = version.assignments[0].post_id
    avant = version.assignments[0].is_locked
    client = _client_admin(world)
    reponse = client.post(
        f"/admin/version/{version.id}/action",
        data={"action": "verrouiller", "post_id": str(poste_id)},
        follow_redirects=True,
    )
    assert reponse.status_code == 200
    # Le refus est signalé, et surtout rien n'a été écrit.
    world.session.expire_all()
    version = world.session.get(type(version), version.id)
    assert version.state is ScheduleState.PUBLIE
    affectation = next(a for a in version.assignments if a.post_id == poste_id)
    assert affectation.is_locked == avant
