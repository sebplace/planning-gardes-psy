"""Lot 1.1 — le périmètre de ligne est une garde **métier**, pas une garde d'écran.

Contre-audit du 04/09/2026 : l'interface vérifiait ``supervises_line`` mais l'API
ne vérifiait qu'un accès administratif général, donc le contrôle était
contournable. Le contrôle vit désormais dans ``handover_service``, appelé
identiquement par les deux couches.

Données fictives.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Line, ProfessionalProfile, permissions
from app.services import handover_service, permission_service
from tests.conftest import publish_plan


def _demande_sur_ligne(world, ligne: Line):
    session = world.session
    for affectation in sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    ):
        session.refresh(affectation)
        if affectation.busy_operation is not None or affectation.post.line is not ligne:
            continue
        titulaire = session.get(ProfessionalProfile, affectation.profile_id)
        return handover_service.request_handover(session, affectation, titulaire)
    pytest.skip(f"aucune garde disponible sur la ligne {ligne.value}")


def _avec_fonction(world, index: int, code: str):
    utilisateur = world.user_of(world.seniors[index])
    permission_service.grant(world.session, utilisateur, code, world.admin)
    return utilisateur


# --------------------------------------------------------------------------- #
# Niveau service : la garde existe au bon endroit
# --------------------------------------------------------------------------- #


def test_la_garde_de_ligne_vit_dans_le_service(world):
    publish_plan(world)
    demande = _demande_sur_ligne(world, Line.L2)
    resp1 = _avec_fonction(world, 0, permissions.RESP_L1)

    with pytest.raises(handover_service.HandoverPermissionError):
        handover_service.advance(
            world.session, demande, actor=resp1, enforce_permissions=True
        )


def test_le_service_expose_la_ligne_concernee(world):
    publish_plan(world)
    demande = _demande_sur_ligne(world, Line.L1)
    assert handover_service.line_of(demande) == "L1"


def test_un_appel_interne_n_exige_aucun_droit(world):
    """Le jeu de démonstration et les enchaînements mécaniques restent possibles."""
    publish_plan(world)
    demande = _demande_sur_ligne(world, Line.L1)
    handover_service.advance(world.session, demande)  # sans acteur, sans contrôle
    world.session.refresh(demande)
    assert demande.waves


# --------------------------------------------------------------------------- #
# Matrice complète, dans les deux couches
# --------------------------------------------------------------------------- #


def _client_pour(world, utilisateur):
    world.session.commit()
    client = TestClient(app)
    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": utilisateur.email, "password": "demo"},
    )
    assert reponse.status_code == 200, reponse.text
    return client


@pytest.mark.parametrize(
    "fonction, ligne, attendu",
    [
        (permissions.RESP_L1, Line.L1, 200),
        (permissions.RESP_L1, Line.L2, 403),
        (permissions.RESP_L2, Line.L2, 200),
        (permissions.RESP_L2, Line.L1, 403),
        (permissions.CHEF_SERVICE, Line.L1, 200),
        (permissions.CHEF_SERVICE, Line.L2, 200),
    ],
)
def test_matrice_api_avancer_une_reprise(world, fonction, ligne, attendu):
    publish_plan(world)
    demande = _demande_sur_ligne(world, ligne)
    utilisateur = _avec_fonction(world, 0, fonction)
    client = _client_pour(world, utilisateur)

    reponse = client.post(f"/api/v1/handover/requests/{demande.id}/advance")
    assert reponse.status_code == attendu, reponse.text
    if attendu == 403:
        assert "ligne" in reponse.json()["detail"]


@pytest.mark.parametrize(
    "fonction, ligne, refus_attendu",
    [
        (permissions.RESP_L1, Line.L1, False),
        (permissions.RESP_L1, Line.L2, True),
        (permissions.RESP_L2, Line.L2, False),
        (permissions.RESP_L2, Line.L1, True),
        (permissions.CHEF_SERVICE, Line.L1, False),
        (permissions.CHEF_SERVICE, Line.L2, False),
    ],
)
def test_matrice_interface_avancer_une_reprise(world, fonction, ligne, refus_attendu):
    publish_plan(world)
    demande = _demande_sur_ligne(world, ligne)
    utilisateur = _avec_fonction(world, 0, fonction)
    client = _client_pour(world, utilisateur)

    reponse = client.post(
        f"/reprises/{demande.id}/avancer", follow_redirects=False
    )
    if refus_attendu:
        assert reponse.status_code == 403, reponse.text
    else:
        assert reponse.status_code == 303, reponse.text


def test_les_deux_couches_repondent_pareil(world):
    """Le point du contre-audit : plus d'écart entre interface et API."""
    publish_plan(world)
    demande = _demande_sur_ligne(world, Line.L2)
    resp1 = _avec_fonction(world, 0, permissions.RESP_L1)
    client = _client_pour(world, resp1)

    api = client.post(f"/api/v1/handover/requests/{demande.id}/advance")
    ui = client.post(f"/reprises/{demande.id}/avancer", follow_redirects=False)
    assert api.status_code == 403
    assert ui.status_code == 403


def test_un_medecin_ordinaire_est_refuse_dans_les_deux_couches(world):
    publish_plan(world)
    demande = _demande_sur_ligne(world, Line.L1)
    medecin = world.user_of(world.seniors[1])
    client = _client_pour(world, medecin)
    assert client.post(
        f"/api/v1/handover/requests/{demande.id}/advance"
    ).status_code == 403
    assert client.post(
        f"/reprises/{demande.id}/avancer", follow_redirects=False
    ).status_code == 403


def test_la_revocation_coupe_l_acces_dans_les_deux_couches(world):
    publish_plan(world)
    demande = _demande_sur_ligne(world, Line.L1)
    resp1 = _avec_fonction(world, 0, permissions.RESP_L1)
    client = _client_pour(world, resp1)
    assert client.post(
        f"/api/v1/handover/requests/{demande.id}/advance"
    ).status_code == 200

    permission_service.revoke(
        world.session, resp1, permissions.RESP_L1, world.admin
    )
    world.session.commit()
    assert client.post(
        f"/api/v1/handover/requests/{demande.id}/advance"
    ).status_code == 403
