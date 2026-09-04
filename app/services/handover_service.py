"""Reprise d'une garde publiée : vagues, collecte, gel, tirage auditable.

Exception métier explicitement bornée (§4.4) : le tirage ne concerne **que** le choix
entre plusieurs volontaires déjà éligibles. Il ne fait pas partie du moteur de
génération du planning initial.

Principes appliqués sans exception :
  * une réponse favorable est une **candidature**, pas une attribution ;
  * l'ordre et la vitesse des réponses n'ont **aucune** influence ;
  * la liste est **figée** avant tout aléa, puis chaque candidature est revérifiée ;
  * un seul tirage officiel est possible (contrainte d'unicité + transition gardée) ;
  * le résultat est **immédiatement officiel**, sans validation administrative ;
  * l'identité du demandeur reste masquée jusqu'à l'attribution officialisée.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Assignment,
    AssignmentOrigin,
    Candidacy,
    CandidacyState,
    Color,
    CoveragePost,
    Draw,
    GardeOccurrence,
    GardeType,
    HandoverRequest,
    HandoverState,
    HandoverWave,
    Line,
    ProfessionalProfile,
    Quarter,
    QuotaCategory,
    ScheduleState,
    ScheduleVersion,
    UrgencyProfile,
    User,
    WaveKind,
    WaveSolicitation,
    WaveState,
    Year,
)
from . import audit_service, engine_bridge, notification_service, quota_service
from .clock import Clock, format_date_fr, format_local

DRAW_ALGORITHM = (
    "index = int(HMAC-SHA256(server_seed, sha256(ids_candidatures_valides_triés))[0:16], 16) "
    "mod nombre_de_candidatures_valides"
)


class HandoverError(Exception):
    pass


class HandoverPermissionError(HandoverError):
    """Refus de droit sur une reprise, distinct d'un refus métier.

    Sous-classe de ``HandoverError`` pour que les appelants existants continuent
    de fonctionner, mais identifiable pour répondre 403 plutôt que 409.
    """


# --------------------------------------------------------------------------- #
# Fenêtres et rappels adaptatifs (OPEN_QUESTIONS.md Q-09)
# --------------------------------------------------------------------------- #


def urgency_tier(session: Session, starts_at: datetime) -> dict:
    profile = session.execute(
        select(UrgencyProfile).where(UrgencyProfile.active)
    ).scalars().first()
    tiers = json.loads(profile.tiers_json) if profile else []
    hours_before = (starts_at - Clock.now()).total_seconds() / 3600.0
    for tier in tiers:
        limit = tier.get("max_hours_before")
        if limit is None or hours_before < limit:
            return dict(tier, hours_before=round(hours_before, 2))
    return {
        "label": "par défaut", "window_minutes": 1440,
        "reminders_minutes": [480], "hours_before": round(hours_before, 2),
    }


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #


def _guard(session: Session, model, pk: int, field: str, expected, new) -> bool:
    """Transition d'état **gardée côté serveur**. Retourne False si un autre
    processus a déjà fait basculer l'état."""
    result = session.execute(
        update(model)
        .where(model.id == pk, getattr(model, field) == expected)
        .values(**{field: new})
    )
    if result.rowcount == 1:
        obj = session.get(model, pk)
        if obj is not None:
            session.expire(obj)
        return True
    return False


def _post_of(request: HandoverRequest) -> CoveragePost:
    return request.assignment.post


def _occurrence_of(request: HandoverRequest) -> GardeOccurrence:
    return request.assignment.post.occurrence


def _context(session: Session, request: HandoverRequest, wave: HandoverWave | None = None) -> dict:
    occurrence = _occurrence_of(request)
    post = _post_of(request)
    return {
        "date": format_date_fr(occurrence.local_date),
        "line": "première ligne" if post.line is Line.L1 else "deuxième ligne",
        "type_label": occurrence.garde_type.label,
        "closes_at": format_local(wave.closes_at) if wave else "—",
    }


