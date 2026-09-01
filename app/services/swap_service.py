"""Échange bilatéral de deux gardes publiées.

Autorisé **uniquement** entre deux occurrences structurellement équivalentes :
même ligne, même catégorie comptable, même poids de décompte, même classe d'échange
validée, même classe de durée et mêmes exigences de couverture.

La classe d'échange matérialise l'équivalence mais ne suffit jamais à la déclarer :
elle ne peut pas servir à rendre artificiellement équivalentes deux gardes de nature
différente (OPEN_QUESTIONS.md Q-12).

Si les gardes ne sont pas de même nature, l'échange est refusé et chaque médecin se
voit proposer d'ouvrir **volontairement** une demande de reprise. Le système ne
déclenche jamais automatiquement deux reprises.
"""

from __future__ import annotations

import json

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..models import (
    Assignment,
    AssignmentOrigin,
    ProfessionalProfile,
    ScheduleState,
    ScheduleVersion,
    SwapProposal,
    SwapState,
    User,
)
from . import audit_service, engine_bridge, notification_service
from .clock import Clock, format_date_fr


class SwapError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Équivalence structurelle
# --------------------------------------------------------------------------- #


def _descriptor(assignment: Assignment) -> dict:
    post = assignment.post
    occurrence = post.occurrence
    garde_type = occurrence.garde_type
    coverage = sorted(
        (p.line.value, p.required_status.value) for p in occurrence.posts if p.required
    )
    return {
        "ligne": post.line.value,
        "statut_exige": post.required_status.value,
        "categorie_comptable": garde_type.category.code,
        "poids_de_decompte": garde_type.count_weight,
        "classe_echange": garde_type.exchange_class.code if garde_type.exchange_class else None,
        "classe_duree": garde_type.duration_class,
        "exigences_couverture": coverage,
        "mode_couverture": occurrence.effective_mode.value,
    }


CRITERIA_LABELS = {
    "ligne": "la ligne",
    "statut_exige": "le statut exigé par le poste",
    "categorie_comptable": "la catégorie comptable",
    "poids_de_decompte": "le poids de décompte",
    "classe_echange": "la classe d'échange validée",
    "classe_duree": "la classe de durée",
    "exigences_couverture": "les exigences de couverture",
    "mode_couverture": "le mode de couverture",
}


def check_equivalence(a: Assignment, b: Assignment) -> tuple[bool, list[str], dict]:
    da, db = _descriptor(a), _descriptor(b)
    differences = []
    for key, label in CRITERIA_LABELS.items():
        if da[key] != db[key]:
            differences.append(f"{label} diffère ({da[key]!r} vs {db[key]!r})")
    if da["classe_echange"] is None or db["classe_echange"] is None:
        differences.append("au moins une garde n'a pas de classe d'échange validée")
    payload = {"garde_a": da, "garde_b": db, "differences": differences}
    return (not differences), differences, payload


# --------------------------------------------------------------------------- #
# Contrôles d'état
# --------------------------------------------------------------------------- #


def _assert_swappable(session: Session, assignment: Assignment, label: str) -> None:
    version = session.get(ScheduleVersion, assignment.schedule_version_id)
    if version.state is not ScheduleState.PUBLIE:
        raise SwapError(f"{label} : la garde n'appartient pas à un planning publié.")
    if assignment.post.occurrence.start_at <= Clock.now():
        raise SwapError(f"{label} : la garde n'est plus future.")


# --------------------------------------------------------------------------- #
# Proposition
# --------------------------------------------------------------------------- #


