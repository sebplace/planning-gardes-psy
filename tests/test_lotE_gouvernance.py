"""Lot E du contre-audit du 04/09/2026 — gouvernance et documents.

Trois vérifications exécutables :

1. les **vraies** routes d'écriture de quotas existent et respectent le
   périmètre objet × ligne, dans l'interface **et** dans l'API ;
2. publication, dérogation et consultation du journal restent des permissions
   séparées, jamais conférées par le simple accès administratif ;
3. la documentation ne contredit plus le registre canonique, notamment sur la
   priorité de collecte de deuxième ligne, déjà acquise.

Données entièrement fictives.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Line, permissions
from app.services import permission_service
from tests.conftest import publish_plan

RACINE = Path(__file__).resolve().parents[1]
MOT_DE_PASSE = "demo"


def _client(world, utilisateur) -> TestClient:
    world.session.commit()
    client = TestClient(app)
    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": utilisateur.email, "password": MOT_DE_PASSE},
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _avec_fonction(world, index: int, code: str):
    utilisateur = world.user_of(world.seniors[index])
    permission_service.grant(world.session, utilisateur, code, world.admin)
    world.session.commit()
    return utilisateur


# --------------------------------------------------------------------------- #
# E.1 — écriture de quotas, périmètre objet × ligne
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fonction, ligne, attendu",
    [
        (permissions.RESP_L1, "L1", 200),
        (permissions.RESP_L1, "L2", 403),
        (permissions.RESP_L2, "L2", 200),
        (permissions.RESP_L2, "L1", 403),
        (permissions.CHEF_SERVICE, "L1", 200),
        (permissions.CHEF_SERVICE, "L2", 200),
    ],
)
def test_E1_api_ecriture_de_quota_par_ligne(world, fonction, ligne, attendu):
    utilisateur = _avec_fonction(world, 0, fonction)
    client = _client(world, utilisateur)
    reponse = client.post(
        "/api/v1/quotas/targets",
        json={
            "profile_id": world.seniors[1].id,
            "category_code": "NUITS_LJ",
            "ligne": ligne,
            "cible": 7.0,
        },
    )
    assert reponse.status_code == attendu, reponse.text
    if attendu == 403:
        assert ligne in reponse.json()["detail"]
    else:
        assert reponse.json()["valide_institutionnellement"] is False


@pytest.mark.parametrize(
    "fonction, ligne, refus_attendu",
    [
        (permissions.RESP_L1, "L1", False),
        (permissions.RESP_L1, "L2", True),
        (permissions.RESP_L2, "L2", False),
        (permissions.RESP_L2, "L1", True),
        (permissions.CHEF_SERVICE, "L1", False),
        (permissions.CHEF_SERVICE, "L2", False),
    ],
)
def test_E1_interface_ecriture_de_quota_par_ligne(world, fonction, ligne, refus_attendu):
    utilisateur = _avec_fonction(world, 0, fonction)
    client = _client(world, utilisateur)
    reponse = client.post(
        "/admin/quotas/cible",
        data={
            "profile_id": world.seniors[1].id,
            "category_code": "NUITS_LJ",
            "ligne": ligne,
            "cible": 7.0,
            "commentaire": "",
        },
        follow_redirects=False,
    )
    if refus_attendu:
        assert reponse.status_code == 403, reponse.text
    else:
        assert reponse.status_code == 303, reponse.text


def test_E1_la_validation_institutionnelle_est_reservee_au_chef(world):
    """Le responsable saisit, seul le chef de service valide."""
    resp = _avec_fonction(world, 0, permissions.RESP_L1)
    client = _client(world, resp)
    ecriture = client.post(
        "/api/v1/quotas/targets",
        json={
            "profile_id": world.seniors[1].id,
            "category_code": "NUITS_LJ",
            "ligne": "L1",
            "cible": 9.0,
        },
    )
    assert ecriture.status_code == 200, ecriture.text
    refus = client.post(
        "/api/v1/quotas/targets/validate",
        json={
            "profile_id": world.seniors[1].id,
            "category_code": "NUITS_LJ",
            "ligne": "L1",
        },
    )
    assert refus.status_code == 403, refus.text

    chef = _avec_fonction(world, 1, permissions.CHEF_SERVICE)
    client_chef = _client(world, chef)
    accepte = client_chef.post(
        "/api/v1/quotas/targets/validate",
        json={
            "profile_id": world.seniors[1].id,
            "category_code": "NUITS_LJ",
            "ligne": "L1",
        },
    )
    assert accepte.status_code == 200, accepte.text
    assert accepte.json()["valide_institutionnellement"] is True


def test_E1_un_medecin_ordinaire_n_ecrit_aucun_quota(world):
    client = _client(world, world.user_of(world.seniors[2]))
    reponse = client.post(
        "/api/v1/quotas/targets",
        json={
            "profile_id": world.seniors[1].id,
            "category_code": "NUITS_LJ",
            "ligne": "L1",
            "cible": 5.0,
        },
    )
    assert reponse.status_code == 403, reponse.text


# --------------------------------------------------------------------------- #
# E.2 — permissions séparées
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "action",
    [
        permissions.ACTION_PUBLIER,
        permissions.ACTION_DEROGER,
        permissions.ACTION_CONSULTER_AUDIT,
    ],
)
def test_E2_les_permissions_explicites_ne_sont_jamais_implicites(world, action):
    """Aucune des trois fonctions ne les obtient par le seul accès administratif."""
    session = world.session
    for fonction in (
        permissions.RESP_L1,
        permissions.RESP_L2,
        permissions.CHEF_SERVICE,
    ):
        utilisateur = world.user_of(world.seniors[0])
        permission_service.grant(session, utilisateur, fonction, world.admin)
        session.flush()
        assert permission_service.may(session, utilisateur, action) is False, (
            f"{fonction} ne doit pas conférer {action}"
        )
        permission_service.revoke(session, utilisateur, fonction, world.admin)
        session.flush()


def test_E2_une_permission_explicite_est_attribuable_et_revocable(world):
    session = world.session
    utilisateur = world.user_of(world.seniors[0])
    permission_service.grant(session, utilisateur, permissions.CHEF_SERVICE, world.admin)
    permission_service.grant(session, utilisateur, permissions.PUBLICATION, world.admin)
    session.flush()
    assert permission_service.may(session, utilisateur, permissions.ACTION_PUBLIER)
    permission_service.revoke(session, utilisateur, permissions.PUBLICATION, world.admin)
    session.flush()
    assert not permission_service.may(session, utilisateur, permissions.ACTION_PUBLIER)


def test_E2_la_matrice_couvre_toutes_les_actions(world):
    session = world.session
    utilisateur = _avec_fonction(world, 0, permissions.RESP_L2)
    matrice = permission_service.action_matrix(session, utilisateur)
    couvertes = {ligne["action"] for ligne in matrice}
    assert couvertes == set(permissions.ACTIONS)
    par_action = {ligne["action"]: ligne for ligne in matrice}
    assert par_action[permissions.ACTION_QUOTAS_SAISIR]["par_ligne"]["L2"] is True
    assert par_action[permissions.ACTION_QUOTAS_SAISIR]["par_ligne"]["L1"] is False


# --------------------------------------------------------------------------- #
# E.3 — documents réconciliés avec le registre canonique
# --------------------------------------------------------------------------- #


DOCUMENTS = [
    RACINE / "README.md",
    RACINE / "OPEN_QUESTIONS.md",
    RACINE / "docs" / "AUDIT_DIFFERENTIEL.md",
]


def test_E3_les_documents_existent():
    for chemin in DOCUMENTS:
        assert chemin.exists(), chemin


def test_E3_la_regle_canonique_de_collecte_L2_est_ecrite_partout():
    """Priorité L2 acquise : collecte commune, verts d'abord, orange à défaut."""
    for chemin in DOCUMENTS:
        texte = chemin.read_text(encoding="utf-8").lower()
        assert "collecte unique" in texte or "collecte commune" in texte, chemin.name
        assert "priorité au vert" in texte or "vert d'abord" in texte or (
            "verts valides" in texte
        ), chemin.name


def test_E3_aucun_message_de_passage_a_une_vague_orange_ne_subsiste():
    """Preuve par le code : le modèle de message obsolète a été retiré."""
    from app.services import notification_service

    assert "REPRISE_PASSAGE_ORANGE" not in notification_service.TEMPLATES


def test_E3_le_contrat_d_anonymat_est_le_bon_partout():
    """Plus de « demandeur masqué » promis dans la documentation."""
    for chemin in DOCUMENTS:
        texte = chemin.read_text(encoding="utf-8").lower()
        assert "identité du demandeur reste masquée" not in texte, chemin.name


def test_E3_le_decompte_des_tests_n_est_pas_arrondi_a_la_hausse():
    """Le rapport doit distinguer collectés, réussis et sautés."""
    rapport = RACINE / "docs" / "TESTS.md"
    assert rapport.exists()
    texte = rapport.read_text(encoding="utf-8")
    assert "406 tests verts" not in texte
