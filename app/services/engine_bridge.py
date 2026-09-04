"""Pont base de données ↔ moteur pur.

**Point unique** de traduction. Les contraintes fermes ne sont donc définies qu'une
seule fois (dans `app/engine/hard.py`) et s'appliquent identiquement au moteur, aux
corrections manuelles, aux reprises, aux échanges et aux appels directs à l'API.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import (
    AvailabilityIn,
    BusyIntervalIn,
    Color,
    ContinuousDutyRuleIn,
    CoverageMode,
    EngineInput,
    Enforcement,
    ExemptionIn,
    Line,
    MonthlyCapIn,
    PeriodQuotaIn,
    PersonIn,
    PostIn,
    QuotaIn,
    Rejection,
    RestRuleIn,
    RuleProfile,
    State,
    Status,
    hard_violation,
)
from ..engine.context import Context
from ..models import (
    Assignment,
    Availability,
    Campaign,
    CoveragePost,
    DUREE_CONTINUE_MAX_HEURES,
    Exemption,
    GardeOccurrence,
    GardeType,
    MonthlyCap,
    PeriodQuota,
    ProfessionalProfile,
    Quarter,
    QuotaAdjustment,
    QuotaCategory,
    QuotaTarget,
    RestRule,
    RuleProfileRow,
    ScheduleState,
    ScheduleVersion,
    Submission,
    WeekendBlockRequest,
)

RULESET_VERSION = "regles_demo_v1"


# --------------------------------------------------------------------------- #
# Conversions élémentaires
# --------------------------------------------------------------------------- #


def to_person(session: Session, profile: ProfessionalProfile, ref_day: date) -> PersonIn:
    start, end = profile.activity_bounds()
    excluded = {
        session.get(GardeType, type_id).code
        for line in (Line.L1, Line.L2)
        for type_id in profile.excluded_type_ids(line, ref_day)
        if type_id is not None
    }
    return PersonIn(
        profile_id=profile.id,
        code=profile.code,
        status=profile.status,
        eligible_l1=profile.eligible_for(Line.L1, None, ref_day),
        eligible_l2=profile.eligible_for(Line.L2, None, ref_day),
        active_from=start,
        active_to=end,
        account_active=profile.user.is_active,
        excluded_type_codes=frozenset(excluded),
        quotite_tenths=profile.quotite_on(ref_day),
    )


def to_post(post: CoveragePost) -> PostIn:
    occurrence = post.occurrence
    garde_type = occurrence.garde_type
    return PostIn(
        post_id=post.id,
        occurrence_id=occurrence.id,
        type_code=garde_type.code,
        category_code=garde_type.category.code,
        line=post.line,
        required_status=post.required_status,
        start_at=occurrence.start_at,
        end_at=occurrence.end_at,
        local_date=occurrence.local_date,
        coverage_mode=occurrence.effective_mode,
        count_weight=garde_type.count_weight,
        painful_weight=garde_type.category.painful_weight,
        is_weekend_block=occurrence.is_weekend_block,
    )


def load_rule_profile(session: Session, name: str = "operationnel") -> RuleProfile:
    row = session.execute(
        select(RuleProfileRow).where(RuleProfileRow.name == name, RuleProfileRow.active)
    ).scalar_one_or_none()
    if row is None:
        return RuleProfile(name="operationnel", version="v1")
    params = json.loads(row.params_json or "{}")
    return RuleProfile(name=row.name, version=row.version, kind=row.kind, **params)


def rest_rules(session: Session) -> list[RestRuleIn]:
    out = []
    for rule in session.execute(select(RestRule).where(RestRule.active)).scalars():
        out.append(
            RestRuleIn(
                code=rule.code,
                label=rule.label,
                enforcement=rule.enforcement,
                min_hours_between=rule.min_hours_between,
                max_count_in_days=(
                    (rule.max_count_window_days, rule.max_count_value)
                    if rule.max_count_window_days and rule.max_count_value
                    else None
                ),
                max_consecutive_weekends=rule.max_consecutive_weekends,
            )
        )
    return out


def monthly_caps_for_year(session: Session, year_id: int) -> list[MonthlyCapIn]:
    """Plafonds mensuels enregistrés pour l'année.

    Tous sont transmis au moteur, y compris les non opposables : c'est le moteur
    qui applique les trois verrous, afin que l'instantané d'exécution garde la
    trace de ce qui existait sans le rendre effectif.
    """
    out: list[MonthlyCapIn] = []
    for row in session.execute(
        select(MonthlyCap).where(MonthlyCap.year_id == year_id)
    ).scalars():
        out.append(
            MonthlyCapIn(
                profile_id=row.profile_id,
                status=row.status,
                max_per_month=row.max_per_month,
                enforcement=row.enforcement,
                institutionally_validated=row.institutionally_validated,
                label=row.label,
            )
        )
    return out


def period_quotas_for_scope(
    session: Session, debut: date, fin: date
) -> list[PeriodQuotaIn]:
    """Quotas de période dont la fenêtre recoupe l'intervalle demandé.

    Tous sont transmis au moteur, y compris les non opposables, afin que
    l'instantané d'exécution garde la trace de ce qui existait.
    """
    out: list[PeriodQuotaIn] = []
    for row in session.execute(
        select(PeriodQuota).where(
            PeriodQuota.start_date <= fin, PeriodQuota.end_date >= debut
        )
    ).scalars():
        out.append(
            PeriodQuotaIn(
                code=row.code,
                label=row.label,
                start_date=row.start_date,
                end_date=row.end_date,
                profile_id=row.profile_id,
                status=row.status,
                target=row.target,
                maximum=row.maximum,
                enforcement=row.enforcement,
                institutionally_validated=row.institutionally_validated,
            )
        )
    return out


def prior_period_load(
    session: Session, quotas: list[PeriodQuotaIn], quarter: Quarter
) -> dict[int, float]:
    """Charge déjà publiée sur les périodes concernées, **hors** trimestre courant.

    Sans cela, un quota couvrant plusieurs trimestres repartirait de zéro à
    chaque génération, ce qui le rendrait inopérant.
    """
    if not quotas:
        return {}
    debut = min(q.start_date for q in quotas)
    fin = max(q.end_date for q in quotas)

    rows = session.execute(
        select(Assignment.profile_id, GardeType.count_weight)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            ScheduleVersion.state == ScheduleState.PUBLIE,
            GardeOccurrence.quarter_id != quarter.id,
            GardeOccurrence.local_date >= debut,
            GardeOccurrence.local_date <= fin,
        )
    ).all()

    out: dict[int, float] = defaultdict(float)
    for profile_id, weight in rows:
        out[profile_id] += float(weight)
    return dict(out)


def continuous_duty_rule(
    session: Session, max_hours: float = DUREE_CONTINUE_MAX_HEURES
) -> ContinuousDutyRuleIn:
    """Règle ferme de durée de service continu, avec ses dérogations datées.

    Portée restreinte aux **assistants** par le client le 04/09/2026 : aucun
    blocage supplémentaire n'est créé pour les seniors, chez qui les autres
    contraintes et la validation humaine habituelle restent seules applicables.

    La seule dérogation possible est une demande explicite formulée par la
    personne elle-même. Une demande couvre le jour d'ancrage et le lendemain, ce
    qui correspond au week-end complet du samedi 9 h au lundi 9 h.
    """
    paires: set[tuple[date, int]] = set()
    for demande in session.execute(
        select(WeekendBlockRequest).where(WeekendBlockRequest.active)
    ).scalars():
        paires.add((demande.profile_id, demande.anchor_date))
        paires.add((demande.profile_id, demande.anchor_date + timedelta(days=1)))
    return ContinuousDutyRuleIn(
        max_hours=max_hours,
        label="durée de service continu",
        applies_to_statuses=frozenset({Status.ASSISTANT}),
        explicit_requests=frozenset(paires),
    )


def availabilities_for_quarter(session: Session, quarter: Quarter) -> list[AvailabilityIn]:
    rows = session.execute(
        select(Availability, Submission)
        .join(Submission, Availability.submission_id == Submission.id)
        .join(Campaign, Submission.campaign_id == Campaign.id)
        .where(Campaign.quarter_id == quarter.id)
    ).all()
    return [
        AvailabilityIn(
            profile_id=submission.profile_id,
            occurrence_id=availability.occurrence_id,
            color=availability.color,
            line=availability.line,
        )
        for availability, submission in rows
    ]


def quotas_for_year(session: Session, year_id: int) -> list[QuotaIn]:
    categories = {c.id: c.code for c in session.execute(select(QuotaCategory)).scalars()}
    out = []
    for target in session.execute(
        select(QuotaTarget).where(QuotaTarget.year_id == year_id)
    ).scalars():
        out.append(
            QuotaIn(
                profile_id=target.profile_id,
                category_code=categories[target.category_id],
                line=target.line,
                target=target.target,
                minimum=target.minimum,
                maximum=target.maximum,
                hard_minimum=target.hard_minimum,
                hard_maximum=target.hard_maximum,
            )
        )
    return out


def exemptions(session: Session) -> list[ExemptionIn]:
    categories = {c.id: c.code for c in session.execute(select(QuotaCategory)).scalars()}
    out = []
    for row in session.execute(select(Exemption)).scalars():
        out.append(
            ExemptionIn(
                profile_id=row.profile_id,
                category_code=categories.get(row.category_id) if row.category_id else None,
                line=row.line,
                total=row.total,
                reduction_ratio=row.reduction_ratio,
                start_date=row.start_date,
                end_date=row.end_date,
            )
        )
    return out


def prior_load(session: Session, year_id: int, exclude_quarter_id: int | None) -> dict:
    """Charge annuelle déjà réalisée ou programmée hors du trimestre généré,
    ajustements de reprise inclus."""
    load: dict[tuple[int, str, str], float] = defaultdict(float)
    rows = session.execute(
        select(Assignment, CoveragePost, GardeOccurrence, GardeType, QuotaCategory, Quarter)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(QuotaCategory, GardeType.category_id == QuotaCategory.id)
        .join(Quarter, GardeOccurrence.quarter_id == Quarter.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            ScheduleVersion.state == ScheduleState.PUBLIE,
            Quarter.year_id == year_id,
        )
    ).all()
    for assignment, post, _occ, garde_type, category, quarter in rows:
        if exclude_quarter_id is not None and quarter.id == exclude_quarter_id:
            continue
        load[(assignment.profile_id, category.code, post.line.value)] += garde_type.count_weight

    categories = {c.id: c.code for c in session.execute(select(QuotaCategory)).scalars()}
    for adj in session.execute(
        select(QuotaAdjustment).where(QuotaAdjustment.year_id == year_id)
    ).scalars():
        load[(adj.profile_id, categories[adj.category_id], adj.line.value)] += adj.delta
    return dict(load)


def busy_intervals(
    session: Session, quarter: Quarter, exclude_version_id: int | None = None
) -> list[BusyIntervalIn]:
    """Gardes publiées hors du trimestre généré : elles créent de vrais conflits."""
    rows = session.execute(
        select(Assignment, GardeOccurrence, GardeType)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            ScheduleVersion.state == ScheduleState.PUBLIE,
            GardeOccurrence.quarter_id != quarter.id,
        )
    ).all()
    out = []
    for assignment, occurrence, garde_type in rows:
        if exclude_version_id and assignment.schedule_version_id == exclude_version_id:
            continue
        out.append(
            BusyIntervalIn(
                profile_id=assignment.profile_id,
                start_at=occurrence.start_at,
                end_at=occurrence.end_at,
                label=f"{garde_type.label} du {occurrence.local_date.isoformat()}",
            )
        )
    return out


def year_fraction(quarter: Quarter) -> float:
    year = quarter.year
    total = (year.end_date - year.start_date).days + 1
    elapsed = (quarter.end_date - year.start_date).days + 1
    return max(min(elapsed / total, 1.0), 0.01)


# --------------------------------------------------------------------------- #
# Construction complète
# --------------------------------------------------------------------------- #


def build_input(
    session: Session,
    quarter: Quarter,
    seed: int = 20260901,
    locked: dict[int, int] | None = None,
    profile_name: str = "operationnel",
) -> EngineInput:
    posts = list(
        session.execute(
            select(CoveragePost)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .where(GardeOccurrence.quarter_id == quarter.id, CoveragePost.required)
        ).scalars()
    )
    profiles = list(session.execute(select(ProfessionalProfile)).scalars())
    ref_day = quarter.start_date
    quotas_periode = period_quotas_for_scope(
        session, quarter.start_date, quarter.end_date
    )
    return EngineInput(
        posts=[to_post(p) for p in posts],
        people=[to_person(session, p, ref_day) for p in profiles],
        availabilities=availabilities_for_quarter(session, quarter),
        quotas=quotas_for_year(session, quarter.year_id),
        exemptions=exemptions(session),
        rest_rules=rest_rules(session),
        monthly_caps=monthly_caps_for_year(session, quarter.year_id),
        period_quotas=quotas_periode,
        prior_period_load=prior_period_load(session, quotas_periode, quarter),
        continuous_duty=continuous_duty_rule(session),
        busy_intervals=busy_intervals(session, quarter),
        locked=locked or {},
        prior_load=prior_load(session, quarter.year_id, quarter.id),
        profile=load_rule_profile(session, profile_name),
        seed=seed,
        ruleset_version=RULESET_VERSION,
        year_fraction_elapsed=year_fraction(quarter),
    )


# --------------------------------------------------------------------------- #
# Contrôle ferme d'une affectation isolée
# --------------------------------------------------------------------------- #


def check_assignment(
    session: Session,
    post: CoveragePost,
    profile: ProfessionalProfile,
    ignore_assignment_ids: set[int] | None = None,
    schedule_version_id: int | None = None,
) -> Rejection | None:
    """Vérifie une affectation unique avec **exactement** les mêmes contraintes fermes
    que le moteur. Utilisé par : correction manuelle, candidature de reprise, tirage,
    échange bilatéral et API. Retourne ``None`` si l'affectation est admissible.
    """
    ignore_assignment_ids = ignore_assignment_ids or set()
    occurrence = post.occurrence
    quarter = occurrence.quarter
    ref_day = occurrence.local_date

    person = to_person(session, profile, ref_day)
    target_post = to_post(post)

    # Gardes déjà détenues par la personne (toutes versions publiées + version en cours).
    rows = session.execute(
        select(Assignment, CoveragePost, GardeOccurrence, GardeType)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            Assignment.profile_id == profile.id,
            ScheduleVersion.state.in_(
                [ScheduleState.PUBLIE, ScheduleState.VALIDE, ScheduleState.EN_REVISION,
                 ScheduleState.GENERE]
            ),
        )
    ).all()

    held_posts: list[PostIn] = []
    for assignment, held_post, _occ, _type in rows:
        if assignment.id in ignore_assignment_ids:
            continue
        if schedule_version_id is not None and assignment.schedule_version_id not in (
            schedule_version_id,
        ):
            # Ne conserver que la version courante + les versions publiées d'autres trimestres
            version = session.get(ScheduleVersion, assignment.schedule_version_id)
            if version.state is not ScheduleState.PUBLIE:
                continue
        if held_post.id == post.id:
            continue
        held_posts.append(to_post(held_post))

    # Quotas de période : dans ce chemin, toutes les gardes déjà détenues par la
    # personne sont chargées explicitement en état (y compris celles des autres
    # trimestres publiés). La charge antérieure est donc déjà comptée, et
    # ``prior_period_load`` doit rester vide pour ne pas la compter deux fois.
    bornes = [target_post.local_date] + [p.local_date for p in held_posts]
    quotas_periode = period_quotas_for_scope(session, min(bornes), max(bornes))

    inp = EngineInput(
        posts=[target_post] + held_posts,
        people=[person],
        availabilities=availabilities_for_quarter(session, quarter)
        + _availabilities_for_posts(session, held_posts),
        quotas=quotas_for_year(session, quarter.year_id),
        exemptions=exemptions(session),
        rest_rules=rest_rules(session),
        monthly_caps=monthly_caps_for_year(session, quarter.year_id),
        period_quotas=quotas_periode,
        prior_period_load={},
        continuous_duty=continuous_duty_rule(session),
        busy_intervals=[],
        prior_load=prior_load(session, quarter.year_id, quarter.id),
        profile=load_rule_profile(session),
        year_fraction_elapsed=year_fraction(quarter),
    )
    ctx = Context(inp)
    state = State(ctx)
    for held in held_posts:
        state.assign(held, person.profile_id)
    return hard_violation(ctx, state, target_post, person)


def _availabilities_for_posts(session: Session, posts: list[PostIn]) -> list[AvailabilityIn]:
    if not posts:
        return []
    occurrence_ids = {p.occurrence_id for p in posts}
    rows = session.execute(
        select(Availability, Submission)
        .join(Submission, Availability.submission_id == Submission.id)
        .where(Availability.occurrence_id.in_(occurrence_ids))
    ).all()
    return [
        AvailabilityIn(
            profile_id=submission.profile_id,
            occurrence_id=availability.occurrence_id,
            color=availability.color,
            line=availability.line,
        )
        for availability, submission in rows
    ]


def current_color(
    session: Session, profile_id: int, occurrence_id: int, line: Line
) -> Color | None:
    """Couleur **actuelle** — revérifiée avant tout tirage ou échange."""
    rows = session.execute(
        select(Availability)
        .join(Submission, Availability.submission_id == Submission.id)
        .where(
            Submission.profile_id == profile_id,
            Availability.occurrence_id == occurrence_id,
        )
    ).scalars()
    generic = None
    for row in rows:
        if row.line is line:
            return row.color
        if row.line is None:
            generic = row.color
    return generic
