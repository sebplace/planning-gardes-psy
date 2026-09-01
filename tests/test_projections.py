"""Tests du module de projections et de simulations capacitaires.

Couvre les exigences §22 : 25, 26, 27, 28, 30.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import func, select

from app.engine.projection import (
    AssistantGroup,
    CategoryVolume,
    ScenarioParams,
    SeniorGroup,
    project_structural,
    sensitivity_matrix,
)
from app.models import (
    Assignment,
    Availability,
    GardeOccurrence,
    ProfessionalProfile,
    QuotaTarget,
    ScheduleVersion,
    Submission,
    User,
)
from app.services import projection_service


def _params(assistants: int = 4, guards: float = 8.0, convert: bool = True):
    return ScenarioParams(
        name=f"Scénario {assistants} × {guards}",
        categories=(
            CategoryVolume("NUITS_LJ", "Nuits lundi-jeudi", 52, 1.0),
            CategoryVolume("WEEKENDS_VEILLES", "Week-ends et veilles", 26, 1.0, 1.5),
            CategoryVolume("FERIES", "Jours fériés", 10, 1.0, 2.0),
        ),
        assistants=AssistantGroup(count=assistants, guards_per_assistant=guards),
        seniors=SeniorGroup(
            quotite_tenths=(10, 10, 10, 8, 8, 5),
            exemption_ratios=(0.0, 0.0, 0.0, 0.0, 0.5, 0.0),
            max_total_per_full_time=30.0,
        ),
        convert_uncovered_b_to_a=convert,
        senior_load_threshold=25.0,
    )


# --------------------------------------------------------------------------- #
# Test 25 — variation du nombre d'assistants
# --------------------------------------------------------------------------- #


def test_25_variation_du_nombre_d_assistants_recalcule_les_charges(world):
    resultats = [project_structural(_params(assistants=n)) for n in (0, 2, 4, 6, 8)]

    l1_seniors = [r.senior_l1 for r in resultats]
    l2_seniors = [r.senior_l2 for r in resultats]

    assert l1_seniors == sorted(l1_seniors, reverse=True), (
        "Plus d'assistants doit réduire la première ligne résiduelle des seniors."
    )
    assert l2_seniors == sorted(l2_seniors), (
        "Plus d'assistants signifie plus de deuxièmes lignes à assurer derrière eux."
    )

    total_occurrences = sum(c.occurrences for c in _params().categories)
    for resultat in resultats:
        # Sans assistant, tout bascule en mode A : aucun poste de deuxième ligne.
        assert resultat.senior_l1 + resultat.senior_l2 == resultat.senior_total
        assert resultat.senior_l1 + (resultat.senior_l2) <= 2 * total_occurrences
    assert resultats[0].senior_l2 == 0
    assert resultats[0].senior_l1 == total_occurrences

    # La matrice de sensibilité croise bien les deux axes.
    cellules = sensitivity_matrix(_params(), [2, 4, 6], [4.0, 8.0])
    assert len(cellules) == 6
    par_axe = {(c.assistants, c.guards_per_assistant): c for c in cellules}
    assert par_axe[(6, 8.0)].senior_l1_residual < par_axe[(2, 4.0)].senior_l1_residual
    assert par_axe[(6, 8.0)].senior_l2_volume > par_axe[(2, 4.0)].senior_l2_volume


# --------------------------------------------------------------------------- #
# Test 26 — égalité arithmétique conservée
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("quota", [0.0, 2.0, 4.0, 8.0, 12.0, 30.0])
def test_26_egalite_arithmetique_conservee(world, quota):
    for convert in (True, False):
        resultat = project_structural(_params(assistants=3, guards=quota, convert=convert))
        assert resultat.arithmetic_identity_holds, (
            f"postes requis {resultat.posts_required} ≠ répartis "
            f"{resultat.posts_assigned} + non couverts {resultat.posts_uncovered}"
        )
        somme_categories = sum(
            c.posts_l1_assistant + c.posts_l1_senior + c.posts_l2_senior + c.posts_uncovered
            for c in resultat.per_category
        )
        assert somme_categories == resultat.posts_required


# --------------------------------------------------------------------------- #
# Test 27 — scénario impossible : déficit explicite
# --------------------------------------------------------------------------- #


def test_27_scenario_impossible_produit_un_deficit_explicite(world):
    resultat = project_structural(_params(assistants=1, guards=2.0, convert=False))
    assert resultat.verdict == "non couvrable avec ces paramètres"
    assert resultat.posts_uncovered > 0
    assert resultat.reasons, "Le déficit doit être explicitement motivé."
    assert any("sans assistant disponible" in r for r in resultat.reasons)
    # Aucune garde n'est fictivement couverte.
    assert resultat.posts_assigned + resultat.posts_uncovered == resultat.posts_required
    assert resultat.assistant_used <= resultat.assistant_capacity

    # Un déficit de capacité senior est également explicité.
    tendu = ScenarioParams(
        name="Capacité senior insuffisante",
        categories=_params().categories,
        assistants=AssistantGroup(count=0, guards_per_assistant=0),
        seniors=SeniorGroup(
            quotite_tenths=(10, 10), exemption_ratios=(0.0, 0.0),
            max_total_per_full_time=5.0,
        ),
    )
    resultat_tendu = project_structural(tendu)
    assert resultat_tendu.senior_deficit > 0
    assert any("capacité déclarée" in r for r in resultat_tendu.reasons)
    assert resultat_tendu.verdict == "non couvrable avec ces paramètres"


# --------------------------------------------------------------------------- #
# Test 28 — aucune modification opérationnelle
# --------------------------------------------------------------------------- #


def _empreinte_operationnelle(session) -> dict:
    modeles = [
        User, ProfessionalProfile, GardeOccurrence, QuotaTarget,
        Availability, Submission, Assignment, ScheduleVersion,
    ]
    empreinte = {m.__name__: session.execute(select(func.count()).select_from(m)).scalar()
                 for m in modeles}
    empreinte["quotas"] = sorted(
        (q.profile_id, q.category_id, q.line.value, q.target)
        for q in session.execute(select(QuotaTarget)).scalars()
    )
    empreinte["couleurs"] = sorted(
        (a.submission_id, a.occurrence_id, a.color.value, a.is_declared)
        for a in session.execute(select(Availability)).scalars()
    )
    return empreinte


def test_28_comparaison_de_scenarios_sans_effet_operationnel(world):
    session = world.session
    avant = _empreinte_operationnelle(session)

    scenario_a = projection_service.save_scenario(session, _params(assistants=2), world.admin)
    scenario_b = projection_service.save_scenario(session, _params(assistants=6), world.admin)
    projection_service.compute(session, scenario_a, with_feasibility=True,
                               simulation_start=date(2027, 1, 4), seed=11)
    projection_service.compute(session, scenario_b, with_feasibility=True,
                               simulation_start=date(2027, 1, 4), seed=11)
    comparaison = projection_service.compare(session, [scenario_a.id, scenario_b.id])

    assert len(comparaison) == 2
    for entree in comparaison:
        assert entree["avertissement"] == "simulation — non applicable au planning réel"
        assert entree["hypotheses"]["avertissement"]
        assert entree["resultat"]["identite_arithmetique"] is True

    apres = _empreinte_operationnelle(session)
    assert avant == apres, (
        "Une simulation ne doit modifier ni comptes, ni quotas, ni disponibilités, "
        "ni planning."
    )

    # La simulation de faisabilité utilise bien le moteur réel.
    for entree in comparaison:
        assert entree["faisabilite"]["execute"] is True
        assert "empreinte_entrees" in entree["faisabilite"]


# --------------------------------------------------------------------------- #
# Test 30 — promotion impossible sans confirmation explicite
# --------------------------------------------------------------------------- #


def test_30_promotion_exige_une_confirmation_administrative_explicite(world):
    session = world.session
    scenario = projection_service.save_scenario(
        session, _params(assistants=1, guards=2.0, convert=False), world.admin
    )
    projection_service.compute(session, scenario, with_feasibility=False)

    # Sans confirmation.
    with pytest.raises(projection_service.ProjectionError) as exc:
        projection_service.promote_to_configuration(session, scenario, world.admin)
    assert "confirmation administrative explicite" in str(exc.value)

    # Avec une confirmation incomplète.
    with pytest.raises(projection_service.ProjectionError):
        projection_service.promote_to_configuration(
            session, scenario, world.admin, confirmed=True, confirmation_text="oui"
        )

    # Par un non-administrateur, même confirmé.
    medecin = world.user_of(world.seniors[0])
    with pytest.raises(projection_service.ProjectionError) as exc:
        projection_service.promote_to_configuration(
            session, scenario, medecin, confirmed=True, confirmation_text="JE CONFIRME"
        )
    assert "réservée aux administrateurs" in str(exc.value)

    avant = _empreinte_operationnelle(session)
    plan = projection_service.promote_to_configuration(
        session, scenario, world.admin, confirmed=True, confirmation_text="JE CONFIRME"
    )
    assert plan["verdict"] == "non couvrable avec ces paramètres"
    assert plan["postes_non_couverts"] > 0
    assert plan["avertissement_si_impossible"]
    assert "Aucune donnée opérationnelle n'a été modifiée" in plan["note"]
    assert _empreinte_operationnelle(session) == avant

    from app.models import AuditEvent

    evenement = session.execute(
        select(AuditEvent).where(AuditEvent.action == "SCENARIO_PROMU")
    ).scalars().first()
    assert evenement is not None


def test_30b_promotion_via_api_refusee_sans_confirmation(world):
    from fastapi.testclient import TestClient

    from app.main import app

    session = world.session
    scenario = projection_service.save_scenario(session, _params(), world.admin)
    session.commit()

    client = TestClient(app)
    client.post(
        "/api/v1/auth/login", json={"email": world.admin.email, "password": "demo"}
    )
    reponse = client.post(f"/api/v1/scenarios/{scenario.id}/promote", json={})
    assert reponse.status_code == 403
    reponse = client.post(
        f"/api/v1/scenarios/{scenario.id}/promote",
        json={"confirmed": True, "confirmation_text": "JE CONFIRME"},
    )
    assert reponse.status_code == 200
    assert "Aucune donnée opérationnelle" in reponse.json()["note"]