def requester_visible_to(user: User | None, request: HandoverRequest) -> bool:
    """L'identité du demandeur n'est visible que des administrateurs, et de tous
    seulement après l'attribution officialisée."""
    if user is None:
        return False
    if user.is_admin:
        return True
    if request.state is HandoverState.ATTRIBUEE:
        return True
    return request.requester.user_id == user.id


# --------------------------------------------------------------------------- #
# Ouverture d'une demande
# --------------------------------------------------------------------------- #


def request_handover(
    session: Session,
    assignment: Assignment,
    requester: ProfessionalProfile,
    comment: str | None = None,
    admin_motive: str | None = None,
) -> HandoverRequest:
    version = session.get(ScheduleVersion, assignment.schedule_version_id)
    if version.state is not ScheduleState.PUBLIE:
        raise HandoverError("Seule une garde d'un planning publié peut faire l'objet d'une reprise.")
    if assignment.profile_id != requester.id:
        raise HandoverError("Seule la personne affectée peut demander une reprise.")
    occurrence = assignment.post.occurrence
    if occurrence.start_at <= Clock.now():
        raise HandoverError("La garde a déjà commencé.")

    # Une garde ne peut participer qu'à une seule opération à la fois (reprise ou échange).
    locked = session.execute(
        update(Assignment)
        .where(Assignment.id == assignment.id, Assignment.busy_operation.is_(None))
        .values(busy_operation="REPRISE")
    )
    if locked.rowcount != 1:
        raise HandoverError(
            "Cette garde participe déjà à une autre opération (reprise ou échange en cours)."
        )
    session.expire(assignment)

    request = HandoverRequest(
        assignment_id=assignment.id,
        requester_profile_id=requester.id,
        comment=(comment or "")[:300] or None,
        admin_motive=(admin_motive or "")[:300] or None,
        state=HandoverState.BROUILLON,
    )
    session.add(request)
    session.flush()
    audit_service.record(
        session, "REPRISE_DEMANDEE", "handover_request", request.id,
        {"affectation": assignment.id, "date": occurrence.local_date.isoformat()},
        actor=requester.user,
    )
    return request


def cancel_request(
    session: Session, request: HandoverRequest, actor: User | None = None
) -> bool:
    """Annulation possible **jusqu'au début atomique du tirage**.

    Si le tirage a déjà commencé, l'annulation échoue et l'état final reste unique.
    """
    for state in (
        HandoverState.BROUILLON,
        HandoverState.COLLECTE_VERTE,
        HandoverState.COLLECTE_UNIQUE,
        HandoverState.COLLECTE_ORANGE,
    ):
        if _guard(session, HandoverRequest, request.id, "state", state, HandoverState.ANNULEE):
            session.refresh(request)
            request.cancelled_at = Clock.now()
            request.closed_at = Clock.now()
            for wave in request.waves:
                if wave.state is WaveState.OUVERTE:
                    wave.state = WaveState.SANS_CANDIDATURE
            session.execute(
                update(Assignment)
                .where(Assignment.id == request.assignment_id)
                .values(busy_operation=None)
            )
            session.flush()
            audit_service.record(
                session, "REPRISE_ANNULEE", "handover_request", request.id, {}, actor=actor
            )
            return True
    return False


# --------------------------------------------------------------------------- #
# Vagues
# --------------------------------------------------------------------------- #


#: Couleurs sollicitables par type de vague. Arbitrage du client du 03/09/2026 :
#: une disponibilité par défaut non confirmée n'est **jamais** sollicitée en reprise.
COULEURS_SOLLICITABLES = {
    WaveKind.VERTE: (Color.VERT,),
    WaveKind.UNIQUE: (Color.VERT, Color.ORANGE),
    WaveKind.ORANGE: (Color.ORANGE,),  # héritage, plus jamais ouverte
}


