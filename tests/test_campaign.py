"""Tests de la campagne de désidératas.

Couvre les exigences §22 : 6, 7, 8, 9, 37, 38, 39.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    Availability,
    AvailabilitySource,
    CampaignState,
    Color,
    GardeOccurrence,
    GardeType,
    HandoverState,
    HolidayRequirement,
    Notification,
    SubmissionState,
    WaveKind,
)
from app.services import (
    campaign_service,
    catalog_service,
    engine_bridge,
    handover_service,
    planning_service,
)
from app.services.clock import Clock
from tests.conftest import CAMPAIGN_DEADLINE, publish_plan, validate_all


def _notifications(session, kind: str, profile_id: int | None = None):
    query = select(Notification).where(Notification.kind == kind)
    if profile_id is not None:
        query = query.where(Notification.recipient_profile_id == profile_id)
    return list(session.execute(query).scalars())


# --------------------------------------------------------------------------- #
# Test 6 — les rappels cessent après validation
# --------------------------------------------------------------------------- #


def test_06_rappels_cessent_apres_validation(world):
    session = world.session
    valide = world.seniors[0]
    silencieux = world.seniors[1]
    campaign_service.validate_submission(session, world.submission(valide))

    Clock.freeze(CAMPAIGN_DEADLINE - timedelta(days=14) + timedelta(hours=1))
    campaign_service.send_due_reminders(session, world.campaign)
    Clock.freeze(CAMPAIGN_DEADLINE - timedelta(days=7) + timedelta(hours=1))
    campaign_service.send_due_reminders(session, world.campaign)
    Clock.freeze(CAMPAIGN_DEADLINE - timedelta(days=2) + timedelta(hours=1))
    campaign_service.send_due_reminders(session, world.campaign)

    assert _notifications(session, "CAMPAGNE_RAPPEL", valide.id) == []
    rappels_silencieux = _notifications(session, "CAMPAGNE_RAPPEL", silencieux.id)
    assert len(rappels_silencieux) == 3

    # Idempotence : rejouer n'ajoute aucun doublon.
    campaign_service.send_due_reminders(session, world.campaign)
    assert len(_notifications(session, "CAMPAGNE_RAPPEL", silencieux.id)) == 3


# --------------------------------------------------------------------------- #
# Test 7 — la non-réponse bloque d'abord la génération
# --------------------------------------------------------------------------- #


def test_07_non_reponse_bloque_d_abord_la_generation(world):
    session = world.session
    non_repondant = world.seniors[3]
    for profile in world.seniors + world.assistants:
        if profile.id != non_repondant.id:
            campaign_service.validate_submission(session, world.submission(profile))

    Clock.freeze(CAMPAIGN_DEADLINE + timedelta(minutes=5))
    campaign_service.close_campaign(session, world.campaign, world.admin)
    assert world.campaign.state is CampaignState.RESOLUTION_NON_REPONDANTS

    blocages = planning_service.generation_blockers(session, world.quarter)
    assert blocages and non_repondant.code in " ".join(blocages)

    run = planning_service.run_engine(session, world.quarter, admin=world.admin)
    assert run.status.value == "ECHEC"
    assert run.blocked_reason
    assert run.proposals == []

    # Les administrateurs sont alertés.
    assert _notifications(session, "ADMIN_NON_REPONDANTS")


# --------------------------------------------------------------------------- #
# Tests 8 et 37 — disponibilité par défaut
# --------------------------------------------------------------------------- #


def test_08_disponibilite_par_defaut_apres_relances_et_delai_de_grace(world):
    session = world.session
    non_repondant = world.seniors[3]
    # On efface toutes ses couleurs pour simuler l'absence totale de réponse.
    session.query(Availability).filter(
        Availability.submission_id == world.submission(non_repondant).id
    ).delete()
    session.flush()
    for profile in world.seniors + world.assistants:
        if profile.id != non_repondant.id:
            campaign_service.validate_submission(session, world.submission(profile))

    Clock.freeze(CAMPAIGN_DEADLINE + timedelta(minutes=5))
    campaign_service.close_campaign(session, world.campaign, world.admin)

    # Trop tôt : le délai de grâce n'est pas écoulé.
    ok, raisons = campaign_service.can_apply_default_availability(world.campaign)
    assert not ok and any("grâce" in r for r in raisons)
    with pytest.raises(campaign_service.CampaignError):
        campaign_service.apply_default_availability(session, world.campaign, world.admin)

    # Après le délai de grâce.
    Clock.freeze(world.campaign.grace_deadline + timedelta(hours=1))
    converties = campaign_service.apply_default_availability(
        session, world.campaign, world.admin
    )
    assert converties[non_repondant.code] == len(world.occurrences)
    assert world.campaign.state is CampaignState.PRETE

    entrees = list(
        session.execute(
            select(Availability).where(
                Availability.submission_id == world.submission(non_repondant).id
            )
        ).scalars()
    )
    assert entrees
    for entree in entrees:
        assert entree.color is Color.DISPO_DEFAUT
        assert entree.is_declared is False, "Distinct d'un vert déclaré."
        assert entree.source is AvailabilitySource.CONVERSION_NON_REPONSE
        assert "non confirmé" in entree.display_label
        assert "Vert" not in entree.display_label

    # Traitée comme un vert par le moteur : la personne redevient affectable.
    poste = next(p for p in world.posts() if p.required_status.value == "SENIOR")
    assert engine_bridge.check_assignment(session, poste, non_repondant) is None

    # La personne et les administrateurs sont informés.
    assert _notifications(session, "DISPO_PAR_DEFAUT", non_repondant.id)
    assert planning_service.generation_blockers(session, world.quarter) == []


def test_37_seuls_les_champs_non_renseignes_sont_convertis(world):
    session = world.session
    cible = world.seniors[3]
    occurrences = world.occurrences
    session.query(Availability).filter(
        Availability.submission_id == world.submission(cible).id
    ).delete()
    session.flush()

    world.set_color(cible, occurrences[0], Color.ROUGE)
    world.set_color(cible, occurrences[1], Color.ORANGE)
    world.set_color(cible, occurrences[2], Color.VERT)

    for profile in world.seniors + world.assistants:
        if profile.id != cible.id:
            campaign_service.validate_submission(session, world.submission(profile))

    Clock.freeze(CAMPAIGN_DEADLINE + timedelta(minutes=5))
    campaign_service.close_campaign(session, world.campaign, world.admin)
    Clock.freeze(world.campaign.grace_deadline + timedelta(hours=1))
    campaign_service.apply_default_availability(session, world.campaign, world.admin)

    assert world.color_of(cible, occurrences[0]) is Color.ROUGE
    assert world.color_of(cible, occurrences[1]) is Color.ORANGE
    assert world.color_of(cible, occurrences[2]) is Color.VERT
    for occurrence in occurrences[3:]:
        assert world.color_of(cible, occurrence) is Color.DISPO_DEFAUT

    # Le rouge conservé continue de bloquer toute affectation.
    poste = next(
        p for p in world.posts()
        if p.occurrence_id == occurrences[0].id and p.required_status.value == "SENIOR"
    )
    rejet = engine_bridge.check_assignment(session, poste, cible)
    assert rejet is not None and rejet.constraint_code == "H02_ROUGE"


def test_38_validation_tardive_et_prolongation(world):
    """Une validation pendant le délai de grâce annule la conversion ;
    une prolongation reprogramme la tâche sans double événement ni double notification."""
    session = world.session
    tardif = world.seniors[3]
    autre = world.seniors[2]
    for profile in world.seniors + world.assistants:
        if profile.id not in (tardif.id, autre.id):
            campaign_service.validate_submission(session, world.submission(profile))

    Clock.freeze(CAMPAIGN_DEADLINE + timedelta(minutes=5))
    campaign_service.close_campaign(session, world.campaign, world.admin)

    # Prolongation : la conversion est reprogrammée.
    nouvelle_echeance = CAMPAIGN_DEADLINE + timedelta(days=3)
    campaign_service.extend_deadline(
        session, world.campaign, nouvelle_echeance, world.admin, "délai supplémentaire"
    )
    assert world.campaign.default_conversion_done_at is None
    assert world.campaign.state is CampaignState.OUVERTE

    # Validation tardive pendant le délai de grâce.
    Clock.freeze(nouvelle_echeance + timedelta(hours=1))
    campaign_service.close_campaign(session, world.campaign, world.admin)
    Clock.freeze(nouvelle_echeance + timedelta(hours=10))
    campaign_service.validate_submission(session, world.submission(tardif))

    Clock.freeze(world.campaign.grace_deadline + timedelta(hours=1))
    converties = campaign_service.apply_default_availability(
        session, world.campaign, world.admin
    )
    assert tardif.code not in converties, (
        "Une validation intervenue pendant le délai de grâce annule la conversion."
    )
    assert world.submission(tardif).state is SubmissionState.VERROUILLEE
    assert world.submission(tardif).validated_at is not None

    # Aucun double événement de conversion.
    with pytest.raises(campaign_service.CampaignError):
        campaign_service.apply_default_availability(session, world.campaign, world.admin)

    # Aucun doublon de notification pour la personne convertie.
    for profile in (tardif, autre):
        messages = _notifications(session, "DISPO_PAR_DEFAUT", profile.id)
        assert len(messages) <= 1


# --------------------------------------------------------------------------- #
# Tests 9 et 39 — paires de jours fériés
# --------------------------------------------------------------------------- #


def _creer_paire(world):
    """Paire fictive dont chaque membre porte un vrai jour férié.

    L'obligation porte désormais sur le **jour férié** lui-même : les occurrences
    concernées doivent donc être du type ``JOUR_FERIE``.
    """
    session = world.session
    debut = world.quarter.start_date
    membres = [
        ("Premier membre", debut + timedelta(days=2), debut + timedelta(days=3)),
        ("Second membre", debut + timedelta(days=10), debut + timedelta(days=11)),
    ]
    paire = catalog_service.create_holiday_pair(
        session, "PAIRE_TEST", "Paire fictive de test", membres
    )
    ferie = session.execute(
        select(GardeType).where(GardeType.code == "JOUR_FERIE")
    ).scalar_one()
    # Le second jour de chaque membre devient le jour férié effectif.
    for _label, _d1, jour_ferie in membres:
        occurrence = session.execute(
            select(GardeOccurrence).where(GardeOccurrence.local_date == jour_ferie)
        ).scalar_one()
        occurrence.garde_type_id = ferie.id
    session.flush()
    return paire


def test_09_paires_feriees_le_jour_ferie_doit_etre_vert(world):
    """Règle confirmée le 04/09/2026 : le jour férié choisi doit être vert déclaré."""
    session = world.session
    paire = _creer_paire(world)
    cible = world.seniors[0]

    # Rouge partout : la paire n'est pas couverte.
    for membre in paire.members:
        for occurrence in catalog_service.occurrences_for_member(session, membre):
            world.set_color(cible, occurrence, Color.ROUGE)
    manquantes = campaign_service.missing_holiday_pairs(session, world.submission(cible))
    assert manquantes and "Paire fictive de test" in manquantes[0]
    with pytest.raises(campaign_service.CampaignError):
        campaign_service.validate_submission(session, world.submission(cible))

    # Un orange sur le jour férié ne suffit **pas**.
    membre = paire.members[0]
    jour_ferie = next(
        o
        for o in catalog_service.occurrences_for_member(session, membre)
        if o.garde_type.code == "JOUR_FERIE"
    )
    world.set_color(cible, jour_ferie, Color.ORANGE)
    assert campaign_service.missing_holiday_pairs(session, world.submission(cible))

    # Une veille verte seule ne suffit pas davantage.
    veille = next(
        o
        for o in catalog_service.occurrences_for_member(session, membre)
        if o.garde_type.code != "JOUR_FERIE"
    )
    world.set_color(cible, veille, Color.VERT)
    assert campaign_service.missing_holiday_pairs(session, world.submission(cible))

    # Un vert déclaré sur le jour férié satisfait l'obligation.
    world.set_color(cible, jour_ferie, Color.VERT)
    assert campaign_service.missing_holiday_pairs(session, world.submission(cible)) == []
    campaign_service.validate_submission(session, world.submission(cible))


def test_39_dispo_par_defaut_ne_couvre_pas_une_paire(world):
    """La disponibilité par défaut ne satisfait aucune obligation de paire."""
    session = world.session
    _creer_paire(world)
    non_repondant = world.seniors[3]
    session.query(Availability).filter(
        Availability.submission_id == world.submission(non_repondant).id
    ).delete()
    session.flush()

    # Aucune couleur déclarée : la paire est manquante.
    assert campaign_service.missing_holiday_pairs(
        session, world.submission(non_repondant)
    )

    for profile in world.seniors + world.assistants:
        if profile.id != non_repondant.id:
            campaign_service.validate_submission(session, world.submission(profile))
    Clock.freeze(CAMPAIGN_DEADLINE + timedelta(minutes=5))
    campaign_service.close_campaign(session, world.campaign, world.admin)
    Clock.freeze(world.campaign.grace_deadline + timedelta(hours=1))
    campaign_service.apply_default_availability(session, world.campaign, world.admin)

    # Après conversion régulière, la disponibilité par défaut ne couvre **pas**
    # la paire : seul un vert déclaré sur le jour férié satisfait l'obligation.
    assert campaign_service.missing_holiday_pairs(
        session, world.submission(non_repondant), include_default=True
    )
    # …et elle n'est jamais présentée comme une réponse volontaire.
    entree = session.execute(
        select(Availability).where(
            Availability.submission_id == world.submission(non_repondant).id
        )
    ).scalars().first()
    assert entree.is_declared is False
    assert entree.display_label == (
        "Disponible par défaut — non confirmé par la personne"
    )

    # …mais elle n'ouvre **aucune** reprise (arbitrage du client du 03/09/2026).
    Clock.freeze(datetime(2026, 12, 29, 14, 0))
    run = planning_service.run_engine(
        session, world.quarter, admin=world.admin, seed=99, variants=1
    )
    version = planning_service.create_version_from_proposal(
        session, run.proposals[0], world.admin
    )
    planning_service.validate_version(session, version, world.admin)
    planning_service.publish_version(session, version, world.admin)

    affectation = next(
        a for a in version.assignments
        if a.profile_id != non_repondant.id
        and a.post.required_status.value == "SENIOR"
    )
    titulaire = session.get(type(non_repondant), affectation.profile_id)
    demande = handover_service.request_handover(session, affectation, titulaire)
    for kind in (WaveKind.VERTE, WaveKind.UNIQUE):
        eligibles = handover_service.eligible_profiles(session, demande, kind)
        assert non_repondant not in eligibles, (
            "Une disponibilité par défaut non confirmée est exclue de toutes les "
            "reprises."
        )
