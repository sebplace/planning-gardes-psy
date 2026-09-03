"""Socle des modèles : base déclarative, énumérations persistées, mixins."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Les énumérations métier du moteur sont réutilisées telles quelles : une seule
# définition fait autorité pour le moteur, la base et l'interface.
from ..engine.types import (  # noqa: F401
    Color,
    CoverageMode,
    Enforcement,
    Line,
    Status,
)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enum_column(enum_cls, **kwargs):
    """Colonne d'énumération portable (stockée en texte, contrainte CHECK)."""
    return mapped_column(
        SAEnum(enum_cls, native_enum=False, length=40, validate_strings=True), **kwargs
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# Énumérations propres à la persistance
# --------------------------------------------------------------------------- #


class Module(str, Enum):
    """Discriminant de module : le futur module de jour réutilise le socle."""

    GARDES = "GARDES"
    PERMANENCES_JOUR = "PERMANENCES_JOUR"


class CampaignState(str, Enum):
    PREPARATION = "PREPARATION"
    OUVERTE = "OUVERTE"
    CLOTUREE = "CLOTUREE"
    RESOLUTION_NON_REPONDANTS = "RESOLUTION_NON_REPONDANTS"
    PRETE = "PRETE"
    ARCHIVEE = "ARCHIVEE"


class SubmissionState(str, Enum):
    NON_COMMENCEE = "NON_COMMENCEE"
    BROUILLON = "BROUILLON"
    VALIDEE = "VALIDEE"
    VERROUILLEE = "VERROUILLEE"


class AvailabilitySource(str, Enum):
    UTILISATEUR = "UTILISATEUR"
    CONVERSION_NON_REPONSE = "CONVERSION_NON_REPONSE"
    ADMIN = "ADMIN"


class ScheduleState(str, Enum):
    GENERE = "GENERE"
    EN_REVISION = "EN_REVISION"
    VALIDE = "VALIDE"
    PUBLIE = "PUBLIE"
    REMPLACE = "REMPLACE"


class AssignmentOrigin(str, Enum):
    MOTEUR = "MOTEUR"
    MANUEL = "MANUEL"
    REPRISE = "REPRISE"
    ECHANGE = "ECHANGE"


class HandoverState(str, Enum):
    BROUILLON = "BROUILLON"
    COLLECTE_VERTE = "COLLECTE_VERTE"
    LISTE_FIGEE_VERTE = "LISTE_FIGEE_VERTE"
    #: Collecte unique de deuxième ligne (verts + orange).
    COLLECTE_UNIQUE = "COLLECTE_UNIQUE"
    LISTE_FIGEE_UNIQUE = "LISTE_FIGEE_UNIQUE"
    #: Conservés pour les données antérieures. Plus jamais atteints.
    COLLECTE_ORANGE = "COLLECTE_ORANGE"
    LISTE_FIGEE_ORANGE = "LISTE_FIGEE_ORANGE"
    ATTRIBUEE = "ATTRIBUEE"
    ESCALADE = "ESCALADE"
    ANNULEE = "ANNULEE"
    EXPIREE = "EXPIREE"


class WaveKind(str, Enum):
    #: Reprise de première ligne : verts déclarés uniquement.
    VERTE = "VERTE"
    #: Reprise de deuxième ligne : collecte unique verts + orange, priorité au vert
    #: au moment du tirage (arbitrage client du 03/09/2026).
    UNIQUE = "UNIQUE"
    #: Conservé pour les données antérieures. Plus jamais ouverte.
    ORANGE = "ORANGE"


class WaveState(str, Enum):
    OUVERTE = "OUVERTE"
    FIGEE = "FIGEE"
    TIREE = "TIREE"
    SANS_CANDIDATURE = "SANS_CANDIDATURE"


class CandidacyState(str, Enum):
    DEPOSEE = "DEPOSEE"
    VALIDE = "VALIDE"
    EXCLUE = "EXCLUE"
    RETENUE = "RETENUE"
    NON_RETENUE = "NON_RETENUE"


class SwapState(str, Enum):
    PROPOSE = "PROPOSE"
    ACCEPTE_PAR_LES_DEUX = "ACCEPTE_PAR_LES_DEUX"
    OFFICIEL = "OFFICIEL"
    REFUSE = "REFUSE"
    ANNULE = "ANNULE"
    EXPIRE = "EXPIRE"


class EngineRunStatus(str, Enum):
    EN_COURS = "EN_COURS"
    TERMINEE = "TERMINEE"
    ECHEC = "ECHEC"


class HolidayRequirement(str, Enum):
    VERT = "VERT"
    VERT_ORANGE = "VERT_ORANGE"
