"""Tests P1.1, P1.2, P1.4, P1.12, P1.13 : jeu de données métier et compteurs.

Arbitrages du client des 02 et 03/09/2026. Données fictives.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models import (
    ActivityPeriod,
    Color,
    Eligibility,
    GardeWeightHistory,
    Line,
    ProfessionalProfile,
    Status,
)
from app.services import counters_service
from tests.conftest import publish_plan

PONDERATIONS = [7, 6, 7, 8, 8, 0, 7, 8, 6, 0, 3, 6, 6, 7, 5]
EFFET = date(2026, 10, 1)


def test_la_somme_des_ponderations_transmises_fait_84_dixiemes():
    """Contrôle arithmétique direct sur la liste du client."""
    assert len(PONDERATIONS) == 15
    assert sum(PONDERATIONS) == 84


def _quinze_seniors(session):
    """Crée quinze seniors fictifs portant les pondérations du client."""
    from app.services import security
    from app.models import User

    profils = []
    for index, poids in enumerate(PONDERATIONS, start=1):
        user = User(
            email=f"pond{index:02d}@demo.invalid",
            display_name=f"Dr Pondération {index:02d} (fictif)",
            password_hash=security.hash_password("demo"),
            is_medecin=True,
        )
        session.add(user)
        session.flush()
        profil = ProfessionalProfile(
            user_id=user.id, code=f"PND-{index:02d}", status=Status.SENIOR
        )
        session.add(profil)
        session.flush()
        session.add(
            ActivityPeriod(profile_id=profil.id, start_date=date(2020, 1, 1))
        )
        session.add(
            GardeWeightHistory(
                profile_id=profil.id, start_date=EFFET, weight_tenths=poids
            )
        )
        profils.append(profil)
    session.flush()
    return profils


def test_somme_des_ponderations_en_base_au_1er_octobre_2026(session):
    _quinze_seniors(session)
    assert counters_service.somme_ponderations(session, EFFET) == pytest.approx(8.4)


def test_ponderation_inexistante_avant_sa_date_d_effet(session):
    profils = _quinze_seniors(session)
    poids, _ = counters_service.poids_a_la_date(
        session, profils[0], date(2026, 9, 30)
    )
    assert poids is None
    poids, effet = counters_service.poids_a_la_date(session, profils[0], EFFET)
    assert poids == 7
    assert effet == EFFET


def test_ponderation_distincte_de_la_quotite(session):
    """La pondération de garde ne se confond jamais avec la quotité de travail."""
    from app.models import QuotiteHistory

    profils = _quinze_seniors(session)
    session.add(
        QuotiteHistory(profile_id=profils[0].id, start_date=EFFET, tenths=10)
    )
    session.flush()
    poids, _ = counters_service.poids_a_la_date(session, profils[0], EFFET)
    quotite = session.execute(
        select(QuotiteHistory).where(QuotiteHistory.profile_id == profils[0].id)
    ).scalar_one()
    assert poids == 7
    assert quotite.tenths == 10


def test_deux_seniors_a_zero_ne_comptent_pas(session):
    profils = _quinze_seniors(session)
    zeros = [
        p
        for p in profils
        if counters_service.poids_a_la_date(session, p, EFFET)[0] == 0
    ]
    assert len(zeros) == 2


# --------------------------------------------------------------------------- #
# Six compteurs seniors
# --------------------------------------------------------------------------- #


def test_six_compteurs_seniors_toujours_presents(world):
    publish_plan(world)
    compteurs = counters_service.compteurs_senior(
        world.session, world.seniors[0], world.year
    )
    assert len(compteurs.cellules) >= 6
    cles = {c.cle for c in compteurs.cellules}
    for categorie in counters_service.CATEGORIES:
        for ligne in counters_service.LIGNES:
            assert (categorie, ligne.value) in cles


def test_les_six_compteurs_couvrent_toutes_les_gardes(world):
    publish_plan(world)
    for profil in world.seniors:
        compteurs = counters_service.compteurs_senior(
            world.session, profil, world.year
        )
        gardes = sum(
            1
            for a in world.version.assignments
            if a.profile_id == profil.id
        )
        assert compteurs.total_gardes == pytest.approx(float(gardes))


def test_compteur_pondere_vide_sans_ponderation_enregistree(world):
    """Sans pondération enregistrée, rien n'est deviné."""
    publish_plan(world)
    compteurs = counters_service.compteurs_senior(
        world.session, world.seniors[0], world.year
    )
    assert compteurs.ponderation_dixiemes is None
    assert compteurs.total_pondere == 0.0


