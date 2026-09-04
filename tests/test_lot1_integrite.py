"""Lot 1.2 et 1.3 — soumission validée figée, candidature retirée non tirable.

Contre-audit du 04/09/2026 :

* une réponse VALIDEE restait modifiable sans réouverture tracée, l'état de la
  campagne n'était pas vérifié, et une occurrence d'un autre trimestre pouvait
  être injectée ;
* un refus postérieur à une candidature favorable laissait la candidature
  tirable, et une modification après gel passait silencieusement.

Données fictives.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import (
    AuditEvent,
    CampaignState,
    Candidacy,
    CandidacyState,
    Color,
    GardeOccurrence,
    ProfessionalProfile,
    Quarter,
    SubmissionState,
    WaveSolicitation,
    WaveState,
)
from app.services import campaign_service, catalog_service, handover_service
from app.services.clock import Clock
from tests.conftest import CAMPAIGN_DEADLINE, publish_plan


# --------------------------------------------------------------------------- #
# 1.2 — soumission validée
# --------------------------------------------------------------------------- #


def test_une_reponse_validee_n_est_plus_modifiable(world):
    profil = world.seniors[0]
    soumission = world.submission(profil)
    campaign_service.validate_submission(world.session, soumission)
    assert soumission.state is SubmissionState.VALIDEE

    with pytest.raises(campaign_service.CampaignError) as exc:
        campaign_service.set_availability(
            world.session, soumission, world.occurrences[0], Color.ROUGE
        )
    assert "rouvrir" in str(exc.value)


def test_une_reponse_validee_ne_peut_pas_etre_revalidee(world):
    soumission = world.submission(world.seniors[0])
    campaign_service.validate_submission(world.session, soumission)
    with pytest.raises(campaign_service.CampaignError):
        campaign_service.validate_submission(world.session, soumission)


def test_la_reouverture_tracee_est_le_seul_chemin(world):
    profil = world.seniors[0]
    soumission = world.submission(profil)
    campaign_service.validate_submission(world.session, soumission)

    campaign_service.reopen_submission(
        world.session, soumission, world.admin, "correction demandée par la personne"
    )
    assert soumission.state is SubmissionState.BROUILLON
    campaign_service.set_availability(
        world.session, soumission, world.occurrences[0], Color.ROUGE
    )
    actions = [
        e.action for e in world.session.execute(select(AuditEvent)).scalars()
    ]
    assert "REPONSE_ROUVERTE" in actions


def test_la_saisie_est_refusee_campagne_fermee(world):
    soumission = world.submission(world.seniors[0])
    world.campaign.state = CampaignState.PRETE
    world.session.flush()
    with pytest.raises(campaign_service.CampaignError) as exc:
        campaign_service.set_availability(
            world.session, soumission, world.occurrences[0], Color.VERT
        )
    assert "saisie est fermée" in str(exc.value)


def test_la_saisie_est_refusee_apres_le_delai_de_grace(world):
    soumission = world.submission(world.seniors[0])
    world.campaign.state = CampaignState.RESOLUTION_NON_REPONDANTS
    world.session.flush()
    Clock.freeze(world.campaign.grace_deadline + timedelta(hours=1))
    with pytest.raises(campaign_service.CampaignError) as exc:
        campaign_service.set_availability(
            world.session, soumission, world.occurrences[0], Color.VERT
        )
    assert "grâce" in str(exc.value)


def test_une_occurrence_d_un_autre_trimestre_est_refusee(world):
    """Injection hors périmètre : le trimestre de la soumission fait foi."""
    session = world.session
    autre_trimestre = session.execute(
        select(Quarter).where(
            Quarter.year_id == world.year.id, Quarter.index == 3
        )
    ).scalar_one()
    catalog_service.generate_occurrences(session, autre_trimestre, holidays=set())
    intruse = session.execute(
        select(GardeOccurrence)
        .where(GardeOccurrence.quarter_id == autre_trimestre.id)
        .limit(1)
    ).scalar_one()

    soumission = world.submission(world.seniors[0])
    with pytest.raises(campaign_service.CampaignError) as exc:
        campaign_service.set_availability(session, soumission, intruse, Color.VERT)
    assert "n'appartient pas au trimestre" in str(exc.value)


def test_une_occurrence_du_bon_trimestre_reste_acceptee(world):
    soumission = world.submission(world.seniors[0])
    entree = campaign_service.set_availability(
        world.session, soumission, world.occurrences[0], Color.ROUGE
    )
    assert entree.color is Color.ROUGE


# --------------------------------------------------------------------------- #
# 1.3 — refus, retrait et gel
# --------------------------------------------------------------------------- #


def _collecte(world, minimum: int = 2):
    session = world.session
    publish_plan(world)
    for affectation in sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    ):
        session.refresh(affectation)
        if affectation.busy_operation is not None:
            continue
        titulaire = session.get(ProfessionalProfile, affectation.profile_id)
        demande = handover_service.request_handover(session, affectation, titulaire)
        vague = handover_service.open_wave(
            session, demande, handover_service.wave_kind_for(affectation.post)
        )
        if vague.solicited_count >= minimum:
            solicites = [
                session.get(ProfessionalProfile, s.profile_id)
                for s in session.execute(
                    select(WaveSolicitation).where(
                        WaveSolicitation.wave_id == vague.id
                    )
                ).scalars()
            ]
            return demande, vague, solicites
        handover_service.cancel_request(session, demande, world.admin)
    pytest.skip("aucune collecte exploitable")


def test_favorable_puis_refus_rend_la_candidature_non_tirable(world):
    session = world.session
    demande, vague, solicites = _collecte(world, minimum=2)
    repentant, autre = solicites[0], solicites[1]

    handover_service.submit_candidacy(session, vague, repentant)
    handover_service.submit_candidacy(session, vague, autre)
    handover_service.decline(session, vague, repentant)

    candidature = session.execute(
        select(Candidacy).where(
            Candidacy.wave_id == vague.id, Candidacy.profile_id == repentant.id
        )
    ).scalar_one()
    assert candidature.state is CandidacyState.RETIREE
    assert "non tirable" in candidature.exclusion_reason

    tirage = handover_service.close_and_draw(session, vague)
    assert tirage is not None
    assert tirage.winner_profile_id != repentant.id
    import json

    assert candidature.id not in json.loads(tirage.candidate_ids_json)
    preuve = json.loads(tirage.proof_json)
    assert candidature.id not in preuve["liste_valide"]
    assert candidature.id not in preuve["liste_tirable"]


def test_le_retrait_est_journalise(world):
    session = world.session
    demande, vague, solicites = _collecte(world, minimum=2)
    handover_service.submit_candidacy(session, vague, solicites[0])
    handover_service.withdraw_candidacy(session, vague, solicites[0])
    actions = [e.action for e in session.execute(select(AuditEvent)).scalars()]
    assert "CANDIDATURE_RETIREE" in actions


def test_une_candidature_retiree_ne_peut_pas_etre_redeposee(world):
    session = world.session
    demande, vague, solicites = _collecte(world, minimum=2)
    handover_service.submit_candidacy(session, vague, solicites[0])
    handover_service.decline(session, vague, solicites[0])
    with pytest.raises(handover_service.HandoverError) as exc:
        handover_service.submit_candidacy(session, vague, solicites[0])
    assert "retirée" in str(exc.value)


def test_tout_le_monde_se_retire_conduit_a_l_escalade(world):
    session = world.session
    demande, vague, solicites = _collecte(world, minimum=2)
    for profil in solicites:
        handover_service.submit_candidacy(session, vague, profil)
    for profil in solicites:
        handover_service.decline(session, vague, profil)
    assert handover_service.close_and_draw(session, vague) is None


def test_aucune_modification_apres_le_gel(world):
    session = world.session
    demande, vague, solicites = _collecte(world, minimum=2)
    for profil in solicites:
        handover_service.submit_candidacy(session, vague, profil)
    handover_service.close_and_draw(session, vague)
    session.refresh(vague)
    assert vague.state is not WaveState.OUVERTE

    with pytest.raises(handover_service.HandoverError):
        handover_service.submit_candidacy(session, vague, solicites[0])
    with pytest.raises(handover_service.HandoverError):
        handover_service.decline(session, vague, solicites[0])


def test_une_tentative_apres_gel_est_tracee(world):
    session = world.session
    demande, vague, solicites = _collecte(world, minimum=2)
    for profil in solicites:
        handover_service.submit_candidacy(session, vague, profil)
    handover_service.close_and_draw(session, vague)

    with pytest.raises(handover_service.HandoverError):
        handover_service.decline(session, vague, solicites[0])
    actions = [e.action for e in session.execute(select(AuditEvent)).scalars()]
    assert "REPONSE_TARDIVE_REFUSEE" in actions


def test_aucune_modification_apres_l_echeance(world):
    session = world.session
    demande, vague, solicites = _collecte(world, minimum=2)
    Clock.freeze(vague.closes_at + timedelta(minutes=1))
    with pytest.raises(handover_service.HandoverError) as exc:
        handover_service.decline(session, vague, solicites[0])
    assert "expirée" in str(exc.value)
    actions = [e.action for e in session.execute(select(AuditEvent)).scalars()]
    assert "REPONSE_TARDIVE_REFUSEE" in actions
