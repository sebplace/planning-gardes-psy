"""Tests P1 (tranche 5) : couverture horaire continue.

Arbitrages du client du 03/09/2026. Données fictives.
Voir docs/AUDIT_DIFFERENTIEL.md (P1.9bis, P1.11).
"""

from __future__ import annotations

from datetime import date, time, timedelta

from sqlalchemy import select

from app.models import GardeOccurrence, GardeType, Quarter, Year
from app.services import catalog_service


def _types(session) -> dict[str, GardeType]:
    catalog_service.ensure_reference_data(session)
    return {t.code: t for t in session.execute(select(GardeType)).scalars()}


def test_horaires_vendredi_et_veille_finissent_a_9h(session):
    """Vendredi 17h -> samedi 9h ; veille ouvrable de férié 17h -> férié 9h."""
    types = _types(session)
    for code in ("NUIT_VENDREDI", "VEILLE_FERIE"):
        assert types[code].start_time == time(17, 0), code
        assert types[code].end_time == time(9, 0), code
        assert types[code].duration_class == "NUIT_16H", code
    # La nuit de semaine reste 17h -> 8h : la relève y est assurée en journée.
    assert types["NUIT_SEMAINE"].end_time == time(8, 0)


def test_plus_aucun_horaire_a_valider(session):
    """Les six horaires sont confirmés : Q-03 est close."""
    types = _types(session)
    assert not any(t.horaires_a_valider for t in types.values())


def test_vendredi_ferie_reste_classe_ferie(session):
    """Un vendredi férié est un jour férié, pas une nuit du vendredi."""
    _types(session)
    vendredi = date(2027, 4, 2)
    assert vendredi.weekday() == 4
    assert catalog_service.resolve_type_code(vendredi, {vendredi}) == "JOUR_FERIE"


def test_veille_deja_week_end_ne_cree_pas_de_veille(session):
    """Si la veille tombe un samedi ou un dimanche, elle garde son propre type."""
    _types(session)
    samedi = date(2027, 5, 1)
    assert samedi.weekday() == 5
    dimanche = samedi + timedelta(days=1)
    # Le dimanche est férié : le samedi reste un samedi.
    assert catalog_service.resolve_type_code(samedi, {dimanche}) == "SAMEDI"
    # Le dimanche férié reste férié.
    assert catalog_service.resolve_type_code(dimanche, {dimanche}) == "JOUR_FERIE"
    # Un lundi férié ne transforme pas le dimanche qui le précède.
    lundi = dimanche + timedelta(days=1)
    assert catalog_service.resolve_type_code(dimanche, {lundi}) == "DIMANCHE"


def test_veille_ouvrable_de_ferie_est_bien_une_veille(session):
    """Une veille en semaine, y compris un vendredi, devient une veille de férié."""
    _types(session)
    jeudi = date(2027, 5, 6)
    assert jeudi.weekday() == 3
    assert catalog_service.resolve_type_code(jeudi, {jeudi + timedelta(days=1)}) == (
        "VEILLE_FERIE"
    )
    vendredi = date(2027, 4, 30)
    assert vendredi.weekday() == 4
    assert catalog_service.resolve_type_code(
        vendredi, {vendredi + timedelta(days=1)}
    ) == "VEILLE_FERIE"


def test_aucune_occurrence_en_double_par_date(session):
    """Une seule occurrence par date, jamais de veille supplémentaire."""
    _types(session)
    year = catalog_service.create_year(
        session, "2027-couverture", date(2027, 1, 1), date(2027, 12, 31)
    )
    quarter = session.execute(
        select(Quarter).where(Quarter.year_id == year.id, Quarter.index == 2)
    ).scalar_one()
    # Fériés fictifs : un vendredi, un samedi, un lundi.
    feries = {date(2027, 4, 2), date(2027, 5, 1), date(2027, 5, 17)}
    catalog_service.generate_occurrences(session, quarter, holidays=feries)

    occurrences = list(
        session.execute(
            select(GardeOccurrence)
            .where(GardeOccurrence.quarter_id == quarter.id)
            .order_by(GardeOccurrence.local_date)
        ).scalars()
    )
    dates = [o.local_date for o in occurrences]
    assert len(dates) == len(set(dates))
    attendu = (quarter.end_date - quarter.start_date).days + 1
    assert len(dates) == attendu


def test_aucun_trou_avant_une_releve_de_9h(session):
    """Invariant de continuité demandé par le client.

    Toute garde suivie d'une garde qui démarre à 9 h doit se terminer exactement à
    l'heure de démarrage de la suivante. C'est ce qui interdit le trou de 8 h à 9 h.
    """
    _types(session)
    year = catalog_service.create_year(
        session, "2027-continuite", date(2027, 1, 1), date(2027, 12, 31)
    )
    quarter = session.execute(
        select(Quarter).where(Quarter.year_id == year.id, Quarter.index == 2)
    ).scalar_one()
    feries = {date(2027, 4, 2), date(2027, 5, 1), date(2027, 5, 17), date(2027, 5, 6)}
    catalog_service.generate_occurrences(session, quarter, holidays=feries)

    occurrences = list(
        session.execute(
            select(GardeOccurrence)
            .where(GardeOccurrence.quarter_id == quarter.id)
            .order_by(GardeOccurrence.local_date)
        ).scalars()
    )
    trous = []
    for precedente, suivante in zip(occurrences, occurrences[1:]):
        if suivante.local_date != precedente.local_date + timedelta(days=1):
            continue
        if suivante.start_at.time() != time(9, 0):
            continue
        if precedente.end_at != suivante.start_at:
            trous.append(
                (
                    precedente.local_date,
                    precedente.end_at.isoformat(),
                    suivante.start_at.isoformat(),
                )
            )
    assert trous == []