def wave_kind_for(post: CoveragePost) -> WaveKind:
    """Type de collecte selon la ligne.

    Première ligne : uniquement les personnes explicitement vertes.
    Deuxième ligne : une seule collecte, verts et orange ensemble, la priorité au
    vert étant appliquée au moment du tirage et non par des vagues successives.
    """
    return WaveKind.VERTE if post.line is Line.L1 else WaveKind.UNIQUE


def eligible_profiles(
    session: Session, request: HandoverRequest, kind: WaveKind
) -> list[ProfessionalProfile]:
    """Personnes sollicitables.

    Seules les couleurs **explicitement déclarées** ouvrent une sollicitation.
    ``DISPO_DEFAUT`` est exclu de toutes les reprises : une non-réponse peut servir
    à la génération initiale, jamais à désigner un volontaire.

    Dans tous les cas, les contraintes fermes sont vérifiées et le demandeur est
    exclu de sa propre vague.
    """
    post = _post_of(request)
    occurrence = post.occurrence
    accepted = COULEURS_SOLLICITABLES[kind]
    out: list[ProfessionalProfile] = []
    for profile in session.execute(select(ProfessionalProfile)).scalars():
        if profile.id == request.requester_profile_id:
            continue  # H12 : le demandeur est exclu de sa propre vague
        color = engine_bridge.current_color(session, profile.id, occurrence.id, post.line)
        if color not in accepted:
            continue
        rejection = engine_bridge.check_assignment(
            session, post, profile, ignore_assignment_ids={request.assignment_id}
        )
        if rejection is not None:
            continue
        out.append(profile)
    return sorted(out, key=lambda p: p.code)


def open_wave(session: Session, request: HandoverRequest, kind: WaveKind) -> HandoverWave:
    occurrence = _occurrence_of(request)
    tier = urgency_tier(session, occurrence.start_at)
    now = Clock.now()
    window = timedelta(minutes=tier["window_minutes"])
    closes_at = min(now + window, occurrence.start_at)

    wave = HandoverWave(
        request_id=request.id,
        kind=kind,
        state=WaveState.OUVERTE,
        opens_at=now,
        closes_at=closes_at,
        reminder_plan_json=json.dumps(tier.get("reminders_minutes", []), ensure_ascii=False),
        urgency_tier=tier.get("label"),
    )
    session.add(wave)
    session.flush()

    profiles = eligible_profiles(session, request, kind)
    wave.solicited_count = len(profiles)
    for profile in profiles:
        session.add(WaveSolicitation(wave_id=wave.id, profile_id=profile.id))
        # Sollicitation anonyme : ni le nom, ni le motif du demandeur.
        notification_service.enqueue(
            session,
            "REPRISE_SOLLICITATION",
            f"reprise:{request.id}:vague:{wave.id}:sollicitation:{profile.id}",
            profile,
            _context(session, request, wave),
            anonymised=True,
        )
    request.state = (
        HandoverState.COLLECTE_VERTE
        if kind is WaveKind.VERTE
        else HandoverState.COLLECTE_UNIQUE
    )
    session.flush()
    audit_service.record(
        session, "VAGUE_OUVERTE", "handover_wave", wave.id,
        {"type": kind.value, "sollicitees": len(profiles),
         "fenetre_minutes": tier["window_minutes"], "palier": tier.get("label"),
         "cloture": closes_at.isoformat()},
        actor_label="SYSTEME",
    )
    return wave


