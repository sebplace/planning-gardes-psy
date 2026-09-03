"""Tests P1.6 : plafond mensuel administrable, jamais transformé en règle.

Arbitrage du client du 03/09/2026. Données fictives.
Voir docs/AUDIT_DIFFERENTIEL.md (P1.5, P1.6).
"""

from __future__ import annotations

from datetime import date

from app.engine.projection import CategoryVolume, SeniorGroup
from app.engine.types import H_PLAFOND_MENSUEL, Enforcement, MonthlyCapIn, Status
from app.models import Color, CoverageMode, Line
from app.services import engine_bridge, projection_service, quota_service

CATEGORIES = (
    CategoryVolume("NUITS_LJ", "Nuits de semaine", 200),
    CategoryVolume("WEEKENDS_VEILLES", "Week-ends et veilles", 110),
    CategoryVolume("FERIES", "Jours fériés", 40),
)
SENIORS = SeniorGroup(quotite_tenths=(7, 6, 7, 8, 8, 0, 7, 8, 6, 0, 3, 6, 6, 7, 5))


# --------------------------------------------------------------------------- #
# Le plafond ne devient jamais une règle par accident
# --------------------------------------------------------------------------- #


def test_plafond_non_chiffre_alerte_sans_inventer(world):
    """Aucun plafond enregistré : alerte explicite, aucune valeur devinée."""
    alertes = quota_service.monthly_cap_alerts(world.session, world.year)
    assert any("SENIOR" in a for a in alertes)
    assert any("ASSISTANT" in a for a in alertes)
    assert all("attendue" in a for a in alertes)
    # Aucun plafond n'a été créé au passage.
    assert quota_service.monthly_caps(world.session, world.year) == []


def test_plafond_saisi_mais_non_valide_reste_informatif(world):
    cap = quota_service.set_monthly_cap(
        world.session,
        world.year,
        world.admin,
        status=Status.ASSISTANT,
        max_per_month=6.0,
        enforcement=Enforcement.FERME,
        institutionally_validated=False,
    )
    assert cap.is_enforceable is False
    assert "non validé institutionnellement" in cap.alert


def test_plafond_valide_mais_souple_reste_informatif(world):
    cap = quota_service.set_monthly_cap(
        world.session,
        world.year,
        world.admin,
        status=Status.ASSISTANT,
        max_per_month=6.0,
        enforcement=Enforcement.SOUPLE,
        institutionally_validated=True,
    )
    assert cap.is_enforceable is False
    assert "souple" in cap.alert


def test_plafond_opposable_exige_les_trois_verrous(world):
    cap = quota_service.set_monthly_cap(
        world.session,
        world.year,
        world.admin,
        status=Status.ASSISTANT,
        max_per_month=6.0,
        enforcement=Enforcement.FERME,
        institutionally_validated=True,
    )
    assert cap.is_enforceable is True
    assert cap.alert is None


# --------------------------------------------------------------------------- #
# Effet moteur, seulement quand le plafond est réellement opposable
# --------------------------------------------------------------------------- #


def _premier_poste_assistant(world):
    for occurrence in world.occurrences:
        if occurrence.effective_mode is not CoverageMode.B:
            continue
        for post in occurrence.posts:
            if post.line is Line.L1:
                return post
    raise AssertionError("aucun poste L1 assistant dans l'univers de test")


def test_plafond_non_opposable_ne_bloque_pas(world):
    """Un plafond de simulation à 0 ne doit refuser aucune affectation."""
    quota_service.set_monthly_cap(
        world.session,
        world.year,
        world.admin,
        status=Status.ASSISTANT,
        max_per_month=0.0,
        enforcement=Enforcement.FERME,
        institutionally_validated=False,
    )
    post = _premier_poste_assistant(world)
    assistant = world.assistants[0]
    world.set_color(assistant, post.occurrence, Color.VERT)
    assert engine_bridge.check_assignment(world.session, post, assistant) is None


def test_plafond_opposable_a_zero_bloque_avec_motif_explicite(world):
    """Trois verrous franchis : le plafond devient une contrainte ferme nommée."""
    quota_service.set_monthly_cap(
        world.session,
        world.year,
        world.admin,
        status=Status.ASSISTANT,
        max_per_month=0.5,
        enforcement=Enforcement.FERME,
        institutionally_validated=True,
        label="plafond mensuel de test",
    )
    post = _premier_poste_assistant(world)
    assistant = world.assistants[0]
    world.set_color(assistant, post.occurrence, Color.VERT)
    refus = engine_bridge.check_assignment(world.session, post, assistant)
    assert refus is not None
    assert refus.constraint_code == H_PLAFOND_MENSUEL
    assert "plafond mensuel de test" in refus.detail


def test_cap_in_verrous():
    """Contrôle unitaire des trois verrous, hors base."""
    base = dict(profile_id=None, status=Status.ASSISTANT, max_per_month=6.0)
    assert not MonthlyCapIn(**base).is_enforceable
    assert not MonthlyCapIn(
        **base, enforcement=Enforcement.FERME
    ).is_enforceable
    assert not MonthlyCapIn(**base, institutionally_validated=True).is_enforceable
    assert MonthlyCapIn(
        **base, enforcement=Enforcement.FERME, institutionally_validated=True
    ).is_enforceable


# --------------------------------------------------------------------------- #
# Les trois scénarios de comparaison demandés
# --------------------------------------------------------------------------- #


def test_periode_assistants_fait_50_semaines():
    mois = projection_service.periode_assistants_en_mois()
    jours = (
        projection_service.ASSISTANTS_FIN - projection_service.ASSISTANTS_DEBUT
    ).days + 1
    assert jours == 350  # 50 semaines exactes, bornes incluses
    assert 11.4 < mois < 11.6


def test_trois_scenarios_de_comparaison():
    lignes = projection_service.comparer_scenarios_assistants(CATEGORIES, SENIORS)
    assert [ligne["scenario"] for ligne in lignes] == [
        "quota 57 avec plafond mensuel 6",
        "quota 68 avec plafond mensuel 7",
        "contrainte : quota 68 avec plafond mensuel 6",
    ]
    for ligne in lignes:
        assert ligne["quota_global_par_assistant"] is not None
        assert ligne["plafond_mensuel"] is not None
        # Quota global et plafond mensuel restent deux paramètres distincts.
        assert ligne["capacite_quota"] != ligne["capacite_plafond"]
        assert any("hypothèse de démonstration" in a for a in ligne["alertes"])


def test_le_troisieme_scenario_est_bien_le_plus_contraint():
    lignes = projection_service.comparer_scenarios_assistants(CATEGORIES, SENIORS)
    saturations = [ligne["saturation"] for ligne in lignes]
    assert saturations[2] == max(saturations)
    assert saturations[2] > 0.95
    contrainte = lignes[2]
    assert any("marge de manœuvre" in a for a in contrainte["alertes"])
    # Les deux premiers scénarios laissent de la marge.
    assert saturations[0] < 0.95
    assert saturations[1] < 0.95


def test_les_scenarios_partent_des_dates_reelles():
    scenarios = projection_service.scenarios_assistants_reference(CATEGORIES, SENIORS)
    for params in scenarios:
        assert params.assistants.start_date == date(2026, 10, 19)
        assert params.assistants.end_date == date(2027, 10, 3)
        assert params.assistants.count == 3
        assert params.assistants.monthly_cap_is_institutional is False
