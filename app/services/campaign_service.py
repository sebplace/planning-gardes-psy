"""Campagne trimestrielle de désidératas.

Cycle : ouverture J-30, rappels J-14, J-7, J-2, échéance, résolution des
non-répondants, disponibilité par défaut après délai de grâce.

Règle centrale : la **non-réponse** ne devient jamais silencieusement un vert.
Elle bloque d'abord la génération, alerte les administrateurs, et n'est convertie
en ``DISPO_DEFAUT`` qu'après les relances **et** le délai de grâce. Ce statut reste
distinct d'un vert déclaré partout : écrans, exports, explications, journaux.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Availability,
    AvailabilitySource,
    Campaign,
    CampaignState,
    Color,
    GardeOccurrence,
    HolidayPair,
    HolidayRequirement,
    Line,
    ProfessionalProfile,
    Status,
    Submission,
    SubmissionState,
    User,
)
from . import audit_service, notification_service
from .clock import Clock, format_date_fr


class CampaignError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Création et ouverture
# --------------------------------------------------------------------------- #


def create_campaign(
    session: Session,
    quarter,
    opens_at: datetime,
    deadline_at: datetime,
    admin: User | None = None,
    grace_period_hours: int = 48,
    requirement: HolidayRequirement = HolidayRequirement.VERT_ORANGE,
    reminder_offsets_days: str = "30,14,7,2",
) -> Campaign:
    campaign = Campaign(
        quarter_id=quarter.id,
        opens_at=opens_at,
        deadline_at=deadline_at,
        grace_period_hours=grace_period_hours,
        holiday_pair_requirement=requirement,
        reminder_offsets_days=reminder_offsets_days,
        created_by_id=admin.id if admin else None,
        state=CampaignState.PREPARATION,
    )
    session.add(campaign)
    session.flush()

    for profile in session.execute(select(ProfessionalProfile)).scalars():
        if not profile.is_active_on(quarter.start_date) and not profile.is_active_on(
            quarter.end_date
        ):
            continue
        session.add(Submission(campaign_id=campaign.id, profile_id=profile.id))
    session.flush()
    audit_service.record(
        session, "CAMPAGNE_CREEE", "campaign", campaign.id,
        {"quarter": quarter.label, "deadline": deadline_at.isoformat()}, actor=admin,
    )
    return campaign


def open_campaign(session: Session, campaign: Campaign, admin: User | None = None) -> Campaign:
    if campaign.state is not CampaignState.PREPARATION:
        raise CampaignError("Seule une campagne en préparation peut être ouverte.")
    campaign.state = CampaignState.OUVERTE
    session.flush()
    for submission in campaign.submissions:
        notification_service.enqueue(
            session,
            "CAMPAGNE_OUVERTURE",
            f"campagne:{campaign.id}:ouverture:{submission.profile_id}",
            submission.profile,
            {
                "quarter": campaign.quarter.label,
                "deadline": campaign.deadline_at.strftime("%d/%m/%Y %H:%M"),
            },
        )
        submission.last_reminder_index = 0
    audit_service.record(
        session, "CAMPAGNE_OUVERTE", "campaign", campaign.id,
        {"submissions": len(campaign.submissions)}, actor=admin,
    )
    return campaign


# --------------------------------------------------------------------------- #
# Saisie des couleurs
# --------------------------------------------------------------------------- #


def _editable(submission: Submission) -> None:
    if submission.state is SubmissionState.VERROUILLEE:
        raise CampaignError(
            "Réponse verrouillée. Un administrateur doit la rouvrir, et la réouverture est tracée."
        )


def set_availability(
    session: Session,
    submission: Submission,
    occurrence: GardeOccurrence,
    color: Color,
    line: Line | None = None,
    comment: str | None = None,
) -> Availability:
    """Enregistre une couleur déclarée par la personne elle-même."""
    _editable(submission)
    if color is Color.DISPO_DEFAUT:
        raise CampaignError(
            "La disponibilité par défaut ne peut pas être saisie : elle résulte "
            "uniquement du mécanisme de non-réponse."
        )
    # P1.4 : un assistant ne saisit que vert ou rouge, jamais orange.
    if color is Color.ORANGE:
        profile = session.get(ProfessionalProfile, submission.profile_id)
        if profile is not None and profile.status is Status.ASSISTANT:
            raise CampaignError(
                "Un assistant déclare uniquement vert ou rouge, jamais orange."
            )
    entry = session.execute(
        select(Availability).where(
            Availability.submission_id == submission.id,
            Availability.occurrence_id == occurrence.id,
            Availability.line.is_(None) if line is None else Availability.line == line,
        )
    ).scalar_one_or_none()
    if entry is None:
        entry = Availability(
            submission_id=submission.id, occurrence_id=occurrence.id, line=line
        )
        session.add(entry)
    entry.color = color
    entry.is_declared = True
    entry.source = AvailabilitySource.UTILISATEUR
    entry.comment = comment
    if submission.state is SubmissionState.NON_COMMENCEE:
        submission.state = SubmissionState.BROUILLON
    session.flush()
    return entry


def set_availability_range(
    session: Session,
    submission: Submission,
    occurrences,
    color: Color,
    line: Line | None = None,
) -> int:
    count = 0
    for occurrence in occurrences:
        set_availability(session, submission, occurrence, color, line)
        count += 1
    return count


# --------------------------------------------------------------------------- #
# Paires de jours fériés
# --------------------------------------------------------------------------- #


def _sufficient(color: Color, requirement: HolidayRequirement) -> bool:
    if requirement is HolidayRequirement.VERT:
        return color in (Color.VERT, Color.DISPO_DEFAUT)
    return color in (Color.VERT, Color.ORANGE, Color.DISPO_DEFAUT)


def applicable_pairs(session: Session, campaign: Campaign) -> list[HolidayPair]:
    quarter = campaign.quarter
    out = []
    for pair in session.execute(select(HolidayPair).where(HolidayPair.active)).scalars():
        for member in pair.members:
            if member.date_start <= quarter.end_date and member.date_end >= quarter.start_date:
                out.append(pair)
                break
    return out


def missing_holiday_pairs(
    session: Session, submission: Submission, include_default: bool = False
) -> list[str]:
    """Paires pour lesquelles aucune disponibilité suffisante n'est déclarée.

    Pendant la saisie volontaire, seules les couleurs **déclarées** comptent.
    Après l'échéance et l'application régulière du mécanisme de non-réponse,
    ``DISPO_DEFAUT`` compte également (``include_default=True``), tout en restant
    distinct d'un vert déclaré dans les écrans, exports et journaux.
    """
    campaign = submission.campaign
    requirement = campaign.holiday_pair_requirement
    quarter = campaign.quarter
    entries = {
        (a.occurrence_id): a
        for a in session.execute(
            select(Availability).where(Availability.submission_id == submission.id)
        ).scalars()
    }
    missing: list[str] = []

    for pair in applicable_pairs(session, campaign):
        satisfied = False
        relevant_members = 0
        for member in pair.members:
            if member.date_end < quarter.start_date or member.date_start > quarter.end_date:
                continue
            relevant_members += 1
            occurrences = session.execute(
                select(GardeOccurrence).where(
                    GardeOccurrence.local_date >= member.date_start,
                    GardeOccurrence.local_date <= member.date_end,
                )
            ).scalars()
            for occurrence in occurrences:
                entry = entries.get(occurrence.id)
                if entry is None:
                    continue
                if entry.color is Color.DISPO_DEFAUT and not include_default:
                    continue
                if _sufficient(entry.color, requirement):
                    satisfied = True
                    break
            if satisfied:
                break
        if relevant_members and not satisfied:
            labels = " / ".join(m.label for m in pair.members)
            missing.append(
                f"Paire « {pair.label} » ({labels}) : aucune disponibilité "
                f"{'verte' if requirement is HolidayRequirement.VERT else 'verte ou orange'} "
                "déclarée sur un des deux membres."
            )
    return missing


# --------------------------------------------------------------------------- #
# Validation, réouverture
# --------------------------------------------------------------------------- #


def validate_submission(session: Session, submission: Submission) -> Submission:
    _editable(submission)
    missing = missing_holiday_pairs(session, submission)
    if missing:
        raise CampaignError(
            "Validation impossible tant qu'une paire de jours fériés n'est pas couverte :\n- "
            + "\n- ".join(missing)
        )
    submission.state = SubmissionState.VALIDEE
    submission.validated_at = Clock.now()
    session.flush()
    notification_service.enqueue(
        session,
        "CAMPAGNE_VALIDATION",
        f"campagne:{submission.campaign_id}:validation:{submission.profile_id}:"
        f"{submission.reopened_count}",
        submission.profile,
        {
            "quarter": submission.campaign.quarter.label,
            "at": submission.validated_at.strftime("%d/%m/%Y %H:%M"),
        },
    )
    audit_service.record(
        session, "REPONSE_VALIDEE", "submission", submission.id,
        {"profile": submission.profile.code}, actor=submission.profile.user,
    )
    return submission


def reopen_submission(
    session: Session, submission: Submission, admin: User, reason: str
) -> Submission:
    """Réouverture tracée. C'est le **seul** chemin permettant à la personne de
    modifier ensuite un rouge : personne d'autre ne peut le faire à sa place."""
    submission.state = SubmissionState.BROUILLON
    submission.validated_at = None
    submission.locked_at = None
    submission.reopened_count += 1
    session.flush()
    audit_service.record(
        session, "REPONSE_ROUVERTE", "submission", submission.id,
        {"profile": submission.profile.code, "motif": reason,
         "occurrence": submission.reopened_count}, actor=admin,
    )
    return submission


