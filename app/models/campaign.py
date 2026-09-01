"""Campagne trimestrielle de désidératas, soumissions et disponibilités."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    AvailabilitySource,
    Base,
    CampaignState,
    Color,
    HolidayRequirement,
    Line,
    Module,
    SubmissionState,
    TimestampMixin,
    enum_column,
)


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("quarter_id", "module", name="uq_campaign_quarter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    quarter_id: Mapped[int] = mapped_column(ForeignKey("quarters.id"), nullable=False)
    module: Mapped[Module] = enum_column(Module, nullable=False, default=Module.GARDES)
    state: Mapped[CampaignState] = enum_column(
        CampaignState, nullable=False, default=CampaignState.PREPARATION
    )
    opens_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Valeurs de démonstration — OPEN_QUESTIONS.md Q-08 et Q-05.
    grace_period_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    reminder_offsets_days: Mapped[str] = mapped_column(
        String(80), default="30,14,7,2", nullable=False
    )
    holiday_pair_requirement: Mapped[HolidayRequirement] = enum_column(
        HolidayRequirement, nullable=False, default=HolidayRequirement.VERT_ORANGE
    )
    default_conversion_done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    quarter = relationship("Quarter")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

    @property
    def reminder_offsets(self) -> list[int]:
        return [int(x) for x in self.reminder_offsets_days.split(",") if x.strip()]

    @property
    def grace_deadline(self) -> datetime:
        from datetime import timedelta

        return self.deadline_at + timedelta(hours=self.grace_period_hours)


class Submission(Base, TimestampMixin):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "profile_id", name="uq_submission_person"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    state: Mapped[SubmissionState] = enum_column(
        SubmissionState, nullable=False, default=SubmissionState.NON_COMMENCEE
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reopened_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_reminder_index: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="submissions")
    profile = relationship("ProfessionalProfile")
    availabilities: Mapped[list["Availability"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )

    @property
    def is_finalised(self) -> bool:
        return self.state in (SubmissionState.VALIDEE, SubmissionState.VERROUILLEE)


class Availability(Base, TimestampMixin):
    """Couleur de disponibilité.

    ``line`` est **nullable** : le modèle supporte déjà une préférence distincte par
    ligne (OPEN_QUESTIONS.md Q-10) ; la saisie de démonstration produit une couleur
    unique applicable à toutes les lignes éligibles.

    ``is_declared`` distingue définitivement un vert **déclaré** d'une disponibilité
    **par défaut** issue d'une non-réponse (DECISIONS.md M-008).
    """

    __tablename__ = "availabilities"
    __table_args__ = (
        UniqueConstraint(
            "submission_id", "occurrence_id", "line", name="uq_availability_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), nullable=False)
    occurrence_id: Mapped[int] = mapped_column(ForeignKey("garde_occurrences.id"), nullable=False)
    line: Mapped[Line | None] = enum_column(Line, nullable=True)
    color: Mapped[Color] = enum_column(Color, nullable=False)
    is_declared: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[AvailabilitySource] = enum_column(
        AvailabilitySource, nullable=False, default=AvailabilitySource.UTILISATEUR
    )
    comment: Mapped[str | None] = mapped_column(String(300), nullable=True)

    submission: Mapped[Submission] = relationship(back_populates="availabilities")
    occurrence = relationship("GardeOccurrence")

    @property
    def display_label(self) -> str:
        """Libellé affiché. Ne présente **jamais** une disponibilité par défaut
        comme une réponse volontaire."""
        if self.color is Color.DISPO_DEFAUT:
            return "Disponible par défaut — non confirmé par la personne"
        return {
            Color.VERT: "Vert — disponible",
            Color.ORANGE: "Orange — possible, à éviter si mieux",
            Color.ROUGE: "Rouge — indisponibilité ferme",
        }[self.color]
