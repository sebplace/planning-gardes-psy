"""Tests P1 (tranche 1) : horaires confirmés et interdiction de l'orange en L1.

Données fictives. Voir docs/AUDIT_DIFFERENTIEL.md (P1.9, P1.15).
"""

from __future__ import annotations

from datetime import time

from sqlalchemy import select

from app.engine.types import H_ORANGE_L1
from app.models import Color, CoverageMode, GardeType, Line
from app.services import catalog_service, engine_bridge


def test_horaires_confirmes(session):
    catalog_service.ensure_reference_data(session)
    types = {t.code: t for t in session.execute(select(GardeType)).scalars()}
    # Lundi à jeudi : 17h -> 8h.
    assert types["NUIT_SEMAINE"].start_time == time(17, 0)
    assert types["NUIT_SEMAINE"].end_time == time(8, 0)
    # Samedi, dimanche, jour férié : 9h -> 9h.
    for code in ("SAMEDI", "DIMANCHE", "JOUR_FERIE"):
        assert types[code].start_time == time(9, 0), code
        assert types[code].end_time == time(9, 0), code


def test_absence_des_anciens_horaires(session):
    catalog_service.ensure_reference_data(session)
    types = list(session.execute(select(GardeType)).scalars())
    for t in types:
        # Plus aucun début à 20h.
        assert t.start_time != time(20, 0), t.code
        # Plus aucun week-end/férié de 8h à 8h.
        assert not (t.start_time == time(8, 0) and t.end_time == time(8, 0)), t.code


def test_orange_interdit_en_l1(world):
    """Un senior orange ne peut pas être affecté en première ligne (mode A)."""
    occ_a = next(o for o in world.occurrences if o.effective_mode is CoverageMode.A)
    post_l1 = next(p for p in occ_a.posts if p.line is Line.L1)
    senior = world.seniors[0]
    world.set_color(senior, occ_a, Color.ORANGE)
    rejection = engine_bridge.check_assignment(world.session, post_l1, senior)
    assert rejection is not None
    assert rejection.constraint_code == H_ORANGE_L1


def test_orange_possible_en_l2(world):
    """Un senior orange reste possible en deuxième ligne (mode B)."""
    occ_b = next(o for o in world.occurrences if o.effective_mode is CoverageMode.B)
    post_l2 = next(p for p in occ_b.posts if p.line is Line.L2)
    senior = world.seniors[1]
    world.set_color(senior, occ_b, Color.ORANGE)
    assert engine_bridge.check_assignment(world.session, post_l2, senior) is None
