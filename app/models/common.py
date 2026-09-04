"""Notifications, journal d'audit chaîné, scénarios de projection."""

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

from .base import Base, TimestampMixin


class Notification(Base):
    """Message simulé.

    ``idempotency_key`` est unique et dérivée du fait métier : un redémarrage ou une
    nouvelle tentative technique ne peut produire ni double rappel, ni double
    sollicitation, ni double changement d'état (DECISIONS.md D-009).
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    recipient_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=True
    )
    recipient_label: Mapped[str] = mapped_column(String(160), nullable=False)
    subject: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    channel: Mapped[str] = mapped_column(String(40), default="EMAIL_SIMULE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    anonymised: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AuditEvent(Base):
    """Journal d'audit **chaîné par empreinte**.

    ``hash = sha256(prev_hash || charge utile canonique)``. Une réécriture a
    posteriori casse la chaîne et devient détectable (DECISIONS.md D-006).
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        # Lot D : deux événements ne peuvent pas partager le même prédécesseur.
        # Une fourche concurrente est refusée par la base, jamais commise.
        UniqueConstraint("prev_hash", name="uq_audit_prev_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(160), nullable=False, default="SYSTEME")
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(60), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prev_hash: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    hash: Mapped[str] = mapped_column(String(80), nullable=False)


class Scenario(Base, TimestampMixin):
    """Scénario de projection.

    **Aucun effet opérationnel.** Un scénario ne modifie jamais comptes, quotas,
    disponibilités ni planning. Sa promotion vers une configuration réelle exige une
    action administrative explicite, une confirmation et une trace d'audit.
    """

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    ruleset_version: Mapped[str] = mapped_column(String(60), nullable=False, default="regles_demo_v1")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_demo_hypothesis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    results: Mapped[list["ScenarioResult"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )


class ScenarioResult(Base):
    __tablename__ = "scenario_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    structural_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sensitivity_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    feasibility_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    verdict: Mapped[str] = mapped_column(String(80), nullable=False)

    scenario: Mapped[Scenario] = relationship(back_populates="results")
