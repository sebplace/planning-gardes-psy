"""Projections et simulations capacitaires.

Garde-fous (§8.4) :
  * un scénario porte visiblement la mention « simulation — non applicable au planning réel » ;
  * aucune IA ne décide quels paramètres seraient acceptables ;
  * une simulation ne modifie **jamais** comptes, quotas, disponibilités ni planning ;
  * la promotion d'un scénario vers une configuration réelle exige une action
    administrative explicite, une confirmation et une trace d'audit ;
  * toute formule non institutionnellement validée est étiquetée « hypothèse de démonstration ».
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import solve
from ..engine.projection import (
    DEMO_LABEL,
    AssistantGroup,
    CategoryVolume,
    ScenarioParams,
    SeniorGroup,
    build_synthetic_input,
    project_structural,
    sensitivity_matrix,
)
from ..models import Scenario, ScenarioResult, User
from . import audit_service
from .clock import Clock


class ProjectionError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Sérialisation
# --------------------------------------------------------------------------- #


def params_to_dict(params: ScenarioParams) -> dict:
    return params.as_dict()


def params_from_dict(data: dict) -> ScenarioParams:
    assistants = data["assistants"]
    seniors = data["seniors"]
    return ScenarioParams(
        name=data["name"],
        description=data.get("description", ""),
        ruleset_version=data.get("ruleset_version", "regles_demo_v1"),
        convert_uncovered_b_to_a=data.get("convert_uncovered_b_to_a", True),
        senior_load_threshold=data.get("senior_load_threshold"),
        categories=tuple(
            CategoryVolume(
                category_code=c["category_code"],
                label=c["label"],
                occurrences=int(c["occurrences"]),
                mode_b_target_share=float(c.get("mode_b_target_share", 1.0)),
                painful_weight=float(c.get("painful_weight", 1.0)),
            )
            for c in data["categories"]
        ),
        assistants=AssistantGroup(
            count=int(assistants["count"]),
            guards_per_assistant=float(assistants.get("guards_per_assistant", 6.0)),
            present_fraction=float(assistants.get("present_fraction", 1.0)),
            start_date=date.fromisoformat(assistants["start_date"])
            if assistants.get("start_date")
            else None,
            end_date=date.fromisoformat(assistants["end_date"])
            if assistants.get("end_date")
            else None,
            monthly_cap=assistants.get("monthly_cap"),
            months=float(assistants.get("months", 12.0)),
            monthly_cap_is_institutional=bool(
                assistants.get("monthly_cap_is_institutional", False)
            ),
        ),
        seniors=SeniorGroup(
            quotite_tenths=tuple(int(x) for x in seniors["quotite_tenths"]),
            exemption_ratios=tuple(float(x) for x in seniors.get("exemption_ratios", [])),
            max_l1_per_full_time=seniors.get("max_l1_per_full_time"),
            max_l2_per_full_time=seniors.get("max_l2_per_full_time"),
            max_total_per_full_time=seniors.get("max_total_per_full_time"),
        ),
    )


# --------------------------------------------------------------------------- #
# Enregistrement et calcul
# --------------------------------------------------------------------------- #


def save_scenario(
    session: Session, params: ScenarioParams, user: User | None = None
) -> Scenario:
    scenario = Scenario(
        name=params.name,
        description=params.description,
        params_json=json.dumps(params_to_dict(params), ensure_ascii=False),
        ruleset_version=params.ruleset_version,
        created_by_id=user.id if user else None,
        is_demo_hypothesis=True,
    )
    session.add(scenario)
    session.flush()
    audit_service.record(
        session, "SCENARIO_ENREGISTRE", "scenario", scenario.id,
        {"nom": params.name, "avertissement": DEMO_LABEL}, actor=user,
    )
    return scenario


def duplicate_scenario(session: Session, scenario: Scenario, new_name: str, user: User | None):
    params = params_from_dict(json.loads(scenario.params_json))
    clone = ScenarioParams(
        name=new_name,
        description=params.description,
        categories=params.categories,
        assistants=params.assistants,
        seniors=params.seniors,
        convert_uncovered_b_to_a=params.convert_uncovered_b_to_a,
        senior_load_threshold=params.senior_load_threshold,
        ruleset_version=params.ruleset_version,
    )
    return save_scenario(session, clone, user)


def compute(
    session: Session,
    scenario: Scenario,
    assistant_counts: list[int] | None = None,
    guards_per_assistant: list[float] | None = None,
    with_feasibility: bool = True,
    simulation_start: date | None = None,
    seed: int = 20260901,
) -> ScenarioResult:
    """Calcule projection structurelle, matrice de sensibilité et, si demandé,
    une simulation de faisabilité exécutée par le **même moteur** que le planning réel.

    N'écrit que dans ``scenarios`` et ``scenario_results``.
    """
    params = params_from_dict(json.loads(scenario.params_json))
    structural = project_structural(params)

    matrix = sensitivity_matrix(
        params,
        assistant_counts or [2, 3, 4, 5, 6],
        guards_per_assistant or [4.0, 6.0, 8.0, 10.0],
    )

    feasibility: dict = {"execute": False}
    if with_feasibility:
        synthetic = build_synthetic_input(
            params, simulation_start or date(2027, 1, 4), weeks=13, seed=seed
        )
        solutions = solve(synthetic, variants=1)
        solution = solutions[0] if solutions else None
        if solution is not None:
            feasibility = {
                "execute": True,
                "moteur": solution.engine_version,
                "graine": solution.seed,
                "empreinte_entrees": solution.input_snapshot_hash,
                "realisable": solution.feasible,
                "postes": len(synthetic.posts),
                "postes_non_pourvus": len(solution.unfilled),
                "score": solution.score_total,
                "oranges_utilises": len(solution.orange_used),
                "tensions": solution.tensions,
                "detail_non_pourvus": [u.to_text() for u in solution.unfilled[:20]],
                "avertissement": DEMO_LABEL,
            }

    verdict = structural.verdict
    if with_feasibility and feasibility.get("execute") and not feasibility["realisable"]:
        verdict = "non couvrable avec ces paramètres"
        structural.reasons.append(
            "La simulation de faisabilité laisse des postes non pourvus : l'équilibre "
            "théorique n'est pas planifiable avec ces disponibilités simulées."
        )

    result = ScenarioResult(
        scenario_id=scenario.id,
        computed_at=Clock.now(),
        structural_json=json.dumps(_structural_payload(structural), ensure_ascii=False),
        sensitivity_json=json.dumps([asdict(cell) for cell in matrix], ensure_ascii=False),
        feasibility_json=json.dumps(feasibility, ensure_ascii=False),
        verdict=verdict,
    )
    session.add(result)
    session.flush()
    audit_service.record(
        session, "SCENARIO_CALCULE", "scenario_result", result.id,
        {"scenario": scenario.name, "verdict": verdict,
         "identite_arithmetique": structural.arithmetic_identity_holds,
         "avertissement": DEMO_LABEL},
        actor_label="SYSTEME",
    )
    return result


def _structural_payload(structural) -> dict:
    payload = asdict(structural)
    payload["identite_arithmetique"] = structural.arithmetic_identity_holds
    payload["avertissement"] = DEMO_LABEL
    return payload


# --------------------------------------------------------------------------- #
# Scénarios de référence demandés par le client (03/09/2026)
# --------------------------------------------------------------------------- #

ASSISTANTS_DEBUT = date(2026, 10, 19)
ASSISTANTS_FIN = date(2027, 10, 3)  # inclus
JOURS_MOYENS_PAR_MOIS = 30.4375

#: Trois comparaisons explicitement demandées : (quota global, plafond mensuel).
COMPARAISONS_ASSISTANTS = (
    (57.0, 6.0, "quota 57 avec plafond mensuel 6"),
    (68.0, 7.0, "quota 68 avec plafond mensuel 7"),
    (68.0, 6.0, "contrainte : quota 68 avec plafond mensuel 6"),
)


def periode_assistants_en_mois(
    debut: date = ASSISTANTS_DEBUT, fin: date = ASSISTANTS_FIN
) -> float:
    """Durée de la période assistante en mois, bornes incluses.

    Calculée depuis les dates réelles, jamais codée en dur.
    """
    jours = (fin - debut).days + 1
    return jours / JOURS_MOYENS_PAR_MOIS


def scenarios_assistants_reference(
    categories: tuple[CategoryVolume, ...],
    seniors: SeniorGroup,
    nb_assistants: int = 3,
    debut: date = ASSISTANTS_DEBUT,
    fin: date = ASSISTANTS_FIN,
) -> list[ScenarioParams]:
    """Construit les trois scénarios de comparaison demandés par le client.

    Le quota global et le plafond mensuel restent **deux paramètres distincts** :
    le premier est une cible sur toute la période, le second une borne mensuelle.
    Les deux sont des hypothèses de simulation, jamais des règles institutionnelles.
    """
    mois = periode_assistants_en_mois(debut, fin)
    scenarios: list[ScenarioParams] = []
    for quota, plafond, libelle in COMPARAISONS_ASSISTANTS:
        scenarios.append(
            ScenarioParams(
                name=libelle,
                description=(
                    f"{nb_assistants} assistants du {debut.isoformat()} au "
                    f"{fin.isoformat()} inclus, soit {mois:.2f} mois. "
                    f"Quota global {quota:g} garde(s) par assistant, plafond mensuel "
                    f"{plafond:g}. Hypothèse de simulation : le plafond n'a pas été "
                    "chiffré institutionnellement et ne devient jamais une règle."
                ),
                categories=categories,
                assistants=AssistantGroup(
                    count=nb_assistants,
                    guards_per_assistant=quota,
                    start_date=debut,
                    end_date=fin,
                    monthly_cap=plafond,
                    months=mois,
                    monthly_cap_is_institutional=False,
                ),
                seniors=seniors,
            )
        )
    return scenarios


def comparer_scenarios_assistants(
    categories: tuple[CategoryVolume, ...],
    seniors: SeniorGroup,
    nb_assistants: int = 3,
) -> list[dict]:
    """Calcule les trois projections de référence, sans rien écrire en base."""
    sorties = []
    for params in scenarios_assistants_reference(categories, seniors, nb_assistants):
        structural = project_structural(params)
        sorties.append(
            {
                "scenario": params.name,
                "quota_global_par_assistant": params.assistants.guards_per_assistant,
                "plafond_mensuel": params.assistants.monthly_cap,
                "mois": round(params.assistants.months, 3),
                "capacite_quota": structural.assistant_quota_capacity,
                "capacite_plafond": structural.assistant_cap_capacity,
                "saturation": structural.assistant_cap_saturation,
                "contrainte_active": structural.assistant_binding_constraint,
                "premieres_lignes_utilisees": structural.assistant_used,
                "verdict": structural.verdict,
                "alertes": structural.warnings,
                "avertissement": DEMO_LABEL,
            }
        )
    return sorties


def compare(session: Session, scenario_ids: list[int]) -> list[dict]:
    """Comparaison côte à côte. **Aucune modification opérationnelle.**"""
    out = []
    for scenario_id in scenario_ids:
        scenario = session.get(Scenario, scenario_id)
        if scenario is None:
            continue
        latest = session.execute(
            select(ScenarioResult)
            .where(ScenarioResult.scenario_id == scenario.id)
            .order_by(ScenarioResult.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        out.append(
            {
                "scenario": scenario.name,
                "hypotheses": json.loads(scenario.params_json),
                "resultat": json.loads(latest.structural_json) if latest else None,
                "faisabilite": json.loads(latest.feasibility_json) if latest else None,
                "verdict": latest.verdict if latest else "non calculé",
                "avertissement": DEMO_LABEL,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Promotion vers une configuration réelle
# --------------------------------------------------------------------------- #


def promote_to_configuration(
    session: Session,
    scenario: Scenario,
    admin: User | None,
    confirmed: bool = False,
    confirmation_text: str = "",
) -> dict:
    """Refuse toute promotion sans administrateur **et** confirmation explicite.

    Cette fonction ne modifie aucune donnée opérationnelle : elle produit un plan de
    modifications à appliquer manuellement, plus une trace d'audit.
    """
    if admin is None or not admin.is_admin:
        raise ProjectionError(
            "La promotion d'un scénario est réservée aux administrateurs."
        )
    if not confirmed or confirmation_text.strip().upper() != "JE CONFIRME":
        raise ProjectionError(
            "Promotion refusée : une confirmation administrative explicite est exigée. "
            "Un scénario reste une hypothèse tant qu'elle n'est pas confirmée."
        )
    params = params_from_dict(json.loads(scenario.params_json))
    structural = project_structural(params)
    plan = {
        "scenario": scenario.name,
        "verdict": structural.verdict,
        "postes_non_couverts": structural.posts_uncovered,
        "note": (
            "Aucune donnée opérationnelle n'a été modifiée. Ce plan doit être appliqué "
            "explicitement, écran par écran, par un administrateur."
        ),
        "avertissement_si_impossible": (
            structural.reasons if structural.posts_uncovered or structural.senior_deficit else []
        ),
    }
    audit_service.record(
        session, "SCENARIO_PROMU", "scenario", scenario.id, plan, actor=admin
    )
    return plan