# --------------------------------------------------------------------------- #
# Rappels
# --------------------------------------------------------------------------- #


def due_reminders(campaign: Campaign, now: datetime) -> list[tuple[int, int]]:
    """Retourne les rappels échus sous forme (index, jours avant échéance)."""
    out = []
    for index, offset in enumerate(campaign.reminder_offsets):
        due_at = campaign.deadline_at - timedelta(days=offset)
        if now >= due_at:
            out.append((index, offset))
    return out


def send_due_reminders(session: Session, campaign: Campaign) -> int:
    """Envoie les rappels échus **uniquement aux non-finalisés**.

    Idempotent : la clé métier contient la campagne, la personne, l'index de rappel
    et le compteur de réouvertures.
    """
    if campaign.state not in (CampaignState.OUVERTE, CampaignState.CLOTUREE,
                              CampaignState.RESOLUTION_NON_REPONDANTS):
        return 0
    now = Clock.now()
    sent = 0
    for index, offset in due_reminders(campaign, now):
        if index == 0:
            continue  # J-30 = message d'ouverture, déjà envoyé
        for submission in campaign.submissions:
            if submission.is_finalised:
                continue  # les rappels cessent après validation
            if submission.last_reminder_index >= index:
                continue
            created = notification_service.enqueue(
                session,
                "CAMPAGNE_RAPPEL",
                f"campagne:{campaign.id}:rappel:{index}:{submission.profile_id}:"
                f"{submission.reopened_count}",
                submission.profile,
                {
                    "quarter": campaign.quarter.label,
                    "days": offset,
                    "deadline": campaign.deadline_at.strftime("%d/%m/%Y %H:%M"),
                },
            )
            submission.last_reminder_index = index
            if created is not None:
                sent += 1
    session.flush()
    return sent


