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
    Line,
    ProfessionalProfile,
    Quarter,
    QuotaCategory,
    QuotaTarget,
    Scenario,
    ScheduleVersion,
    SwapProposal,
    SwapSearch,
    User,
    Year,
)
from ...models import permissions
from ...services import (
    audit_service,
    campaign_service,
    handover_service,
    http_security,
    permission_service,
    planning_service,
    projection_service,
    quota_service,
    security,
    swap_flow_service,
    swap_search_service,
    swap_service,
    visibility_service,
)
from ...services.clock import Clock
from ..deps import current_user, profile_medecin, require_action, require_permission

router = APIRouter(prefix="/api/v1", tags=["api"])


# --------------------------------------------------------------------------- #
# Authentification
# --------------------------------------------------------------------------- #


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(payload: LoginIn, request: Request, session: Session = Depends(get_session)):
    adresse = request.client.host if request.client else "?"
    hors_bornes = http_security.identifiants_hors_bornes(
        payload.email, payload.password
    )
    if hors_bornes is not None:
        raise HTTPException(400, "Identifiants invalides.")
    blocage = http_security.limiteur.bloque(payload.email, adresse)
    if blocage is not None:
        audit_service.record(
            session, "AUTHENTIFICATION_LIMITEE", "user", 0,
            {"motif": blocage, "adresse": adresse, "canal": "api"},
            actor_label="ANONYME",
        )
        session.commit()
        raise HTTPException(429, "Trop de tentatives. Réessayez plus tard.")

    http_security.limiteur.enregistrer_tentative(adresse)
    user = security.authenticate(session, payload.email, payload.password)
    if user is None:
        http_security.limiteur.enregistrer_echec(payload.email)
        audit_service.record(
            session, "AUTHENTIFICATION_ECHEC", "user", 0,
            {"adresse": adresse, "canal": "api"}, actor_label="ANONYME",
        )
        session.commit()
        raise HTTPException(401, "Identifiants invalides.")

    http_security.limiteur.reinitialiser(payload.email)
    request.session.clear()
    http_security.ouvrir_session(request.session)
    request.session["user_id"] = user.id
    audit_service.record(
        session, "AUTHENTIFICATION_SUCCES", "user", user.id,
        {"adresse": adresse, "canal": "api"}, actor=user,
    )
    session.commit()
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
    admin: User = Depends(require_action(permissions.ACTION_BROUILLON)),
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
    admin: User = Depends(require_action(permissions.ACTION_BROUILLON)),
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
    """Lecture d'une version.

    Lot A, point 2 : un médecin ordinaire ne lit que la version **publiée**.
    Un brouillon, une version en révision ou remplacée est un document de
    travail administratif et exige l'action ``BROUILLON``. Le refus emprunte la
    réponse 404 uniforme pour ne pas révéler l'existence de la version.
    """
    version = session.get(ScheduleVersion, version_id)
    visibility_service.assert_version_lisible(session, user, version)
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
    profile: ProfessionalProfile = Depends(profile_medecin),
    session: Session = Depends(get_session),
):
    assignment = session.get(Assignment, payload.assignment_id)
    if assignment is None:
        raise HTTPException(404, "Affectation inconnue.")
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
    profile: ProfessionalProfile = Depends(profile_medecin),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    wave = session.get(HandoverWave, wave_id)
    visibility_service.assert_vague_lisible(session, user, wave)
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


