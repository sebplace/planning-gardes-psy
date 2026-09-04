"""API JSON.

Toute règle métier passe par la couche services : il n'existe **aucune** règle
accessible par l'interface web et contournable par l'API, ni l'inverse.
En particulier, aucun point d'entrée ne permet de forcer une date rouge.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_session
from ...models import (
    Assignment,
    CoveragePost,
    Draw,
    HandoverRequest,
    HandoverWave,
    ProfessionalProfile,
    Quarter,
    Scenario,
    ScheduleVersion,
    SwapProposal,
    User,
    Year,
)
from ...models import permissions
from ...services import (
    audit_service,
    campaign_service,
    handover_service,
    planning_service,
    projection_service,
    quota_service,
    security,
    swap_service,
)
from ...services.clock import Clock
from ..deps import current_user, require_administrative_access, require_permission

router = APIRouter(prefix="/api/v1", tags=["api"])


# --------------------------------------------------------------------------- #
# Authentification
# --------------------------------------------------------------------------- #


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, session: Session = Depends(get_session)):
    user = security.authenticate(session, payload.email, payload.password)
    if user is None:
        raise HTTPException(401, "Identifiants invalides.")
    request.session["user_id"] = user.id
    return {
        "id": user.id,
        "email": user.email,
        "nom": user.display_name,
        "droits": {"medecin": user.is_medecin, "administrateur": user.is_admin},
    }


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "nom": user.display_name,
        "droits": {"medecin": user.is_medecin, "administrateur": user.is_admin},
    }


# --------------------------------------------------------------------------- #
# Quotas — confidentialité stricte
# --------------------------------------------------------------------------- #


@router.get("/quotas/{profile_id}")
def quotas(
    profile_id: int,
    year_id: int | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    profile = session.get(ProfessionalProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Profil inconnu.")
    try:
        security.assert_can_view_profile_details(user, profile)
    except security.PermissionError_ as exc:
        raise HTTPException(403, str(exc))
    year = (
        session.get(Year, year_id)
        if year_id
        else session.execute(select(Year).order_by(Year.id.desc())).scalars().first()
    )
    summary = quota_service.summary(session, profile, year)
    return {
        "profil": summary.profile_code,
        "annee": summary.year_label,
        "lignes": [
            {
                "categorie": line.category_code,
                "libelle": line.category_label,
                "ligne": line.line,
                "cible": line.target,
                "realise": line.realise,
                "programme": line.programme,
                "ajustements": line.ajustements,
                "total": line.total,
                "restant": line.restant,
                "ecart": line.ecart,
                "source": line.source,
            }
            for line in summary.lines
        ],
        "projection": summary.projection,
        "notes": summary.notes,
    }


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


class ManualAssignmentIn(BaseModel):
    post_id: int
    profile_id: int | None = None
    reason: str = Field(min_length=3, max_length=300)


@router.post("/planning/versions/{version_id}/assignments")
def manual_assignment(
    version_id: int,
    payload: ManualAssignmentIn,
    admin: User = Depends(require_administrative_access),
    session: Session = Depends(get_session),
):
    """Correction manuelle. Les contraintes fermes s'appliquent intégralement :
    il n'existe aucun paramètre de dérogation."""
    version = session.get(ScheduleVersion, version_id)
    post = session.get(CoveragePost, payload.post_id)
    if version is None or post is None:
        raise HTTPException(404, "Version ou poste inconnu.")
    profile = (
        session.get(ProfessionalProfile, payload.profile_id) if payload.profile_id else None
    )
    try:
        assignment = planning_service.manual_correction(
            session, version, post, profile, admin, payload.reason
        )
    except planning_service.HardConstraintError as exc:
        session.rollback()
        raise HTTPException(409, str(exc))
    except planning_service.ImmutableVersionError as exc:
        session.rollback()
        raise HTTPException(409, str(exc))
    except planning_service.PlanningError as exc:
        session.rollback()
        raise HTTPException(400, str(exc))
    session.commit()
    return {"ok": True, "affectation": assignment.id if assignment else None}


