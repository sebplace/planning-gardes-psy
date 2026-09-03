"""Projections structurelles et simulation de faisabilité.

Deux niveaux, comme exigé au §8 du cahier des charges :

* **Projection structurelle** : arithmétique pure de la couverture, sans nom ni
  désidérata individuel.
* **Simulation de faisabilité** : construction de profils fictifs et exécution du
  **même moteur de contraintes** que le planning réel, afin de vérifier qu'un
  équilibre théorique reste réellement planifiable.

Garde-fou structurel : ce module ne lit ni n'écrit aucune donnée opérationnelle.
Il ne manipule que des paramètres de scénario. Toute formule de répartition
utilisée ici est une **hypothèse de démonstration**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .types import (
    AvailabilityIn,
    Color,
    CoverageMode,
    EngineInput,
    Enforcement,
    Line,
    PersonIn,
    PostIn,
    QuotaIn,
    RestRuleIn,
    RuleProfile,
    Status,
)

DEMO_LABEL = "simulation — non applicable au planning réel"
DEMO_FORMULA_LABEL = "hypothèse de démonstration"


# --------------------------------------------------------------------------- #
# Paramètres de scénario
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CategoryVolume:
    category_code: str
    label: str
    occurrences: int
    mode_b_target_share: float = 1.0  # part d'occurrences visées en mode B
    painful_weight: float = 1.0


@dataclass(frozen=True)
class AssistantGroup:
    count: int
    guards_per_assistant: float = 6.0  # premières lignes exigées sur la période
    present_fraction: float = 1.0  # part de la période réellement couverte
    start_date: date | None = None
    end_date: date | None = None
    # Plafond mensuel : hypothèse de simulation tant qu'il n'est pas chiffré
    # institutionnellement (arbitrage client du 03/09/2026). Il borne la capacité
    # dans la projection mais ne devient jamais une règle du planning.
    monthly_cap: float | None = None
    months: float = 12.0
    monthly_cap_is_institutional: bool = False

    @property
    def cap_capacity(self) -> float | None:
        """Capacité maximale imposée par le plafond mensuel, si un plafond est posé."""
        if self.monthly_cap is None:
            return None
        return self.count * self.monthly_cap * self.months * self.present_fraction

    @property
    def quota_capacity(self) -> float:
        return self.count * self.guards_per_assistant * self.present_fraction


@dataclass(frozen=True)
class SeniorGroup:
    quotite_tenths: tuple[int, ...]
    exemption_ratios: tuple[float, ...] = ()
    max_l1_per_full_time: float | None = None
    max_l2_per_full_time: float | None = None
    max_total_per_full_time: float | None = None

    @property
    def count(self) -> int:
        return len(self.quotite_tenths)

    def weights(self) -> list[float]:
        ratios = list(self.exemption_ratios) + [0.0] * (
            self.count - len(self.exemption_ratios)
        )
        return [
            (tenths / 10.0) * (1.0 - min(max(ratio, 0.0), 1.0))
            for tenths, ratio in zip(self.quotite_tenths, ratios)
        ]


@dataclass(frozen=True)
class ScenarioParams:
    name: str
    categories: tuple[CategoryVolume, ...]
    assistants: AssistantGroup
    seniors: SeniorGroup
    convert_uncovered_b_to_a: bool = True
    senior_load_threshold: float | None = None
    description: str = ""
    ruleset_version: str = "regles_demo_v1"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "ruleset_version": self.ruleset_version,
            "convert_uncovered_b_to_a": self.convert_uncovered_b_to_a,
            "senior_load_threshold": self.senior_load_threshold,
            "assistants": {
                "count": self.assistants.count,
                "guards_per_assistant": self.assistants.guards_per_assistant,
                "present_fraction": self.assistants.present_fraction,
                "start_date": self.assistants.start_date.isoformat()
                if self.assistants.start_date
                else None,
                "end_date": self.assistants.end_date.isoformat()
                if self.assistants.end_date
                else None,
                "monthly_cap": self.assistants.monthly_cap,
                "months": self.assistants.months,
                "monthly_cap_is_institutional": (
                    self.assistants.monthly_cap_is_institutional
                ),
            },
            "seniors": {
                "quotite_tenths": list(self.seniors.quotite_tenths),
                "exemption_ratios": list(self.seniors.exemption_ratios),
                "max_l1_per_full_time": self.seniors.max_l1_per_full_time,
                "max_l2_per_full_time": self.seniors.max_l2_per_full_time,
                "max_total_per_full_time": self.seniors.max_total_per_full_time,
            },
            "categories": [
                {
                    "category_code": c.category_code,
                    "label": c.label,
                    "occurrences": c.occurrences,
                    "mode_b_target_share": c.mode_b_target_share,
                    "painful_weight": c.painful_weight,
                }
                for c in self.categories
            ],
            "avertissement": DEMO_LABEL,
        }


# --------------------------------------------------------------------------- #
# Résultats
# --------------------------------------------------------------------------- #


@dataclass
class CategoryProjection:
    category_code: str
    label: str
    occurrences: int
    mode_b_target: int
    mode_b_effective: int
    mode_a: int
    posts_required: int
    posts_l1_assistant: int
    posts_l1_senior: int
    posts_l2_senior: int
    posts_uncovered: int


@dataclass
class SeniorLoad:
    index: int
    quotite_tenths: int
    exemption_ratio: float
    weight: float
    l1: float
    l2: float
    total: float


@dataclass
class StructuralProjection:
    scenario: dict
    per_category: list[CategoryProjection]
    total_occurrences: int
    posts_required: int
    posts_assigned: int
    posts_uncovered: int
    assistant_capacity: float
    assistant_used: int
    assistant_surplus: float
    senior_l1: int
    senior_l2: int
    senior_total: int
    senior_capacity_total: float | None
    senior_deficit: float
    per_senior: list[SeniorLoad]
    mean_per_senior: float
    min_per_senior: float
    max_per_senior: float
    dispersion: float
    threshold_exceeded: int
    verdict: str
    assistant_quota_capacity: float = 0.0
    assistant_cap_capacity: float | None = None
    assistant_cap_saturation: float | None = None
    assistant_binding_constraint: str = "quota global"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def arithmetic_identity_holds(self) -> bool:
        """Égalité stricte : postes requis == postes répartis + postes non couverts."""
        return self.posts_required == self.posts_assigned + self.posts_uncovered


def _largest_remainder(targets: list[int], capacity: int) -> list[int]:
    """Répartition déterministe d'une capacité entière entre plusieurs cibles."""
    total = sum(targets)
    if total <= 0 or capacity <= 0:
        return [0] * len(targets)
    if capacity >= total:
        return list(targets)
    raw = [capacity * t / total for t in targets]
    base = [int(x) for x in raw]
    remainder = capacity - sum(base)
    order = sorted(
        range(len(targets)), key=lambda i: (-(raw[i] - base[i]), i)
    )
    for i in order[:remainder]:
        base[i] += 1
    return [min(b, t) for b, t in zip(base, targets)]


