"""Catalogue : données de référence, années/trimestres, occurrences et postes.

Le mode de couverture est **matérialisé par les postes** : mode A ⇒ un unique poste
L1 senior. Il est donc structurellement impossible de créer une deuxième ligne
derrière un senior de première ligne (DECISIONS.md M-001).
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CoverageMode,
    CoveragePost,
    Enforcement,
    ExchangeClass,
    GardeOccurrence,
    GardeType,
    HolidayPair,
    HolidayPairMember,
    Line,
    Module,
    Quarter,
    QuotaCategory,
    RestRule,
    RuleProfileRow,
    Status,
    UrgencyProfile,
    Year,
)
from .clock import wall_clock_window

# --------------------------------------------------------------------------- #
# Données de référence — toutes administrables, aucune n'est une règle validée
# --------------------------------------------------------------------------- #

CATEGORIES = [
    ("NUITS_LJ", "Nuits de semaine (lundi à jeudi)", 0, 1.0),
    ("WEEKENDS_VEILLES", "Week-ends et veilles de jours fériés", 1, 1.5),
    ("FERIES", "Jours fériés", 2, 2.0),
]

EXCHANGE_CLASSES = [
    ("NUIT_SEMAINE", "Nuit de semaine 12 h"),
    ("NUIT_VENDREDI", "Nuit du vendredi 12 h"),
    ("WEEKEND_24H", "Journée de week-end 24 h (samedi ou dimanche)"),
    ("VEILLE_FERIE", "Veille de jour férié 12 h"),
    ("JOUR_FERIE", "Jour férié 24 h"),
]

# Horaires : OPEN_QUESTIONS.md Q-03.
# Confirmés par le client (02/09/2026) : lun-jeu hors férié 17h->8h ;
# samedi / dimanche / jour férié 9h->9h.
# Confirmés par le client (03/09/2026), pour supprimer le trou de 8h à 9h avant la
# relève du matin : vendredi non férié 17h->9h (samedi) ; veille ouvrable d'un jour
# férié 17h->9h (le jour férié).
GARDE_TYPES = [
    ("NUIT_SEMAINE", "Nuit de semaine (lundi à jeudi)", "NUITS_LJ",
     time(17, 0), time(8, 0), "NUIT_12H", "NUIT_SEMAINE"),
    ("NUIT_VENDREDI", "Nuit du vendredi (vendredi 17 h au samedi 9 h)", "WEEKENDS_VEILLES",
     time(17, 0), time(9, 0), "NUIT_16H", "NUIT_VENDREDI"),
    ("SAMEDI", "Samedi 9 h au dimanche 9 h", "WEEKENDS_VEILLES",
     time(9, 0), time(9, 0), "JOUR_24H", "WEEKEND_24H"),
    ("DIMANCHE", "Dimanche 9 h au lundi 9 h", "WEEKENDS_VEILLES",
     time(9, 0), time(9, 0), "JOUR_24H", "WEEKEND_24H"),
    ("VEILLE_FERIE", "Veille ouvrable d'un jour férié (17 h au jour férié 9 h)",
     "WEEKENDS_VEILLES", time(17, 0), time(9, 0), "NUIT_16H", "VEILLE_FERIE"),
    ("JOUR_FERIE", "Garde d'un jour férié (9 h au lendemain 9 h)", "FERIES",
     time(9, 0), time(9, 0), "JOUR_24H", "JOUR_FERIE"),
]

REST_RULES = [
    # Arbitrage du client du 03/09/2026 : aucune interdiction universelle de 24 h
    # entre toutes les gardes. L'espacement ordinaire reste un objectif souple,
    # configurable et non validé institutionnellement.
    {
        "code": "ESPACEMENT_7J",
        "label": "Espacement souhaité de 7 jours entre deux gardes",
        "enforcement": Enforcement.SOUPLE,
        "min_hours_between": 168.0,
    },
    {
        "code": "MAX_2_WEEKENDS",
        "label": "Au plus 2 week-ends consécutifs",
        "enforcement": Enforcement.SOUPLE,
        "max_consecutive_weekends": 2,
    },
]

#: Règles retirées et à supprimer d'une base existante (voir migration f6a5b4c3d210).
REST_RULES_RETIREES = ["REPOS_MIN_24H"]

# Profil opérationnel : la priorité seniors (M-006) y est active et non désactivable.
RULE_PROFILE_OPERATIONNEL = {
    "w_orange": 100.0,
    "w_quota": 60.0,
    "w_catchup": 25.0,
    "w_spacing": 40.0,
    "w_concentration": 30.0,
    "w_painful": 20.0,
    "senior_soft_priority_multiplier": 3.0,
    "target_spacing_days": 7.0,
    "concentration_window_days": 14,
    "concentration_threshold": 2,
    "max_consecutive_weekends_soft": 2,
}

# Fenêtres et rappels adaptatifs — OPEN_QUESTIONS.md Q-09.
URGENCY_TIERS = [
    {"label": "moins de 12 h", "max_hours_before": 12, "window_minutes": 90,
     "reminders_minutes": [30, 60]},
    {"label": "12 h à 48 h", "max_hours_before": 48, "window_minutes": 360,
     "reminders_minutes": [120, 240]},
    {"label": "48 h à 7 jours", "max_hours_before": 168, "window_minutes": 1440,
     "reminders_minutes": [480, 960]},
    {"label": "plus de 7 jours", "max_hours_before": None, "window_minutes": 4320,
     "reminders_minutes": [1440, 2880]},
]


def ensure_reference_data(session: Session) -> None:
    """Crée les données de référence si elles n'existent pas. Idempotent."""
    for code, label in EXCHANGE_CLASSES:
        if not session.execute(
            select(ExchangeClass).where(ExchangeClass.code == code)
        ).scalar_one_or_none():
            session.add(ExchangeClass(code=code, label=label))
    session.flush()

    for code, label, position, painful in CATEGORIES:
        if not session.execute(
            select(QuotaCategory).where(QuotaCategory.code == code)
        ).scalar_one_or_none():
            session.add(
                QuotaCategory(
                    code=code, label=label, position=position,
                    painful_weight=painful, module=Module.GARDES,
                )
            )
    session.flush()

    categories = {c.code: c for c in session.execute(select(QuotaCategory)).scalars()}
    classes = {c.code: c for c in session.execute(select(ExchangeClass)).scalars()}

    for code, label, cat_code, start, end, duration_class, class_code in GARDE_TYPES:
        if session.execute(
            select(GardeType).where(GardeType.code == code)
        ).scalar_one_or_none():
            continue
        crosses = end <= start
        base = date(2027, 1, 4)
        _, _, hours = wall_clock_window(base, start, end, crosses)
        session.add(
            GardeType(
                code=code,
                label=label,
                module=Module.GARDES,
                category_id=categories[cat_code].id,
                # Mode B par défaut : assistant en L1 + senior en L2.
                default_coverage_mode=CoverageMode.B,
                start_time=start,
                end_time=end,
                duration_hours=hours,
                duration_class=duration_class,
                count_weight=1.0,
                exchange_class_id=classes[class_code].id,
                # Les six horaires sont désormais confirmés par le client
                # (02/09/2026 et 03/09/2026). Q-03 est close.
                horaires_a_valider=False,
            )
        )
    session.flush()

    for spec in REST_RULES:
        if not session.execute(
            select(RestRule).where(RestRule.code == spec["code"])
        ).scalar_one_or_none():
            session.add(RestRule(**spec))

    # Une base créée avant l'arbitrage du 03/09/2026 peut encore porter la règle
    # ferme des 24 h entre gardes : elle est désactivée, jamais réactivée.
    for code in REST_RULES_RETIREES:
        obsolete = session.execute(
            select(RestRule).where(RestRule.code == code)
        ).scalar_one_or_none()
        if obsolete is not None:
            obsolete.active = False

    if not session.execute(
        select(RuleProfileRow).where(RuleProfileRow.name == "operationnel")
    ).scalar_one_or_none():
        session.add(
            RuleProfileRow(
                name="operationnel",
                version="v1",
                kind="OPERATIONNEL",
                params_json=json.dumps(RULE_PROFILE_OPERATIONNEL),
                is_demo_hypothesis=True,
            )
        )

    if not session.execute(
        select(UrgencyProfile).where(UrgencyProfile.name == "urgence_demo")
    ).scalar_one_or_none():
        session.add(
            UrgencyProfile(
                name="urgence_demo",
                version="v1",
                tiers_json=json.dumps(URGENCY_TIERS),
                is_demo_hypothesis=True,
            )
        )
    session.flush()


