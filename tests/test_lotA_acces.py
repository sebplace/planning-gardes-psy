"""Lot A du contre-audit du 04/09/2026 — contrôles d'accès réellement appliqués.

Quatre points, chacun avec sa contre-épreuve HTTP réelle :

1. le statut médical est contrôlé au **même endroit** pour tous les points
   d'entrée métier, API comme interface ;
2. une version non publiée est fermée à un médecin ordinaire ;
3. reprises, vagues, tirages et échanges ne sont lisibles que par leurs acteurs
   légitimes, avec une réponse ``404`` **uniforme** qui ne révèle pas
   l'existence d'un identifiant ;
4. le contrat d'anonymat est honnête : plus de « demandeur masqué » suivi du
   « titulaire actuel ».

Données entièrement fictives.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import (
    Assignment,
    Color,
    Draw,
    GardeOccurrence,
    HandoverWave,
    Line,
    ProfessionalProfile,
    ScheduleState,
    Status,
    SwapProposal,
    WaveSolicitation,
    permissions,
)
from app.services import handover_service, permission_service, planning_service
from app.services.clock import Clock
from tests.conftest import publish_plan

MOT_DE_PASSE = "demo"


# --------------------------------------------------------------------------- #
# Outillage
# --------------------------------------------------------------------------- #


def _client(world, utilisateur) -> TestClient:
    world.session.commit()
    client = TestClient(app)
    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": utilisateur.email, "password": MOT_DE_PASSE},
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _une_garde_future(world, profile=None, ligne: Line | None = None):
    """Affectation publiée, future et non engagée dans une autre opération."""
    session = world.session
    for affectation in sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    ):
        session.refresh(affectation)
        if affectation.busy_operation is not None:
            continue
        if ligne is not None and affectation.post.line is not ligne:
            continue
        if profile is not None and affectation.profile_id != profile.id:
            continue
        if affectation.post.occurrence.start_at <= Clock.now():
            continue
        return affectation
    pytest.skip("aucune garde future disponible dans l'univers de test")


def _titulaire(world, affectation) -> ProfessionalProfile:
    return world.session.get(ProfessionalProfile, affectation.profile_id)


def _revoquer(world, profile) -> None:
    utilisateur = world.user_of(profile)
    utilisateur.is_medecin = False
    world.session.flush()
    world.session.commit()


def _etranger(world) -> ProfessionalProfile:
    """Médecin réellement étranger à toute demande de l'univers de test.

    Créé **après** la clôture de la campagne : il n'a donc aucune couleur
    déclarée, n'est jamais sollicité, jamais candidat, et n'est partie prenante
    d'aucun échange. C'est exactement le profil dont le contre-audit exige qu'il
    ne puisse pas énumérer les identifiants.
    """
    from tests.conftest import _add_person

    profil = _add_person(world.session, "ETR-99", Status.SENIOR, 99)
    world.session.flush()
    world.session.commit()
    return profil


# --------------------------------------------------------------------------- #
# A.1 — statut médical central, sur tous les points d'entrée
# --------------------------------------------------------------------------- #


def test_A1_contre_epreuve_le_post_de_reprise_ne_repond_plus_200(world):
    """Reproduction exacte du contre-audit : POST de reprise après révocation."""
    publish_plan(world)
    affectation = _une_garde_future(world)
    titulaire = _titulaire(world, affectation)
    client = _client(world, world.user_of(titulaire))

    # Avant révocation, le point d'entrée fonctionne.
    ok = client.post(
        "/api/v1/handover/requests", json={"assignment_id": affectation.id}
    )
    assert ok.status_code == 200, ok.text

    # Après révocation, il doit être fermé — c'était le 200 reproduit par l'audit.
    _revoquer(world, titulaire)
    seconde = _une_garde_future(world, profile=titulaire)
    refus = client.post(
        "/api/v1/handover/requests", json={"assignment_id": seconde.id}
    )
    assert refus.status_code == 403, refus.text
    assert "médecin" in refus.json()["detail"]


def test_A1_tous_les_points_d_entree_medicaux_sont_fermes(world):
    """Disponibilités, reprise, candidature, refus et échanges : 403 partout."""
    publish_plan(world)
    affectation = _une_garde_future(world)
    titulaire = _titulaire(world, affectation)
    demande = handover_service.request_handover(
        world.session, affectation, titulaire
    )
    handover_service.advance(world.session, demande)
    world.session.flush()
    vague = demande.waves[0]
    sollicite = world.session.get(
        ProfessionalProfile,
        world.session.execute(
            select(WaveSolicitation.profile_id).where(
                WaveSolicitation.wave_id == vague.id
            )
        ).scalars().first(),
    )
    assert sollicite is not None, "l'univers de test doit produire au moins un sollicité"

    autre = _une_garde_future(world, ligne=affectation.post.line)
    client = _client(world, world.user_of(sollicite))
    _revoquer(world, sollicite)

    appels = [
        ("post", "/api/v1/handover/requests", {"json": {"assignment_id": autre.id}}),
        ("post", f"/api/v1/handover/waves/{vague.id}/candidacies", {}),
        ("post", f"/api/v1/handover/waves/{vague.id}/refus", {}),
        (
            "post",
            "/api/v1/swaps",
            {"json": {"assignment_a_id": affectation.id, "assignment_b_id": autre.id}},
        ),
        (
            "post",
            "/campagne/couleur",
            {
                "data": {"occurrence_id": affectation.post.occurrence_id,
                         "couleur": Color.ROUGE.value},
                "follow_redirects": False,
            },
        ),
        (
            "post",
            "/reprises/demander",
            {"data": {"assignment_id": autre.id}, "follow_redirects": False},
        ),
    ]
    for methode, chemin, options in appels:
        reponse = getattr(client, methode)(chemin, **options)
        assert reponse.status_code == 403, f"{chemin} → {reponse.status_code}"


# --------------------------------------------------------------------------- #
# A.2 — versions non publiées
# --------------------------------------------------------------------------- #


def _version_de_travail(world):
    """Version non publiée, produite par une seconde exécution du moteur."""
    run = planning_service.run_engine(
        world.session, world.quarter, admin=world.admin, seed=9191, variants=1
    )
    version = planning_service.create_version_from_proposal(
        world.session, run.proposals[0], world.admin, note="brouillon de test"
    )
    world.session.flush()
    assert version.state is not ScheduleState.PUBLIE
    return version


def test_A2_un_brouillon_est_ferme_au_medecin_ordinaire(world):
    """Contre-épreuve : le 200 nominatif sur un brouillon n'est plus possible."""
    publish_plan(world)
    brouillon = _version_de_travail(world)
    medecin = world.user_of(world.seniors[0])
    client = _client(world, medecin)

    refus = client.get(f"/api/v1/planning/versions/{brouillon.id}")
    assert refus.status_code == 404, refus.text
    assert "affectations" not in refus.json()

    # La version publiée, elle, reste lisible.
    publiee = client.get(f"/api/v1/planning/versions/{world.version.id}")
    assert publiee.status_code == 200, publiee.text