def propose_swap(
    session: Session,
    assignment_a: Assignment,
    assignment_b: Assignment,
    proposer: ProfessionalProfile,
) -> SwapProposal:
    if assignment_a.id == assignment_b.id:
        raise SwapError("Les deux gardes doivent être distinctes.")
    if assignment_a.profile_id == assignment_b.profile_id:
        raise SwapError("Un échange suppose deux médecins différents.")
    if proposer.id not in (assignment_a.profile_id, assignment_b.profile_id):
        raise SwapError("Seul un des deux titulaires peut proposer l'échange.")

    _assert_swappable(session, assignment_a, "Garde A")
    _assert_swappable(session, assignment_b, "Garde B")

    equivalent, differences, payload = check_equivalence(assignment_a, assignment_b)
    if not equivalent:
        proposal = SwapProposal(
            assignment_a_id=assignment_a.id,
            assignment_b_id=assignment_b.id,
            proposer_profile_id=proposer.id,
            announced_profile_a_id=assignment_a.profile_id,
            announced_profile_b_id=assignment_b.profile_id,
            state=SwapState.REFUSE,
            refusal_reason=(
                "Gardes de nature différente : " + " ; ".join(differences) + ". "
                "Chaque médecin peut, s'il le souhaite, ouvrir volontairement une demande "
                "de reprise. Aucune reprise n'est déclenchée automatiquement."
            ),
            equivalence_json=json.dumps(payload, ensure_ascii=False),
        )
        session.add(proposal)
        session.flush()
        audit_service.record(
            session, "ECHANGE_REFUSE_NATURE", "swap_proposal", proposal.id,
            payload, actor=proposer.user,
        )
        _notify_refusal(session, proposal)
        return proposal

    for assignment, label in ((assignment_a, "Garde A"), (assignment_b, "Garde B")):
        locked = session.execute(
            update(Assignment)
            .where(Assignment.id == assignment.id, Assignment.busy_operation.is_(None))
            .values(busy_operation="ECHANGE")
        )
        if locked.rowcount != 1:
            session.execute(
                update(Assignment)
                .where(
                    Assignment.id.in_([assignment_a.id, assignment_b.id]),
                    Assignment.busy_operation == "ECHANGE",
                )
                .values(busy_operation=None)
            )
            raise SwapError(
                f"{label} participe déjà à une autre opération (reprise ou échange en cours)."
            )
        session.expire(assignment)

    proposal = SwapProposal(
        assignment_a_id=assignment_a.id,
        assignment_b_id=assignment_b.id,
        proposer_profile_id=proposer.id,
        announced_profile_a_id=assignment_a.profile_id,
        announced_profile_b_id=assignment_b.profile_id,
        state=SwapState.PROPOSE,
        equivalence_json=json.dumps(payload, ensure_ascii=False),
    )
    if proposer.id == assignment_a.profile_id:
        proposal.accepted_a_at = Clock.now()
    else:
        proposal.accepted_b_at = Clock.now()
    session.add(proposal)
    session.flush()

    other_id = (
        assignment_b.profile_id if proposer.id == assignment_a.profile_id
        else assignment_a.profile_id
    )
    notification_service.enqueue(
        session, "ECHANGE_PROPOSE", f"echange:{proposal.id}:proposition:{other_id}",
        session.get(ProfessionalProfile, other_id), _context(assignment_a, assignment_b),
    )
    audit_service.record(
        session, "ECHANGE_PROPOSE", "swap_proposal", proposal.id, payload, actor=proposer.user
    )
    return proposal


def _context(a: Assignment, b: Assignment) -> dict:
    return {
        "date_a": format_date_fr(a.post.occurrence.local_date),
        "date_b": format_date_fr(b.post.occurrence.local_date),
    }


def _notify_refusal(session: Session, proposal: SwapProposal) -> None:
    a = proposal.assignment_a
    b = proposal.assignment_b
    context = _context(a, b) | {"reason": proposal.refusal_reason}
    for profile_id in {a.profile_id, b.profile_id}:
        notification_service.enqueue(
            session, "ECHANGE_REFUSE", f"echange:{proposal.id}:refus:{profile_id}",
            session.get(ProfessionalProfile, profile_id), context,
        )


def _release(session: Session, proposal: SwapProposal) -> None:
    session.execute(
        update(Assignment)
        .where(Assignment.id.in_([proposal.assignment_a_id, proposal.assignment_b_id]))
        .values(busy_operation=None)
    )


# --------------------------------------------------------------------------- #
# Acceptation et exécution
# --------------------------------------------------------------------------- #


def accept_swap(
    session: Session, proposal: SwapProposal, profile: ProfessionalProfile
) -> SwapProposal:
    """Au **second** accord, l'échange est revérifié atomiquement puis officialisé."""
    session.refresh(proposal)
    if proposal.state is not SwapState.PROPOSE:
        raise SwapError("Cette proposition n'est plus en attente d'accord.")

    a, b = proposal.assignment_a, proposal.assignment_b
    if profile.id == a.profile_id:
        proposal.accepted_a_at = Clock.now()
    elif profile.id == b.profile_id:
        proposal.accepted_b_at = Clock.now()
    else:
        raise SwapError("Seuls les deux titulaires peuvent accepter l'échange.")
    session.flush()

    if proposal.accepted_a_at is None or proposal.accepted_b_at is None:
        return proposal

    # Transition gardée : une seule officialisation possible.
    updated = session.execute(
        update(SwapProposal)
        .where(SwapProposal.id == proposal.id, SwapProposal.state == SwapState.PROPOSE)
        .values(state=SwapState.ACCEPTE_PAR_LES_DEUX)
    )
    if updated.rowcount != 1:
        raise SwapError("Une opération concurrente a déjà traité cette proposition.")
    session.expire(proposal)
    session.refresh(proposal)
    return execute_swap(session, proposal)


