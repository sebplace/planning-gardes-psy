"""Tests P2 (tranche 3) : intégrité de publication.

Au plus une version publiée par trimestre, garanti en base. Données fictives.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import ScheduleState, ScheduleVersion
from tests.conftest import publish_plan


def test_une_seule_version_publiee_par_trimestre(world):
    version = publish_plan(world)
    assert version.state is ScheduleState.PUBLIE
    # Tentative d'insérer une seconde version PUBLIE pour le même trimestre.
    doublon = ScheduleVersion(
        quarter_id=version.quarter_id, version_no=999, state=ScheduleState.PUBLIE
    )
    world.session.add(doublon)
    with pytest.raises(IntegrityError):
        world.session.flush()
    world.session.rollback()


def test_republication_bascule_l_ancienne_en_remplace(world):
    version = publish_plan(world)
    # Une nouvelle version validée du même trimestre, publiée, doit remplacer l'ancienne.
    from app.services import planning_service

    proposal = version.source_proposal_id
    # Reproduit une publication via le service (démotion atomique de l'ancienne).
    nouvelle = ScheduleVersion(
        quarter_id=version.quarter_id,
        version_no=version.version_no + 1,
        state=ScheduleState.VALIDE,
        source_proposal_id=proposal,
    )
    world.session.add(nouvelle)
    world.session.flush()
    planning_service.publish_version(world.session, nouvelle, world.admin)
    world.session.flush()
    world.session.refresh(version)
    assert version.state is ScheduleState.REMPLACE
    assert nouvelle.state is ScheduleState.PUBLIE
