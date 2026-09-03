"""Tests P1.19 : six permissions distinctes, datées et traçables.

Données fictives.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import PERMISSIONS, AuditEvent, PermissionGrant, permissions
from app.services import permission_service
from app.services.clock import Clock


def test_les_six_permissions_sont_definies():
    assert len(PERMISSIONS) == 6
    assert set(PERMISSIONS) == {
        permissions.RESP_L1,
        permissions.RESP_L2,
        permissions.CHEF_SERVICE,
        permissions.GESTION_COMPTES,
        permissions.PUBLICATION,
        permissions.CONSULTATION_AUDIT,
    }
    for code in PERMISSIONS:
        assert permissions.LIBELLES[code]


def test_un_medecin_ordinaire_n_a_aucune_permission(world):
    utilisateur = world.user_of(world.seniors[0])
    for code in PERMISSIONS:
        assert not permission_service.has_permission(world.session, utilisateur, code)


def test_une_permission_accordee_n_en_donne_pas_d_autre(world):
    utilisateur = world.user_of(world.seniors[0])
    permission_service.grant(
        world.session, utilisateur, permissions.RESP_L1, world.admin
    )
    assert permission_service.has_permission(
        world.session, utilisateur, permissions.RESP_L1
    )
    for code in PERMISSIONS:
        if code != permissions.RESP_L1:
            assert not permission_service.has_permission(
                world.session, utilisateur, code
            )


def test_une_permission_est_datee(world):
    utilisateur = world.user_of(world.seniors[0])
    permission_service.grant(
        world.session,
        utilisateur,
        permissions.PUBLICATION,
        world.admin,
        start_date=date(2027, 6, 1),
    )
    assert not permission_service.has_permission(
        world.session, utilisateur, permissions.PUBLICATION, jour=date(2027, 5, 31)
    )
    assert permission_service.has_permission(
        world.session, utilisateur, permissions.PUBLICATION, jour=date(2027, 6, 1)
    )


def test_une_revocation_pose_une_date_de_fin_sans_rien_effacer(world):
    utilisateur = world.user_of(world.seniors[0])
    permission_service.grant(
        world.session,
        utilisateur,
        permissions.CHEF_SERVICE,
        world.admin,
        start_date=date(2027, 1, 1),
    )
    retirees = permission_service.revoke(
        world.session,
        utilisateur,
        permissions.CHEF_SERVICE,
        world.admin,
        end_date=date(2027, 3, 31),
    )
    assert retirees == 1
    ligne = world.session.execute(
        select(PermissionGrant).where(PermissionGrant.user_id == utilisateur.id)
    ).scalar_one()
    assert ligne.end_date == date(2027, 3, 31)
    assert ligne.active is False
    assert permission_service.has_permission(
        world.session, utilisateur, permissions.CHEF_SERVICE, jour=date(2027, 2, 1)
    ) is False


def test_les_mouvements_de_droits_sont_journalises(world):
    utilisateur = world.user_of(world.seniors[0])
    permission_service.grant(
        world.session, utilisateur, permissions.GESTION_COMPTES, world.admin
    )
    permission_service.revoke(
        world.session, utilisateur, permissions.GESTION_COMPTES, world.admin
    )
    types = [
        e.action
        for e in world.session.execute(select(AuditEvent)).scalars()
    ]
    assert "PERMISSION_ACCORDEE" in types
    assert "PERMISSION_RETIREE" in types


def test_un_administrateur_detient_tout(world):
    for code in PERMISSIONS:
        assert permission_service.has_permission(world.session, world.admin, code)
    matrice = permission_service.matrix(world.session, world.admin)
    assert all(ligne.accordee for ligne in matrice)
    assert all(ligne.origine == "administrateur" for ligne in matrice)


def test_la_matrice_est_lisible_pour_un_non_administrateur(world):
    utilisateur = world.user_of(world.seniors[0])
    permission_service.grant(
        world.session, utilisateur, permissions.RESP_L2, world.admin
    )
    matrice = {
        ligne.code: ligne
        for ligne in permission_service.matrix(world.session, utilisateur)
    }
    assert len(matrice) == 6
    assert matrice[permissions.RESP_L2].accordee is True
    assert matrice[permissions.RESP_L2].origine == "attribution datée"
    assert matrice[permissions.RESP_L1].accordee is False
    assert matrice[permissions.RESP_L1].origine == "aucune"


def test_une_permission_inconnue_est_refusee(world):
    utilisateur = world.user_of(world.seniors[0])
    with pytest.raises(permission_service.PermissionError_):
        permission_service.grant(
            world.session, utilisateur, "INVENTEE", world.admin
        )


# --------------------------------------------------------------------------- #
# Effet réel sur les routes
# --------------------------------------------------------------------------- #


def test_le_journal_d_audit_exige_le_droit_correspondant(world):
    session = world.session
    medecin = world.user_of(world.seniors[0])
    session.commit()
    client = TestClient(app)

    client.post(
        "/api/v1/auth/login", json={"email": medecin.email, "password": "demo"}
    )
    refus = client.get("/api/v1/audit/verify")
    assert refus.status_code == 403
    assert "Consultation du journal d'audit" in refus.json()["detail"]

    Clock.freeze(datetime(2027, 1, 15, 9, 0))
    permission_service.grant(
        session,
        medecin,
        permissions.CONSULTATION_AUDIT,
        world.admin,
        start_date=date(2027, 1, 1),
    )
    session.commit()
    accepte = client.get("/api/v1/audit/verify")
    assert accepte.status_code == 200
    assert accepte.json()["chaine_integre"] is True


def test_un_administrateur_garde_l_acces_au_journal(world):
    world.session.commit()
    client = TestClient(app)
    client.post(
        "/api/v1/auth/login",
        json={"email": world.admin.email, "password": "demo"},
    )
    assert client.get("/api/v1/audit/verify").status_code == 200
