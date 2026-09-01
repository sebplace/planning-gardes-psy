"""Génération, révision, validation et publication du planning.

Le logiciel ne publie **jamais** seul le planning initial : un administrateur
contrôle, corrige éventuellement, valide puis publie. Toute correction manuelle est
journalisée avec son auteur, sa date et un motif bref.

Aucune interface, aucun paramètre et aucun point d'entrée d'API ne permet de déroger
à un rouge, à une inéligibilité, à une incompatibilité ou à une règle ferme de repos.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engine import ENGINE_VERSION, Solution, diversity_distance, solve
from ..engine.solver import impossibility_report
from ..models import (
    Assignment,
    AssignmentOrigin,
    Campaign,
    CoveragePost,
    EngineRun,
    EngineRunStatus,
    GardeOccurrence,
    ManualCorrection,
    ProfessionalProfile,
    Proposal,
    ProposalAssignment,
    Quarter,
    ScheduleState,
    ScheduleVersion,
    User,
)
from . import audit_service, campaign_service, engine_bridge, notification_service
from .clock import Clock


class PlanningError(Exception):
    pass


class HardConstraintError(PlanningError):
    """Tentative de violer une contrainte ferme. Jamais contournable."""


# --------------------------------------------------------------------------- #
# Génération
# --------------------------------------------------------------------------- #


def generation_blockers(session: Session, quarter: Quarter) -> list[str]:
    campaign = session.execute(
        select(Campaign).where(Campaign.quarter_id == quarter.id)
    ).scalar_one_or_none()
    if campaign is None:
        return ["Aucune campagne de désidératas n'existe pour ce trimestre."]
    ok, reasons = campaign_service.can_generate(session, campaign)
    return [] if ok else reasons


def run_engine(
    session: Session,
    quarter: Quarter,
    admin: User | None = None,
    seed: int = 20260901,
    variants: int = 3,
    min_diversity: float = 0.12,
    locked: dict[int, int] | None = None,
    force: bool = False,
) -> EngineRun:
    """Exécute le moteur et persiste un instantané reproductible."""
    blockers = generation_blockers(session, quarter)
    run = EngineRun(
        quarter_id=quarter.id,
        seed=seed,
        ruleset_version=engine_bridge.RULESET_VERSION,
        engine_version=ENGINE_VERSION,
        rule_profile_label="",
        started_at=Clock.now(),
        input_snapshot_hash="",
        status=EngineRunStatus.EN_COURS,
        created_by_id=admin.id if admin else None,
    )
    session.add(run)
    session.flush()

    if blockers and not force:
        run.status = EngineRunStatus.ECHEC
        run.finished_at = Clock.now()
        run.blocked_reason = " | ".join(blockers)
        session.flush()
        audit_service.record(
            session, "GENERATION_BLOQUEE", "engine_run", run.id,
            {"raisons": blockers}, actor=admin,
        )
        return run

    inp = engine_bridge.build_input(session, quarter, seed=seed, locked=locked)
    run.rule_profile_label = f"{inp.profile.name}@{inp.profile.version}"
    run.input_snapshot_hash = inp.snapshot_hash()
    run.params_json = json.dumps(
        {
            "variants": variants,
            "min_diversity": min_diversity,
            "profil": run.rule_profile_label,
            "postes": len(inp.posts),
            "personnes": len(inp.people),
            "verrouillees": len(inp.locked),
            "fraction_annee": inp.year_fraction_elapsed,
        },
        ensure_ascii=False,
    )
    run.input_snapshot_json = json.dumps(
        {
            "posts": [
                {"post_id": p.post_id, "occurrence_id": p.occurrence_id,
                 "type": p.type_code, "categorie": p.category_code, "ligne": p.line.value,
                 "mode": p.coverage_mode.value, "debut": p.start_at.isoformat()}
                for p in inp.posts
            ],
            "personnes": [
                {"profile_id": q.profile_id, "code": q.code, "statut": q.status.value}
                for q in inp.people
            ],
        },
        ensure_ascii=False,
    )

    solutions = solve(inp, variants=variants, min_diversity=min_diversity)
    ordered = [p.post_id for p in sorted(inp.posts, key=lambda x: x.key)]

    for index, solution in enumerate(solutions):
        distances = [
            diversity_distance(solution, other, ordered)
            for j, other in enumerate(solutions)
            if j != index
        ]
        proposal = Proposal(
            engine_run_id=run.id,
            variant_index=solution.variant_index,
            seed=solution.seed,
            score_total=solution.score_total,
            score_breakdown_json=json.dumps(solution.score_breakdown, ensure_ascii=False),
            feasible=solution.feasible,
            unfilled_json=json.dumps(
                [
                    {
                        "post_id": u.post_id, "ligne": u.line,
                        "date": u.local_date.isoformat(), "type": u.type_code,
                        "exclusions": [
                            {"profil": r.profile_code, "code": r.constraint_code,
                             "libelle": r.label, "detail": r.detail}
                            for r in u.rejections
                        ],
                    }
                    for u in solution.unfilled
                ],
                ensure_ascii=False,
            ),
            tensions_json=json.dumps(solution.tensions, ensure_ascii=False),
            quota_gaps_json=json.dumps(solution.quota_gaps, ensure_ascii=False),
            orange_count=len(solution.orange_used),
            default_availability_count=len(solution.default_availability_used),
            diversity_min=round(min(distances), 4) if distances else 0.0,
        )
        session.add(proposal)
        session.flush()
        for post_id, profile_id in sorted(solution.assignments.items()):
            explanation = solution.explanations.get(post_id)
            session.add(
                ProposalAssignment(
                    proposal_id=proposal.id,
                    post_id=post_id,
                    profile_id=profile_id,
                    explanation_json=json.dumps(
                        _explanation_payload(explanation), ensure_ascii=False
                    ),
                )
            )

    run.status = EngineRunStatus.TERMINEE
    run.finished_at = Clock.now()
    session.flush()
    audit_service.record(
        session, "GENERATION_EXECUTEE", "engine_run", run.id,
        {"graine": seed, "variantes": len(solutions),
         "empreinte_entrees": run.input_snapshot_hash,
         "version_regles": run.ruleset_version, "version_moteur": ENGINE_VERSION},
        actor=admin,
    )
    return run


def _explanation_payload(explanation) -> dict:
    if explanation is None:
        return {}
    payload = asdict(explanation)
    payload["rejected_candidates"] = [
        {"profil": r.profile_code, "code": r.constraint_code,
         "libelle": r.label, "detail": r.detail}
        for r in explanation.rejected_candidates
    ]
    payload["texte"] = explanation.to_text()
    payload["couleur_libelle"] = explanation.color_label
    return payload


def impossibility(session: Session, proposal: Proposal) -> dict:
    return {
        "postes_non_pourvus": json.loads(proposal.unfilled_json),
        "tensions": json.loads(proposal.tensions_json),
        "note": (
            "Aucune contrainte ferme n'a été relâchée. Le moteur rapporte une "
            "impossibilité constatée poste par poste, pas une preuve d'infaisabilité globale."
        ),
    }


# --------------------------------------------------------------------------- #
# Versions de planning
# --------------------------------------------------------------------------- #


def create_version_from_proposal(
    session: Session, proposal: Proposal, admin: User, note: str | None = None
) -> ScheduleVersion:
    quarter_id = proposal.run.quarter_id
    next_no = (
        session.execute(
            select(func.coalesce(func.max(ScheduleVersion.version_no), 0)).where(
                ScheduleVersion.quarter_id == quarter_id
            )
        ).scalar_one()
        + 1
    )
    version = ScheduleVersion(
        quarter_id=quarter_id,
        version_no=next_no,
        state=ScheduleState.EN_REVISION,
        source_proposal_id=proposal.id,
        note=note,
    )
    session.add(version)
    session.flush()
    for item in proposal.items:
        session.add(
            Assignment(
                schedule_version_id=version.id,
                post_id=item.post_id,
                profile_id=item.profile_id,
                origin=AssignmentOrigin.MOTEUR,
                explanation_json=item.explanation_json,
            )
        )
    session.flush()
    audit_service.record(
        session, "VERSION_CREEE", "schedule_version", version.id,
        {"proposition": proposal.id, "version": next_no}, actor=admin,
    )
    return version


def manual_correction(
    session: Session,
    version: ScheduleVersion,
    post: CoveragePost,
    new_profile: ProfessionalProfile | None,
    admin: User,
    reason: str,
) -> Assignment | None:
    """Déplacement manuel. **Les contraintes fermes s'appliquent intégralement.**

    Il n'existe aucun paramètre permettant de forcer un rouge, une inéligibilité,
    une incompatibilité ou une règle ferme de repos.
    """
    if version.state in (ScheduleState.PUBLIE, ScheduleState.REMPLACE):
        raise PlanningError(
            "Un planning publié n'est jamais réécrit : créez une nouvelle version."
        )
    if not reason or not reason.strip():
        raise PlanningError("Un motif bref est obligatoire pour toute correction manuelle.")

    assignment = session.execute(
        select(Assignment).where(
            Assignment.schedule_version_id == version.id, Assignment.post_id == post.id
        )
    ).scalar_one_or_none()
    previous_profile_id = assignment.profile_id if assignment else None

    if new_profile is not None:
        rejection = engine_bridge.check_assignment(
            session,
            post,
            new_profile,
            ignore_assignment_ids={assignment.id} if assignment else set(),
            schedule_version_id=version.id,
        )
        if rejection is not None:
            raise HardConstraintError(
                f"Correction refusée — contrainte ferme « {rejection.label} » : {rejection.detail}"
            )

    if new_profile is None:
        if assignment is not None:
            session.delete(assignment)
            assignment = None
    elif assignment is None:
        assignment = Assignment(
            schedule_version_id=version.id, post_id=post.id,
            profile_id=new_profile.id, origin=AssignmentOrigin.MANUEL,
        )
        session.add(assignment)
    else:
        assignment.profile_id = new_profile.id
        assignment.origin = AssignmentOrigin.MANUEL
        assignment.row_version += 1
    session.flush()

    session.add(
        ManualCorrection(
            schedule_version_id=version.id,
            post_id=post.id,
            from_profile_id=previous_profile_id,
            to_profile_id=new_profile.id if new_profile else None,
            author_id=admin.id,
            reason=reason.strip()[:300],
        )
    )
    audit_service.record(
        session, "CORRECTION_MANUELLE", "assignment", assignment.id if assignment else post.id,
        {"version": version.id, "poste": post.id, "avant": previous_profile_id,
         "apres": new_profile.id if new_profile else None, "motif": reason.strip()[:300]},
        actor=admin,
    )
    return assignment


def set_lock(
    session: Session, version: ScheduleVersion, post_id: int, locked: bool, admin: User
) -> Assignment:
    assignment = session.execute(
        select(Assignment).where(
            Assignment.schedule_version_id == version.id, Assignment.post_id == post_id
        )
    ).scalar_one()
    assignment.is_locked = locked
    session.flush()
    audit_service.record(
        session, "VERROUILLAGE" if locked else "DEVERROUILLAGE", "assignment",
        assignment.id, {"poste": post_id}, actor=admin,
    )
    return assignment


def regenerate_keeping_locks(
    session: Session, version: ScheduleVersion, admin: User, seed: int, variants: int = 1
) -> EngineRun:
    """Nouvelle génération conservant les affectations verrouillées."""
    locked = {
        a.post_id: a.profile_id
        for a in version.assignments
        if a.is_locked
    }
    quarter = session.get(Quarter, version.quarter_id)
    return run_engine(
        session, quarter, admin=admin, seed=seed, variants=variants, locked=locked
    )


def validate_version(session: Session, version: ScheduleVersion, admin: User) -> ScheduleVersion:
    missing = _missing_posts(session, version)
    if missing:
        raise PlanningError(
            f"{len(missing)} poste(s) requis ne sont pas pourvus. "
            "La validation d'un planning incomplet est refusée."
        )
    version.state = ScheduleState.VALIDE
    version.validated_by_id = admin.id
    session.flush()
    audit_service.record(
        session, "PLANNING_VALIDE", "schedule_version", version.id, {}, actor=admin
    )
    return version


def _missing_posts(session: Session, version: ScheduleVersion) -> list[int]:
    posts = session.execute(
        select(CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .where(GardeOccurrence.quarter_id == version.quarter_id, CoveragePost.required)
    ).scalars()
    covered = {a.post_id for a in version.assignments}
    return [pid for pid in posts if pid not in covered]


def publish_version(
    session: Session, version: ScheduleVersion, admin: User
) -> ScheduleVersion:
    """Publication : la version devient la référence opérationnelle.

    Les versions précédentes passent en REMPLACE ; aucune n'est réécrite.
    """
    if version.state is not ScheduleState.VALIDE:
        raise PlanningError("Seule une version validée peut être publiée.")

    previous = list(
        session.execute(
            select(ScheduleVersion).where(
                ScheduleVersion.quarter_id == version.quarter_id,
                ScheduleVersion.state == ScheduleState.PUBLIE,
            )
        ).scalars()
    )
    for old in previous:
        old.state = ScheduleState.REMPLACE

    version.state = ScheduleState.PUBLIE
    version.published_at = Clock.now()
    version.published_by_id = admin.id
    session.flush()

    quarter = session.get(Quarter, version.quarter_id)
    kind = "PLANNING_MODIFIE" if previous else "PLANNING_PUBLIE"
    recipients = {a.profile_id for a in version.assignments}
    for profile_id in sorted(recipients):
        profile = session.get(ProfessionalProfile, profile_id)
        notification_service.enqueue(
            session,
            kind,
            f"planning:{version.id}:{kind}:{profile_id}",
            profile,
            {"quarter": quarter.label, "version": version.version_no},
        )
    audit_service.record(
        session, "PLANNING_PUBLIE", "schedule_version", version.id,
        {"version": version.version_no, "remplace": [v.id for v in previous],
         "affectations": len(version.assignments)}, actor=admin,
    )
    return version


def published_version(session: Session, quarter_id: int) -> ScheduleVersion | None:
    return session.execute(
        select(ScheduleVersion).where(
            ScheduleVersion.quarter_id == quarter_id,
            ScheduleVersion.state == ScheduleState.PUBLIE,
        )
    ).scalar_one_or_none()


def clone_version_for_edit(
    session: Session, version: ScheduleVersion, admin: User, note: str
) -> ScheduleVersion:
    """Crée une nouvelle version modifiable à partir d'une version publiée."""
    next_no = (
        session.execute(
            select(func.coalesce(func.max(ScheduleVersion.version_no), 0)).where(
                ScheduleVersion.quarter_id == version.quarter_id
            )
        ).scalar_one()
        + 1
    )
    clone = ScheduleVersion(
        quarter_id=version.quarter_id,
        version_no=next_no,
        state=ScheduleState.EN_REVISION,
        source_proposal_id=version.source_proposal_id,
        note=note,
    )
    session.add(clone)
    session.flush()
    for assignment in version.assignments:
        session.add(
            Assignment(
                schedule_version_id=clone.id,
                post_id=assignment.post_id,
                profile_id=assignment.profile_id,
                is_locked=assignment.is_locked,
                origin=assignment.origin,
                explanation_json=assignment.explanation_json,
            )
        )
    session.flush()
    audit_service.record(
        session, "VERSION_CLONEE", "schedule_version", clone.id,
        {"source": version.id, "motif": note}, actor=admin,
    )
    return clone