@router.post("/handover/waves/{wave_id}/refus")
def decline_candidacy(
    wave_id: int,
    profile: ProfessionalProfile = Depends(profile_medecin),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Refus ou retrait explicite. Rend la candidature définitivement non tirable."""
    wave = session.get(HandoverWave, wave_id)
    visibility_service.assert_vague_lisible(session, user, wave)
    try:
        handover_service.decline(session, wave, profile)
    except handover_service.HandoverError as exc:
        session.rollback()
        raise HTTPException(409, str(exc))
    session.commit()
    return {"vague": wave.id, "reponse": "REFUS"}


@router.post("/handover/requests/{request_id}/advance")
def advance_handover(
    request_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    request_obj = session.get(HandoverRequest, request_id)
    # Ordre volontaire : d'abord le périmètre de lecture (404 uniforme, aucune
    # fuite d'existence), ensuite seulement l'action nommée puis le périmètre de
    # ligne (403 explicites, car la personne sait déjà que la demande existe).
    visibility_service.assert_reprise_lisible(session, user, request_obj)
    if not permission_service.may(session, user, permissions.ACTION_OPERATIONNEL):
        raise HTTPException(403, permission_service.refus(permissions.ACTION_OPERATIONNEL))
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
    visibility_service.assert_reprise_lisible(session, user, request_obj)
    details = visibility_service.details_reprise_visibles(session, user, request_obj)
    occurrence = request_obj.assignment.post.occurrence
    return {
        "demande": request_obj.id,
        "etat": request_obj.state.value,
        # Contrat d'anonymat honnête (lot A, point 4) : le planning publié est
        # nominatif, donc l'application ne prétend pas masquer le titulaire.
        # Ce qui est réellement garanti : la sollicitation ne porte ni nom ni
        # motif, et le commentaire reste réservé au demandeur et aux
        # responsables compétents.
        "titulaire_actuel": request_obj.assignment.profile.code,
        "demandeur": request_obj.requester.code,
        "commentaire": request_obj.comment if details else None,
        "motif_administratif": request_obj.admin_motive if details else None,
        "contrat_anonymat": visibility_service.CONTRAT_ANONYMAT,
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
    visibility_service.assert_tirage_lisible(session, user, draw)
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
    profile: ProfessionalProfile = Depends(profile_medecin),
    session: Session = Depends(get_session),
):
    a = session.get(Assignment, payload.assignment_a_id)
    b = session.get(Assignment, payload.assignment_b_id)
    if a is None or b is None:
        raise HTTPException(404, "Affectation inconnue.")
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


@router.get("/swaps/{swap_id}")
def swap_detail(
    swap_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    proposal = session.get(SwapProposal, swap_id)
    visibility_service.assert_echange_lisible(session, user, proposal)
    return {
        "echange": proposal.id,
        "etat": proposal.state.value,
        "garde_a": proposal.assignment_a_id,
        "garde_b": proposal.assignment_b_id,
        "motif_refus": proposal.refusal_reason,
        "accord_a": proposal.accepted_a_at.isoformat() if proposal.accepted_a_at else None,
        "accord_b": proposal.accepted_b_at.isoformat() if proposal.accepted_b_at else None,
    }


@router.post("/swaps/{swap_id}/accept")
def accept_swap(
    swap_id: int,
    profile: ProfessionalProfile = Depends(profile_medecin),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    proposal = session.get(SwapProposal, swap_id)
    visibility_service.assert_echange_lisible(session, user, proposal)
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
# Recherches d'échange — parcours nominal (lot B)
# --------------------------------------------------------------------------- #


class SwapSearchIn(BaseModel):
    assignment_id: int
    commentaire: str | None = None


class SwapAnswerIn(BaseModel):
    favorable: bool
    assignment_id: int | None = None


def _recherche_json(session: Session, search, details: bool) -> dict:
    import json as _json

    cedee = search.assignment.post.occurrence
    return {
        "recherche": search.id,
        "etat": search.state.value,
        "garde_cedee": search.assignment_id,
        "date_cedee": cedee.local_date.isoformat(),
        "ligne": search.assignment.post.line.value,
        "titulaire_actuel": search.assignment.profile.code,
        "demandeur": search.requester.code,
        "commentaire": search.comment if details else None,
        "fenetre": {
            "palier": search.window_label,
            "ouvre_a": search.opens_at.isoformat() if search.opens_at else None,
            "ferme_a": search.closes_at.isoformat() if search.closes_at else None,
            "circuit_urgent": search.urgent,
        },
        "sollicites": search.solicited_count,
        "propositions": [
            {
                "id": c.id,
                "partenaire": c.profile.code,
                "garde_reprise": c.assignment_id,
                "date_reprise": c.assignment.post.occurrence.local_date.isoformat(),
                "etat": c.state.value,
                "motif_exclusion": c.exclusion_reason if details else None,
            }
            for c in search.candidates
        ],
        "classement": _json.loads(search.ranking_json or "{}") if details else None,
        "tirage": _json.loads(search.draw_json) if search.draw_json else None,
        "resultat": search.outcome_reason,
        "proposition_retenue": search.retained_proposal_id,
        "contrat_anonymat": visibility_service.CONTRAT_ANONYMAT,
        "regle": (
            "Le parcours part d'une seule garde à céder. Tous les partenaires "
            "éligibles sont sollicités simultanément ; à la clôture, seules les "
            "réponses positives sont classées par maximin, et un tirage auditable "
            "ne départage qu'en cas d'égalité parfaite."
        ),
    }


@router.post("/swap-searches")
def open_swap_search(
    payload: SwapSearchIn,
    profile: ProfessionalProfile = Depends(profile_medecin),
    session: Session = Depends(get_session),
):
    """Parcours nominal : aucune garde de contrepartie n'est fournie."""
    assignment = session.get(Assignment, payload.assignment_id)
    if assignment is None:
        raise HTTPException(404, "Affectation inconnue.")
    try:
        search = swap_flow_service.ouvrir(
            session, assignment, profile, commentaire=payload.commentaire
        )
        swap_flow_service.avancer(session, search)
    except (swap_flow_service.SwapFlowError, swap_search_service.SwapSearchError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc))
    session.commit()
    session.refresh(search)
    return _recherche_json(session, search, details=True)