def send_due_reminders(session: Session, wave: HandoverWave) -> int:
    """Rappels adaptatifs, sans doublon possible (clé d'idempotence)."""
    if wave.state is not WaveState.OUVERTE:
        return 0
    plan = json.loads(wave.reminder_plan_json or "[]")
    now = Clock.now()
    sent = 0
    responded = {
        s.profile_id
        for s in session.execute(
            select(WaveSolicitation).where(
                WaveSolicitation.wave_id == wave.id,
                WaveSolicitation.responded_at.is_not(None),
            )
        ).scalars()
    }
    for index, offset in enumerate(plan, start=1):
        due_at = wave.opens_at + timedelta(minutes=offset)
        if now < due_at or due_at >= wave.closes_at:
            continue
        for solicitation in session.execute(
            select(WaveSolicitation).where(WaveSolicitation.wave_id == wave.id)
        ).scalars():
            if solicitation.profile_id in responded:
                continue
            profile = session.get(ProfessionalProfile, solicitation.profile_id)
            created = notification_service.enqueue(
                session,
                "REPRISE_RAPPEL",
                f"reprise:{wave.request_id}:vague:{wave.id}:rappel:{index}:{profile.id}",
                profile,
                dict(_context(session, wave.request, wave), index=index),
                anonymised=True,
            )
            if created is not None:
                sent += 1
    return sent


# --------------------------------------------------------------------------- #
# Candidatures
# --------------------------------------------------------------------------- #


def _assert_collecte_ouverte(session: Session, wave: HandoverWave, action: str) -> None:
    """Refuse et **trace** toute tentative de modification après gel ou échéance.

    Contre-audit du 04/09/2026 : un refus déposé après le gel était silencieux, et
    un refus postérieur à une candidature favorable laissait la candidature
    tirable.
    """
    session.refresh(wave)
    motif = None
    if wave.state is not WaveState.OUVERTE:
        motif = f"collecte close (état {wave.state.value})"
    elif Clock.now() > wave.closes_at:
        motif = "fenêtre de réponse expirée"
    if motif is None:
        return
    audit_service.record(
        session,
        "REPONSE_TARDIVE_REFUSEE",
        "handover_wave",
        wave.id,
        {"action": action, "motif": motif, "cloture": wave.closes_at.isoformat()},
        actor_label="SYSTEME",
    )
    raise HandoverError(
        f"Modification refusée : {motif}. La liste des candidatures est figée."
    )


def submit_candidacy(
    session: Session, wave: HandoverWave, profile: ProfessionalProfile
) -> Candidacy:
    """Dépôt d'une candidature. **Ce n'est pas une attribution.**"""
    _assert_collecte_ouverte(session, wave, "candidature")
    now = Clock.now()
    occurrence = _occurrence_of(wave.request)
    if occurrence.start_at <= now:
        raise HandoverError("La garde a déjà commencé.")
    if profile.id == wave.request.requester_profile_id:
        raise HandoverError("Le demandeur ne peut pas candidater à sa propre reprise.")

    solicitation = session.execute(
        select(WaveSolicitation).where(
            WaveSolicitation.wave_id == wave.id, WaveSolicitation.profile_id == profile.id
        )
    ).scalar_one_or_none()
    if solicitation is None:
        raise HandoverError("Vous n'avez pas été sollicité·e pour cette vague.")

    existante = session.execute(
        select(Candidacy).where(
            Candidacy.wave_id == wave.id, Candidacy.profile_id == profile.id
        )
    ).scalar_one_or_none()
    if existante is not None:
        if existante.state is CandidacyState.RETIREE:
            raise HandoverError(
                "Candidature déjà retirée : elle ne peut pas être redéposée sur "
                "cette collecte."
            )
        raise HandoverError("Candidature déjà enregistrée.")

    candidacy = Candidacy(wave_id=wave.id, profile_id=profile.id, state=CandidacyState.DEPOSEE)
    session.add(candidacy)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HandoverError("Candidature déjà enregistrée.")
    solicitation.responded_at = now
    solicitation.response = "FAVORABLE"
    session.flush()
    audit_service.record(
        session, "CANDIDATURE_DEPOSEE", "candidacy", candidacy.id,
        {"vague": wave.id, "profil": profile.code,
         "note": "l'ordre et la vitesse de réponse n'influencent pas le tirage"},
        actor=profile.user,
    )
    return candidacy


