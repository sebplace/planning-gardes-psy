"""Tests P1 (tranche 4) : pondération de garde historisée (84/10) et interdiction
de l'orange à la saisie pour les assistants. Données fictives.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import Color, GardeWeightHistory, ProfessionalProfile, Status, User
from app.services import campaign_service, security

# Pondérations fictives du brief client (P1.1) : total 84/10 au 01/10/2026.
WEIGHTS = [7, 6, 7, 8, 8, 0, 7, 8, 6, 0, 3, 6, 6, 7, 5]


def _make_senior(session, index, weight, day):
    user = User(
        email=f"wsen{index:02d}@demo.invalid",
        display_name=f"Senior fictif {index}",
        password_hash=security.hash_password("demo"),
        is_medecin=True,
        is_admin=False,
    )
    session.add(user)
    session.flush()
    profile = ProfessionalProfile(user_id=user.id, code=f"WSEN-{index:02d}", status=Status.SENIOR)
    session.add(profile)
    session.flush()
    session.add(
        GardeWeightHistory(profile_id=profile.id, start_date=day, weight_tenths=weight)
    )
    session.flush()
    return profile


def test_ponderation_seniors_totale_84_sur_10(session):
    day = date(2026, 10, 1)
    assert len(WEIGHTS) == 15
    profiles = [_make_senior(session, i, w, day) for i, w in enumerate(WEIGHTS, start=1)]
    total = sum((p.garde_weight_on(day) or 0) for p in profiles)
    assert total == 84


def test_ponderation_respecte_les_dates_d_effet(session):
    profile = _make_senior(session, 1, 7, date(2026, 10, 1))
    assert profile.garde_weight_on(date(2026, 9, 30)) is None
    assert profile.garde_weight_on(date(2026, 10, 1)) == 7
    assert profile.garde_weight_on(date(2027, 1, 15)) == 7


def test_assistant_orange_refuse_a_la_saisie(world):
    occurrence = world.occurrences[0]
    submission = world.submission(world.assistants[0])
    with pytest.raises(campaign_service.CampaignError):
        campaign_service.set_availability(
            world.session, submission, occurrence, Color.ORANGE
        )


def test_senior_orange_autorise_a_la_saisie(world):
    occurrence = world.occurrences[0]
    submission = world.submission(world.seniors[0])
    entry = campaign_service.set_availability(
        world.session, submission, occurrence, Color.ORANGE
    )
    assert entry.color is Color.ORANGE