class GenerateIn(BaseModel):
    quarter_id: int
    seed: int = 20260901
    variants: int = 3
    min_diversity: float = 0.08


@router.post("/planning/generate")
def generate(
    payload: GenerateIn,
    admin: User = Depends(require_administrative_access),
    session: Session = Depends(get_session),
):
    quarter = session.get(Quarter, payload.quarter_id)
    if quarter is None:
        raise HTTPException(404, "Trimestre inconnu.")
    try:
        run = planning_service.run_engine(
            session, quarter, admin=admin, seed=payload.seed,
            variants=payload.variants, min_diversity=payload.min_diversity,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    session.commit()
    return {
        "execution": run.id,
        "statut": run.status.value,
        "blocage": run.blocked_reason,
        "empreinte_entrees": run.input_snapshot_hash,
        "variantes": [
            {
                "index": p.variant_index, "score": p.score_total, "realisable": p.feasible,
                "oranges": p.orange_count, "diversite_min": p.diversity_min,
            }
            for p in run.proposals
        ],
    }


@router.get("/planning/versions/{version_id}")
def planning_detail(
    version_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    version = session.get(ScheduleVersion, version_id)
    if version is None:
        raise HTTPException(404, "Version inconnue.")
    return {
        "version": version.version_no,
        "etat": version.state.value,
        "publie_le": version.published_at.isoformat() if version.published_at else None,
        "affectations": [
            {
                "poste": a.post_id,
                "occurrence": a.post.occurrence_id,
                "date": a.post.occurrence.local_date.isoformat(),
                "ligne": a.post.line.value,
                "profil": a.profile.code,
                "origine": a.origin.value,
                "verrouillee": a.is_locked,
            }
            for a in sorted(version.assignments, key=lambda x: x.post.occurrence.start_at)
        ],
    }


# --------------------------------------------------------------------------- #
# Reprises
# --------------------------------------------------------------------------- #


class HandoverIn(BaseModel):
    assignment_id: int
    comment: str | None = None


@router.post("/handover/requests")
def create_handover(
    payload: HandoverIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    assignment = session.get(Assignment, payload.assignment_id)
    if assignment is None:
        raise HTTPException(404, "Affectation inconnue.")
    profile = session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(403, "Aucun profil médical associé à ce compte.")
    try:
        request_obj = handover_service.request_handover(
            session, assignment, profile, comment=payload.comment
        )
        handover_service.advance(session, request_obj)
    except handover_service.HandoverError as exc:
        session.rollback()
        raise HTTPException(400, str(exc))
    session.commit()
    return {"demande": request_obj.id, "etat": request_obj.state.value}


@router.post("/handover/waves/{wave_id}/candidacies")
def candidate(
    wave_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    wave = session.get(HandoverWave, wave_id)
    if wave is None:
        raise HTTPException(404, "Vague inconnue.")
    profile = session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(403, "Aucun profil médical associé à ce compte.")
    try:
        candidacy = handover_service.submit_candidacy(session, wave, profile)
    except handover_service.HandoverError as exc:
        session.rollback()
        raise HTTPException(409, str(exc))
    session.commit()
    return {
        "candidature": candidacy.id,
        "etat": candidacy.state.value,
        "note": (
            "Candidature enregistrée. Toutes les réponses favorables sont collectées "
            "puis départagées par tirage au sort : répondre plus vite ne procure aucun avantage."
        ),
    }


@router.post("/handover/requests/{request_id}/advance")
def advance_handover(
    request_id: int,
    user: User = Depends(require_administrative_access),
    session: Session = Depends(get_session),
):
    request_obj = session.get(HandoverRequest, request_id)
    if request_obj is None:
        raise HTTPException(404, "Demande inconnue.")
    # Le périmètre de ligne est vérifié dans le service, donc identiquement en
    # interface et en API : ce chemin ne peut plus contourner le contrôle.
    try:
        handover_service.advance(
            session, request_obj, actor=user, enforce_permissions=True
        )
    except handover_service.HandoverPermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except handover_service.HandoverError as exc:
        raise HTTPException(409, str(exc)) from None
    session.commit()
    session.refresh(request_obj)
    return {"demande": request_obj.id, "etat": request_obj.state.value}


@router.get("/handover/requests/{request_id}")
def handover_detail(
    request_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    request_obj = session.get(HandoverRequest, request_id)
    if request_obj is None:
        raise HTTPException(404, "Demande inconnue.")
    visible = handover_service.requester_visible_to(user, request_obj)
    occurrence = request_obj.assignment.post.occurrence
    return {
        "demande": request_obj.id,
        "etat": request_obj.state.value,
        # L'identité du demandeur reste masquée jusqu'à l'attribution officialisée.
        "demandeur": request_obj.requester.code if visible else "masqué",
        "date": occurrence.local_date.isoformat(),
        "ligne": request_obj.assignment.post.line.value,
        "vagues": [
            {
                "id": w.id, "type": w.kind.value, "etat": w.state.value,
                "ouverture": w.opens_at.isoformat(), "cloture": w.closes_at.isoformat(),
                "palier_urgence": w.urgency_tier, "sollicitees": w.solicited_count,
                "candidatures": len(w.candidacies),
            }
            for w in request_obj.waves
        ],
    }


@router.get("/handover/draws/{draw_id}")
def draw_detail(
    draw_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Preuve vérifiable du tirage : engagement, graine révélée, liste figée, index."""
    import json

    draw = session.get(Draw, draw_id)
    if draw is None:
        raise HTTPException(404, "Tirage inconnu.")
    return {
        "tirage": draw.id,
        "execute_le": draw.executed_at.isoformat(),
        "algorithme": draw.algorithm,
        "preuve": json.loads(draw.proof_json),
        "exclusions": json.loads(draw.excluded_json),
        "candidature_unique": draw.single_candidate,
    }


# --------------------------------------------------------------------------- #
# Échanges
# --------------------------------------------------------------------------- #


class SwapIn(BaseModel):
    assignment_a_id: int
    assignment_b_id: int


@router.post("/swaps")
def propose_swap(
    payload: SwapIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    a = session.get(Assignment, payload.assignment_a_id)
    b = session.get(Assignment, payload.assignment_b_id)
    if a is None or b is None:
        raise HTTPException(404, "Affectation inconnue.")
    profile = session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(403, "Aucun profil médical associé à ce compte.")
    try:
        proposal = swap_service.propose_swap(session, a, b, profile)
    except swap_service.SwapError as exc:
        session.rollback()
        raise HTTPException(400, str(exc))
    session.commit()
    return {
        "echange": proposal.id,
        "etat": proposal.state.value,
        "motif_refus": proposal.refusal_reason,
    }


@router.post("/swaps/{swap_id}/accept")
def accept_swap(
    swap_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    proposal = session.get(SwapProposal, swap_id)
    if proposal is None:
        raise HTTPException(404, "Proposition inconnue.")
    profile = session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()
    try:
        proposal = swap_service.accept_swap(session, proposal, profile)
    except swap_service.SwapError as exc:
        session.rollback()
        raise HTTPException(409, str(exc))
    session.commit()
    return {
        "echange": proposal.id,
        "etat": proposal.state.value,
        "motif_refus": proposal.refusal_reason,
    }


# --------------------------------------------------------------------------- #
# Projections
# --------------------------------------------------------------------------- #


class PromoteIn(BaseModel):
    confirmed: bool = False
    confirmation_text: str = ""


@router.post("/scenarios/{scenario_id}/promote")
def promote(
    scenario_id: int,
    payload: PromoteIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    scenario = session.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(404, "Scénario inconnu.")
    try:
        plan = projection_service.promote_to_configuration(
            session, scenario, user, payload.confirmed, payload.confirmation_text
        )
    except projection_service.ProjectionError as exc:
        session.rollback()
        raise HTTPException(403, str(exc))
    session.commit()
    return plan


@router.get("/audit/verify")
def verify_audit(
    user: User = Depends(require_permission(permissions.CONSULTATION_AUDIT)),
    session: Session = Depends(get_session),
):
    ok, problems = audit_service.verify_chain(session)
    return {"chaine_integre": ok, "anomalies": problems}
