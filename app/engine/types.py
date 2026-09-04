"""Types du moteur de planification.

Ce paquet est **pur** : il n'importe ni SQLAlchemy, ni FastAPI, ni aucun accès base.
Entrées et sorties sont des dataclasses, ce qui rend le moteur testable seul et
rejouable à l'identique hors de l'application (cf. DECISIONS.md D-005).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Mapping, Sequence


# --------------------------------------------------------------------------- #
# Énumérations métier
# --------------------------------------------------------------------------- #


class Status(str, Enum):
    """Statut professionnel. Distinct des droits applicatifs."""

    SENIOR = "SENIOR"
    ASSISTANT = "ASSISTANT"


class Line(str, Enum):
    L1 = "L1"
    L2 = "L2"


class Color(str, Enum):
    """Couleur de disponibilité.

    ``DISPO_DEFAUT`` est une valeur à part entière : le moteur la traite comme un
    vert, mais elle ne doit **jamais** être présentée comme un vert déclaré
    (cf. DECISIONS.md M-008). L'absence d'entrée signifie « non renseigné », ce qui
    est une exclusion ferme au niveau du moteur : la génération est bloquée en amont.
    """

    VERT = "VERT"
    ORANGE = "ORANGE"
    ROUGE = "ROUGE"
    DISPO_DEFAUT = "DISPO_DEFAUT"

    @property
    def is_declared(self) -> bool:
        return self is not Color.DISPO_DEFAUT

    @property
    def counts_as_green(self) -> bool:
        return self in (Color.VERT, Color.DISPO_DEFAUT)


class CoverageMode(str, Enum):
    """Mode A : senior en L1, **aucune L2**. Mode B : assistant L1 + senior L2."""

    A = "A"
    B = "B"


class Enforcement(str, Enum):
    FERME = "FERME"
    SOUPLE = "SOUPLE"


# Codes de contraintes fermes — utilisés dans les explications et rapports.
H_ROUGE = "H02_ROUGE"
H_NON_RENSEIGNE = "H02b_NON_RENSEIGNE"
H_ORANGE_L1 = "H02c_ORANGE_INTERDIT_EN_L1"
H_ASSISTANT_L2 = "H03_ASSISTANT_JAMAIS_L2"
H_L2_NON_SENIOR = "H04_L2_RESERVEE_SENIOR"
H_STATUT_POSTE = "H10_STATUT_EXIGE_PAR_LE_POSTE"
H_CHEVAUCHEMENT = "H06_CHEVAUCHEMENT"
H_INCOMPATIBILITE = "H06b_INCOMPATIBILITE_DECLAREE"
H_INACTIF = "H07_COMPTE_INACTIF_OU_EXPIRE"
H_ELIGIBILITE = "H07b_NON_ELIGIBLE_LIGNE_OU_TYPE"
H_EXEMPTION = "H08_EXEMPTION_OU_QUOTA_NUL"
H_MAX_FERME = "H08b_MAXIMUM_FERME_ATTEINT"
H_REPOS = "H09_REGLE_DE_REPOS_FERME"
H_DOUBLE_POSTE = "H11_DEUX_POSTES_MEME_OCCURRENCE"
H_PLAFOND_MENSUEL = "H12_PLAFOND_MENSUEL_FERME"
H_DUREE_CONTINUE = "H13_DUREE_CONTINUE_MAXIMALE"
H_QUOTA_PERIODE = "H14_QUOTA_DE_PERIODE_FERME"

HARD_CONSTRAINT_LABELS: Mapping[str, str] = {
    H_ROUGE: "Indisponibilité rouge déclarée par la personne",
    H_NON_RENSEIGNE: "Disponibilité non renseignée (génération bloquée en amont)",
    H_ORANGE_L1: "Orange : possible en deuxième ligne uniquement, jamais en première ligne",
    H_ASSISTANT_L2: "Un assistant n'est jamais en deuxième ligne",
    H_L2_NON_SENIOR: "Toute deuxième ligne est assurée par un senior",
    H_STATUT_POSTE: "Statut professionnel exigé par le poste non satisfait",
    H_CHEVAUCHEMENT: "Chevauchement avec une autre garde",
    H_INCOMPATIBILITE: "Incompatibilité déclarée sur cette occurrence",
    H_INACTIF: "Compte inactif, expiré ou hors période d'activité",
    H_ELIGIBILITE: "Non éligible à cette ligne ou à ce type de garde",
    H_EXEMPTION: "Exemption ou quota nul sur cette catégorie/ligne",
    H_MAX_FERME: "Maximum ferme de quota déjà atteint",
    H_REPOS: "Règle de repos ferme non respectée",
    H_DOUBLE_POSTE: "Déjà affectée sur un autre poste de la même occurrence",
    H_PLAFOND_MENSUEL: (
        "Plafond mensuel de gardes atteint (plafond chiffré, validé "
        "institutionnellement et déclaré ferme)"
    ),
    H_DUREE_CONTINUE: (
        "Durée de service continu d'un assistant supérieure au maximum, sans "
        "demande explicite et datée de la personne"
    ),
    H_QUOTA_PERIODE: (
        "Quota de période atteint (maximum chiffré, validé institutionnellement "
        "et déclaré ferme)"
    ),
}


# --------------------------------------------------------------------------- #
# Entrées
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PersonIn:
    profile_id: int
    code: str
    status: Status
    eligible_l1: bool
    eligible_l2: bool
    active_from: date | None = None
    active_to: date | None = None
    account_active: bool = True
    excluded_type_codes: frozenset[str] = frozenset()
    quotite_tenths: int = 10

    def is_active_on(self, day: date) -> bool:
        if not self.account_active:
            return False
        if self.active_from is not None and day < self.active_from:
            return False
        if self.active_to is not None and day > self.active_to:
            return False
        return True


@dataclass(frozen=True)
class PostIn:
    """Un poste de couverture à pourvoir.

    Le mode A/B est matérialisé par les postes eux-mêmes : une occurrence en mode A
    n'a qu'un poste L1 senior, il est donc structurellement impossible de lui
    rattacher une deuxième ligne (cf. DECISIONS.md M-001).
    """

    post_id: int
    occurrence_id: int
    type_code: str
    category_code: str
    line: Line
    required_status: Status | None
    start_at: datetime
    end_at: datetime
    local_date: date
    coverage_mode: CoverageMode
    count_weight: float = 1.0
    painful_weight: float = 1.0
    is_weekend_block: bool = False

    @property
    def key(self) -> tuple:
        """Clé stable pour tout tri déterministe."""
        return (self.start_at, self.line.value, self.post_id)


@dataclass(frozen=True)
class AvailabilityIn:
    profile_id: int
    occurrence_id: int
    color: Color
    line: Line | None = None  # None = s'applique à toutes les lignes éligibles


@dataclass(frozen=True)
class QuotaIn:
    profile_id: int
    category_code: str
    line: Line
    target: float
    minimum: float | None = None
    maximum: float | None = None
    hard_minimum: bool = False
    hard_maximum: bool = False


@dataclass(frozen=True)
class ExemptionIn:
    """Exemption totale ou partielle. Une exemption totale est une contrainte ferme."""

    profile_id: int
    category_code: str | None = None
    line: Line | None = None
    total: bool = True
    reduction_ratio: float = 0.0
    start_date: date | None = None
    end_date: date | None = None

    def applies(self, day: date, category: str, line: Line) -> bool:
        if self.category_code is not None and self.category_code != category:
            return False
        if self.line is not None and self.line != line:
            return False
        if self.start_date is not None and day < self.start_date:
            return False
        if self.end_date is not None and day > self.end_date:
            return False
        return True


@dataclass(frozen=True)
class RestRuleIn:
    code: str
    label: str
    enforcement: Enforcement
    min_hours_between: float | None = None
    max_count_in_days: tuple[int, int] | None = None  # (fenêtre_jours, max)
    max_consecutive_weekends: int | None = None


@dataclass(frozen=True)
class MonthlyCapIn:
    """Plafond mensuel de gardes.

    Le client n'a **pas** chiffré ce plafond institutionnellement (03/09/2026).
    Trois verrous cumulés sont donc exigés avant qu'il ne bloque quoi que ce soit :
    une valeur non nulle, une validation institutionnelle explicite, et un caractère
    déclaré ferme. Tant que l'un des trois manque, le plafond est **informatif**
    et ne peut jamais devenir silencieusement une règle.
    """

    profile_id: int | None  # None = s'applique à tout un statut
    status: Status | None  # None = s'applique à un profil précis
    max_per_month: float | None = None
    enforcement: Enforcement = Enforcement.SOUPLE
    institutionally_validated: bool = False
    label: str = "plafond mensuel"

    @property
    def is_enforceable(self) -> bool:
        return (
            self.max_per_month is not None
            and self.max_per_month > 0
            and self.institutionally_validated
            and self.enforcement is Enforcement.FERME
        )

    def applies_to(self, person: PersonIn) -> bool:
        if self.profile_id is not None:
            return person.profile_id == self.profile_id
        if self.status is not None:
            return person.status is self.status
        return False


@dataclass(frozen=True)
class PeriodQuotaIn:
    """Quota portant sur une **période de dates de service**.

    Rend le quota 57/68 des assistants réellement opposable au moteur, au lieu
    d'un simple calcul de projection. La période est unique, à cheval sur deux
    années civiles et sur plusieurs trimestres.

    Comme le plafond mensuel, le maximum n'est opposable qu'après trois verrous
    cumulés : chiffré, validé institutionnellement, déclaré ferme.
    """

    code: str
    label: str
    start_date: date
    end_date: date  # incluse
    profile_id: int | None = None
    status: Status | None = None
    target: float = 0.0
    maximum: float | None = None
    enforcement: Enforcement = Enforcement.SOUPLE
    institutionally_validated: bool = False

    @property
    def is_enforceable(self) -> bool:
        return (
            self.maximum is not None
            and self.maximum > 0
            and self.institutionally_validated
            and self.enforcement is Enforcement.FERME
        )

    def applies_to(self, person: PersonIn) -> bool:
        if self.profile_id is not None:
            return person.profile_id == self.profile_id
        if self.status is not None:
            return person.status is self.status
        return False

    def covers(self, date_de_service: date) -> bool:
        """Rattachement par la date de **début** de service, jamais par la fin."""
        return self.start_date <= date_de_service <= self.end_date


@dataclass(frozen=True)
class ContinuousDutyRuleIn:
    """Durée maximale de service **continu** planifié.

    Portée restreinte par le client le 04/09/2026 : cette règle ferme ne vise
    **que les assistants**. Pour les seniors, aucun blocage supplémentaire n'est
    ajouté ; les autres contraintes connues et la validation humaine habituelle
    restent applicables.

    Pour un assistant, un bloc de service continu dépassant ``max_hours`` n'est
    possible que sur demande explicite et datée de l'intéressé : c'est le
    mécanisme du week-end complet.

    Elle ne présume rien du travail réellement effectué sur place : elle borne
    seulement la durée de service planifiée d'un seul tenant.
    """

    max_hours: float = 24.0
    label: str = "durée de service continu"
    #: Statuts réellement soumis à la règle. Assistants uniquement par défaut.
    applies_to_statuses: frozenset[Status] = frozenset({Status.ASSISTANT})
    #: (profile_id, date d'ancrage du bloc) des dérogations explicites et datées.
    explicit_requests: frozenset[tuple[int, date]] = frozenset()

    def applies_to(self, person: PersonIn) -> bool:
        return person.status in self.applies_to_statuses

    def has_request(self, profile_id: int, days: set[date]) -> bool:
        """Vrai seulement si **toute** la chaîne est couverte par la dérogation.

        Lot C, point 6 du contre-audit du 04/09/2026 : avec un simple ``any``,
        une demande de week-end couvrant samedi et dimanche autorisait aussi une
        chaîne débordant sur le lundi. Une demande partielle ne doit jamais
        ouvrir une chaîne plus longue que ce qui a été explicitement demandé.
        """
        if not days:
            return False
        return all((profile_id, day) in self.explicit_requests for day in days)


@dataclass(frozen=True)
class BusyIntervalIn:
    """Occupation connue hors du périmètre généré (garde déjà publiée ailleurs)."""

    profile_id: int
    start_at: datetime
    end_at: datetime
    label: str = "garde existante"


@dataclass(frozen=True)
class RuleProfile:
    """Profil de règles **versionné**.

    Les poids sont des données, jamais des constantes enfouies (DECISIONS.md D-010).
    """

    name: str
    version: str
    kind: str = "OPERATIONNEL"  # OPERATIONNEL | SIMULATION
    w_orange: float = 100.0
    w_quota: float = 60.0
    w_catchup: float = 25.0
    w_spacing: float = 40.0
    w_concentration: float = 30.0
    w_painful: float = 20.0
    senior_soft_priority_multiplier: float = 3.0
    target_spacing_days: float = 7.0
    concentration_window_days: int = 14
    concentration_threshold: int = 2
    max_consecutive_weekends_soft: int = 2
    is_demo_hypothesis: bool = True

    def __post_init__(self) -> None:
        if self.kind == "OPERATIONNEL" and self.senior_soft_priority_multiplier < 1.0:
            # M-006 : en opérationnel, la priorité seniors ne peut pas être désactivée.
            raise ValueError(
                "Profil OPERATIONNEL : la priorité des préférences souples des seniors "
                "ne peut pas être désactivée (multiplicateur < 1). Voir DECISIONS.md M-006."
            )


@dataclass
class EngineInput:
    posts: list[PostIn]
    people: list[PersonIn]
    availabilities: list[AvailabilityIn] = field(default_factory=list)
    quotas: list[QuotaIn] = field(default_factory=list)
    exemptions: list[ExemptionIn] = field(default_factory=list)
    rest_rules: list[RestRuleIn] = field(default_factory=list)
    monthly_caps: list[MonthlyCapIn] = field(default_factory=list)
    period_quotas: list[PeriodQuotaIn] = field(default_factory=list)
    #: Charge déjà connue sur la période, hors du périmètre généré
    #: (trimestres antérieurs publiés). Clé : profile_id.
    prior_period_load: Mapping[int, float] = field(default_factory=dict)
    continuous_duty: ContinuousDutyRuleIn | None = None
    busy_intervals: list[BusyIntervalIn] = field(default_factory=list)
    incompatibilities: frozenset[tuple[int, int]] = frozenset()  # (profile_id, occurrence_id)
    locked: Mapping[int, int] = field(default_factory=dict)  # post_id -> profile_id
    prior_load: Mapping[tuple[int, str, str], float] = field(default_factory=dict)
    profile: RuleProfile = field(
        default_factory=lambda: RuleProfile(name="demo", version="v1")
    )
    seed: int = 20260901
    ruleset_version: str = "regles_demo_v1"
    engine_version: str = "0.1.0"
    year_fraction_elapsed: float = 0.25

    # ------------------------------------------------------------------ #

    def snapshot_hash(self) -> str:
        """Empreinte reproductible des entrées, pour l'instantané d'exécution."""
        payload = {
            "posts": sorted(
                (
                    p.post_id,
                    p.occurrence_id,
                    p.type_code,
                    p.category_code,
                    p.line.value,
                    p.required_status.value if p.required_status else None,
                    p.start_at.isoformat(),
                    p.end_at.isoformat(),
                    p.coverage_mode.value,
                    p.count_weight,
                )
                for p in self.posts
            ),
            "people": sorted(
                (
                    q.profile_id,
                    q.code,
                    q.status.value,
                    q.eligible_l1,
                    q.eligible_l2,
                    q.active_from.isoformat() if q.active_from else None,
                    q.active_to.isoformat() if q.active_to else None,
                    q.account_active,
                    sorted(q.excluded_type_codes),
                    q.quotite_tenths,
                )
                for q in self.people
            ),
            "avail": sorted(
                (a.profile_id, a.occurrence_id, a.line.value if a.line else None, a.color.value)
                for a in self.availabilities
            ),
            "quotas": sorted(
                (
                    q.profile_id,
                    q.category_code,
                    q.line.value,
                    q.target,
                    q.minimum,
                    q.maximum,
                    q.hard_minimum,
                    q.hard_maximum,
                )
                for q in self.quotas
            ),
            "exemptions": sorted(
                (
                    e.profile_id,
                    e.category_code or "",
                    e.line.value if e.line else "",
                    e.total,
                    e.reduction_ratio,
                    e.start_date.isoformat() if e.start_date else "",
                    e.end_date.isoformat() if e.end_date else "",
                )
                for e in self.exemptions
            ),
            "rest": sorted(
                (
                    r.code,
                    r.enforcement.value,
                    r.min_hours_between if r.min_hours_between is not None else -1,
                    list(r.max_count_in_days) if r.max_count_in_days else [],
                    r.max_consecutive_weekends if r.max_consecutive_weekends is not None else -1,
                )
                for r in self.rest_rules
            ),
            "busy": sorted(
                (b.profile_id, b.start_at.isoformat(), b.end_at.isoformat())
                for b in self.busy_intervals
            ),
            "monthly_caps": sorted(
                (
                    c.profile_id if c.profile_id is not None else -1,
                    c.status.value if c.status else "",
                    c.max_per_month if c.max_per_month is not None else -1,
                    c.enforcement.value,
                    c.institutionally_validated,
                )
                for c in self.monthly_caps
            ),
            "period_quotas": sorted(
                (
                    q.code,
                    q.profile_id if q.profile_id is not None else -1,
                    q.status.value if q.status else "",
                    q.start_date.isoformat(),
                    q.end_date.isoformat(),
                    q.target,
                    q.maximum if q.maximum is not None else -1,
                    q.enforcement.value,
                    q.institutionally_validated,
                )
                for q in self.period_quotas
            ),
            "continuous_duty": (
                [
                    self.continuous_duty.max_hours,
                    sorted(s.value for s in self.continuous_duty.applies_to_statuses),
                    sorted(
                        (pid, day.isoformat())
                        for pid, day in self.continuous_duty.explicit_requests
                    ),
                ]
                if self.continuous_duty
                else []
            ),
            "incompat": sorted(self.incompatibilities),
            "locked": sorted(self.locked.items()),
            "prior": sorted((k[0], k[1], k[2], v) for k, v in self.prior_load.items()),
            "profile": [
                self.profile.name,
                self.profile.version,
                self.profile.kind,
                self.profile.w_orange,
                self.profile.w_quota,
                self.profile.w_catchup,
                self.profile.w_spacing,
                self.profile.w_concentration,
                self.profile.w_painful,
                self.profile.senior_soft_priority_multiplier,
                self.profile.target_spacing_days,
                self.profile.concentration_window_days,
                self.profile.concentration_threshold,
            ],
            "seed": self.seed,
            "ruleset_version": self.ruleset_version,
            "engine_version": self.engine_version,
            "year_fraction_elapsed": self.year_fraction_elapsed,
        }
        blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Sorties
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rejection:
    profile_id: int
    profile_code: str
    constraint_code: str
    detail: str

    @property
    def label(self) -> str:
        return HARD_CONSTRAINT_LABELS.get(self.constraint_code, self.constraint_code)


@dataclass
class Explanation:
    """Explication d'une affectation, lisible par une personne autorisée."""

    post_id: int
    profile_id: int
    profile_code: str
    status: str
    line: str
    color: str
    color_is_declared: bool
    quota_target: float
    quota_before: float
    quota_lag: float
    spacing_days: float | None
    criteria: dict[str, float]
    rejected_candidates: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def color_label(self) -> str:
        if not self.color_is_declared:
            return "disponible par défaut — non confirmé par la personne"
        return {"VERT": "vert (déclaré)", "ORANGE": "orange (déclaré)"}.get(self.color, self.color)

    def to_text(self) -> str:
        parts = [
            f"{self.profile_code} ({self.status}) affecté·e en {self.line}",
            f"couleur : {self.color_label}",
            f"quota catégorie : {self.quota_before:.2f} / cible au prorata de la période "
            f"{self.quota_target:.2f} (retard {self.quota_lag:+.2f})",
        ]
        if self.spacing_days is not None:
            parts.append(f"espacement le plus court : {self.spacing_days:.1f} j")
        if self.rejected_candidates:
            parts.append(
                f"{len(self.rejected_candidates)} candidat·e(s) écarté·e(s) par une contrainte ferme"
            )
        return " · ".join(parts)