# --------------------------------------------------------------------------- #
# Années, trimestres
# --------------------------------------------------------------------------- #


def create_year(session: Session, label: str, start: date, end: date) -> Year:
    year = Year(label=label, start_date=start, end_date=end)
    session.add(year)
    session.flush()
    boundaries = [
        (1, date(start.year, 1, 1), date(start.year, 3, 31)),
        (2, date(start.year, 4, 1), date(start.year, 6, 30)),
        (3, date(start.year, 7, 1), date(start.year, 9, 30)),
        (4, date(start.year, 10, 1), date(start.year, 12, 31)),
    ]
    for index, q_start, q_end in boundaries:
        session.add(
            Quarter(
                year_id=year.id,
                index=index,
                label=f"T{index} {start.year}",
                start_date=max(q_start, start),
                end_date=min(q_end, end),
            )
        )
    session.flush()
    return year


# --------------------------------------------------------------------------- #
# Occurrences et postes
# --------------------------------------------------------------------------- #


def resolve_type_code(day: date, holidays: set[date]) -> str:
    """Type applicable à une date. Un seul type par date, donc jamais d'occurrence
    supplémentaire.

    Ordre confirmé par le client (03/09/2026) :

    1. un jour férié reste férié, **y compris un vendredi férié** ;
    2. un samedi ou un dimanche garde son propre type même s'il précède un jour férié
       (interdiction explicite de créer une occurrence de veille supplémentaire) ;
    3. sinon, une veille **ouvrable** de jour férié est une veille de férié, y compris
       le vendredi, dont l'horaire 17h->9h est identique ;
    4. sinon vendredi, sinon nuit de semaine.

    Rattachement comptable des veilles : OPEN_QUESTIONS.md Q-04.
    """
    if day in holidays:
        return "JOUR_FERIE"
    weekday = day.weekday()
    if weekday == 5:
        return "SAMEDI"
    if weekday == 6:
        return "DIMANCHE"
    if (day + timedelta(days=1)) in holidays:
        return "VEILLE_FERIE"
    if weekday == 4:
        return "NUIT_VENDREDI"
    return "NUIT_SEMAINE"