def decline(session: Session, wave: HandoverWave, profile: ProfessionalProfile) -> None:
    """Refus ou retrait. Rend la candidature éventuelle **définitivement non tirable**."""
    _assert_collecte_ouverte(session, wave, "refus")
    solicitation = session.execute(
        select(WaveSolicitation).where(
            WaveSolicitation.wave_id == wave.id, WaveSolicitation.profile_id == profile.id
        )
    ).scalar_one_or_none()
    if solicitation is None:
        return

    # Un refus postérieur à une candidature favorable la retire réellement.
    candidature = session.execute(
        select(Candidacy).where(
            Candidacy.wave_id == wave.id,
            Candidacy.profile_id == profile.id,
            Candidacy.state == CandidacyState.DEPOSEE,
        )
    ).scalar_one_or_none()
    if candidature is not None:
        candidature.state = CandidacyState.RETIREE
        candidature.exclusion_reason = (
            "Retrait explicite par la personne après une réponse favorable : "
            "candidature non tirable."
        )
        session.flush()
        audit_service.record(
            session,
            "CANDIDATURE_RETIREE",
            "candidacy",
            candidature.id,
            {"vague": wave.id, "profil": profile.code},
            actor=profile.user,
        )

    solicitation.responded_at = Clock.now()
    solicitation.response = "REFUS"
    session.flush()


def withdraw_candidacy(
    session: Session, wave: HandoverWave, profile: ProfessionalProfile
) -> None:
    """Retrait explicite. Synonyme opérationnel du refus, tracé de la même façon."""
    decline(session, wave, profile)


def all_responded(session: Session, wave: HandoverWave) -> bool:
    pending = session.execute(
        select(WaveSolicitation).where(
            WaveSolicitation.wave_id == wave.id, WaveSolicitation.responded_at.is_(None)
        )
    ).scalars().first()
    return pending is None


# --------------------------------------------------------------------------- #
# Gel, revérification, tirage
# --------------------------------------------------------------------------- #


def _sha(values: list[int]) -> str:
    return hashlib.sha256(",".join(str(v) for v in sorted(values)).encode()).hexdigest()


