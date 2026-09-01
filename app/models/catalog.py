"""Catalogue : catégories comptables, types concrets, occurrences datées, postes de
couverture, paires fériées, classes d'échange.

Les quatre notions du §6 sont séparées et ne peuvent pas être confondues.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CoverageMode, Line, Module, Status, TimestampMixin, enum_column


class ExchangeClass(Base, TimestampMixin):
    """Classe d'équivalence pour l'échange bilatéral.

    Elle **matérialise** une équivalence mais ne suffit jamais à la déclarer :
    le contrôle d'échange vérifie aussi ligne, catégorie, poids, durée et couverture
    (OPEN_QUESTIONS.md Q-12).
    """

    __tablename__ = "exchange_classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    is_demo_hypothesis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class QuotaCategory(Base, TimestampMixin):
    """Catégorie **comptable** de quota, distincte du type concret de garde."""

    __tablename__ = "quota_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    module: Mapped[Module] = enum_column(Module, nullable=False, default=Module.GARDES)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    painful_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    types: Mapped[list["GardeType"]] = relationship(back_populates="category")


class GardeType(Base, TimestampMixin):
    """Type **concret** de garde. Horaires administrables (OPEN_QUESTIONS.md Q-03)."""

    __tablename__ = "garde_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    module: Mapped[Module] = enum_column(Module, nullable=False, default=Module.GARDES)
    category_id: Mapped[int] = mapped_column(ForeignKey("quota_categories.id"), nullable=False)
    default_coverage_mode: Mapped[CoverageMode] = enum_column(
        CoverageMode, nullable=False, default=CoverageMode.B
    )
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False, default=12.0)
    duration_class: Mapped[str] = mapped_column(String(40), nullable=False, default="NUIT_12H")
    count_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    exchange_class_id: Mapped[int | None] = mapped_column(
        ForeignKey("exchange_classes.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    horaires_a_valider: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped[QuotaCategory] = relationship(back_populates="types")
    exchange_class: Mapped[ExchangeClass | None] = relationship()

    @property
    def crosses_midnight(self) -> bool:
        return self.end_time <= self.start_time


class Year(Base, TimestampMixin):
    __tablename__ = "years"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    quarters: Mapped[list["Quarter"]] = relationship(back_populates="year")


class Quarter(Base, TimestampMixin):
    __tablename__ = "quarters"
    __table_args__ = (UniqueConstraint("year_id", "index", name="uq_quarter_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    year_id: Mapped[int] = mapped_column(ForeignKey("years.id"), nullable=False)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    year: Mapped[Year] = relationship(back_populates="quarters")
    occurrences: Mapped[list["GardeOccurrence"]] = relationship(back_populates="quarter")


class GardeOccurrence(Base, TimestampMixin):
    """Instance **datée** d'un type de garde.

    ``start_at`` / ``end_at`` sont stockés en heure locale naïve calculée à partir du
    fuseau Europe/Brussels ; les gardes traversant minuit, les changements d'heure et
    les années bissextiles sont gérés à la construction (voir services/catalog_service).
    """

    __tablename__ = "garde_occurrences"
    __table_args__ = (
        UniqueConstraint("garde_type_id", "local_date", name="uq_occurrence_type_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    garde_type_id: Mapped[int] = mapped_column(ForeignKey("garde_types.id"), nullable=False)
    quarter_id: Mapped[int] = mapped_column(ForeignKey("quarters.id"), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False, default=12.0)
    coverage_mode: Mapped[CoverageMode | None] = enum_column(CoverageMode, nullable=True)
    is_weekend_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    garde_type: Mapped[GardeType] = relationship()
    quarter: Mapped[Quarter] = relationship(back_populates="occurrences")
    posts: Mapped[list["CoveragePost"]] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan"
    )

    @property
    def effective_mode(self) -> CoverageMode:
        return self.coverage_mode or self.garde_type.default_coverage_mode


class CoveragePost(Base, TimestampMixin):
    """Poste de couverture requis.

    **La matérialisation des postes est la seule source de vérité du mode A/B.**
    Mode A ⇒ un unique poste L1 senior : il est structurellement impossible de créer
    une deuxième ligne derrière un senior de première ligne (DECISIONS.md M-001).
    """

    __tablename__ = "coverage_posts"
    __table_args__ = (UniqueConstraint("occurrence_id", "line", name="uq_post_occurrence_line"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(
        ForeignKey("garde_occurrences.id", ondelete="CASCADE"), nullable=False
    )
    line: Mapped[Line] = enum_column(Line, nullable=False)
    required_status: Mapped[Status] = enum_column(Status, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    occurrence: Mapped[GardeOccurrence] = relationship(back_populates="posts")


class HolidayPair(Base, TimestampMixin):
    """Paire de jours fériés. Liste **administrable**, jamais figée dans le code."""

    __tablename__ = "holiday_pairs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo_hypothesis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    members: Mapped[list["HolidayPairMember"]] = relationship(
        back_populates="pair", cascade="all, delete-orphan"
    )


class HolidayPairMember(Base, TimestampMixin):
    """Membre d'une paire, exprimé comme intervalle de dates.

    Cet intervalle permet de rattacher la **veille nocturne** à la période fériée
    (OPEN_QUESTIONS.md Q-04) et de gérer une paire à cheval sur deux années.
    """

    __tablename__ = "holiday_pair_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    pair_id: Mapped[int] = mapped_column(
        ForeignKey("holiday_pairs.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    include_eve: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    pair: Mapped[HolidayPair] = relationship(back_populates="members")

    def covers(self, day: date) -> bool:
        return self.date_start <= day <= self.date_end
