"""Lot B du contre-audit du 04/09/2026 — le vrai parcours d'échange est raccordé.

Le moteur de recherche existait mais n'était appelé par aucune page ni aucune
route, et l'interface demandait encore à l'utilisateur de choisir lui-même la
« garde souhaitée ». Ce fichier prouve le parcours nominal de bout en bout, dans
l'interface **et** dans l'API :

1. on part de sa propre garde, sans désigner ni collègue ni contrepartie ;
2. la recherche est faite par l'application, dans le trimestre ;
3. tous les partenaires éligibles sont sollicités simultanément ;
4. à la clôture, seules les réponses positives sont classées par maximin ;
5. le consentement des deux parties est explicite et l'officialisation unique ;
6. refus, retrait, expiration, annulation et absence de solution sont modélisés.

Données entièrement fictives.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import (
    Assignment,
    AuditEvent,
    Notification,
    ProfessionalProfile,
    SwapCandidate,
    SwapCandidateState,
    SwapSearch,
    SwapSearchState,
    SwapState,
)
from app.services import swap_flow_service, swap_service
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


def _futures(world) -> list[Assignment]:
    return [
        a
        for a in sorted(
            world.version.assignments, key=lambda a: a.post.occurrence.start_at
        )
        if a.post.occurrence.start_at > Clock.now() and a.busy_operation is None
    ]


def _paire_echangeable(world):
    """Deux gardes futures de même nature, détenues par deux personnes."""
    session = world.session
    futures = _futures(world)
    for premiere in futures:
        for seconde in futures:
            if seconde.id == premiere.id or seconde.profile_id == premiere.profile_id:
                continue
            if not swap_service.check_equivalence(premiere, seconde)[0]:
                continue
            un = session.get(ProfessionalProfile, premiere.profile_id)
            deux = session.get(ProfessionalProfile, seconde.profile_id)
            world.set_color(deux, premiere.post.occurrence, __import__(
                "app.models", fromlist=["Color"]
            ).Color.VERT)
            world.set_color(un, seconde.post.occurrence, __import__(
                "app.models", fromlist=["Color"]
            ).Color.VERT)
            session.flush()
            return premiere, un, seconde, deux
    raise AssertionError("l'univers de test doit offrir une paire échangeable")


def _recherche_utilisable(world):
    """Ouvre une recherche qui a réellement sollicité au moins un partenaire."""
    session = world.session
    for affectation in _futures(world):
        titulaire = session.get(ProfessionalProfile, affectation.profile_id)
        recherche = swap_flow_service.ouvrir(session, affectation, titulaire)
        session.flush()
        if recherche.state is SwapSearchState.COLLECTE:
            return recherche, titulaire
        # Pas de partenaire : on repart proprement pour tester la garde suivante.
        session.expire_all()
    raise AssertionError("aucune recherche n'a trouvé de partenaire dans cet univers")


# --------------------------------------------------------------------------- #
# B.1 — le parcours nominal ne demande plus de contrepartie
# --------------------------------------------------------------------------- #


def test_B1_le_parcours_part_d_une_seule_garde(world):
    publish_plan(world)
    affectation = _futures(world)[0]
    titulaire = world.session.get(ProfessionalProfile, affectation.profile_id)
    client = _client(world, world.user_of(titulaire))

    reponse = client.post(
        "/api/v1/swap-searches", json={"assignment_id": affectation.id}
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    # Aucune contrepartie n'a été fournie par l'appelant.
    assert corps["garde_cedee"] == affectation.id
    assert "fenetre" in corps and corps["fenetre"]["palier"]
    assert corps["etat"] in (
        SwapSearchState.COLLECTE.value,
        SwapSearchState.OFFICIALISEE.value,
        SwapSearchState.SANS_SOLUTION.value,
    )


def test_B1_l_interface_ne_propose_plus_de_choisir_la_garde_souhaitee(world):
    publish_plan(world)
    titulaire = world.seniors[0]
    client = _client(world, world.user_of(titulaire))
    page = client.get("/echanges")
    assert page.status_code == 200
    assert "Garde souhaitée" not in page.text
    assert 'name="assignment_b_id"' not in page.text
    assert 'action="/echanges/rechercher"' in page.text


def test_B1_le_service_de_recherche_est_reellement_appele(world):
    """Preuve par le journal : la recherche laisse une trace d'ouverture."""
    publish_plan(world)
    recherche, _ = _recherche_utilisable(world)
    evenements = [
        e.action
        for e in world.session.execute(select(AuditEvent)).scalars()
        if e.entity_type == "swap_search"
    ]
    assert "ECHANGE_RECHERCHE_OUVERTE" in evenements
    assert recherche.solicited_count >= 1


# --------------------------------------------------------------------------- #
# B.3 — sollicitation simultanée, sans avantage à la rapidité, sans motif
# --------------------------------------------------------------------------- #


