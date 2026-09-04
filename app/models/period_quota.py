"""Quota de période : compteur **opérationnel**, utilisable par le moteur.

Lot 2, point 1 du contre-audit du 04/09/2026 :

    Le quota assistant 57/68 porte sur la période unique du 19/10/2026 au
    03/10/2027 inclus, à cheval sur deux années, et ne peut rester un simple
    calcul de projection.

Un ``PeriodQuota`` est donc :

* une **période de dates de service** explicite, bornes incluses, qui peut être à
  cheval sur deux années civiles et sur plusieurs trimestres ;
* une **cible** et, optionnellement, un **maximum ferme** ;
* rattaché soit à un profil précis, soit à un statut.

Comme pour le plafond mensuel, un maximum n'est opposable au moteur que s'il est
chiffré, validé institutionnellement et déclaré ferme. Le client n'ayant pas
tranché entre 57 et 68, aucune valeur n'est écrite par défaut.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Enforcement, Status, TimestampMixin, enum_column


class PeriodQuota(Base, TimestampMixin):
    """Quota portant sur une période de dates de service."""

    __tablename__ = "period_quotas"
    __table_args__ = (
        UniqueConstraint(
            "code", "status", "profile_id", name="uq_period_quota_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)

    status: Mapped[Status | None] = enum_column(Status, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=True
    )

    #: Bornes de **dates de service**, toutes deux incluses.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    #: Cible souple sur la période entière.
    target: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Maximum, opposable seulement si les trois verrous sont franchis.
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)
    enforcement: Mapped[Enforcement] = enum_column(
        Enforcement, nullable=False, default=Enforcement.SOUPLE
    )
    institutionally_validated: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    profile = relationship("ProfessionalProfile")

    # ------------------------------------------------------------------ #

    def covers(self, date_de_service: date) -> bool:
        """Le rattachement se fait sur la date de **début de service**."""
        return self.start_date <= date_de_service <= self.end_date

    @property
    def is_enforceable(self) -> bool:
        return (
            self.maximum is not None
            and self.maximum > 0
            and self.institutionally_validated
            and self.enforcement is Enforcement.FERME
        )

    @property
    def alert(self) -> str | None:
        if self.maximum is None:
            return (
                f"« {self.label} » : aucun maximum chiffré. La cible de "
                f"{self.target:g} reste indicative."
            )
        if not self.institutionally_validated:
            return (
                f"« {self.label} » : maximum de {self.maximum:g} saisi mais non "
                "validé institutionnellement ; il n'est pas opposable."
            )
        if self.enforcement is not Enforcement.FERME:
            return (
                f"« {self.label} » : maximum de {self.maximum:g} validé mais "
                "déclaré souple ; il oriente sans bloquer."
            )
        return None
