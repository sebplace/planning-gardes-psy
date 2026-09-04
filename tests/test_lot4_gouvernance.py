"""Lot 4 — matrice route × action × rôle × ligne.

Gouvernance confirmée par le client le 04/09/2026 :

* responsable gardes 1 : actions opérationnelles et quotas de L1 ;
* responsable gardes 2 : idem sur L2 ;
* chef de service : les deux lignes et validation des quotas ;
* tous trois : simulations et brouillons ;
* publication finale et dérogations transversales : permissions explicites,
  attribuables et révocables, sans pouvoir implicite lié au simple accès à
  l'espace administratif ;
* consultation du journal : permission distincte, jamais automatique.

Les autorisations positives **et** les refus sont prouvés, dans l'interface
comme dans l'API. Données fictives.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import permissions
from app.services import permission_service

FONCTIONS = (
    permissions.RESP_L1,
    permissions.RESP_L2,
    permissions.CHEF_SERVICE,
)


def _avec(world, *codes, index: int = 0):
    utilisateur = world.user_of(world.seniors[index])
    for code in codes:
        permission_service.grant(world.session, utilisateur, code, world.admin)
    return utilisateur


def _client(world, utilisateur):
    world.session.commit()
    client = TestClient(app)
    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": utilisateur.email, "password": "demo"},
    )
    assert reponse.status_code == 200, reponse.text
    return client


# --------------------------------------------------------------------------- #
# Vocabulaire de la matrice
# --------------------------------------------------------------------------- #


def test_toutes_les_actions_sont_nommees_et_classees():
    assert len(permissions.ACTIONS) == 8
    for action in permissions.ACTIONS:
        assert permissions.ACTIONS_LIBELLES[action]
    classees = (
        set(permissions.ACTIONS_COMMUNES_AUX_FONCTIONS)
        | set(permissions.ACTIONS_PORTEES_PAR_LA_LIGNE)
        | set(permissions.ACTIONS_CHEF_DE_SERVICE)
        | set(permissions.ACTIONS_A_PERMISSION_EXPLICITE)
    )
    assert classees == set(permissions.ACTIONS)


def test_une_action_inconnue_est_refusee(world):
    with pytest.raises(permission_service.PermissionError_):
        permission_service.may(world.session, world.admin, "INVENTEE")


# --------------------------------------------------------------------------- #
# Simulations et brouillons : ouverts aux trois fonctions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fonction", FONCTIONS)
@pytest.mark.parametrize(
    "action", [permissions.ACTION_SIMULER, permissions.ACTION_BROUILLON]
)
def test_les_trois_fonctions_simulent_et_brouillonnent(world, fonction, action):
    utilisateur = _avec(world, fonction)
    assert permission_service.may(world.session, utilisateur, action) is True


@pytest.mark.parametrize(
    "action", [permissions.ACTION_SIMULER, permissions.ACTION_BROUILLON]
)
def test_un_medecin_ordinaire_ne_simule_pas(world, action):
    utilisateur = world.user_of(world.seniors[1])
    assert permission_service.may(world.session, utilisateur, action) is False


# --------------------------------------------------------------------------- #
# Actions portées par une ligne
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "action",
    [permissions.ACTION_OPERATIONNEL, permissions.ACTION_QUOTAS_SAISIR],
)
@pytest.mark.parametrize(
    "fonction, ligne, attendu",
    [
        (permissions.RESP_L1, "L1", True),
        (permissions.RESP_L1, "L2", False),
        (permissions.RESP_L2, "L2", True),
        (permissions.RESP_L2, "L1", False),
        (permissions.CHEF_SERVICE, "L1", True),
        (permissions.CHEF_SERVICE, "L2", True),
    ],
)
def test_matrice_action_role_ligne(world, action, fonction, ligne, attendu):
    utilisateur = _avec(world, fonction)
    assert permission_service.may(world.session, utilisateur, action, ligne) is attendu


# --------------------------------------------------------------------------- #
# Validation des quotas : chef de service seulement
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fonction, attendu",
    [
        (permissions.RESP_L1, False),
        (permissions.RESP_L2, False),
        (permissions.CHEF_SERVICE, True),
    ],
)
def test_seul_le_chef_valide_les_quotas(world, fonction, attendu):
    utilisateur = _avec(world, fonction)
    assert (
        permission_service.may(
            world.session, utilisateur, permissions.ACTION_QUOTAS_VALIDER
        )
        is attendu
    )


# --------------------------------------------------------------------------- #
# Aucun pouvoir implicite : publication, dérogations, journal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fonction", FONCTIONS)
@pytest.mark.parametrize(
    "action",
    [
        permissions.ACTION_PUBLIER,
        permissions.ACTION_DEROGER,
        permissions.ACTION_CONSULTER_AUDIT,
    ],
)
def test_aucun_pouvoir_implicite_lie_a_l_acces_administratif(world, fonction, action):
    """L'accès à l'espace administratif ne confère jamais ces trois actions."""
    utilisateur = _avec(world, fonction)
    assert permission_service.has_administrative_access(world.session, utilisateur)
    assert permission_service.may(world.session, utilisateur, action) is False


def test_la_publication_exige_sa_permission_explicite(world):
    utilisateur = _avec(world, permissions.CHEF_SERVICE)
    assert (
        permission_service.may(world.session, utilisateur, permissions.ACTION_PUBLIER)
        is False
    )
    permission_service.grant(
        world.session, utilisateur, permissions.PUBLICATION, world.admin
    )
    assert (
        permission_service.may(world.session, utilisateur, permissions.ACTION_PUBLIER)
        is True
    )


def test_la_publication_est_revocable(world):
    utilisateur = _avec(world, permissions.CHEF_SERVICE, permissions.PUBLICATION)
    assert permission_service.may(
        world.session, utilisateur, permissions.ACTION_PUBLIER
    )
    permission_service.revoke(
        world.session, utilisateur, permissions.PUBLICATION, world.admin
    )
    assert not permission_service.may(
        world.session, utilisateur, permissions.ACTION_PUBLIER
    )


def test_le_journal_ne_s_accorde_jamais_automatiquement(world):
    """Aucune fonction, ni aucune autre permission, ne l'ouvre par ricochet."""
    utilisateur = _avec(
        world,
        permissions.CHEF_SERVICE,
        permissions.PUBLICATION,
        permissions.GESTION_COMPTES,
    )
    assert not permission_service.may(
        world.session, utilisateur, permissions.ACTION_CONSULTER_AUDIT
    )
    permission_service.grant(
        world.session, utilisateur, permissions.CONSULTATION_AUDIT, world.admin
    )
    assert permission_service.may(
        world.session, utilisateur, permissions.ACTION_CONSULTER_AUDIT
    )