def all_reminders_sent(campaign: Campaign) -> bool:
    last_index = len(campaign.reminder_offsets) - 1
    if last_index <= 0:
        return True
    return Clock.now() >= campaign.deadline_at - timedelta(
        days=campaign.reminder_offsets[last_index]
    )


# --------------------------------------------------------------------------- #
# Échéance et non-répondants
# --------------------------------------------------------------------------- #


def pending_submissions(campaign: Campaign) -> list[Submission]:
    return [s for s in campaign.submissions if not s.is_finalised]


def close_campaign(session: Session, campaign: Campaign, admin: User | None = None) -> Campaign:
    """À l'échéance : la génération est **d'abord bloquée** et les administrateurs alertés."""
    if Clock.now() < campaign.deadline_at:
        raise CampaignError("L'échéance n'est pas atteinte.")
    pending = pending_submissions(campaign)
    if pending:
        campaign.state = CampaignState.RESOLUTION_NON_REPONDANTS
        notification_service.enqueue(
            session,
            "ADMIN_NON_REPONDANTS",
            f"campagne:{campaign.id}:alerte_non_repondants",
            None,
            {"quarter": campaign.quarter.label, "count": len(pending)},
            recipient_label="administrateurs",
        )
    else:
        campaign.state = CampaignState.PRETE
        for submission in campaign.submissions:
            submission.state = SubmissionState.VERROUILLEE
            submission.locked_at = Clock.now()
    session.flush()
    audit_service.record(
        session, "CAMPAGNE_CLOTUREE", "campaign", campaign.id,
        {"non_finalises": len(pending), "etat": campaign.state.value}, actor=admin,
    )
    return campaign