@dataclass
class UnfilledPost:
    post_id: int
    occurrence_id: int
    line: str
    local_date: date
    type_code: str
    rejections: list[Rejection] = field(default_factory=list)

    def to_text(self) -> str:
        return (
            f"Poste {self.line} du {self.local_date.isoformat()} ({self.type_code}) non pourvu : "
            f"{len(self.rejections)} personne(s) écartée(s) par une contrainte ferme."
        )


@dataclass
class Solution:
    variant_index: int
    seed: int
    assignments: dict[int, int]  # post_id -> profile_id
    explanations: dict[int, Explanation]
    unfilled: list[UnfilledPost]
    score_total: float
    score_breakdown: dict[str, float]
    orange_used: list[int]
    default_availability_used: list[int]
    quota_gaps: dict[str, float]
    tensions: list[str]
    input_snapshot_hash: str = ""
    ruleset_version: str = ""
    engine_version: str = ""
    profile_name: str = ""

    @property
    def feasible(self) -> bool:
        return not self.unfilled

    def assignment_vector(self, ordered_post_ids: Sequence[int]) -> tuple:
        return tuple(self.assignments.get(pid, -1) for pid in ordered_post_ids)


@dataclass
class ImpossibilityReport:
    """Rapport d'impossibilité : jamais de relâchement automatique d'une contrainte ferme."""

    unfilled: list[UnfilledPost]
    summary: list[str]

    @property
    def is_empty(self) -> bool:
        return not self.unfilled


def diversity_distance(a: Solution, b: Solution, ordered_post_ids: Sequence[int]) -> float:
    """Distance de Hamming normalisée entre deux solutions (0 = identiques)."""
    if not ordered_post_ids:
        return 0.0
    va = a.assignment_vector(ordered_post_ids)
    vb = b.assignment_vector(ordered_post_ids)
    diff = sum(1 for x, y in zip(va, vb) if x != y)
    return diff / len(ordered_post_ids)