def close_and_draw(session: Session, wave: HandoverWave) -> Draw | None:
    """Gel de la liste, revérification, puis tirage. Une seule tentative officielle.

    Retourne ``None`` si aucune candidature valide ne subsiste (la vague suivante ou
    l'escalade est alors décidée par ``advance``).
    """
    request = wave.request
    if not _guard(session, HandoverWave, wave.id, "state", WaveState.OUVERTE, WaveState.FIGEE):
        raise HandoverError("La collecte a déjà été close par une autre opération.")
    session.refresh(wave)

    expected_state = (
        HandoverState.COLLECTE_VERTE
        if wave.kind is WaveKind.VERTE
        else HandoverState.COLLECTE_UNIQUE
    )
    frozen_state = (
        HandoverState.LISTE_FIGEE_VERTE
        if wave.kind is WaveKind.VERTE
        else HandoverState.LISTE_FIGEE_UNIQUE
    )
    if not _guard(
        session, HandoverRequest, request.id, "state", expected_state, frozen_state
    ):
        raise HandoverError(
            "La demande a changé d'état entre-temps (annulation ou opération concurrente)."
        )
    session.refresh(request)

    # 1. Gel de la liste des candidatures reçues.
    frozen = list(
        session.execute(
            select(Candidacy)
            .where(Candidacy.wave_id == wave.id, Candidacy.state == CandidacyState.DEPOSEE)
            .order_by(Candidacy.id)
        ).scalars()
    )
    frozen_ids = [c.id for c in frozen]
    list_hash = _sha(frozen_ids)

    # 2. Engagement sur la graine : l'empreinte est enregistrée **avant** tout calcul.
    server_seed = secrets.token_hex(32)
    seed_commitment = hashlib.sha256(server_seed.encode()).hexdigest()
    wave.frozen_at = Clock.now()
    wave.list_hash = list_hash
    wave.seed_commitment = seed_commitment
    session.flush()
    audit_service.record(
        session, "LISTE_FIGEE", "handover_wave", wave.id,
        {"candidatures": frozen_ids, "empreinte_liste": list_hash,
         "engagement_graine": seed_commitment,
         "note": "la graine est tirée au gel ; seule son empreinte est publiée à ce stade"},
        actor_label="SYSTEME",
    )
    for profile_id in {c.profile_id for c in frozen}:
        notification_service.enqueue(
            session,
            "REPRISE_CLOTURE_COLLECTE",
            f"reprise:{request.id}:vague:{wave.id}:cloture:{profile_id}",
            session.get(ProfessionalProfile, profile_id),
            _context(session, request, wave),
            anonymised=True,
        )

    # 3. Revérification de chaque candidature figée.
    post = _post_of(request)
    occurrence = post.occurrence
    valid: list[Candidacy] = []
    excluded: list[dict] = []
    couleurs: dict[int, Color | None] = {}
    for candidacy in frozen:
        profile = candidacy.profile
        reason = None
        color = engine_bridge.current_color(session, profile.id, occurrence.id, post.line)
        couleurs[candidacy.id] = color
        if color is Color.ROUGE:
            reason = "Rouge déclaré depuis le dépôt de la candidature : exclusion immédiate."
        elif color is Color.DISPO_DEFAUT:
            reason = (
                "Disponibilité par défaut non confirmée : exclue de toutes les "
                "reprises."
            )
        elif color not in COULEURS_SOLLICITABLES[wave.kind]:
            reason = (
                "Couleur devenue non sollicitable pour ce type de collecte depuis "
                "le dépôt de la candidature."
            )
        elif occurrence.start_at <= Clock.now():
            reason = "La garde a commencé avant le tirage."
        else:
            rejection = engine_bridge.check_assignment(
                session, post, profile, ignore_assignment_ids={request.assignment_id}
            )
            if rejection is not None:
                reason = f"{rejection.label} — {rejection.detail}"
        if reason is None:
            candidacy.state = CandidacyState.VALIDE
            valid.append(candidacy)
        else:
            candidacy.state = CandidacyState.EXCLUE
            candidacy.exclusion_reason = reason[:300]
            excluded.append({"candidature": candidacy.id, "profil": profile.code, "motif": reason})
    session.flush()

    if not valid:
        wave.state = WaveState.SANS_CANDIDATURE
        session.flush()
        audit_service.record(
            session, "VAGUE_SANS_CANDIDATURE", "handover_wave", wave.id,
            {"exclusions": excluded, "candidatures_figees": frozen_ids},
            actor_label="SYSTEME",
        )
        return None

    # 3bis. Priorité au vert, appliquée **au tirage** et non par vagues successives.
    # S'il existe au moins un volontaire vert valide, le tirage ne porte que sur eux.
    verts = [c for c in valid if couleurs.get(c.id) is Color.VERT]
    oranges = [c for c in valid if couleurs.get(c.id) is Color.ORANGE]
    if verts:
        tirables = verts
        priorite = "VERT"
    else:
        tirables = oranges or valid
        priorite = "ORANGE"

    # 4. Tirage sur la liste figée, revalidée et restreinte au palier prioritaire.
    valid_ids = sorted(c.id for c in tirables)
    valid_hash = _sha(valid_ids)
    digest = hmac.new(server_seed.encode(), valid_hash.encode(), hashlib.sha256).hexdigest()
    index = int(digest[:16], 16) % len(valid_ids)
    winner_id = valid_ids[index]
    winner = next(c for c in tirables if c.id == winner_id)

    draw = Draw(
        wave_id=wave.id,
        executed_at=Clock.now(),
        list_hash=list_hash,
        seed_commitment=seed_commitment,
        server_seed=server_seed,
        algorithm=DRAW_ALGORITHM,
        candidate_ids_json=json.dumps(frozen_ids),
        excluded_json=json.dumps(excluded, ensure_ascii=False),
        winner_candidacy_id=winner.id,
        winner_profile_id=winner.profile_id,
        single_candidate=len(valid_ids) == 1,
        proof_json=json.dumps(
            {
                "liste_figee": frozen_ids,
                "liste_valide": sorted(c.id for c in valid),
                "palier_prioritaire": priorite,
                "liste_tirable": valid_ids,
                "verts_valides": sorted(c.id for c in verts),
                "orange_valides": sorted(c.id for c in oranges),
                "empreinte_liste_figee": list_hash,
                "empreinte_liste_valide": valid_hash,
                "engagement_graine": seed_commitment,
                "graine_revelee": server_seed,
                "hmac": digest,
                "index": index,
                "verification": (
                    "sha256(graine_revelee) doit égaler l'engagement ; "
                    "index = int(HMAC-SHA256(graine, empreinte_liste_valide)[0:16],16) mod n"
                ),
                "regle_de_priorite": (
                    "collecte unique, mais tirage restreint aux volontaires verts "
                    "valides ; les orange ne sont tirés qu'en l'absence totale de "
                    "vert valide"
                ),
                "candidature_unique": len(valid_ids) == 1,
            },
            ensure_ascii=False,
        ),
    )
    session.add(draw)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HandoverError("Un tirage a déjà été exécuté pour cette vague.")

    wave.state = WaveState.TIREE
    session.flush()
    audit_service.record(
        session, "TIRAGE_EXECUTE", "draw", draw.id,
        json.loads(draw.proof_json) | {"gagnant": winner.profile.code},
        actor_label="SYSTEME",
    )
    _apply_attribution(session, request, wave, winner)
    return draw