def test_compteur_pondere_utilise_le_poids_de_la_date(world):
    publish_plan(world)
    profil = world.seniors[0]
    world.session.add(
        GardeWeightHistory(
            profile_id=profil.id, start_date=date(2020, 1, 1), weight_tenths=5
        )
    )
    world.session.flush()
    compteurs = counters_service.compteurs_senior(world.session, profil, world.year)
    assert compteurs.ponderation_dixiemes == 5
    assert compteurs.total_pondere == pytest.approx(compteurs.total_gardes * 0.5)


def test_tableau_seniors_couvre_tout_le_pool(world):
    publish_plan(world)
    tableau = counters_service.tableau_seniors(world.session, world.year)
    assert [t.profile_code for t in tableau] == sorted(
        p.code for p in world.seniors
    )


# --------------------------------------------------------------------------- #
# Sous-compteurs assistants
# --------------------------------------------------------------------------- #


def test_cinq_sous_compteurs_assistants(world):
    publish_plan(world)
    sous = counters_service.sous_compteurs_assistant(
        world.session, world.assistants[0], world.year
    )
    assert set(sous.compteurs) == set(counters_service.SOUS_COMPTEURS_ASSISTANTS)
    assert len(sous.compteurs) == 5


def test_les_sous_compteurs_sont_statistiques_et_non_contraignants(world):
    """Ils comptent, mais ne bloquent rien : le planning reste publié."""
    version = publish_plan(world)
    tableau = counters_service.tableau_assistants(world.session, world.year)
    assert tableau
    world.session.refresh(version)
    assert version.state.value == "PUBLIE"


def test_les_sous_compteurs_ne_comptent_pas_les_nuits_de_semaine(world):
    """Les cinq sous-compteurs portent sur vendredi, veille, samedi, dimanche, férié."""
    publish_plan(world)
    assert "NUIT_SEMAINE" not in counters_service.SOUS_COMPTEURS_ASSISTANTS
    for sous in counters_service.tableau_assistants(world.session, world.year):
        assert "NUIT_SEMAINE" not in sous.compteurs


# --------------------------------------------------------------------------- #
# Assistants : période et ligne
# --------------------------------------------------------------------------- #


def test_un_assistant_n_est_jamais_en_deuxieme_ligne(world):
    """Contrôlé par une contrainte ferme, indépendamment de toute éligibilité."""
    from app.engine.types import H_ASSISTANT_L2
    from app.services import engine_bridge

    publish_plan(world)
    poste_l2 = next(
        p
        for occurrence in world.occurrences
        for p in occurrence.posts
        if p.line is Line.L2
    )
    refus = engine_bridge.check_assignment(
        world.session, poste_l2, world.assistants[0]
    )
    assert refus is not None
    assert refus.constraint_code == H_ASSISTANT_L2


def test_la_periode_d_activite_borne_les_affectations(world):
    """Hors période d'activité, une affectation est refusée par contrainte ferme."""
    from app.engine.types import H_INACTIF
    from app.services import engine_bridge

    publish_plan(world)
    assistant = world.assistants[0]
    periode = world.session.execute(
        select(ActivityPeriod).where(ActivityPeriod.profile_id == assistant.id)
    ).scalar_one()
    periode.end_date = date(2026, 12, 31)
    world.session.flush()

    poste = next(
        p
        for occurrence in world.occurrences
        for p in occurrence.posts
        if p.line is Line.L1 and p.required_status is Status.ASSISTANT
    )
    world.set_color(assistant, poste.occurrence, Color.VERT)
    refus = engine_bridge.check_assignment(world.session, poste, assistant)
    assert refus is not None
    assert refus.constraint_code == H_INACTIF
