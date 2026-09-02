"""Exécutions du moteur, propositions, versions de planning, affectations, corrections."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    AssignmentOrigin,
    Base,
    EngineRunStatus,
    ScheduleState,
    TimestampMixin,
    enum_column,
)


class RuleProfileRow(Base, TimestampMixin):
    """Profil de règles **versionné** : les poids sont des données administrables."""

    __tablename__ = "rule_profiles"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_rule_profile"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="OPERATIONNEL", nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo_hypothesis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class UrgencyProfile(Base, TimestampMixin):
    """Seuils de proximité, fenêtres et rappels des vagues de reprise.

    Valeurs de démonstration, administrables et versionnées (OPEN_QUESTIONS.md Q-09).
    """

    __tablename__ = "urgency_profiles"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_urgency_profile"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    tiers_json: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo_hypothesis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EngineRun(Base, TimestampMixin):
    """Instantané reproductible d'une exécution du moteur."""

    __tablename__ = "engine_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    quarter_id: Mapped[int] = mapped_column(ForeignKey("quarters.id"), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String(60), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    rule_profile_label: Mapped[str] = mapped_column(String(120), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    input_snapshot_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    input_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[EngineRunStatus] = enum_column(
        EngineRunStatus, nullable=False, default=EngineRunStatus.EN_COURS
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposals: Mapped[list["Proposal"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Proposal(Base, TimestampMixin):
    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("engine_run_id", "variant_index", name="uq_proposal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    engine_run_id: Mapped[int] = mapped_column(ForeignKey("engine_runs.id"), nullable=False)
    variant_index: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    score_total: Mapped[float] = mapped_column(Float, nullable=False)
    score_breakdown_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    feasible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unfilled_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tensions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    quota_gaps_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    orange_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_availability_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    diversity_min: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    run: Mapped[EngineRun] = relationship(back_populates="proposals")
    items: Mapped[list["ProposalAssignment"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )


class ProposalAssignment(Base):
    __tablename__ = "proposal_assignments"
    __table_args__ = (UniqueConstraint("proposal_id", "post_id", name="uq_proposal_post"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), nullable=False)
    post_id: Mapped[int] = mapped_column(ForeignKey("coverage_posts.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    explanation_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    proposal: Mapped[Proposal] = relationship(back_populates="items")
    post = relationship("CoveragePost")
    profile = relationship("ProfessionalProfile")


class ScheduleVersion(Base, TimestampMixin):
    """Version de planning. **Un planning publié n'est jamais réécrit** : toute
    modification crée une nouvelle version et bascule la précédente en REMPLACE."""

    __tablename__ = "schedule_versions"
    __table_args__ = (
        UniqueConstraint("quarter_id", "version_no", name="uq_schedule_version"),
        # P2.2 : au plus UNE version publiée par trimestre (garanti en base).
        Index(
            "uq_one_published_per_quarter",
            "quarter_id",
            unique=True,
            sqlite_where=text("state = 'PUBLIE'"),
            postgresql_where=text("state = 'PUBLIE'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    quarter_id: Mapped[int] = mapped_column(ForeignKey("quarters.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[ScheduleState] = enum_column(
        ScheduleState, nullable=False, default=ScheduleState.GENERE
    )
    source_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposals.id"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    validated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    quarter = relationship("Quarter")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="schedule_version", cascade="all, delete-orphan"
    )


class Assignment(Base, TimestampMixin):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("schedule_version_id", "post_id", name="uq_assignment_post"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=False
    )
    post_id: Mapped[int] = mapped_column(ForeignKey("coverage_posts.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    origin: Mapped[AssignmentOrigin] = enum_column(
        AssignmentOrigin, nullable=False, default=AssignmentOrigin.MOTEUR
    )
    explanation_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Verrouillage optimiste : toute opération concurrente incrémente ce compteur.
    row_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Garde-fou : une garde ne peut participer qu'à une seule opération à la fois.
    busy_operation: Mapped[str | None] = mapped_column(String(40), nullable=True)

    schedule_version: Mapped[ScheduleVersion] = relationship(back_populates="assignments")
    post = relationship("CoveragePost")
    profile = relationship("ProfessionalProfile")


class ManualCorrection(Base, TimestampMixin):
    """Toute correction manuelle est journalisée : auteur, date, motif bref."""

    __tablename__ = "manual_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=False
    )
    post_id: Mapped[int] = mapped_column(ForeignKey("coverage_posts.id"), nullable=False)
    from_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=True
    )
    to_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
