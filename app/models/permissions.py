"""Permissions applicatives distinctes et traçables.

Exigence P1.19. Six permissions ont été demandées par le client, chacune
attribuable séparément, datée et journalisée :

* ``RESP_L1`` : responsable de la première ligne ;
* ``RESP_L2`` : responsable de la deuxième ligne ;
* ``CHEF_SERVICE`` : chef de service ;
* ``GESTION_COMPTES`` : création et désactivation des comptes ;
* ``PUBLICATION`` : validation et publication d'un planning ;
* ``CONSULTATION_AUDIT`` : lecture du journal d'audit.

Elles sont **indépendantes du statut professionnel** : un senior peut être
responsable de ligne sans être administrateur, et un administrateur n'est pas
nécessairement médecin.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

RESP_L1 = "RESP_L1"
RESP_L2 = "RESP_L2"
CHEF_SERVICE = "CHEF_SERVICE"
GESTION_COMPTES = "GESTION_COMPTES"
PUBLICATION = "PUBLICATION"
CONSULTATION_AUDIT = "CONSULTATION_AUDIT"

PERMISSIONS = (
    RESP_L1,
    RESP_L2,
    CHEF_SERVICE,
    GESTION_COMPTES,
    PUBLICATION,
    CONSULTATION_AUDIT,
)

LIBELLES = {
    RESP_L1: "Responsable de la première ligne",
    RESP_L2: "Responsable de la deuxième ligne",
    CHEF_SERVICE: "Chef de service",
    GESTION_COMPTES: "Gestion des comptes",
    PUBLICATION: "Validation et publication du planning",
    CONSULTATION_AUDIT: "Consultation du journal d'audit",
}


class PermissionGrant(Base, TimestampMixin):
    """Attribution datée d'une permission à un compte.

    Une attribution n'est jamais supprimée : elle est révoquée en posant une date
    de fin, afin que l'historique des droits reste lisible a posteriori.
    """

    __tablename__ = "permission_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "code", "start_date", name="uq_permission_grant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    granted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    user = relationship("User", foreign_keys=[user_id])

    def covers(self, jour: date) -> bool:
        if not self.active:
            return False
        if jour < self.start_date:
            return False
        if self.end_date is not None and jour > self.end_date:
            return False
        return True

    @property
    def label(self) -> str:
        return LIBELLES.get(self.code, self.code)
