"""Compteurs annuels : six compteurs seniors, sous-compteurs assistants.

Exigences P1.12 et P1.13 de la matrice d'audit.

* Seniors : six compteurs pré-agrégés, soit les trois catégories comptables
  croisées avec les deux lignes, pondérés en dixièmes et datés. La pondération
  retenue est celle en vigueur **à la date de la garde**, jamais la dernière
  connue : c'est ce qui rend le compteur relisible a posteriori.
* Assistants : cinq sous-compteurs statistiques distincts (vendredi, veille de
  jour férié, samedi, dimanche, jour férié). Ce sont des indicateurs de
  répartition, pas des quotas : ils ne contraignent rien.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.cycle import cycle_pour

from ..models import (
    Assignment,
    CoveragePost,
    GardeOccurrence,
    GardeType,
    GardeWeightHistory,
    Line,
    ProfessionalProfile,
    Quarter,
    QuotaCategory,
    ScheduleState,
    ScheduleVersion,
    Status,
    Year,
)

#: Les trois catégories comptables, dans l'ordre institutionnel.
CATEGORIES = ("NUITS_LJ", "WEEKENDS_VEILLES", "FERIES")
#: Les deux lignes.
LIGNES = (Line.L1, Line.L2)
#: Les cinq sous-compteurs statistiques des assistants.
SOUS_COMPTEURS_ASSISTANTS = (
    "NUIT_VENDREDI",
    "VEILLE_FERIE",
    "SAMEDI",
    "DIMANCHE",
    "JOUR_FERIE",
)


@dataclass
class Cellule:
    category_code: str
    line: str
    gardes: float = 0.0
    pondere: float = 0.0

    @property
    def cle(self) -> tuple[str, str]:
        return (self.category_code, self.line)


@dataclass
class CompteursSenior:
    profile_code: str
    year_label: str
    cellules: list[Cellule] = field(default_factory=list)
    ponderation_dixiemes: int | None = None
    ponderation_date: date | None = None

    @property
    def total_gardes(self) -> float:
        return round(sum(c.gardes for c in self.cellules), 3)

    @property
    def total_pondere(self) -> float:
        return round(sum(c.pondere for c in self.cellules), 3)

    def cellule(self, category_code: str, line: Line) -> Cellule:
        for c in self.cellules:
            if c.cle == (category_code, line.value):
                return c
        raise KeyError((category_code, line.value))


@dataclass
class SousCompteursAssistant:
    profile_code: str
    year_label: str
    compteurs: dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return round(sum(self.compteurs.values()), 3)


# --------------------------------------------------------------------------- #


def poids_a_la_date(
    session: Session, profile: ProfessionalProfile, jour: date
) -> tuple[int | None, date | None]:
    """Pondération de garde en vigueur à cette date, avec sa date d'effet.

    Retourne ``(None, None)`` si aucune pondération n'a été enregistrée : rien
    n'est deviné, et le compteur pondéré reste alors vide.
    """
    ligne = session.execute(
        select(GardeWeightHistory)
        .where(
            GardeWeightHistory.profile_id == profile.id,
            GardeWeightHistory.start_date <= jour,
        )
        .order_by(GardeWeightHistory.start_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ligne is None:
        return None, None
    if ligne.end_date is not None and ligne.end_date < jour:
        return None, None
    return ligne.weight_tenths, ligne.start_date


def _gardes_publiees(session: Session, profile: ProfessionalProfile, year: Year):
    """Gardes publiées de la personne sur le **cycle** de quota de l'année.

    Lot C, point 4 du contre-audit du 04/09/2026 : ce n'est plus l'année civile
    des trimestres qui délimite les compteurs, mais le cycle canonique du
    premier lundi d'octobre inclus au premier lundi d'octobre suivant exclu. Le
    rattachement suit la **date de service**.
    """
    cycle = cycle_pour(year.start_date)
    return session.execute(
        select(CoveragePost, GardeOccurrence, GardeType, QuotaCategory)
        .select_from(Assignment)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(QuotaCategory, GardeType.category_id == QuotaCategory.id)
        .join(Quarter, GardeOccurrence.quarter_id == Quarter.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            Assignment.profile_id == profile.id,
            ScheduleVersion.state == ScheduleState.PUBLIE,
            GardeOccurrence.local_date >= cycle.debut,
            GardeOccurrence.local_date <= cycle.fin,
        )
    ).all()


def compteurs_senior(
    session: Session, profile: ProfessionalProfile, year: Year
) -> CompteursSenior:
    """Les six compteurs d'un senior, toujours présents même à zéro."""
    resultat = CompteursSenior(
        profile_code=profile.code,
        year_label=year.label,
        cellules=[
            Cellule(category_code=code, line=line.value)
            for code in CATEGORIES
            for line in LIGNES
        ],
    )
    index = {c.cle: c for c in resultat.cellules}

    for post, occurrence, garde_type, category in _gardes_publiees(
        session, profile, year
    ):
        cle = (category.code, post.line.value)
        if cle not in index:
            cellule = Cellule(category_code=category.code, line=post.line.value)
            resultat.cellules.append(cellule)
            index[cle] = cellule
        poids, effet = poids_a_la_date(session, profile, occurrence.local_date)
        index[cle].gardes += garde_type.count_weight
        if poids is not None:
            index[cle].pondere += garde_type.count_weight * (poids / 10.0)
            if resultat.ponderation_dixiemes is None:
                resultat.ponderation_dixiemes = poids
                resultat.ponderation_date = effet

    if resultat.ponderation_dixiemes is None:
        poids, effet = poids_a_la_date(session, profile, year.start_date)
        resultat.ponderation_dixiemes = poids
        resultat.ponderation_date = effet

    for cellule in resultat.cellules:
        cellule.gardes = round(cellule.gardes, 3)
        cellule.pondere = round(cellule.pondere, 3)
    resultat.cellules.sort(key=lambda c: (CATEGORIES.index(c.category_code)
                                          if c.category_code in CATEGORIES
                                          else 99, c.line))
    return resultat


