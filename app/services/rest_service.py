"""Repos, récupération et demandes explicites de bloc continu.

Arbitrages du client du 03/09/2026, appliqués sans extrapolation :

* aucune interdiction universelle de 24 h entre toutes les gardes ;
* interdiction de dépasser une durée de service continu configurable, 24 h par
  défaut, dérogeable **uniquement** par une demande explicite et datée de la
  personne concernée ;
* au moins 12 h de récupération après 12 h continues réellement travaillées sur
  place, **proposées** puis validées humainement ;
* aucun droit ouvert par un simple appel sans déplacement ;
* aucune présomption de nuit travaillée du seul fait d'avoir été de garde ;
* une concentration problématique produit une **alerte**, jamais une règle ferme.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine import ContinuousDutyRuleIn  # noqa: F401  (référence documentaire)
from ..models import (
    DUREE_CONTINUE_MAX_HEURES,  # noqa: F401  (seuil administrable exposé ici)
    DUREE_RECUPERATION_HEURES,
    SEUIL_RECUPERATION_HEURES,
    Assignment,
    CoveragePost,
    GardeOccurrence,
    OnSiteReport,
    ProfessionalProfile,
    RecoveryProposal,
    User,
    WeekendBlockRequest,
)
from . import audit_service
from .clock import Clock


class RestError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Demandes explicites de bloc continu (week-end complet)
# --------------------------------------------------------------------------- #


def request_weekend_block(
    session: Session,
    profile: ProfessionalProfile,
    anchor_date: date,
    requested_by: User | None,
    comment: str | None = None,
) -> WeekendBlockRequest:
    """Enregistre la demande **de la personne** pour un bloc continu daté.

    L'application ne crée jamais cette demande d'elle-même : sans elle, tout bloc
    de service continu dépassant le maximum est refusé par la contrainte ferme.
    """
    existing = session.execute(
        select(WeekendBlockRequest).where(
            WeekendBlockRequest.profile_id == profile.id,
            WeekendBlockRequest.anchor_date == anchor_date,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.active = True
        existing.requested_at = Clock.now()
        existing.comment = comment
        session.flush()
        row = existing
    else:
        row = WeekendBlockRequest(
            profile_id=profile.id,
            anchor_date=anchor_date,
            requested_at=Clock.now(),
            requested_by_id=requested_by.id if requested_by else None,
            comment=comment,
        )
        session.add(row)
        session.flush()

    audit_service.record(
        session,
        "DEMANDE_BLOC_CONTINU",
        "weekend_block_request",
        row.id,
        {
            "profil": profile.code,
            "date_ancrage": anchor_date.isoformat(),
            "demande_le": row.requested_at.isoformat(),
        },
        actor=requested_by,
    )
    return row


def withdraw_weekend_block(
    session: Session, request: WeekendBlockRequest, actor: User | None
) -> WeekendBlockRequest:
    request.active = False
    session.flush()
    audit_service.record(
        session,
        "DEMANDE_BLOC_CONTINU_RETIREE",
        "weekend_block_request",
        request.id,
        {"date_ancrage": request.anchor_date.isoformat()},
        actor=actor,
    )
    return request





# --------------------------------------------------------------------------- #
# Déclaration de travail sur place et proposition de récupération
# --------------------------------------------------------------------------- #


def declare_on_site(
    session: Session,
    assignment: Assignment,
    profile: ProfessionalProfile,
    hours_on_site: float,
    moved_on_site: bool,
    continuous: bool = True,
    declared_by: User | None = None,
    comment: str | None = None,
) -> tuple[OnSiteReport, RecoveryProposal | None]:
    """Enregistre une déclaration et, le cas échéant, **propose** une récupération.

    Rien n'est présumé : sans déclaration, une garde ne vaut aucune heure sur
    place. Un simple appel traité à distance (``moved_on_site`` faux) n'ouvre
    jamais de droit, quelle que soit la durée déclarée.
    """
    if hours_on_site < 0:
        raise RestError("Le nombre d'heures déclarées ne peut pas être négatif.")

    report = OnSiteReport(
        assignment_id=assignment.id,
        profile_id=profile.id,
        hours_on_site=hours_on_site,
        moved_on_site=moved_on_site,
        continuous=continuous,
        declared_at=Clock.now(),
        comment=comment,
    )
    session.add(report)
    session.flush()

    audit_service.record(
        session,
        "TRAVAIL_SUR_PLACE_DECLARE",
        "on_site_report",
        report.id,
        {
            "profil": profile.code,
            "heures": hours_on_site,
            "deplacement": moved_on_site,
            "continu": continuous,
            "ouvre_recuperation": report.opens_recovery,
        },
        actor=declared_by,
    )

    if not report.opens_recovery:
        return report, None

    occurrence = _occurrence_of(session, assignment)
    debut = occurrence.end_at if occurrence else Clock.now()
    proposal = RecoveryProposal(
        report_id=report.id,
        profile_id=profile.id,
        hours=DUREE_RECUPERATION_HEURES,
        starts_at=debut,
        ends_at=debut + timedelta(hours=DUREE_RECUPERATION_HEURES),
        state="PROPOSEE",
        rationale=(
            f"{hours_on_site:g} h continues réellement travaillées sur place, avec "
            f"déplacement, au-delà du seuil de {SEUIL_RECUPERATION_HEURES:g} h. "
            "Proposition soumise à validation humaine : aucun droit n'est ouvert "
            "automatiquement."
        ),
    )
    session.add(proposal)
    session.flush()

    audit_service.record(
        session,
        "RECUPERATION_PROPOSEE",
        "recovery_proposal",
        proposal.id,
        {
            "profil": profile.code,
            "heures_proposees": DUREE_RECUPERATION_HEURES,
            "etat": proposal.state,
        },
        actor=declared_by,
    )
    return report, proposal


def decide_recovery(
    session: Session,
    proposal: RecoveryProposal,
    accepted: bool,
    decided_by: User | None,
    comment: str | None = None,
) -> RecoveryProposal:
    """Décision humaine explicite. Une proposition ne s'applique jamais seule."""
    if proposal.state != "PROPOSEE":
        raise RestError(
            f"Proposition déjà tranchée (état {proposal.state}) : "
            "aucune décision automatique n'est possible."
        )
    proposal.state = "VALIDEE" if accepted else "REFUSEE"
    proposal.decided_by_id = decided_by.id if decided_by else None
    proposal.decided_at = Clock.now()
    proposal.decision_comment = comment
    session.flush()

    audit_service.record(
        session,
        "RECUPERATION_TRANCHEE",
        "recovery_proposal",
        proposal.id,
        {"etat": proposal.state, "commentaire": comment},
        actor=decided_by,
    )
    return proposal


