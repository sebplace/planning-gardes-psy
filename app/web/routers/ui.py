"""Interface web française, sobre, responsive et accessible.

Les couleurs ne sont jamais le seul vecteur d'information : chaque état porte aussi
un libellé texte et une icône typographique.
"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...db import get_session
from ...models import (
    Assignment,
    AuditEvent,
    Availability,
    Campaign,
    CampaignState,
    Color,
    CoveragePost,
    Draw,
    EngineRun,
    GardeOccurrence,
    HandoverRequest,
    HandoverWave,
    ProfessionalProfile,
    Proposal,
    Quarter,
    Scenario,
    ScenarioResult,
    ScheduleState,
    ScheduleVersion,
    Submission,
    SwapProposal,
    User,
    WaveSolicitation,
    WaveState,
    Year,
)
from ...services import (
    audit_service,
    campaign_service,
    handover_service,
    notification_service,
    planning_service,
    projection_service,
    quota_service,
    security,
    swap_service,
)
from ...services.clock import Clock, format_date_fr, format_local
from ..deps import optional_user, profile_of

router = APIRouter()
templates = Jinja2Templates(directory=str(__file__).rsplit("routers", 1)[0] + "templates")

COLOR_LABEL = {
    Color.VERT: ("V", "Vert — disponible", "vert"),
    Color.ORANGE: ("O", "Orange — possible, à éviter si mieux", "orange"),
    Color.ROUGE: ("R", "Rouge — indisponibilité ferme", "rouge"),
    Color.DISPO_DEFAUT: (
        "D", "Disponible par défaut — non confirmé par la personne", "defaut"
    ),
}


def flash(request: Request, categorie: str, texte: str) -> None:
    request.session.setdefault("messages", []).append([categorie, texte])


def pop_messages(request: Request) -> list:
    messages = request.session.pop("messages", [])
    return [(m[0], m[1]) for m in messages]


def render(request: Request, template: str, user: User | None, page: str, **context):
    return templates.TemplateResponse(
        request,
        template,
        {
            "user": user,
            "page": page,
            "flashes": pop_messages(request),
            "demo_banner": settings.demo_banner,
            "patient_warning": settings.patient_data_warning,
            "color_label": COLOR_LABEL,
            "format_date_fr": format_date_fr,
            "format_local": format_local,
            "now": Clock.now(),
            **context,
        },
    )


def _require(user: User | None):
    if user is None:
        raise HTTPException(status_code=307, headers={"Location": "/connexion"})
    return user


# --------------------------------------------------------------------------- #
# Connexion et modules
# --------------------------------------------------------------------------- #


@router.get("/", response_class=HTMLResponse)
def root(user: User | None = Depends(optional_user)):
    return RedirectResponse("/modules" if user else "/connexion", status_code=303)


@router.get("/connexion", response_class=HTMLResponse)
def login_form(request: Request, session: Session = Depends(get_session)):
    comptes = list(
        session.execute(select(User).order_by(User.email)).scalars()
    )
    return render(request, "connexion.html", None, "connexion", comptes=comptes)


@router.post("/connexion", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    mot_de_passe: str = Form(...),
    session: Session = Depends(get_session),
):
    user = security.authenticate(session, email, mot_de_passe)
    if user is None:
        flash(request, "erreur", "Identifiants invalides. Comptes de démonstration : mot de passe « demo ».")
        return RedirectResponse("/connexion", status_code=303)
    request.session["user_id"] = user.id
    return RedirectResponse("/modules", status_code=303)


@router.get("/deconnexion")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/connexion", status_code=303)


@router.get("/modules", response_class=HTMLResponse)
def modules(request: Request, user: User | None = Depends(optional_user)):
    _require(user)
    return render(request, "modules.html", user, "modules")


# --------------------------------------------------------------------------- #
# Tableau de bord personnel
# --------------------------------------------------------------------------- #


@router.get("/tableau-de-bord", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    affectations = []
    quotas = None
    campagne = None
    sollicitations = []

    if profile is not None:
        rows = session.execute(
            select(Assignment, CoveragePost, GardeOccurrence)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
            .where(
                Assignment.profile_id == profile.id,
                ScheduleVersion.state == ScheduleState.PUBLIE,
            )
            .order_by(GardeOccurrence.start_at)
        ).all()
        affectations = rows
        year = session.execute(select(Year).order_by(Year.id.desc())).scalars().first()
        if year:
            quotas = quota_service.summary(session, profile, year)
        campagne = session.execute(
            select(Submission).where(Submission.profile_id == profile.id)
            .order_by(Submission.id.desc())
        ).scalars().first()
        sollicitations = list(
            session.execute(
                select(WaveSolicitation, HandoverWave)
                .join(HandoverWave, WaveSolicitation.wave_id == HandoverWave.id)
                .where(
                    WaveSolicitation.profile_id == profile.id,
                    HandoverWave.state == WaveState.OUVERTE,
                )
            ).all()
        )
    return render(
        request, "tableau_de_bord.html", user, "tableau",
        profile=profile, affectations=affectations, quotas=quotas,
        campagne=campagne, sollicitations=sollicitations,
    )


# --------------------------------------------------------------------------- #
# Campagne : calendrier vert / orange / rouge
# --------------------------------------------------------------------------- #


@router.get("/campagne", response_class=HTMLResponse)
def campaign_view(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    if profile is None:
        flash(request, "info", "Ce compte n'a pas de profil médical : aucun désidérata à saisir.")
        return RedirectResponse("/tableau-de-bord", status_code=303)

    submission = session.execute(
        select(Submission).where(Submission.profile_id == profile.id)
        .order_by(Submission.id.desc())
    ).scalars().first()
    if submission is None:
        return render(request, "campagne.html", user, "campagne",
                      submission=None, semaines=[], manquantes=[], couleurs={})

    campaign = submission.campaign
    occurrences = list(
        session.execute(
            select(GardeOccurrence)
            .where(GardeOccurrence.quarter_id == campaign.quarter_id)
            .order_by(GardeOccurrence.local_date)
        ).scalars()
    )
    couleurs = {
        a.occurrence_id: a
        for a in session.execute(
            select(Availability).where(Availability.submission_id == submission.id)
        ).scalars()
    }
    semaines = _weeks(occurrences)
    manquantes = campaign_service.missing_holiday_pairs(
        session, submission,
        include_default=campaign.default_conversion_done_at is not None,
    )
    return render(
        request, "campagne.html", user, "campagne",
        submission=submission, campaign=campaign, semaines=semaines,
        couleurs=couleurs, manquantes=manquantes,
    )


def _weeks(occurrences: list[GardeOccurrence]) -> list[list]:
    """Regroupe les occurrences par semaine calendaire pour l'affichage."""
    if not occurrences:
        return []
    weeks: dict[tuple[int, int], list] = {}
    for occurrence in occurrences:
        key = occurrence.local_date.isocalendar()[:2]
        weeks.setdefault(key, [None] * 7)
        weeks[key][occurrence.local_date.weekday()] = occurrence
    return [weeks[key] for key in sorted(weeks)]


