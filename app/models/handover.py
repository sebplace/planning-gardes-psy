"""Reprises (vagues, candidatures, tirage) et échanges bilatéraux."""

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
    Base,
    CandidacyState,
    HandoverState,
    SwapState,
    TimestampMixin,
    WaveKind,
    WaveState,
    enum_column,
)


class HandoverRequest(Base, TimestampMixin):
    """Demande de reprise d'une garde publiée.

    Tant qu'aucun remplacement n'est confirmé, la garde reste officiellement à la
    charge de la personne initialement affectée.
    """

    __tablename__ = "handover_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), nullable=False)
    requester_profile_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(String(300), nullable=True)
    admin_motive: Mapped[str | None] = mapped_column(String(300), nullable=True)
    state: Mapped[HandoverState] = enum_column(
        HandoverState, nullable=False, default=HandoverState.BROUILLON
    )
    result_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    assignment = relationship("Assignment")
    requester = relationship("ProfessionalProfile", foreign_keys=[requester_profile_id])
    waves: Mapped[list["HandoverWave"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )

    @property
    def is_open(self) -> bool:
        return self.state in (
            HandoverState.BROUILLON,
            HandoverState.COLLECTE_VERTE,
            HandoverState.LISTE_FIGEE_VERTE,
            HandoverState.COLLECTE_UNIQUE,
            HandoverState.LISTE_FIGEE_UNIQUE,
            # États hérités, conservés pour les données antérieures.
            HandoverState.COLLECTE_ORANGE,
            HandoverState.LISTE_FIGEE_ORANGE,
        )


class HandoverWave(Base, TimestampMixin):
    """Vague de sollicitation simultanée.

    ``seed_commitment`` est l'empreinte de la graine, enregistrée **au gel de la
    liste**, donc prouvablement antérieure au calcul du résultat (DECISIONS.md D-007).
    """

    __tablename__ = "handover_waves"
    __table_args__ = (UniqueConstraint("request_id", "kind", name="uq_wave_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("handover_requests.id"), nullable=False)
    kind: Mapped[WaveKind] = enum_column(WaveKind, nullable=False)
    state: Mapped[WaveState] = enum_column(WaveState, nullable=False, default=WaveState.OUVERTE)
    opens_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    list_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    seed_commitment: Mapped[str | None] = mapped_column(String(80), nullable=True)
    solicited_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reminder_plan_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    urgency_tier: Mapped[str | None] = mapped_column(String(60), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    request: Mapped[HandoverRequest] = relationship(back_populates="waves")
    candidacies: Mapped[list["Candidacy"]] = relationship(
        back_populates="wave", cascade="all, delete-orphan"
    )


class WaveSolicitation(Base, TimestampMixin):
    """Personne sollicitée dans une vague. Permet de savoir si tout le monde a répondu."""

    __tablename__ = "wave_solicitations"
    __table_args__ = (UniqueConstraint("wave_id", "profile_id", name="uq_solicitation"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wave_id: Mapped[int] = mapped_column(ForeignKey("handover_waves.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response: Mapped[str | None] = mapped_column(String(20), nullable=True)  # FAVORABLE | REFUS


class Candidacy(Base, TimestampMixin):
    """Réponse favorable. **Une candidature n'est pas une attribution.**

    L'ordre et la vitesse de dépôt n'ont aucune influence sur le tirage.
    """

    __tablename__ = "candidacies"
    __table_args__ = (UniqueConstraint("wave_id", "profile_id", name="uq_candidacy"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wave_id: Mapped[int] = mapped_column(ForeignKey("handover_waves.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    state: Mapped[CandidacyState] = enum_column(
        CandidacyState, nullable=False, default=CandidacyState.DEPOSEE
    )
    exclusion_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    wave: Mapped[HandoverWave] = relationship(back_populates="candidacies")
    profile = relationship("ProfessionalProfile")


class Draw(Base, TimestampMixin):
    """Événement d'attribution auditable.

    Contrainte d'unicité sur la vague : **une seule tentative officielle**.
    Exécuté même lorsqu'une seule candidature reste valide (il n'y a alors pas d'aléa
    utile, mais l'officialisation résulte toujours de cet événement journalisé).
    """

    __tablename__ = "draws"
    __table_args__ = (UniqueConstraint("wave_id", name="uq_draw_wave"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wave_id: Mapped[int] = mapped_column(ForeignKey("handover_waves.id"), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    list_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    seed_commitment: Mapped[str] = mapped_column(String(80), nullable=False)
    server_seed: Mapped[str] = mapped_column(String(80), nullable=False)
    algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    excluded_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    winner_candidacy_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidacies.id"), nullable=True
    )
    winner_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=True
    )
    proof_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    single_candidate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    wave: Mapped[HandoverWave] = relationship()


class SwapProposal(Base, TimestampMixin):
    """Échange bilatéral, autorisé uniquement entre gardes structurellement équivalentes."""

    __tablename__ = "swap_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_a_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), nullable=False)
    assignment_b_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), nullable=False)
    proposer_profile_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False
    )
    # Titulaires **annoncés** au moment de la proposition : revérifiés à l'exécution.
    announced_profile_a_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False
    )
    announced_profile_b_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False
    )
    state: Mapped[SwapState] = enum_column(SwapState, nullable=False, default=SwapState.PROPOSE)
    accepted_a_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_b_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    equivalence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    row_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    assignment_a = relationship("Assignment", foreign_keys=[assignment_a_id])
    assignment_b = relationship("Assignment", foreign_keys=[assignment_b_id])