def test_une_permission_explicite_seule_n_ouvre_pas_l_espace_administratif(world):
    """Réciproque : détenir PUBLICATION ne donne pas l'accès administratif."""
    utilisateur = _avec(world, permissions.PUBLICATION)
    assert not permission_service.has_administrative_access(world.session, utilisateur)
    assert permission_service.may(
        world.session, utilisateur, permissions.ACTION_PUBLIER
    )
    assert not permission_service.may(
        world.session, utilisateur, permissions.ACTION_SIMULER
    )


# --------------------------------------------------------------------------- #
# Matrice lisible
# --------------------------------------------------------------------------- #


def test_la_matrice_est_complete_et_lisible(world):
    utilisateur = _avec(world, permissions.RESP_L1)
    matrice = {ligne["action"]: ligne for ligne in
               permission_service.action_matrix(world.session, utilisateur)}
    assert set(matrice) == set(permissions.ACTIONS)
    operationnel = matrice[permissions.ACTION_OPERATIONNEL]
    assert operationnel["par_ligne"] == {"L1": True, "L2": False}
    assert matrice[permissions.ACTION_PUBLIER]["exige_permission_explicite"] is True
    assert matrice[permissions.ACTION_SIMULER]["exige_permission_explicite"] is False


def test_l_administrateur_global_couvre_tout(world):
    matrice = permission_service.action_matrix(world.session, world.admin)
    for ligne in matrice:
        assert ligne["globale"] is True
        assert all(ligne["par_ligne"].values())


# --------------------------------------------------------------------------- #
# Effet réel sur les routes : positifs et refus, UI et API
# --------------------------------------------------------------------------- #


ROUTES_UI = (
    ("/admin", permissions.ACTION_SIMULER),
    ("/projections", permissions.ACTION_SIMULER),
    ("/admin/quotas", permissions.ACTION_QUOTAS_SAISIR),
)


@pytest.mark.parametrize("chemin, action", ROUTES_UI)
@pytest.mark.parametrize("fonction", FONCTIONS)
def test_routes_ui_positives(world, chemin, action, fonction):
    utilisateur = _avec(world, fonction)
    client = _client(world, utilisateur)
    assert client.get(chemin).status_code == 200


@pytest.mark.parametrize("chemin, action", ROUTES_UI)
def test_routes_ui_refusees_a_un_medecin_ordinaire(world, chemin, action):
    utilisateur = world.user_of(world.seniors[1])
    client = _client(world, utilisateur)
    reponse = client.get(chemin)
    assert reponse.status_code == 403
    assert permissions.ACTIONS_LIBELLES[action] in reponse.text


def test_api_generation_exige_le_brouillon(world):
    medecin = world.user_of(world.seniors[1])
    client = _client(world, medecin)
    reponse = client.post(
        "/api/v1/planning/generate", json={"quarter_id": world.quarter.id}
    )
    assert reponse.status_code == 403
    assert "brouillon" in reponse.json()["detail"].lower()


def test_api_generation_ouverte_a_une_fonction(world):
    utilisateur = _avec(world, permissions.RESP_L1)
    client = _client(world, utilisateur)
    reponse = client.post(
        "/api/v1/planning/generate", json={"quarter_id": world.quarter.id}
    )
    # 403 exclu : le droit est accordé. Le code métier peut refuser pour une
    # autre raison, ce qui n'est pas l'objet de ce test.
    assert reponse.status_code != 403


def test_api_journal_refuse_puis_accepte(world):
    utilisateur = _avec(world, permissions.CHEF_SERVICE)
    client = _client(world, utilisateur)
    assert client.get("/api/v1/audit/verify").status_code == 403

    permission_service.grant(
        world.session, utilisateur, permissions.CONSULTATION_AUDIT, world.admin
    )
    world.session.commit()
    assert client.get("/api/v1/audit/verify").status_code == 200


def test_les_deux_couches_repondent_identiquement(world):
    """Aucune action ne doit être plus permissive dans une couche que dans l'autre."""
    medecin = world.user_of(world.seniors[1])
    client = _client(world, medecin)
    assert client.get("/admin").status_code == 403
    assert (
        client.post(
            "/api/v1/planning/generate", json={"quarter_id": world.quarter.id}
        ).status_code
        == 403
    )