def extend_deadline(
    session: Session, campaign: Campaign, new_deadline: datetime, admin: User, reason: str
) -> Campaign:
    """Prolongation : reprogramme la tâche de conversion sans double événement."""
    old = campaign.deadline_at
    campaign.deadline_at = new_deadline
    campaign.default_conversion_done_at = None
    if campaign.state is CampaignState.RESOLUTION_NON_REPONDANTS:
        campaign.state = CampaignState.OUVERTE
    session.flush()
    audit_service.record(
        session, "CAMPAGNE_PROLONGEE", "campaign", campaign.id,
        {"ancienne_echeance": old.isoformat(), "nouvelle_echeance": new_deadline.isoformat(),
         "motif": reason}, actor=admin,
    )
    return campaign


def can_apply_default_availability(campaign: Campaign) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if campaign.default_conversion_done_at is not None:
        reasons.append("La conversion a déjà été appliquée pour cette échéance.")
    if Clock.now() < campaign.deadline_at:
        reasons.append("L'échéance n'est pas atteinte.")
    if not all_reminders_sent(campaign):
        reasons.append("Les relances prévues n'ont pas toutes été envoyées.")
    if Clock.now() < campaign.grace_deadline:
        reasons.append(
            f"Le délai de grâce de {campaign.grace_period_hours} h "
            "(hypothèse de démonstration) n'est pas écoulé."
        )
    return (not reasons), reasons


def apply_default_availability(
    session: Session, campaign: Campaign, admin: User | None = None
) -> dict:
    """Convertit **uniquement les dates réellement non renseignées**.

    Aucun vert, orange ou rouge déjà saisi n'est modifié. Une validation intervenue
    pendant le délai de grâce annule la conversion pour la personne concernée.
    """
    ok, reasons = can_apply_default_availability(campaign)
    if not ok:
        raise CampaignError("Conversion impossible : " + " ".join(reasons))

    quarter = campaign.quarter
    occurrences = list(
        session.execute(
            select(GardeOccurrence).where(GardeOccurrence.quarter_id == quarter.id)
        ).scalars()
    )
    converted: dict[str, int] = {}
    for submission in campaign.submissions:
        if submission.is_finalised:
            continue  # validation tardive pendant le délai de grâce : rien n'est converti
        existing = {
            a.occurrence_id
            for a in session.execute(
                select(Availability).where(Availability.submission_id == submission.id)
            ).scalars()
        }
        count = 0
        for occurrence in occurrences:
            if occurrence.id in existing:
                continue
            session.add(
                Availability(
                    submission_id=submission.id,
                    occurrence_id=occurrence.id,
                    line=None,
                    color=Color.DISPO_DEFAUT,
                    is_declared=False,
                    source=AvailabilitySource.CONVERSION_NON_REPONSE,
                )
            )
            count += 1
        submission.state = SubmissionState.VERROUILLEE
        submission.locked_at = Clock.now()
        converted[submission.profile.code] = count
        if count:
            notification_service.enqueue(
                session,
                "DISPO_PAR_DEFAUT",
                f"campagne:{campaign.id}:dispo_defaut:{submission.profile_id}:"
                f"{submission.reopened_count}",
                submission.profile,
                {"quarter": quarter.label, "count": count},
            )

    for submission in campaign.submissions:
        if submission.state is not SubmissionState.VERROUILLEE:
            submission.state = SubmissionState.VERROUILLEE
            submission.locked_at = Clock.now()

    campaign.default_conversion_done_at = Clock.now()
    campaign.state = CampaignState.PRETE
    session.flush()

    notification_service.enqueue(
        session,
        "ADMIN_NON_REPONDANTS",
        f"campagne:{campaign.id}:dispo_defaut_admin",
        None,
        {"quarter": quarter.label, "count": len(converted)},
        recipient_label="administrateurs",
    )
    audit_service.record(
        session, "DISPO_PAR_DEFAUT_APPLIQUEE", "campaign", campaign.id,
        {"detail": converted,
         "note": "statut distinct d'un vert déclaré, jamais présenté comme volontaire"},
        actor=admin,
    )
    return converted


def can_generate(session: Session, campaign: Campaign) -> tuple[bool, list[str]]:
    """La génération est bloquée tant que les non-réponses ne sont pas résolues."""
    reasons: list[str] = []
    if campaign.state in (CampaignState.PREPARATION, CampaignState.OUVERTE):
        reasons.append("La campagne n'est pas clôturée.")
    pending = pending_submissions(campaign)
    if pending:
        codes = ", ".join(sorted(s.profile.code for s in pending))
        reasons.append(
            f"{len(pending)} réponse(s) non finalisée(s) : {codes}. "
            "Prolongez, contactez les personnes, ou appliquez la disponibilité par défaut "
            "après le délai de grâce."
        )
    return (not reasons), reasons