def sous_compteurs_assistant(
    session: Session, profile: ProfessionalProfile, year: Year
) -> SousCompteursAssistant:
    """Les cinq sous-compteurs d'un assistant. Statistiques, jamais contraignants."""
    resultat = SousCompteursAssistant(
        profile_code=profile.code,
        year_label=year.label,
        compteurs={code: 0.0 for code in SOUS_COMPTEURS_ASSISTANTS},
    )
    for _post, _occurrence, garde_type, _category in _gardes_publiees(
        session, profile, year
    ):
        if garde_type.code in resultat.compteurs:
            resultat.compteurs[garde_type.code] += garde_type.count_weight
    resultat.compteurs = {k: round(v, 3) for k, v in resultat.compteurs.items()}
    return resultat


def tableau_seniors(session: Session, year: Year) -> list[CompteursSenior]:
    return [
        compteurs_senior(session, profile, year)
        for profile in session.execute(
            select(ProfessionalProfile)
            .where(ProfessionalProfile.status == Status.SENIOR)
            .order_by(ProfessionalProfile.code)
        ).scalars()
    ]


def tableau_assistants(session: Session, year: Year) -> list[SousCompteursAssistant]:
    return [
        sous_compteurs_assistant(session, profile, year)
        for profile in session.execute(
            select(ProfessionalProfile)
            .where(ProfessionalProfile.status == Status.ASSISTANT)
            .order_by(ProfessionalProfile.code)
        ).scalars()
    ]


def somme_ponderations(session: Session, jour: date) -> float:
    """Somme des pondérations de garde des seniors à une date, en dixièmes.

    Sert de contrôle de cohérence : le client a transmis 84/10 au 01/10/2026.
    """
    total = 0
    for profile in session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.status == Status.SENIOR)
    ).scalars():
        poids, _ = poids_a_la_date(session, profile, jour)
        total += poids or 0
    return total / 10.0
