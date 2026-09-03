"""Quotas, exemptions, règles de repos, ajustements reportés."""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Enforcement, Line, Status, TimestampMixin, enum_column


class QuotaTarget(Base, TimestampMixin):
    """Cible **souple**, minimum et maximum **fermes optionnels**.

    Saisie manuellement tant que la formule institutionnelle n'est pas stabilisée
    (DECISIONS.md M-004, OPEN_QUESTIONS.md Q-01). Le moteur n'invente aucune formule.
    """

    __tablename__ = "quota_targets"
    __table_args__ = (
        UniqueConstraint("profile_id", "year_id", "category_id", "line", name="uq_quota_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    year_id: Mapped[int] = mapped_column(ForeignKey("years.id"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("quota_categories.id"), nullable=False)
    line: Mapped[Line] = enum_column(Line, nullable=False)

    target: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    minimum: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_minimum: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hard_maximum: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    source: Mapped[str] = mapped_column(String(40), default="MANUEL_ADMIN", nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile = relationship("ProfessionalProfile")
    category = relationship("QuotaCategory")
    year = relationship("Year")


class QuotaTargetHistory(Base, TimestampMixin):
    """Historique des changements de quota (auteur, date, ancienne et nouvelle valeur)."""

    __tablename__ = "quota_target_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    quota_target_id: Mapped[int] = mapped_column(ForeignKey("quota_targets.id"), nullable=False)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    old_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class Exemption(Base, TimestampMixin):
    """Exemption totale ou partielle, datée et commentée.

    **Aucun seuil d'âge n'est codé** (OPEN_QUESTIONS.md Q-02) : une exemption est un
    fait administratif saisi, jamais une condition dérivée d'un attribut personnel.
    """

    __tablename__ = "exemptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("quota_categories.id"), nullable=True
    )
    line: Mapped[Line | None] = enum_column(Line, nullable=True)
    total: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reduction_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    profile = relationship("ProfessionalProfile")
    category = relationship("QuotaCategory")


class RestRule(Base, TimestampMixin):
    """Règle de repos. Son caractère **ferme ou souple** est une décision
    institutionnelle attendue (OPEN_QUESTIONS.md Q-06)."""

    __tablename__ = "rest_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    enforcement: Mapped[Enforcement] = enum_column(Enforcement, nullable=False)
    min_hours_between: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_count_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_count_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_consecutive_weekends: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[str] = mapped_column(String(40), default="v1", nullable=False)
    is_demo_hypothesis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MonthlyCap(Base, TimestampMixin):
    """Plafond mensuel de gardes, **administrable et distinct du quota global**.

    Arbitrage du client du 03/09/2026 : le plafond institutionnel n'a pas été
    chiffré. On ne transforme donc jamais automatiquement une valeur de simulation
    (5, 6 ou 7) en règle ferme. Trois verrous cumulés sont exigés pour qu'un
    plafond devienne opposable :

    1. ``max_per_month`` renseigné et strictement positif ;
    2. ``institutionally_validated`` mis à vrai par une décision explicite ;
    3. ``enforcement`` déclaré ``FERME``.

    Tant que l'un des trois manque, le plafond est **informatif** : il alimente les
    projections et les alertes, jamais le moteur.
    """

    __tablename__ = "monthly_caps"
    __table_args__ = (
        UniqueConstraint(
            "year_id", "status", "profile_id", name="uq_monthly_cap_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year_id: Mapped[int] = mapped_column(ForeignKey("years.id"), nullable=False)
    status: Mapped[Status | None] = enum_column(Status, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=True
    )

    max_per_month: Mapped[float | None] = mapped_column(Float, nullable=True)
    enforcement: Mapped[Enforcement] = enum_column(
        Enforcement, nullable=False, default=Enforcement.SOUPLE
    )
    institutionally_validated: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    label: Mapped[str] = mapped_column(
        String(200), default="plafond mensuel", nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    profile = relationship("ProfessionalProfile")
    year = relationship("Year")

    @property
    def is_enforceable(self) -> bool:
        return (
            self.max_per_month is not None
            and self.max_per_month > 0
            and self.institutionally_validated
            and self.enforcement is Enforcement.FERME
        )

    @property
    def alert(self) -> str | None:
        """Message d'alerte si le plafond n'est pas utilisable tel quel."""
        cible = (
            f"le profil {self.profile_id}"
            if self.profile_id is not None
            else f"le statut {self.status.value if self.status else 'inconnu'}"
        )
        if self.max_per_month is None:
            return (
                f"Plafond mensuel non chiffré pour {cible}. Valeur institutionnelle "
                "attendue avant tout planning officiel : rien n'a été inventé."
            )
        if not self.institutionally_validated:
            return (
                f"Plafond mensuel de {self.max_per_month:g} saisi pour {cible} mais "
                "non validé institutionnellement : il reste une hypothèse de "
                "simulation et n'est pas opposable."
            )
        if self.enforcement is not Enforcement.FERME:
            return (
                f"Plafond mensuel de {self.max_per_month:g} validé pour {cible} mais "
                "déclaré souple : il oriente sans jamais bloquer."
            )
        return None


class QuotaAdjustment(Base, TimestampMixin):
    """Écart reporté après une reprise.

    Une reprise crédite la personne qui **assure réellement** la garde et débite la
    personne remplacée. L'écart résiduel est conservé ici pour être pris en compte
    lors de la campagne suivante, **sans remanier le planning publié**
    (DECISIONS.md M-007).
    """

    __tablename__ = "quota_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    year_id: Mapped[int] = mapped_column(ForeignKey("years.id"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("quota_categories.id"), nullable=False)
    line: Mapped[Line] = enum_column(Line, nullable=False)
    delta: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    carried_to_next_campaign: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    profile = relationship("ProfessionalProfile")
    category = relationship("QuotaCategory")