def pending_recoveries(session: Session) -> list[RecoveryProposal]:
    return list(
        session.execute(
            select(RecoveryProposal)
            .where(RecoveryProposal.state == "PROPOSEE")
            .order_by(RecoveryProposal.id)
        ).scalars()
    )


# --------------------------------------------------------------------------- #
# Alerte de concentration — jamais une contrainte
# --------------------------------------------------------------------------- #


@dataclass
class ConcentrationAlert:
    profile_code: str
    window_start: date
    window_days: int
    count: int
    threshold: int

    @property
    def message(self) -> str:
        return (
            f"{self.profile_code} : {self.count} gardes en {self.window_days} jours "
            f"à partir du {self.window_start.isoformat()} (seuil d'alerte "
            f"{self.threshold}). Signalement pour appréciation humaine, "
            "aucune règle ferme n'a été inventée."
        )


def concentration_alerts(
    session: Session,
    schedule_version_id: int,
    window_days: int = 14,
    threshold: int = 3,
) -> list[ConcentrationAlert]:
    """Signale les concentrations, sans jamais bloquer quoi que ce soit."""
    rows = session.execute(
        select(ProfessionalProfile.code, GardeOccurrence.local_date)
        .select_from(Assignment)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(
            ProfessionalProfile,
            Assignment.profile_id == ProfessionalProfile.id,
        )
        .where(Assignment.schedule_version_id == schedule_version_id)
        .order_by(ProfessionalProfile.code, GardeOccurrence.local_date)
    ).all()

    par_personne: dict[str, list[date]] = {}
    for code, day in rows:
        par_personne.setdefault(code, []).append(day)

    alertes: list[ConcentrationAlert] = []
    for code, jours in par_personne.items():
        jours.sort()
        for i, depart in enumerate(jours):
            fin = depart + timedelta(days=window_days)
            compte = sum(1 for j in jours[i:] if j < fin)
            if compte >= threshold:
                alertes.append(
                    ConcentrationAlert(
                        profile_code=code,
                        window_start=depart,
                        window_days=window_days,
                        count=compte,
                        threshold=threshold,
                    )
                )
                break
    return alertes


# --------------------------------------------------------------------------- #


def _occurrence_of(session: Session, assignment: Assignment) -> GardeOccurrence | None:
    post = session.get(CoveragePost, assignment.post_id)
    if post is None:
        return None
    return session.get(GardeOccurrence, post.occurrence_id)


def _now() -> datetime:
    return Clock.now()