def materialise_posts(
    session: Session, occurrence: GardeOccurrence, mode: CoverageMode
) -> list[CoveragePost]:
    """(Re)crée les postes d'une occurrence selon le mode.

    Mode A : **un seul** poste, L1 senior, aucune deuxième ligne.
    Mode B : L1 assistant + L2 senior.
    """
    for post in list(occurrence.posts):
        session.delete(post)
    session.flush()

    specs = (
        [(Line.L1, Status.SENIOR)]
        if mode is CoverageMode.A
        else [(Line.L1, Status.ASSISTANT), (Line.L2, Status.SENIOR)]
    )
    created = []
    for line, required in specs:
        post = CoveragePost(
            occurrence_id=occurrence.id, line=line, required_status=required, required=True
        )
        session.add(post)
        created.append(post)
    occurrence.coverage_mode = mode
    session.flush()
    return created


def set_coverage_mode(
    session: Session, occurrence: GardeOccurrence, mode: CoverageMode
) -> list[CoveragePost]:
    return materialise_posts(session, occurrence, mode)


def generate_occurrences(
    session: Session,
    quarter: Quarter,
    holidays: set[date] | None = None,
    mode_resolver=None,
) -> list[GardeOccurrence]:
    """Génère une occurrence par jour du trimestre, avec ses postes.

    Gère les gardes traversant minuit, les changements d'heure et les années
    bissextiles : la durée réelle est calculée en UTC à partir d'horaires muraux.
    """
    holidays = holidays or set()
    types = {t.code: t for t in session.execute(select(GardeType)).scalars()}
    created: list[GardeOccurrence] = []

    day = quarter.start_date
    while day <= quarter.end_date:
        type_code = resolve_type_code(day, holidays)
        garde_type = types[type_code]
        existing = session.execute(
            select(GardeOccurrence).where(
                GardeOccurrence.garde_type_id == garde_type.id,
                GardeOccurrence.local_date == day,
            )
        ).scalar_one_or_none()
        if existing is None:
            start_at, end_at, hours = wall_clock_window(
                day, garde_type.start_time, garde_type.end_time, garde_type.crosses_midnight
            )
            occurrence = GardeOccurrence(
                garde_type_id=garde_type.id,
                quarter_id=quarter.id,
                local_date=day,
                start_at=start_at,
                end_at=end_at,
                duration_hours=hours,
                is_weekend_block=day.weekday() >= 5 or type_code in ("SAMEDI", "DIMANCHE"),
            )
            session.add(occurrence)
            session.flush()
            mode = (
                mode_resolver(occurrence)
                if mode_resolver
                else garde_type.default_coverage_mode
            )
            materialise_posts(session, occurrence, mode)
            created.append(occurrence)
        day += timedelta(days=1)
    session.flush()
    return created


# --------------------------------------------------------------------------- #
# Paires de jours fériés
# --------------------------------------------------------------------------- #


def create_holiday_pair(
    session: Session,
    code: str,
    label: str,
    members: list[tuple[str, date, date]],
    include_eve: bool = True,
) -> HolidayPair:
    pair = session.execute(
        select(HolidayPair).where(HolidayPair.code == code)
    ).scalar_one_or_none()
    if pair is None:
        pair = HolidayPair(code=code, label=label, is_demo_hypothesis=True)
        session.add(pair)
        session.flush()
    for member_label, start, end in members:
        session.add(
            HolidayPairMember(
                pair_id=pair.id,
                label=member_label,
                date_start=start,
                date_end=end,
                include_eve=include_eve,
            )
        )
    session.flush()
    return pair


def occurrences_for_member(
    session: Session, member: HolidayPairMember
) -> list[GardeOccurrence]:
    query = select(GardeOccurrence).where(
        GardeOccurrence.local_date >= member.date_start,
        GardeOccurrence.local_date <= member.date_end,
    )
    return list(session.execute(query).scalars())