@router.post("/campagne/couleur", response_class=HTMLResponse)
def set_color(
    request: Request,
    occurrence_id: int = Form(...),
    couleur: str = Form(...),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    submission = session.execute(
        select(Submission).where(Submission.profile_id == profile.id)
        .order_by(Submission.id.desc())
    ).scalars().first()
    occurrence = session.get(GardeOccurrence, occurrence_id)
    try:
        campaign_service.set_availability(
            session, submission, occurrence, Color(couleur)
        )
        session.commit()
    except campaign_service.CampaignError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse("/campagne", status_code=303)


@router.post("/campagne/valider", response_class=HTMLResponse)
def validate_campaign(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    submission = session.execute(
        select(Submission).where(Submission.profile_id == profile.id)
        .order_by(Submission.id.desc())
    ).scalars().first()
    try:
        campaign_service.validate_submission(session, submission)
        session.commit()
        flash(request, "succes", "Réponse validée. Vous ne recevrez plus de rappel.")
    except campaign_service.CampaignError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse("/campagne", status_code=303)


# --------------------------------------------------------------------------- #
# Planning publié
# --------------------------------------------------------------------------- #


@router.get("/planning", response_class=HTMLResponse)
def planning(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    versions = list(
        session.execute(
            select(ScheduleVersion)
            .where(ScheduleVersion.state == ScheduleState.PUBLIE)
            .order_by(ScheduleVersion.id.desc())
        ).scalars()
    )
    version = versions[0] if versions else None
    lignes = []
    if version is not None:
        lignes = session.execute(
            select(Assignment, CoveragePost, GardeOccurrence)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .where(Assignment.schedule_version_id == version.id)
            .order_by(GardeOccurrence.start_at, CoveragePost.line)
        ).all()
    profile = profile_of(session, user)
    return render(request, "planning.html", user, "planning",
                  version=version, lignes=lignes, profile=profile, versions=versions)


@router.get("/planning/export.csv")
def export_csv(
    user: User | None = Depends(optional_user), session: Session = Depends(get_session)
):
    from fastapi.responses import PlainTextResponse

    _require(user)
    version = session.execute(
        select(ScheduleVersion).where(ScheduleVersion.state == ScheduleState.PUBLIE)
        .order_by(ScheduleVersion.id.desc())
    ).scalars().first()
    lines = ["date;type;ligne;statut_requis;personne;origine"]
    if version:
        rows = session.execute(
            select(Assignment, CoveragePost, GardeOccurrence)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .where(Assignment.schedule_version_id == version.id)
            .order_by(GardeOccurrence.start_at)
        ).all()
        for assignment, post, occurrence in rows:
            lines.append(
                f"{occurrence.local_date};{occurrence.garde_type.code};{post.line.value};"
                f"{post.required_status.value};{assignment.profile.code};{assignment.origin.value}"
            )
    return PlainTextResponse("\n".join(lines), media_type="text/csv; charset=utf-8")


@router.get("/planning/export.xlsx")
def export_xlsx(
    user: User | None = Depends(optional_user), session: Session = Depends(get_session)
):
    """Export Excel du planning publié (§16)."""
    from io import BytesIO

    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook

    _require(user)
    version = session.execute(
        select(ScheduleVersion).where(ScheduleVersion.state == ScheduleState.PUBLIE)
        .order_by(ScheduleVersion.id.desc())
    ).scalars().first()

    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Planning"
    feuille.append(
        ["Date", "Type", "Mode", "Ligne", "Statut requis", "Personne", "Origine",
         "Début", "Fin"]
    )
    if version:
        rows = session.execute(
            select(Assignment, CoveragePost, GardeOccurrence)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .where(Assignment.schedule_version_id == version.id)
            .order_by(GardeOccurrence.start_at, CoveragePost.line)
        ).all()
        for assignment, post, occurrence in rows:
            feuille.append([
                occurrence.local_date.isoformat(),
                occurrence.garde_type.label,
                occurrence.effective_mode.value,
                post.line.value,
                post.required_status.value,
                assignment.profile.code,
                assignment.origin.value,
                format_local(occurrence.start_at),
                format_local(occurrence.end_at),
            ])
    note = classeur.create_sheet("Avertissement")
    note.append(["Prototype de démonstration — données entièrement fictives."])
    note.append(["Aucune donnée patient. Ni outil institutionnel, ni logiciel de production."])

    flux = BytesIO()
    classeur.save(flux)
    flux.seek(0)
    return StreamingResponse(
        flux,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="planning_demo.xlsx"'},
    )


@router.get("/planning/mon-calendrier.ics")
def export_ics(
    user: User | None = Depends(optional_user), session: Session = Depends(get_session)
):
    from fastapi.responses import PlainTextResponse

    _require(user)
    profile = profile_of(session, user)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Prototype gardes//FR"]
    if profile is not None:
        rows = session.execute(
            select(Assignment, CoveragePost, GardeOccurrence)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
            .where(
                Assignment.profile_id == profile.id,
                ScheduleVersion.state == ScheduleState.PUBLIE,
            )
        ).all()
        for assignment, post, occurrence in rows:
            lines += [
                "BEGIN:VEVENT",
                f"UID:garde-{assignment.id}@prototype.invalid",
                f"DTSTART:{occurrence.start_at.strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{occurrence.end_at.strftime('%Y%m%dT%H%M%SZ')}",
                f"SUMMARY:Garde {post.line.value} — {occurrence.garde_type.label}",
                "DESCRIPTION:Prototype de démonstration, données fictives.",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return PlainTextResponse("\r\n".join(lines), media_type="text/calendar; charset=utf-8")


# --------------------------------------------------------------------------- #
# Suivi administratif, génération, comparaison, publication
# --------------------------------------------------------------------------- #


@router.get("/admin", response_class=HTMLResponse)
def admin_home(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    campaigns = list(session.execute(select(Campaign).order_by(Campaign.id.desc())).scalars())
    quarters = list(session.execute(select(Quarter).order_by(Quarter.id)).scalars())
    runs = list(session.execute(select(EngineRun).order_by(EngineRun.id.desc())).scalars())
    versions = list(
        session.execute(select(ScheduleVersion).order_by(ScheduleVersion.id.desc())).scalars()
    )
    blockers = {q.id: planning_service.generation_blockers(session, q) for q in quarters}
    return render(request, "admin.html", user, "admin",
                  campaigns=campaigns, quarters=quarters, runs=runs,
                  versions=versions, blockers=blockers)


@router.post("/admin/generer", response_class=HTMLResponse)
def admin_generate(
    request: Request,
    quarter_id: int = Form(...),
    graine: int = Form(20260901),
    variantes: int = Form(3),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    quarter = session.get(Quarter, quarter_id)
    run = planning_service.run_engine(
        session, quarter, admin=user, seed=graine, variants=variantes, min_diversity=0.08
    )
    session.commit()
    if run.blocked_reason:
        flash(request, "alerte", f"Génération bloquée : {run.blocked_reason}")
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse(f"/admin/execution/{run.id}", status_code=303)


@router.get("/admin/execution/{run_id}", response_class=HTMLResponse)
def run_detail(
    run_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    run = session.get(EngineRun, run_id)
    proposals = sorted(run.proposals, key=lambda p: p.variant_index)
    details = [
        {
            "proposition": p,
            "score": json.loads(p.score_breakdown_json),
            "non_pourvus": json.loads(p.unfilled_json),
            "tensions": json.loads(p.tensions_json),
        }
        for p in proposals
    ]
    return render(request, "execution.html", user, "admin", run=run, details=details)


@router.post("/admin/execution/{run_id}/retenir", response_class=HTMLResponse)
def keep_proposal(
    run_id: int,
    request: Request,
    proposal_id: int = Form(...),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    proposal = session.get(Proposal, proposal_id)
    version = planning_service.create_version_from_proposal(
        session, proposal, user, note=f"Variante {proposal.variant_index}"
    )
    session.commit()
    return RedirectResponse(f"/admin/version/{version.id}", status_code=303)


@router.get("/admin/version/{version_id}", response_class=HTMLResponse)
def version_detail(
    version_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    version = session.get(ScheduleVersion, version_id)
    rows = session.execute(
        select(Assignment, CoveragePost, GardeOccurrence)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .where(Assignment.schedule_version_id == version.id)
        .order_by(GardeOccurrence.start_at, CoveragePost.line)
    ).all()
    profils = list(session.execute(select(ProfessionalProfile).order_by(ProfessionalProfile.code)).scalars())
    manquants = planning_service._missing_posts(session, version)
    return render(request, "version.html", user, "admin",
                  version=version, lignes=rows, profils=profils, manquants=manquants)


@router.post("/admin/version/{version_id}/corriger", response_class=HTMLResponse)
def correct(
    version_id: int,
    request: Request,
    post_id: int = Form(...),
    profile_id: str = Form(""),
    motif: str = Form(...),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    version = session.get(ScheduleVersion, version_id)
    post = session.get(CoveragePost, post_id)
    profile = session.get(ProfessionalProfile, int(profile_id)) if profile_id else None
    try:
        planning_service.manual_correction(session, version, post, profile, user, motif)
        session.commit()
        flash(request, "succes", "Correction enregistrée et journalisée.")
    except planning_service.PlanningError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse(f"/admin/version/{version_id}", status_code=303)


@router.post("/admin/version/{version_id}/action", response_class=HTMLResponse)
def version_action(
    version_id: int,
    request: Request,
    action: str = Form(...),
    post_id: str = Form(""),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    version = session.get(ScheduleVersion, version_id)
    try:
        if action == "verrouiller":
            planning_service.set_lock(session, version, int(post_id), True, user)
        elif action == "deverrouiller":
            planning_service.set_lock(session, version, int(post_id), False, user)
        elif action == "valider":
            planning_service.validate_version(session, version, user)
            flash(request, "succes", "Planning validé. Il peut maintenant être publié.")
        elif action == "publier":
            planning_service.publish_version(session, version, user)
            flash(request, "succes", "Planning publié. Les médecins ont été notifiés (simulation).")
        elif action == "regenerer":
            run = planning_service.regenerate_keeping_locks(
                session, version, user, seed=20260902, variants=1
            )
            session.commit()
            return RedirectResponse(f"/admin/execution/{run.id}", status_code=303)
        session.commit()
    except planning_service.PlanningError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse(f"/admin/version/{version_id}", status_code=303)


@router.get("/admin/quotas", response_class=HTMLResponse)
def admin_quotas(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    year = session.execute(select(Year).order_by(Year.id.desc())).scalars().first()
    resumes = quota_service.admin_overview(session, year) if year else []
    return render(request, "quotas.html", user, "quotas", year=year, resumes=resumes)


# --------------------------------------------------------------------------- #
# Reprises et échanges
# --------------------------------------------------------------------------- #


@router.get("/reprises", response_class=HTMLResponse)
def handovers(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    demandes = list(
        session.execute(select(HandoverRequest).order_by(HandoverRequest.id.desc())).scalars()
    )
    visibles = [
        (d, handover_service.requester_visible_to(user, d)) for d in demandes
    ]
    sollicitations = []
    mes_gardes = []
    if profile is not None:
        sollicitations = list(
            session.execute(
                select(WaveSolicitation, HandoverWave)
                .join(HandoverWave, WaveSolicitation.wave_id == HandoverWave.id)
                .where(
                    WaveSolicitation.profile_id == profile.id,
                    HandoverWave.state == WaveState.OUVERTE,
                )
            ).all()
        )
        mes_gardes = session.execute(
            select(Assignment, CoveragePost, GardeOccurrence)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
            .where(
                Assignment.profile_id == profile.id,
                ScheduleVersion.state == ScheduleState.PUBLIE,
                GardeOccurrence.start_at > Clock.now(),
            )
            .order_by(GardeOccurrence.start_at)
        ).all()
    return render(request, "reprises.html", user, "reprises",
                  demandes=visibles, sollicitations=sollicitations,
                  mes_gardes=mes_gardes, profile=profile)


@router.post("/reprises/demander", response_class=HTMLResponse)
def request_handover_ui(
    request: Request,
    assignment_id: int = Form(...),
    commentaire: str = Form(""),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    assignment = session.get(Assignment, assignment_id)
    try:
        demande = handover_service.request_handover(
            session, assignment, profile, comment=commentaire
        )
        handover_service.advance(session, demande)
        session.commit()
        flash(request, "succes",
              "Demande ouverte. Les personnes éligibles sont sollicitées simultanément "
              "et anonymement ; le départage se fera par tirage au sort.")
    except handover_service.HandoverError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse("/reprises", status_code=303)


@router.get("/reprises/{request_id}", response_class=HTMLResponse)
def handover_detail(
    request_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    demande = session.get(HandoverRequest, request_id)
    if demande is None:
        raise HTTPException(404, "Demande inconnue.")
    visible = handover_service.requester_visible_to(user, demande)
    tirages = {}
    for wave in demande.waves:
        draw = session.execute(select(Draw).where(Draw.wave_id == wave.id)).scalar_one_or_none()
        if draw:
            tirages[wave.id] = (draw, json.loads(draw.proof_json), json.loads(draw.excluded_json))
    profile = profile_of(session, user)
    return render(request, "reprise_detail.html", user, "reprises",
                  demande=demande, visible=visible, tirages=tirages, profile=profile)


@router.post("/reprises/{request_id}/reponse", response_class=HTMLResponse)
def handover_response(
    request_id: int,
    request: Request,
    wave_id: int = Form(...),
    reponse: str = Form(...),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    wave = session.get(HandoverWave, wave_id)
    try:
        if reponse == "favorable":
            handover_service.submit_candidacy(session, wave, profile)
            flash(request, "succes",
                  "Candidature enregistrée. Toutes les réponses favorables sont collectées, "
                  "puis départagées par tirage au sort : répondre plus vite ne procure aucun avantage.")
        else:
            handover_service.decline(session, wave, profile)
            flash(request, "info", "Réponse négative enregistrée.")
        handover_service.advance(session, wave.request)
        session.commit()
    except handover_service.HandoverError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse(f"/reprises/{request_id}", status_code=303)


@router.post("/reprises/{request_id}/avancer", response_class=HTMLResponse)
def handover_advance(
    request_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    demande = session.get(HandoverRequest, request_id)
    try:
        handover_service.advance(session, demande)
        session.commit()
    except handover_service.HandoverError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse(f"/reprises/{request_id}", status_code=303)


@router.get("/echanges", response_class=HTMLResponse)
def swaps(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    propositions = list(
        session.execute(select(SwapProposal).order_by(SwapProposal.id.desc())).scalars()
    )
    mes_gardes = []
    autres_gardes = []
    if profile is not None:
        rows = session.execute(
            select(Assignment, CoveragePost, GardeOccurrence)
            .join(CoveragePost, Assignment.post_id == CoveragePost.id)
            .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
            .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
            .where(
                ScheduleVersion.state == ScheduleState.PUBLIE,
                GardeOccurrence.start_at > Clock.now(),
            )
            .order_by(GardeOccurrence.start_at)
        ).all()
        mes_gardes = [r for r in rows if r[0].profile_id == profile.id]
        autres_gardes = [r for r in rows if r[0].profile_id != profile.id]
    return render(request, "echanges.html", user, "echanges",
                  propositions=propositions, mes_gardes=mes_gardes,
                  autres_gardes=autres_gardes, profile=profile)


@router.post("/echanges/proposer", response_class=HTMLResponse)
def propose_swap_ui(
    request: Request,
    assignment_a_id: int = Form(...),
    assignment_b_id: int = Form(...),
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    a = session.get(Assignment, assignment_a_id)
    b = session.get(Assignment, assignment_b_id)
    try:
        proposal = swap_service.propose_swap(session, a, b, profile)
        session.commit()
        if proposal.refusal_reason:
            flash(request, "alerte", proposal.refusal_reason)
        else:
            flash(request, "succes", "Proposition envoyée. L'échange devient officiel "
                                     "au second accord, après revérification des deux côtés.")
    except swap_service.SwapError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse("/echanges", status_code=303)


@router.post("/echanges/{swap_id}/accepter", response_class=HTMLResponse)
def accept_swap_ui(
    swap_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    proposal = session.get(SwapProposal, swap_id)
    try:
        proposal = swap_service.accept_swap(session, proposal, profile)
        session.commit()
        if proposal.refusal_reason:
            flash(request, "alerte", f"Échange refusé : {proposal.refusal_reason}")
        else:
            flash(request, "succes", "Échange officialisé. Les compteurs restent inchangés.")
    except swap_service.SwapError as exc:
        session.rollback()
        flash(request, "erreur", str(exc))
    return RedirectResponse("/echanges", status_code=303)


# --------------------------------------------------------------------------- #
# Notifications, audit, projections
# --------------------------------------------------------------------------- #


@router.get("/notifications", response_class=HTMLResponse)
def notifications(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    profile = profile_of(session, user)
    if user.is_admin:
        messages = notification_service.inbox(session)
    else:
        messages = notification_service.inbox(session, profile.id if profile else -1)
    return render(request, "notifications.html", user, "notifications", messages=messages)


@router.get("/audit", response_class=HTMLResponse)
def audit(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    ok, problems = audit_service.verify_chain(session)
    events = list(
        session.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(300)).scalars()
    )
    return render(request, "audit.html", user, "audit",
                  events=events, chaine_ok=ok, anomalies=problems)


@router.get("/projections", response_class=HTMLResponse)
def projections(
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    scenarios = list(session.execute(select(Scenario).order_by(Scenario.id.desc())).scalars())
    latest = {}
    for scenario in scenarios:
        result = session.execute(
            select(ScenarioResult).where(ScenarioResult.scenario_id == scenario.id)
            .order_by(ScenarioResult.id.desc()).limit(1)
        ).scalar_one_or_none()
        if result:
            latest[scenario.id] = result
    return render(request, "projections.html", user, "projections",
                  scenarios=scenarios, latest=latest)


@router.get("/projections/{scenario_id}", response_class=HTMLResponse)
def projection_detail(
    scenario_id: int,
    request: Request,
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
):
    _require(user)
    if not user.is_admin:
        raise HTTPException(403, "Réservé aux administrateurs.")
    scenario = session.get(Scenario, scenario_id)
    result = session.execute(
        select(ScenarioResult).where(ScenarioResult.scenario_id == scenario.id)
        .order_by(ScenarioResult.id.desc()).limit(1)
    ).scalar_one_or_none()
    structural = json.loads(result.structural_json) if result else None
    sensitivity = json.loads(result.sensitivity_json) if result else []
    feasibility = json.loads(result.feasibility_json) if result else {}
    return render(request, "projection_detail.html", user, "projections",
                  scenario=scenario, result=result, structural=structural,
                  sensitivity=sensitivity, feasibility=feasibility,
                  hypotheses=json.loads(scenario.params_json))