def project_structural(params: ScenarioParams) -> StructuralProjection:
    """Projection arithmétique de la couverture. Aucun nom, aucun désidérata."""
    reasons: list[str] = []
    warnings: list[str] = [DEMO_LABEL]

    categories = list(params.categories)
    b_targets = [
        min(c.occurrences, int(round(c.mode_b_target_share * c.occurrences)))
        for c in categories
    ]
    capacity_raw = (
        params.assistants.count
        * params.assistants.guards_per_assistant
        * params.assistants.present_fraction
    )
    quota_capacity = params.assistants.quota_capacity
    cap_capacity = params.assistants.cap_capacity
    binding = "quota global"
    saturation: float | None = None
    if cap_capacity is not None and cap_capacity > 0:
        saturation = quota_capacity / cap_capacity
    if cap_capacity is not None and cap_capacity < quota_capacity - 1e-9:
        capacity_raw = cap_capacity
        binding = "plafond mensuel"
        warnings.append(
            f"Le plafond mensuel de {params.assistants.monthly_cap:g} garde(s) borne "
            f"la capacité à {cap_capacity:.1f} première(s) ligne(s), en dessous du "
            f"quota global de {quota_capacity:.1f}. C'est donc le plafond qui "
            "détermine le résultat de ce scénario."
        )
    if saturation is not None and saturation > 0.95:
        warnings.append(
            f"Scénario de contrainte : le quota global occupe {saturation * 100:.1f} % "
            f"de ce que le plafond mensuel de {params.assistants.monthly_cap:g} "
            "autorise sur la période. La marge de manœuvre mensuelle est quasi nulle, "
            "toute absence devient difficile à absorber."
        )
    if params.assistants.monthly_cap is not None and not (
        params.assistants.monthly_cap_is_institutional
    ):
        warnings.append(
            f"Plafond mensuel de {params.assistants.monthly_cap:g} : "
            f"{DEMO_FORMULA_LABEL}. Valeur non validée institutionnellement, elle ne "
            "devient jamais une règle du planning."
        )
    capacity_int = int(capacity_raw)
    allocated = _largest_remainder(b_targets, capacity_int)

    per_category: list[CategoryProjection] = []
    posts_required = 0
    posts_assigned = 0
    posts_uncovered = 0
    senior_l1 = senior_l2 = 0
    assistant_used = 0

    for cat, target, alloc in zip(categories, b_targets, allocated):
        if params.convert_uncovered_b_to_a:
            b_effective = alloc
            uncovered = 0
        else:
            b_effective = target
            uncovered = target - alloc
        mode_a = cat.occurrences - b_effective

        l1_assistant = alloc
        l1_senior = mode_a
        l2_senior = b_effective
        required = mode_a * 1 + b_effective * 2
        assigned = l1_assistant + l1_senior + l2_senior

        per_category.append(
            CategoryProjection(
                category_code=cat.category_code,
                label=cat.label,
                occurrences=cat.occurrences,
                mode_b_target=target,
                mode_b_effective=b_effective,
                mode_a=mode_a,
                posts_required=required,
                posts_l1_assistant=l1_assistant,
                posts_l1_senior=l1_senior,
                posts_l2_senior=l2_senior,
                posts_uncovered=uncovered,
            )
        )
        posts_required += required
        posts_assigned += assigned
        posts_uncovered += uncovered
        senior_l1 += l1_senior
        senior_l2 += l2_senior
        assistant_used += l1_assistant

    surplus = capacity_raw - assistant_used
    if surplus > 0.5:
        warnings.append(
            f"Capacité assistante non utilisée : {surplus:.1f} première(s) ligne(s). "
            "Le mode A absorbe le reste, les seniors travaillent alors seuls."
        )
    if posts_uncovered:
        reasons.append(
            f"{posts_uncovered} première(s) ligne(s) de mode B sans assistant disponible "
            "(conversion en mode A désactivée dans ce scénario)."
        )

    weights = params.seniors.weights()
    weight_sum = sum(weights)
    senior_total = senior_l1 + senior_l2
    per_senior: list[SeniorLoad] = []
    if weight_sum > 0:
        for i, w in enumerate(weights):
            share = w / weight_sum
            ratios = list(params.seniors.exemption_ratios) + [0.0] * (
                params.seniors.count - len(params.seniors.exemption_ratios)
            )
            per_senior.append(
                SeniorLoad(
                    index=i,
                    quotite_tenths=params.seniors.quotite_tenths[i],
                    exemption_ratio=ratios[i],
                    weight=round(w, 4),
                    l1=round(senior_l1 * share, 3),
                    l2=round(senior_l2 * share, 3),
                    total=round(senior_total * share, 3),
                )
            )
        warnings.append(
            "Répartition par senior au prorata de la quotité : " + DEMO_FORMULA_LABEL
        )
    else:
        reasons.append("Aucun senior disponible (quotités nulles ou exemptions totales).")

    totals = [s.total for s in per_senior] or [0.0]
    mean = sum(totals) / len(totals)
    capacity_total: float | None = None
    if params.seniors.max_total_per_full_time is not None:
        capacity_total = params.seniors.max_total_per_full_time * weight_sum
    elif (
        params.seniors.max_l1_per_full_time is not None
        and params.seniors.max_l2_per_full_time is not None
    ):
        capacity_total = (
            params.seniors.max_l1_per_full_time + params.seniors.max_l2_per_full_time
        ) * weight_sum

    deficit = 0.0
    if capacity_total is not None and senior_total > capacity_total + 1e-9:
        deficit = senior_total - capacity_total
        reasons.append(
            f"Charge senior de {senior_total} postes supérieure à la capacité déclarée "
            f"de {capacity_total:.1f} (déficit {deficit:.1f})."
        )

    threshold_exceeded = 0
    if params.senior_load_threshold is not None:
        threshold_exceeded = sum(1 for t in totals if t > params.senior_load_threshold + 1e-9)
        if threshold_exceeded:
            reasons.append(
                f"{threshold_exceeded} senior(s) au-dessus du seuil de charge "
                f"{params.senior_load_threshold} retenu pour ce scénario."
            )

    coverable = posts_uncovered == 0 and deficit == 0.0 and weight_sum > 0
    verdict = (
        "théoriquement couvrable"
        if coverable
        else "non couvrable avec ces paramètres"
    )

    return StructuralProjection(
        scenario=params.as_dict(),
        per_category=per_category,
        total_occurrences=sum(c.occurrences for c in categories),
        posts_required=posts_required,
        posts_assigned=posts_assigned,
        posts_uncovered=posts_uncovered,
        assistant_capacity=round(capacity_raw, 3),
        assistant_used=assistant_used,
        assistant_surplus=round(surplus, 3),
        senior_l1=senior_l1,
        senior_l2=senior_l2,
        senior_total=senior_total,
        senior_capacity_total=None if capacity_total is None else round(capacity_total, 3),
        senior_deficit=round(deficit, 3),
        per_senior=per_senior,
        mean_per_senior=round(mean, 3),
        min_per_senior=round(min(totals), 3),
        max_per_senior=round(max(totals), 3),
        dispersion=round(max(totals) - min(totals), 3),
        threshold_exceeded=threshold_exceeded,
        verdict=verdict,
        assistant_quota_capacity=round(quota_capacity, 3),
        assistant_cap_capacity=(
            None if cap_capacity is None else round(cap_capacity, 3)
        ),
        assistant_cap_saturation=(
            None if saturation is None else round(saturation, 4)
        ),
        assistant_binding_constraint=binding,
        reasons=reasons,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Matrice de sensibilité
# --------------------------------------------------------------------------- #


@dataclass
class SensitivityCell:
    assistants: int
    guards_per_assistant: float
    senior_l1_residual: int
    senior_l2_volume: int
    senior_total: int
    posts_uncovered: int
    verdict: str


def sensitivity_matrix(
    params: ScenarioParams,
    assistant_counts: list[int],
    guards_per_assistant: list[float],
) -> list[SensitivityCell]:
    """Matrice « nombre d'assistants × quota par assistant »."""
    cells: list[SensitivityCell] = []
    for n in assistant_counts:
        for g in guards_per_assistant:
            variant = ScenarioParams(
                name=f"{params.name} · {n} assistant(s) × {g}",
                categories=params.categories,
                assistants=AssistantGroup(
                    count=n,
                    guards_per_assistant=g,
                    present_fraction=params.assistants.present_fraction,
                    start_date=params.assistants.start_date,
                    end_date=params.assistants.end_date,
                ),
                seniors=params.seniors,
                convert_uncovered_b_to_a=params.convert_uncovered_b_to_a,
                senior_load_threshold=params.senior_load_threshold,
                ruleset_version=params.ruleset_version,
            )
            result = project_structural(variant)
            cells.append(
                SensitivityCell(
                    assistants=n,
                    guards_per_assistant=g,
                    senior_l1_residual=result.senior_l1,
                    senior_l2_volume=result.senior_l2,
                    senior_total=result.senior_total,
                    posts_uncovered=result.posts_uncovered,
                    verdict=result.verdict,
                )
            )
    return cells


# --------------------------------------------------------------------------- #
# Simulation de faisabilité : construction d'entrées fictives pour le vrai moteur
# --------------------------------------------------------------------------- #


def build_synthetic_input(
    params: ScenarioParams,
    start_date: date,
    weeks: int = 13,
    seed: int = 20260901,
    red_ratio: float = 0.1,
    orange_ratio: float = 0.2,
) -> EngineInput:
    """Construit un jeu d'entrées **fictif** exploitable par le moteur réel.

    Les profils portent des codes explicitement fictifs (`SIM-SEN-01`, `SIM-ASS-01`).
    Aucune donnée opérationnelle n'est lue.
    """
    import random as _random

    rng = _random.Random(seed)
    projection = project_structural(params)

    people: list[PersonIn] = []
    for i in range(params.seniors.count):
        people.append(
            PersonIn(
                profile_id=1000 + i,
                code=f"SIM-SEN-{i + 1:02d}",
                status=Status.SENIOR,
                eligible_l1=True,
                eligible_l2=True,
                quotite_tenths=params.seniors.quotite_tenths[i],
            )
        )
    for i in range(params.assistants.count):
        people.append(
            PersonIn(
                profile_id=2000 + i,
                code=f"SIM-ASS-{i + 1:02d}",
                status=Status.ASSISTANT,
                eligible_l1=True,
                eligible_l2=False,
                active_from=params.assistants.start_date,
                active_to=params.assistants.end_date,
            )
        )

    posts: list[PostIn] = []
    availabilities: list[AvailabilityIn] = []
    occurrence_id = 1
    post_id = 1
    day = start_date

    plan: list[tuple[str, str, float]] = []
    for cat in projection.per_category:
        plan.extend([(cat.category_code, "B", 1.0)] * cat.mode_b_effective)
        plan.extend([(cat.category_code, "A", 1.0)] * cat.mode_a)

    horizon_days = max(weeks * 7, len(plan))
    step = max(horizon_days // max(len(plan), 1), 1)

    painful = {c.category_code: c.painful_weight for c in params.categories}

    for index, (category_code, mode, weight) in enumerate(plan):
        local_day = day + timedelta(days=index * step)
        start_at = datetime.combine(local_day, time(20, 0))
        end_at = start_at + timedelta(hours=12)
        mode_enum = CoverageMode.A if mode == "A" else CoverageMode.B
        specs = (
            [(Line.L1, Status.SENIOR)]
            if mode_enum is CoverageMode.A
            else [(Line.L1, Status.ASSISTANT), (Line.L2, Status.SENIOR)]
        )
        for line, required in specs:
            posts.append(
                PostIn(
                    post_id=post_id,
                    occurrence_id=occurrence_id,
                    type_code=f"SIM_{category_code}",
                    category_code=category_code,
                    line=line,
                    required_status=required,
                    start_at=start_at,
                    end_at=end_at,
                    local_date=local_day,
                    coverage_mode=mode_enum,
                    count_weight=weight,
                    painful_weight=painful.get(category_code, 1.0),
                    is_weekend_block=local_day.weekday() >= 5,
                )
            )
            post_id += 1

        for person in people:
            draw = rng.random()
            if draw < red_ratio:
                color = Color.ROUGE
            elif draw < red_ratio + orange_ratio:
                color = Color.ORANGE
            else:
                color = Color.VERT
            availabilities.append(
                AvailabilityIn(
                    profile_id=person.profile_id,
                    occurrence_id=occurrence_id,
                    color=color,
                )
            )
        occurrence_id += 1

    quotas: list[QuotaIn] = []
    weights = params.seniors.weights()
    weight_sum = sum(weights) or 1.0
    for i, person in enumerate(p for p in people if p.status is Status.SENIOR):
        share = weights[i] / weight_sum
        for cat in projection.per_category:
            if cat.posts_l1_senior:
                quotas.append(
                    QuotaIn(
                        profile_id=person.profile_id,
                        category_code=cat.category_code,
                        line=Line.L1,
                        target=round(cat.posts_l1_senior * share, 3),
                    )
                )
            if cat.posts_l2_senior:
                quotas.append(
                    QuotaIn(
                        profile_id=person.profile_id,
                        category_code=cat.category_code,
                        line=Line.L2,
                        target=round(cat.posts_l2_senior * share, 3),
                    )
                )
    n_assistants = max(params.assistants.count, 1)
    for person in (p for p in people if p.status is Status.ASSISTANT):
        for cat in projection.per_category:
            if cat.posts_l1_assistant:
                quotas.append(
                    QuotaIn(
                        profile_id=person.profile_id,
                        category_code=cat.category_code,
                        line=Line.L1,
                        target=round(cat.posts_l1_assistant / n_assistants, 3),
                    )
                )

    return EngineInput(
        posts=posts,
        people=people,
        availabilities=availabilities,
        quotas=quotas,
        rest_rules=[
            RestRuleIn(
                code="REPOS_24H",
                label="Repos minimal de 24 h (hypothèse de démonstration)",
                enforcement=Enforcement.FERME,
                min_hours_between=24.0,
            )
        ],
        profile=RuleProfile(
            name="simulation", version="v1", kind="SIMULATION", is_demo_hypothesis=True
        ),
        seed=seed,
        ruleset_version=params.ruleset_version,
        year_fraction_elapsed=1.0,
    )