def execute_swap(session: Session, proposal: SwapProposal) -> SwapProposal:
    """Revérification atomique des deux côtés, puis permutation des titulaires.

    Les compteurs restent inchangés puisque les deux gardes sont équivalentes.
    """
    a, b = proposal.assignment_a, proposal.assignment_b
    profile_a = session.get(ProfessionalProfile, a.profile_id)
    profile_b = session.get(ProfessionalProfile, b.profile_id)
    problems: list[str] = []

    for assignment, label in ((a, "Garde A"), (b, "Garde B")):
        version = session.get(ScheduleVersion, assignment.schedule_version_id)
        if version.state is not ScheduleState.PUBLIE:
            problems.append(f"{label} n'appartient plus à un planning publié.")
        if assignment.post.occurrence.start_at <= Clock.now():
            problems.append(f"{label} n'est plus future.")
        if assignment.busy_operation not in (None, "ECHANGE"):
            problems.append(f"{label} participe à une autre opération.")

    # La garde doit toujours être détenue par la personne annoncée à la proposition.
    if a.profile_id != proposal.announced_profile_a_id:
        problems.append(
            "Garde A n'est plus détenue par la personne annoncée lors de la proposition."
        )
    if b.profile_id != proposal.announced_profile_b_id:
        problems.append(
            "Garde B n'est plus détenue par la personne annoncée lors de la proposition."
        )

    equivalent, differences, payload = check_equivalence(a, b)
    if not equivalent:
        problems.extend(differences)

    # Revérification **séparée** pour chacun des deux médecins.
    ignore = {a.id, b.id}
    rejection_a = engine_bridge.check_assignment(
        session, b.post, profile_a, ignore_assignment_ids=ignore
    )
    if rejection_a is not None:
        problems.append(
            f"{profile_a.code} ne peut pas prendre la garde B — "
            f"{rejection_a.label} : {rejection_a.detail}"
        )
    rejection_b = engine_bridge.check_assignment(
        session, a.post, profile_b, ignore_assignment_ids=ignore
    )
    if rejection_b is not None:
        problems.append(
            f"{profile_b.code} ne peut pas prendre la garde A — "
            f"{rejection_b.label} : {rejection_b.detail}"
        )

    if problems:
        proposal.state = SwapState.REFUSE
        proposal.refusal_reason = " ; ".join(problems)[:2000]
        _release(session, proposal)
        session.flush()
        audit_service.record(
            session, "ECHANGE_REFUSE", "swap_proposal", proposal.id,
            {"problemes": problems}, actor_label="SYSTEME",
        )
        _notify_refusal(session, proposal)
        return proposal

    a.profile_id = profile_b.id
    b.profile_id = profile_a.id
    a.origin = AssignmentOrigin.ECHANGE
    b.origin = AssignmentOrigin.ECHANGE
    a.row_version += 1
    b.row_version += 1
    proposal.state = SwapState.OFFICIEL
    proposal.executed_at = Clock.now()
    _release(session, proposal)
    session.flush()

    context = _context(a, b)
    for profile in (profile_a, profile_b):
        notification_service.enqueue(
            session, "ECHANGE_OFFICIEL", f"echange:{proposal.id}:officiel:{profile.id}",
            profile, context,
        )
    audit_service.record(
        session, "ECHANGE_OFFICIALISE", "swap_proposal", proposal.id,
        {
            "permutation": {
                "garde_a": {"affectation": a.id, "avant": profile_a.code, "apres": profile_b.code},
                "garde_b": {"affectation": b.id, "avant": profile_b.code, "apres": profile_a.code},
            },
            "equivalence": payload,
            "compteurs": "inchangés (gardes équivalentes)",
        },
        actor_label="SYSTEME",
    )
    return proposal


def cancel_swap(session: Session, proposal: SwapProposal, actor: User | None = None) -> bool:
    updated = session.execute(
        update(SwapProposal)
        .where(SwapProposal.id == proposal.id, SwapProposal.state == SwapState.PROPOSE)
        .values(state=SwapState.ANNULE)
    )
    if updated.rowcount != 1:
        return False
    _release(session, proposal)
    session.expire(proposal)
    session.flush()
    audit_service.record(
        session, "ECHANGE_ANNULE", "swap_proposal", proposal.id, {}, actor=actor
    )
    return True