@router.get("/swap-searches/{search_id}")
def swap_search_detail(
    search_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    search = session.get(SwapSearch, search_id)
    visibility_service.assert_recherche_lisible(session, user, search)
    details = visibility_service.details_recherche_visibles(session, user, search)
    return _recherche_json(session, search, details=details)


@router.post("/swap-searches/{search_id}/reponse")
def answer_swap_search(
    search_id: int,
    payload: SwapAnswerIn,
    profile: ProfessionalProfile = Depends(profile_medecin),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    search = session.get(SwapSearch, search_id)
    visibility_service.assert_recherche_lisible(session, user, search)
    try:
        swap_flow_service.repondre(
            session, search, profile,
            favorable=payload.favorable, assignment_id=payload.assignment_id,
        )
        swap_flow_service.avancer(session, search)
    except swap_flow_service.SwapFlowError as exc:
        session.rollback()
        raise HTTPException(409, str(exc))
    session.commit()
    session.refresh(search)
    details = visibility_service.details_recherche_visibles(session, user, search)
    return _recherche_json(session, search, details=details)


@router.post("/swap-searches/{search_id}/advance")
def advance_swap_search(
    search_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    search = session.get(SwapSearch, search_id)
    visibility_service.assert_recherche_lisible(session, user, search)
    try:
        swap_flow_service.avancer(session, search)
    except swap_flow_service.SwapFlowError as exc:
        session.rollback()
        raise HTTPException(409, str(exc))
    session.commit()
    session.refresh(search)
    details = visibility_service.details_recherche_visibles(session, user, search)
    return _recherche_json(session, search, details=details)


@router.post("/swap-searches/{search_id}/annuler")
def cancel_swap_search(
    search_id: int,
    profile: ProfessionalProfile = Depends(profile_medecin),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    search = session.get(SwapSearch, search_id)
    visibility_service.assert_recherche_lisible(session, user, search)
    if search.requester_profile_id != profile.id:
        raise HTTPException(403, "Seul l'auteur d'une recherche peut la retirer.")
    if not swap_flow_service.annuler(session, search, actor=user):
        session.rollback()
        raise HTTPException(409, "Cette recherche n'est plus annulable.")
    session.commit()
    session.refresh(search)
    return _recherche_json(session, search, details=True)


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
    user: User = Depends(require_action(permissions.ACTION_CONSULTER_AUDIT)),
    session: Session = Depends(get_session),
):
    ok, problems = audit_service.verify_chain(session)
    return {"chaine_integre": ok, "anomalies": problems}


# --------------------------------------------------------------------------- #
# Écriture des quotas — périmètre objet × ligne (lot E)
# --------------------------------------------------------------------------- #


class QuotaTargetIn(BaseModel):
    profile_id: int
    category_code: str
    ligne: str
    cible: float
    commentaire: str | None = None


@router.post("/quotas/targets")
def ecrire_cible_de_quota(
    payload: QuotaTargetIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Saisie d'une cible, refusée hors du périmètre de ligne de la personne."""
    if not permission_service.may(
        session, user, permissions.ACTION_QUOTAS_SAISIR, payload.ligne
    ):
        raise HTTPException(
            403,
            permission_service.refus(
                permissions.ACTION_QUOTAS_SAISIR, payload.ligne
            ),
        )
    profile = session.get(ProfessionalProfile, payload.profile_id)
    year = session.execute(select(Year).order_by(Year.id.desc())).scalars().first()
    category = session.execute(
        select(QuotaCategory).where(QuotaCategory.code == payload.category_code)
    ).scalar_one_or_none()
    if profile is None or year is None or category is None:
        raise HTTPException(404, "Profil, année ou catégorie inconnue.")
    cible = quota_service.set_target(
        session, profile, year, category, Line(payload.ligne), payload.cible, user,
        comment=payload.commentaire,
    )
    session.commit()
    return {
        "cible": cible.id,
        "profil": profile.code,
        "categorie": category.code,
        "ligne": payload.ligne,
        "valeur": cible.target,
        "valide_institutionnellement": cible.institutionally_validated,
        "note": (
            "une cible saisie reste une valeur de simulation tant qu'elle n'est "
            "pas validée institutionnellement"
        ),
    }


class QuotaValidateIn(BaseModel):
    profile_id: int
    category_code: str
    ligne: str


@router.post("/quotas/targets/validate")
def valider_cible_de_quota(
    payload: QuotaValidateIn,
    user: User = Depends(require_action(permissions.ACTION_QUOTAS_VALIDER)),
    session: Session = Depends(get_session),
):
    """Validation institutionnelle : action distincte, réservée au chef de service."""
    profile = session.get(ProfessionalProfile, payload.profile_id)
    year = session.execute(select(Year).order_by(Year.id.desc())).scalars().first()
    category = session.execute(
        select(QuotaCategory).where(QuotaCategory.code == payload.category_code)
    ).scalar_one_or_none()
    if profile is None or year is None or category is None:
        raise HTTPException(404, "Profil, année ou catégorie inconnue.")
    cible = session.execute(
        select(QuotaTarget).where(
            QuotaTarget.profile_id == profile.id,
            QuotaTarget.year_id == year.id,
            QuotaTarget.category_id == category.id,
            QuotaTarget.line == Line(payload.ligne),
        )
    ).scalar_one_or_none()
    if cible is None:
        raise HTTPException(404, "Cible inconnue.")
    cible.institutionally_validated = True
    session.flush()
    audit_service.record(
        session, "QUOTA_VALIDE_INSTITUTIONNELLEMENT", "quota_target", cible.id,
        {"profil": profile.code, "categorie": category.code, "ligne": payload.ligne},
        actor=user,
    )
    session.commit()
    return {"cible": cible.id, "valide_institutionnellement": True}