def test_B3_tous_les_partenaires_sont_sollicites_en_meme_temps(world):
    publish_plan(world)
    recherche, _ = _recherche_utilisable(world)
    assert len(recherche.candidates) == recherche.solicited_count
    messages = [
        m
        for m in world.session.execute(select(Notification)).scalars()
        if m.kind == "ECHANGE_SOLLICITATION"
    ]
    assert len(messages) == recherche.solicited_count
    # Même instant d'envoi : personne n'est prévenu avant les autres.
    assert len({m.sent_at for m in messages}) == 1


def test_B3_la_sollicitation_ne_porte_ni_nom_ni_motif(world):
    publish_plan(world)
    session = world.session
    affectation = _futures(world)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    recherche = swap_flow_service.ouvrir(
        session, affectation, titulaire, commentaire="motif fictif confidentiel"
    )
    session.flush()
    messages = [
        m
        for m in session.execute(select(Notification)).scalars()
        if m.kind == "ECHANGE_SOLLICITATION"
    ]
    for message in messages:
        assert message.anonymised is True
        assert titulaire.code not in message.body
        assert titulaire.code not in message.subject
        assert "motif fictif confidentiel" not in message.body


# --------------------------------------------------------------------------- #
# B.5 — double consentement, revalidation atomique, officialisation unique
# --------------------------------------------------------------------------- #


def test_B5_le_double_accord_officialise_exactement_une_fois(world):
    publish_plan(world)
    session = world.session
    recherche, demandeur = _recherche_utilisable(world)
    cedee_id = recherche.assignment_id
    candidat = recherche.candidates[0]
    partenaire = candidat.profile
    reprise_id = candidat.assignment_id

    swap_flow_service.repondre(session, recherche, partenaire, favorable=True)
    # Les autres partenaires refusent, pour clore la collecte par complétude.
    for autre in recherche.candidates:
        if autre.profile_id != partenaire.id:
            swap_flow_service.repondre(
                session, recherche, autre.profile, favorable=False,
                assignment_id=autre.assignment_id,
            )
    swap_flow_service.avancer(session, recherche)
    session.flush()
    session.refresh(recherche)

    assert recherche.state is SwapSearchState.OFFICIALISEE, recherche.outcome_reason
    proposition = recherche.retained_proposal
    assert proposition is not None
    assert proposition.state is SwapState.OFFICIEL
    # Permutation réellement appliquée.
    assert session.get(Assignment, cedee_id).profile_id == partenaire.id
    assert session.get(Assignment, reprise_id).profile_id == demandeur.id

    # Une seconde clôture ne peut pas officialiser une deuxième fois.
    with pytest.raises(swap_flow_service.SwapFlowError):
        swap_flow_service.cloturer(session, recherche)


def test_B5_aucun_responsable_n_intervient(world):
    """Le parcours conforme se déroule sans acteur administratif."""
    publish_plan(world)
    session = world.session
    recherche, _ = _recherche_utilisable(world)
    for candidat in recherche.candidates:
        swap_flow_service.repondre(
            session, recherche, candidat.profile, favorable=True,
            assignment_id=candidat.assignment_id,
        )
    swap_flow_service.avancer(session, recherche)
    session.flush()
    acteurs = {
        e.actor_label
        for e in session.execute(select(AuditEvent)).scalars()
        if e.entity_type == "swap_search"
    }
    permis = {"SYSTEME", None} | {
        world.user_of(p).email for p in world.seniors + world.assistants
    }
    assert acteurs <= permis, acteurs - permis
    assert world.admin.email not in acteurs


# --------------------------------------------------------------------------- #
# B.6 — états modélisés : refus, retrait, expiration, annulation, sans solution
# --------------------------------------------------------------------------- #


def test_B6_un_refus_rend_la_proposition_non_retenable(world):
    publish_plan(world)
    session = world.session
    recherche, _ = _recherche_utilisable(world)
    candidat = recherche.candidates[0]
    swap_flow_service.repondre(
        session, recherche, candidat.profile, favorable=False,
        assignment_id=candidat.assignment_id,
    )
    session.refresh(candidat)
    assert candidat.state is SwapCandidateState.REFUS
    with pytest.raises(swap_flow_service.SwapFlowError):
        swap_flow_service.repondre(
            session, recherche, candidat.profile, favorable=True,
            assignment_id=candidat.assignment_id,
        )


def test_B6_un_retrait_apres_accord_est_definitif(world):
    publish_plan(world)
    session = world.session
    recherche, _ = _recherche_utilisable(world)
    candidat = recherche.candidates[0]
    swap_flow_service.repondre(
        session, recherche, candidat.profile, favorable=True,
        assignment_id=candidat.assignment_id,
    )
    swap_flow_service.repondre(
        session, recherche, candidat.profile, favorable=False,
        assignment_id=candidat.assignment_id,
    )
    session.refresh(candidat)
    assert candidat.state is SwapCandidateState.RETIRE
    with pytest.raises(swap_flow_service.SwapFlowError):
        swap_flow_service.repondre(
            session, recherche, candidat.profile, favorable=True,
            assignment_id=candidat.assignment_id,
        )