def test_A2_le_brouillon_reste_ouvert_a_qui_detient_l_action(world):
    publish_plan(world)
    brouillon = _version_de_travail(world)
    responsable = world.user_of(world.seniors[1])
    permission_service.grant(
        world.session, responsable, permissions.RESP_L1, world.admin
    )
    client = _client(world, responsable)
    reponse = client.get(f"/api/v1/planning/versions/{brouillon.id}")
    assert reponse.status_code == 200, reponse.text


# --------------------------------------------------------------------------- #
# A.3 — pas d'énumération d'identifiants
# --------------------------------------------------------------------------- #


def _demande_d_un_tiers(world):
    """Demande ouverte par une personne, lue par un médecin réellement étranger."""
    affectation = _une_garde_future(world)
    titulaire = _titulaire(world, affectation)
    demande = handover_service.request_handover(world.session, affectation, titulaire)
    handover_service.advance(world.session, demande)
    world.session.flush()
    return demande, _etranger(world)


def test_A3_une_reprise_hors_perimetre_repond_comme_une_inexistante(world):
    publish_plan(world)
    demande, etranger = _demande_d_un_tiers(world)
    client = _client(world, world.user_of(etranger))

    existante = client.get(f"/api/v1/handover/requests/{demande.id}")
    inexistante = client.get("/api/v1/handover/requests/999999")
    assert existante.status_code == 404
    assert inexistante.status_code == 404
    # Réponse strictement identique : aucune fuite d'existence par différence.
    assert existante.json() == inexistante.json()


def test_A3_un_tirage_hors_perimetre_est_invisible(world):
    publish_plan(world)
    affectation = _une_garde_future(world)
    titulaire = _titulaire(world, affectation)
    demande = handover_service.request_handover(world.session, affectation, titulaire)
    handover_service.advance(world.session, demande)
    world.session.flush()
    vague = demande.waves[0]
    sollicites = [
        world.session.get(ProfessionalProfile, pid)
        for pid in world.session.execute(
            select(WaveSolicitation.profile_id).where(
                WaveSolicitation.wave_id == vague.id
            )
        ).scalars()
    ]
    assert sollicites, "l'univers de test doit produire au moins un sollicité"
    for profil in sollicites:
        handover_service.submit_candidacy(world.session, vague, profil)
    handover_service.advance(world.session, demande)
    world.session.flush()
    tirage = world.session.execute(select(Draw)).scalars().first()
    assert tirage is not None, "toutes les réponses reçues : le tirage doit avoir eu lieu"

    client = _client(world, world.user_of(_etranger(world)))
    reponse = client.get(f"/api/v1/handover/draws/{tirage.id}")
    assert reponse.status_code == 404
    assert reponse.json() == client.get("/api/v1/handover/draws/999999").json()