def _apply_attribution(
    session: Session, request: HandoverRequest, wave: HandoverWave, winner: Candidacy
) -> None:
    """Planning, quota, historique et clôture de la demande : **une seule transaction**."""
    assignment = request.assignment
    replaced = session.get(ProfessionalProfile, assignment.profile_id)
    taker = winner.profile
    post = assignment.post
    occurrence = post.occurrence
    quarter = session.get(Quarter, occurrence.quarter_id)
    year = session.get(Year, quarter.year_id)
    category = session.get(QuotaCategory, occurrence.garde_type.category_id)

    assignment.profile_id = taker.id
    assignment.origin = AssignmentOrigin.REPRISE
    assignment.row_version += 1
    assignment.busy_operation = None

    winner.state = CandidacyState.RETENUE
    for candidacy in wave.candidacies:
        if candidacy.id != winner.id and candidacy.state is CandidacyState.VALIDE:
            candidacy.state = CandidacyState.NON_RETENUE

    request.state = HandoverState.ATTRIBUEE
    request.result_profile_id = taker.id
    request.closed_at = Clock.now()
    session.flush()

    quota_service.apply_handover_adjustment(
        session, replaced=replaced, taker=taker, year=year, category=category,
        line=post.line, weight=occurrence.garde_type.count_weight,
        source_ref=f"reprise:{request.id}",
    )

    context = _context(session, request, wave)
    notification_service.enqueue(
        session, "REPRISE_TIRAGE_GAGNANT",
        f"reprise:{request.id}:attribution:{taker.id}", taker, context,
    )
    for candidacy in wave.candidacies:
        if candidacy.id == winner.id:
            continue
        # Une seule notification de clôture par candidat non retenu.
        notification_service.enqueue(
            session, "REPRISE_TIRAGE_NON_RETENU",
            f"reprise:{request.id}:non_retenu:{candidacy.profile_id}",
            candidacy.profile, context, anonymised=True,
        )
    notification_service.enqueue(
        session, "REPRISE_TIRAGE_NON_RETENU",
        f"reprise:{request.id}:demandeur:{replaced.id}", replaced, context,
    )
    audit_service.record(
        session, "REPRISE_OFFICIALISEE", "handover_request", request.id,
        {"remplacee": replaced.code, "reprend": taker.code,
         "affectation": assignment.id, "vague": wave.kind.value,
         "note": "résultat immédiatement officiel, sans validation administrative"},
        actor_label="SYSTEME",
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def escalate(session: Session, request: HandoverRequest, reasons: list[str]) -> None:
    request.state = HandoverState.ESCALADE
    request.escalated_at = Clock.now()
    request.closed_at = Clock.now()
    for wave in request.waves:
        if wave.state is WaveState.OUVERTE:
            wave.state = WaveState.SANS_CANDIDATURE
    session.execute(
        update(Assignment)
        .where(Assignment.id == request.assignment_id)
        .values(busy_operation=None)
    )
    session.flush()
    context = _context(session, request)
    notification_service.enqueue(
        session, "REPRISE_ECHEC", f"reprise:{request.id}:escalade_admin", None,
        context, recipient_label="administrateurs et chefs de service",
    )
    notification_service.enqueue(
        session, "REPRISE_ECHEC", f"reprise:{request.id}:escalade_demandeur",
        request.requester, context,
    )
    audit_service.record(
        session, "REPRISE_ESCALADE", "handover_request", request.id,
        {"raisons": reasons,
         "note": "affectation initiale maintenue ; aucune personne rouge n'a été sollicitée"},
        actor_label="SYSTEME",
    )


def current_wave(request: HandoverRequest) -> HandoverWave | None:
    open_waves = [w for w in request.waves if w.state is WaveState.OUVERTE]
    return open_waves[0] if open_waves else None


def advance(
    session: Session,
    request: HandoverRequest,
    actor: User | None = None,
    enforce_permissions: bool = False,
) -> HandoverRequest:
    """Fait progresser la demande : ouverture, rappels, clôture, tirage, escalade.

    Quand ``enforce_permissions`` est vrai, le périmètre de ligne est vérifié
    **ici**, au niveau métier. C'est le point d'entrée unique de l'interface web
    comme de l'API : un contrôle posé dans une seule couche de présentation
    serait contournable par l'autre.

    Les appels internes (jeu de démonstration, ``run_until_settled`` mécanique)
    laissent la valeur par défaut, car ils ne sont pas déclenchés par un
    utilisateur.
    """
    if enforce_permissions:
        assert_may_advance(session, request, actor)
    session.refresh(request)
    if not request.is_open:
        return request
    if request.state is HandoverState.BROUILLON:
        open_wave(session, request, wave_kind_for(_post_of(request)))
        return request

    wave = current_wave(request)
    if wave is not None:
        send_due_reminders(session, wave)
        due = Clock.now() >= wave.closes_at
        complete = all_responded(session, wave)
        if not (due or complete):
            return request
        draw = close_and_draw(session, wave)
        if draw is not None:
            return request
        # Plus de vague orange successive : la collecte est unique (03/09/2026).
        # Sans volontaire valide, le titulaire publié reste responsable et les
        # responsables sont alertés.
        motif = (
            "Aucun volontaire vert valide en première ligne."
            if wave.kind is WaveKind.VERTE
            else "Aucun volontaire valide, ni vert ni orange, en deuxième ligne."
        )
        escalate(session, request, [motif])
    return request


def line_of(request: HandoverRequest) -> str:
    """Ligne de garde concernée par une demande de reprise."""
    return _post_of(request).line.value


def assert_may_advance(
    session: Session, request: HandoverRequest, actor: User | None
) -> None:
    """Garde métier unique du périmètre de ligne sur l'avancement d'une reprise.

    Le responsable des gardes de première ligne ne peut pas faire avancer une
    reprise de deuxième ligne, et inversement. Le chef de service couvre les deux.
    """
    from . import permission_service

    try:
        permission_service.require_line_supervision(session, actor, line_of(request))
    except permission_service.PermissionError_ as exc:
        raise HandoverPermissionError(str(exc)) from None


def run_until_settled(session: Session, request: HandoverRequest, max_steps: int = 8):
    for _ in range(max_steps):
        advance(session, request)
        session.refresh(request)
        if not request.is_open:
            break
    return request