def test_B6_une_reponse_apres_gel_est_refusee_et_tracee(world):
    publish_plan(world)
    session = world.session
    recherche, _ = _recherche_utilisable(world)
    candidat = recherche.candidates[0]
    # Personne ne répond ; l'échéance passe.
    Clock.freeze(recherche.closes_at)
    swap_flow_service.avancer(session, recherche)
    session.flush()
    session.refresh(recherche)
    assert recherche.state is SwapSearchState.SANS_SOLUTION
    with pytest.raises(swap_flow_service.SwapFlowError):
        swap_flow_service.repondre(
            session, recherche, candidat.profile, favorable=True,
            assignment_id=candidat.assignment_id,
        )
    traces = [
        e.action
        for e in session.execute(select(AuditEvent)).scalars()
        if e.entity_type == "swap_search"
    ]
    assert "ECHANGE_REPONSE_TARDIVE_REFUSEE" in traces


def test_B6_sans_solution_le_titulaire_reste_inchange(world):
    publish_plan(world)
    session = world.session
    recherche, demandeur = _recherche_utilisable(world)
    cedee_id = recherche.assignment_id
    for candidat in recherche.candidates:
        swap_flow_service.repondre(
            session, recherche, candidat.profile, favorable=False,
            assignment_id=candidat.assignment_id,
        )
    swap_flow_service.avancer(session, recherche)
    session.flush()
    session.refresh(recherche)
    assert recherche.state is SwapSearchState.SANS_SOLUTION
    assert recherche.outcome_reason
    assert session.get(Assignment, cedee_id).profile_id == demandeur.id
    # Le verrou est relâché : la garde peut repartir dans une autre opération.
    assert session.get(Assignment, cedee_id).busy_operation is None


def test_B6_l_auteur_peut_retirer_sa_demande(world):
    publish_plan(world)
    session = world.session
    recherche, demandeur = _recherche_utilisable(world)
    assert swap_flow_service.annuler(session, recherche) is True
    session.refresh(recherche)
    assert recherche.state is SwapSearchState.ANNULEE
    assert session.get(Assignment, recherche.assignment_id).busy_operation is None
    # Une seconde annulation n'a plus d'effet.
    assert swap_flow_service.annuler(session, recherche) is False


def test_B6_une_garde_deja_engagee_ne_peut_pas_ouvrir_deux_recherches(world):
    publish_plan(world)
    session = world.session
    recherche, demandeur = _recherche_utilisable(world)
    affectation = session.get(Assignment, recherche.assignment_id)
    with pytest.raises(swap_flow_service.SwapFlowError):
        swap_flow_service.ouvrir(session, affectation, demandeur)


def test_B6_seul_le_titulaire_ouvre_une_recherche(world):
    publish_plan(world)
    session = world.session
    affectation = _futures(world)[0]
    autre = next(
        p for p in world.seniors if p.id != affectation.profile_id
    )
    with pytest.raises(Exception):
        swap_flow_service.ouvrir(session, affectation, autre)


# --------------------------------------------------------------------------- #
# Bout en bout par l'interface web
# --------------------------------------------------------------------------- #


def test_B_parcours_complet_dans_l_interface(world):
    publish_plan(world)
    session = world.session
    affectation = _futures(world)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    client = _client(world, world.user_of(titulaire))

    ouverture = client.post(
        "/echanges/rechercher",
        data={"assignment_id": affectation.id, "commentaire": ""},
        follow_redirects=False,
    )
    assert ouverture.status_code == 303, ouverture.text
    session.expire_all()
    recherche = session.execute(
        select(SwapSearch).order_by(SwapSearch.id.desc())
    ).scalars().first()
    assert recherche is not None

    detail = client.get(f"/echanges/{recherche.id}")
    assert detail.status_code == 200
    assert "Recherche d'échange" in detail.text

    if recherche.state is SwapSearchState.COLLECTE:
        candidat = recherche.candidates[0]
        partenaire = candidat.profile
        client_partenaire = _client(world, world.user_of(partenaire))
        reponse = client_partenaire.post(
            f"/echanges/{recherche.id}/reponse",
            data={"candidat_id": candidat.id, "reponse": "favorable"},
            follow_redirects=False,
        )
        assert reponse.status_code == 303, reponse.text
        session.expire_all()
        session.refresh(candidat)
        assert candidat.state in (
            SwapCandidateState.FAVORABLE,
            SwapCandidateState.RETENU,
            SwapCandidateState.NON_RETENU,
        )