def test_A3_la_liste_des_reprises_est_filtree_cote_serveur(world):
    publish_plan(world)
    demande, etranger = _demande_d_un_tiers(world)
    client = _client(world, world.user_of(etranger))
    page = client.get("/reprises")
    assert page.status_code == 200
    # La demande d'un tiers n'apparaît pas, même masquée, dans la liste.
    assert f'href="/reprises/{demande.id}"' not in page.text


def test_A3_un_echange_hors_perimetre_est_invisible(world):
    publish_plan(world)
    a = _une_garde_future(world)
    b = next(
        (
            x
            for x in sorted(
                world.version.assignments, key=lambda a: a.post.occurrence.start_at
            )
            if x.profile_id != a.profile_id
            and x.post.line is a.post.line
            and x.busy_operation is None
            and x.post.occurrence.start_at > Clock.now()
        ),
        None,
    )
    assert b is not None, "l'univers de test doit offrir une seconde garde échangeable"
    from app.services import swap_service

    proposition = swap_service.propose_swap(
        world.session, a, b, _titulaire(world, a)
    )
    world.session.flush()

    client = _client(world, world.user_of(_etranger(world)))
    reponse = client.get(f"/api/v1/swaps/{proposition.id}")
    assert reponse.status_code == 404
    assert reponse.json() == client.get("/api/v1/swaps/999999").json()


# --------------------------------------------------------------------------- #
# A.4 — contrat d'anonymat honnête
# --------------------------------------------------------------------------- #


def test_A4_le_detail_ne_masque_plus_le_demandeur_a_cote_du_titulaire(world):
    publish_plan(world)
    affectation = _une_garde_future(world)
    titulaire = _titulaire(world, affectation)
    demande = handover_service.request_handover(
        world.session, affectation, titulaire, comment="commentaire fictif"
    )
    handover_service.advance(world.session, demande)
    world.session.flush()
    sollicite = world.session.get(
        ProfessionalProfile,
        world.session.execute(
            select(WaveSolicitation.profile_id).where(
                WaveSolicitation.wave_id == demande.waves[0].id
            )
        ).scalars().first(),
    )
    client = _client(world, world.user_of(sollicite))
    detail = client.get(f"/api/v1/handover/requests/{demande.id}").json()

    # Plus de fausse promesse : ni « masqué », ni contradiction avec le titulaire.
    assert detail["demandeur"] != "masqué"
    assert detail["titulaire_actuel"] == titulaire.code
    assert "ne mentionne ni le nom du demandeur ni son motif" in detail["contrat_anonymat"]
    # Ce qui reste réellement restreint : le commentaire.
    assert detail["commentaire"] is None


def test_A4_le_commentaire_reste_reserve_au_demandeur(world):
    publish_plan(world)
    affectation = _une_garde_future(world)
    titulaire = _titulaire(world, affectation)
    demande = handover_service.request_handover(
        world.session, affectation, titulaire, comment="commentaire fictif"
    )
    handover_service.advance(world.session, demande)
    world.session.flush()
    client = _client(world, world.user_of(titulaire))
    detail = client.get(f"/api/v1/handover/requests/{demande.id}").json()
    assert detail["commentaire"] == "commentaire fictif"


def test_A4_la_sollicitation_ne_porte_ni_nom_ni_motif(world):
    """Ce que l'application garantit réellement, prouvé sur les messages émis."""
    from app.models import Notification

    publish_plan(world)
    affectation = _une_garde_future(world)
    titulaire = _titulaire(world, affectation)
    demande = handover_service.request_handover(
        world.session, affectation, titulaire, comment="motif fictif confidentiel"
    )
    handover_service.advance(world.session, demande)
    world.session.flush()
    messages = list(
        world.session.execute(
            select(Notification).where(
                Notification.kind == "REPRISE_SOLLICITATION"
            )
        ).scalars()
    )
    assert messages
    for message in messages:
        assert titulaire.code not in message.body
        assert titulaire.code not in message.subject
        assert "motif fictif confidentiel" not in message.body
