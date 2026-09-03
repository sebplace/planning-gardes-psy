"""Repos, récupération et demandes explicites.

Arbitrages du client du 03/09/2026, à la lettre :

* aucune interdiction universelle de 24 h entre toutes les gardes ;
* interdiction de dépasser une durée continue de service configurable (24 h par
  défaut), avec dérogation possible **uniquement** par une demande explicite datée ;
* au moins 12 h de récupération après 12 h continues **réellement travaillées sur
  place**, proposées et non déclenchées ;
* aucun déclenchement automatique après un simple appel sans déplacement ;
* **aucune présomption** de nuit travaillée du seul fait d'avoir été de garde ;
* appréciation et validation humaines pour les situations intermédiaires.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin

#: Seuils de démonstration, administrables. Le client a confirmé 12 h et 24 h.
SEUIL_RECUPERATION_HEURES = 12.0
DUREE_RECUPERATION_HEURES = 12.0
DUREE_CONTINUE_MAX_HEURES = 24.0


class WeekendBlockRequest(Base, TimestampMixin):
    """Demande explicite et datée d'un bloc de service continu dépassant 24 h.

    Cas confirmé par le client : le week-end complet d'un assistant (samedi 9 h au
    lundi 9 h). Sans cette demande, le service continu de plus de 24 h est refusé
    par une contrainte ferme. La demande est **toujours** à l'initiative de la
    personne, jamais créée par l'application ni par un administrateur seul.
    """

    __tablename__ = "weekend_block_requests"
    __table_args__ = (
        UniqueConstraint("profile_id", "anchor_date", name="uq_weekend_block_request"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False
    )
    #: Premier jour du bloc demandé (le samedi, pour un week-end complet).
    anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Date à laquelle la personne a formulé la demande. Traçabilité exigée.
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    requested_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile = relationship("ProfessionalProfile")


class OnSiteReport(Base, TimestampMixin):
    """Déclaration de travail réellement effectué sur place pendant une garde.

    Rien n'est présumé : cette ligne n'existe que si une personne la déclare. Une
    garde sans déclaration ne vaut donc **aucune** heure travaillée sur place, et
    un simple appel téléphonique sans déplacement n'ouvre aucun droit.
    """

    __tablename__ = "on_site_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id"), nullable=False
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False
    )
    #: Heures réellement travaillées sur place, déclarées par la personne.
    hours_on_site: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Faux pour un simple appel traité à distance : aucun droit ouvert.
    moved_on_site: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Vrai si les heures déclarées ont été travaillées d'un seul tenant.
    continuous: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    declared_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    assignment = relationship("Assignment")
    profile = relationship("ProfessionalProfile")

    @property
    def opens_recovery(self) -> bool:
        """Une récupération n'est **proposable** que si les trois conditions tiennent."""
        return (
            self.moved_on_site
            and self.continuous
            and self.hours_on_site >= SEUIL_RECUPERATION_HEURES - 1e-9
        )


class RecoveryProposal(Base, TimestampMixin):
    """Proposition de récupération, jamais un octroi automatique.

    L'état initial est toujours ``PROPOSEE``. Seule une décision humaine tracée la
    fait passer à ``VALIDEE`` ou ``REFUSEE``. Les situations intermédiaires, par
    exemple une présence partielle, restent volontairement sans automatisme.
    """

    __tablename__ = "recovery_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("on_site_reports.id"), nullable=False
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False
    )
    hours: Mapped[float] = mapped_column(
        Float, nullable=False, default=DUREE_RECUPERATION_HEURES
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="PROPOSEE", nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    report = relationship("OnSiteReport")
    profile = relationship("ProfessionalProfile")
