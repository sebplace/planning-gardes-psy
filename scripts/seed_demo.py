"""Jeu de démonstration **entièrement fictif** et parcours complet.

Aucun nom réel. Toutes les adresses utilisent le domaine réservé ``.invalid``.
Aucun message n'est envoyé : les notifications sont écrites dans une boîte locale.

Parcours joué :
  1. socle (comptes, catalogue, trimestre, quotas manuels, exemptions, paires fériées) ;
  2. projections capacitaires (structurelle, matrice de sensibilité, faisabilité) ;
  3. campagne de désidératas avec un non-répondant ;
  4. blocage de la génération, puis disponibilité par défaut après délai de grâce ;
  5. génération, comparaison de variantes, correction, validation, publication ;
  6. reprise verte avec tirage, reprise orange, reprise échouée ;
  7. échange bilatéral valide et échange refusé.
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, create_all, drop_all  # noqa: E402
from app.engine.projection import (  # noqa: E402
    AssistantGroup,
    CategoryVolume,
    ScenarioParams,
    SeniorGroup,
)
from app.models import (  # noqa: E402
    ActivityPeriod,
    Assignment,
    Availability,
    Campaign,
    Color,
    CoverageMode,
    CoveragePost,
    Eligibility,
    Exemption,
    GardeOccurrence,
    GardeType,
    HandoverState,
    HolidayRequirement,
    Line,
    ProfessionalProfile,
    Quarter,
    QuotaCategory,
    QuotiteHistory,
    ScheduleState,
    Status,
    Submission,
    SwapState,
    User,
    WaveKind,
    Year,
)
from app.services import (  # noqa: E402
    audit_service,
    campaign_service,
    catalog_service,
    handover_service,
    planning_service,
    projection_service,
    quota_service,
    security,
    swap_service,
)
from app.services.clock import Clock  # noqa: E402

SENIOR_NAMES = [
    "Alpha", "Bêta", "Gamma", "Delta", "Epsilon", "Zêta", "Êta",
    "Thêta", "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi",
]
ASSISTANT_NAMES = ["Omicron", "Pi", "Rhô", "Sigma", "Tau", "Upsilon"]

# Jours fériés fictifs positionnés sur 2027 — administrables, jamais figés en dur.
HOLIDAYS_2027 = {
    date(2027, 1, 1), date(2027, 3, 29), date(2027, 5, 1), date(2027, 5, 6),
    date(2027, 5, 17), date(2027, 7, 21), date(2027, 8, 15), date(2027, 11, 1),
    date(2027, 11, 11), date(2027, 12, 25),
}

QUOTITES = [10, 10, 10, 10, 10, 10, 9, 9, 8, 8, 8, 6, 5, 5]


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


# --------------------------------------------------------------------------- #
# 1. Socle
# --------------------------------------------------------------------------- #


def seed_people(session) -> dict:
    people = {"seniors": [], "assistants": [], "admins": []}

    for index, name in enumerate(SENIOR_NAMES, start=1):
        user = User(
            email=f"sen{index:02d}@demo.invalid",
            display_name=f"Dr {name} (senior fictif {index:02d})",
            password_hash=security.hash_password("demo"),
            is_medecin=True,
            is_admin=False,
        )
        session.add(user)
        session.flush()
        profile = ProfessionalProfile(
            user_id=user.id, code=f"SEN-{index:02d}", status=Status.SENIOR
        )
        session.add(profile)
        session.flush()
        session.add(ActivityPeriod(profile_id=profile.id, start_date=date(2020, 1, 1)))
        session.add(
            QuotiteHistory(
                profile_id=profile.id, start_date=date(2020, 1, 1),
                tenths=QUOTITES[index - 1], tima_label=f"TIMA {QUOTITES[index-1]}/10",
                note="Donnée enregistrée ; aucune formule institutionnelle appliquée.",
            )
        )
        people["seniors"].append(profile)

    # SEN-13 n'est pas éligible à la première ligne (éligibilité métier, pas un quota).
    session.add(
        Eligibility(
            profile_id=people["seniors"][12].id, line=Line.L1, eligible=False,
            comment="Éligibilité métier fictive : deuxième ligne uniquement.",
        )
    )

    for index, name in enumerate(ASSISTANT_NAMES, start=1):
        user = User(
            email=f"ass{index:02d}@demo.invalid",
            display_name=f"Dr {name} (assistant fictif {index:02d})",
            password_hash=security.hash_password("demo"),
            is_medecin=True,
            is_admin=False,
        )
        session.add(user)
        session.flush()
        profile = ProfessionalProfile(
            user_id=user.id, code=f"ASS-{index:02d}", status=Status.ASSISTANT
        )
        session.add(profile)
        session.flush()
        # Assistant temporaire : période d'activité datée, expiration dérivée.
        end = date(2027, 12, 31) if index < 6 else date(2026, 12, 31)
        session.add(
            ActivityPeriod(
                profile_id=profile.id, start_date=date(2026, 9, 1), end_date=end,
                reason="Contrat d'assistanat fictif",
            )
        )
        session.add(
            QuotiteHistory(profile_id=profile.id, start_date=date(2026, 9, 1), tenths=10)
        )
        people["assistants"].append(profile)

    # Administrateurs : droits applicatifs séparés du statut professionnel.
    admin = User(
        email="admin@demo.invalid", display_name="Responsable de service (fictif)",
        password_hash=security.hash_password("demo"), is_medecin=False, is_admin=True,
    )
    session.add(admin)
    session.flush()
    people["admins"].append(admin)

    # Cumul explicite : médecin senior **et** administrateur, permissions séparées.
    cumul = session.get(User, people["seniors"][0].user_id)
    cumul.is_admin = True
    people["admins"].append(cumul)

    session.flush()
    print(
        f"  {len(people['seniors'])} seniors, {len(people['assistants'])} assistants, "
        f"{len(people['admins'])} administrateurs (dont 1 cumul médecin/administrateur)"
    )
    return people


def mode_for(occurrence: GardeOccurrence) -> CoverageMode:
    """Mode A tous les 3 jours : un senior assure alors la L1 **seul**, sans L2."""
    return CoverageMode.A if occurrence.local_date.toordinal() % 3 == 0 else CoverageMode.B


def seed_catalog(session) -> tuple[Year, Quarter]:
    catalog_service.ensure_reference_data(session)
    year = catalog_service.create_year(
        session, "2027", date(2027, 1, 1), date(2027, 12, 31)
    )
    quarter = session.execute(
        select(Quarter).where(Quarter.year_id == year.id, Quarter.index == 1)
    ).scalar_one()
    occurrences = catalog_service.generate_occurrences(
        session, quarter, holidays=HOLIDAYS_2027, mode_resolver=mode_for
    )
    modes = {"A": 0, "B": 0}
    for occurrence in occurrences:
        modes[occurrence.effective_mode.value] += 1
    posts = session.execute(
        select(CoveragePost)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .where(GardeOccurrence.quarter_id == quarter.id)
    ).scalars().all()
    print(
        f"  {len(occurrences)} occurrences ({modes['A']} en mode A, {modes['B']} en mode B) "
        f"→ {len(posts)} postes de couverture"
    )

    pairs = [
        ("NOEL_NOUVEL_AN", "Noël / Nouvel An",
         [("Noël", date(2027, 12, 24), date(2027, 12, 25)),
          ("Nouvel An", date(2026, 12, 31), date(2027, 1, 1))]),
        ("PAQUES_1MAI", "Lundi de Pâques / 1er mai",
         [("Lundi de Pâques", date(2027, 3, 28), date(2027, 3, 29)),
          ("1er mai", date(2027, 4, 30), date(2027, 5, 1))]),
        ("ASCENSION_PENTECOTE", "Ascension / Lundi de Pentecôte",
         [("Ascension", date(2027, 5, 5), date(2027, 5, 6)),
          ("Lundi de Pentecôte", date(2027, 5, 16), date(2027, 5, 17))]),
        ("JUILLET_AOUT", "21 juillet / 15 août",
         [("21 juillet", date(2027, 7, 20), date(2027, 7, 21)),
          ("15 août", date(2027, 8, 14), date(2027, 8, 15))]),
        ("NOVEMBRE", "1er novembre / 11 novembre",
         [("1er novembre", date(2027, 10, 31), date(2027, 11, 1)),
          ("11 novembre", date(2027, 11, 10), date(2027, 11, 11))]),
    ]
    for code, label, members in pairs:
        catalog_service.create_holiday_pair(session, code, label, members)
    print(f"  {len(pairs)} paires de jours fériés fictives (dont une à cheval sur deux années)")
    return year, quarter


def seed_quotas(session, year: Year, quarter: Quarter, people: dict, admin: User) -> None:
    """Saisie **manuelle** des cibles (M-004). Aucune formule TIMA n'est appliquée."""
    categories = {c.code: c for c in session.execute(select(QuotaCategory)).scalars()}
    counts: dict[tuple[str, str], int] = {}
    rows = session.execute(
        select(CoveragePost, GardeType, QuotaCategory)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(QuotaCategory, GardeType.category_id == QuotaCategory.id)
        .where(GardeOccurrence.quarter_id == quarter.id)
    ).all()
    for post, _type, category in rows:
        key = (category.code, post.line.value)
        counts[key] = counts.get(key, 0) + 1

    seniors = people["seniors"]
    assistants = people["assistants"]
    for (category_code, line_value), quarter_count in sorted(counts.items()):
        annual = quarter_count * 4  # cible annuelle fictive
        line = Line(line_value)
        pool = assistants if line is Line.L1 and category_code != "" else seniors
        if line is Line.L2:
            pool = seniors
        # En mode B la L1 revient aux assistants, en mode A aux seniors : les deux
        # populations sont donc concernées par la L1.
        if line is Line.L1:
            share_assistants = 0.6
            per_assistant = annual * share_assistants / max(len(assistants), 1)
            per_senior = annual * (1 - share_assistants) / max(len(seniors), 1)
            for profile in assistants:
                quota_service.set_target(
                    session, profile, year, categories[category_code], line,
                    round(per_assistant, 1), admin,
                    comment="Cible fictive saisie manuellement (OPEN_QUESTIONS.md Q-01)",
                )
            for profile in seniors:
                quota_service.set_target(
                    session, profile, year, categories[category_code], line,
                    round(per_senior, 1), admin,
                    comment="Cible fictive saisie manuellement (OPEN_QUESTIONS.md Q-01)",
                )
        else:
            per_senior = annual / max(len(seniors), 1)
            for profile in seniors:
                quota_service.set_target(
                    session, profile, year, categories[category_code], line,
                    round(per_senior, 1), admin,
                    comment="Cible fictive saisie manuellement (OPEN_QUESTIONS.md Q-01)",
                )

    # Exemption totale : SEN-14 ne fait aucun jour férié (quota nul de fait).
    session.add(
        Exemption(
            profile_id=seniors[13].id, category_id=categories["FERIES"].id, total=True,
            start_date=date(2027, 1, 1), end_date=date(2027, 12, 31),
            comment="Exemption administrative fictive. Aucun motif d'âge n'est encodé.",
            created_by_id=admin.id,
        )
    )
    # Exemption partielle : SEN-12 à 50 % sur les week-ends.
    session.add(
        Exemption(
            profile_id=seniors[11].id, category_id=categories["WEEKENDS_VEILLES"].id,
            total=False, reduction_ratio=0.5,
            start_date=date(2027, 1, 1), end_date=date(2027, 12, 31),
            comment="Réduction administrative fictive de 50 %.",
            created_by_id=admin.id,
        )
    )
    session.flush()
    print("  quotas manuels saisis, 1 exemption totale et 1 exemption partielle")


# --------------------------------------------------------------------------- #
# 2. Projections
# --------------------------------------------------------------------------- #


def demo_projections(session, admin: User, quarter: Quarter) -> None:
    counts: dict[str, int] = {}
    rows = session.execute(
        select(GardeOccurrence, GardeType, QuotaCategory)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(QuotaCategory, GardeType.category_id == QuotaCategory.id)
        .where(GardeOccurrence.quarter_id == quarter.id)
    ).all()
    labels = {}
    for _occ, _type, category in rows:
        counts[category.code] = counts.get(category.code, 0) + 1
        labels[category.code] = category.label

    params = ScenarioParams(
        name="Trimestre T1 2027 — 6 assistants",
        description="Scénario de référence, hypothèses de démonstration.",
        categories=tuple(
            CategoryVolume(code, labels[code], n, 0.66) for code, n in sorted(counts.items())
        ),
        assistants=AssistantGroup(count=6, guards_per_assistant=10),
        seniors=SeniorGroup(
            quotite_tenths=tuple(QUOTITES),
            exemption_ratios=(0.0,) * 11 + (0.25, 0.0, 0.3),
            max_total_per_full_time=14.0,
        ),
        senior_load_threshold=12.0,
    )
    scenario = projection_service.save_scenario(session, params, admin)
    result = projection_service.compute(session, scenario, seed=20260901)
    import json

    structural = json.loads(result.structural_json)
    print(f"  scénario « {scenario.name} » → {result.verdict}")
    print(
        f"    postes requis={structural['posts_required']} "
        f"répartis={structural['posts_assigned']} non couverts={structural['posts_uncovered']} "
        f"identité arithmétique={structural['identite_arithmetique']}"
    )
    print(
        f"    L1 senior résiduelle={structural['senior_l1']} · L2 senior={structural['senior_l2']} "
        f"· moyenne/senior={structural['mean_per_senior']} "
        f"(min {structural['min_per_senior']} / max {structural['max_per_senior']})"
    )
    feasibility = json.loads(result.feasibility_json)
    print(
        f"    simulation de faisabilité : réalisable={feasibility.get('realisable')} "
        f"postes non pourvus={feasibility.get('postes_non_pourvus')}"
    )

    tight = ScenarioParams(
        name="Trimestre T1 2027 — 2 assistants seulement",
        description="Variante tendue, sans conversion du mode B en mode A.",
        categories=params.categories,
        assistants=AssistantGroup(count=2, guards_per_assistant=6),
        seniors=params.seniors,
        convert_uncovered_b_to_a=False,
        senior_load_threshold=12.0,
    )
    tight_scenario = projection_service.save_scenario(session, tight, admin)
    tight_result = projection_service.compute(
        session, tight_scenario, with_feasibility=False, seed=20260901
    )
    tight_structural = json.loads(tight_result.structural_json)
    print(f"  scénario « {tight_scenario.name} » → {tight_result.verdict}")
    print(f"    déficit explicite : {tight_structural['posts_uncovered']} poste(s) non couvert(s)")
    for reason in tight_structural["reasons"]:
        print(f"    raison : {reason}")

    try:
        projection_service.promote_to_configuration(session, tight_scenario, admin, confirmed=False)
    except projection_service.ProjectionError as exc:
        print(f"  promotion sans confirmation refusée : {exc}")


# --------------------------------------------------------------------------- #
# 3-4. Campagne de désidératas
# --------------------------------------------------------------------------- #


def demo_campaign(session, quarter: Quarter, people: dict, admin: User) -> Campaign:
    opens_at = datetime(2026, 11, 27, 8, 0)
    deadline = datetime(2026, 12, 27, 12, 0)
    Clock.freeze(opens_at)

    campaign = campaign_service.create_campaign(
        session, quarter, opens_at=opens_at, deadline_at=deadline, admin=admin,
        grace_period_hours=48, requirement=HolidayRequirement.VERT_ORANGE,
    )
    campaign_service.open_campaign(session, campaign, admin)
    print(f"  campagne ouverte le 27/11/2026, échéance le 27/12/2026 "
          f"({len(campaign.submissions)} personnes)")

    occurrences = list(
        session.execute(
            select(GardeOccurrence).where(GardeOccurrence.quarter_id == quarter.id)
        ).scalars()
    )
    pairs_occurrences = _holiday_pair_occurrences(session, campaign)

    Clock.freeze(datetime(2026, 12, 13, 13, 0))
    sent = campaign_service.send_due_reminders(session, campaign)
    print(f"  rappel J-14 envoyé à {sent} personne(s) non finalisée(s)")

    non_repondant = people["seniors"][6]  # SEN-07 ne répondra jamais
    rng = random.Random(20260901)
    for submission in campaign.submissions:
        if submission.profile_id == non_repondant.id:
            continue
        for occurrence in occurrences:
            draw = rng.random()
            color = Color.ROUGE if draw < 0.15 else (
                Color.ORANGE if draw < 0.35 else Color.VERT
            )
            campaign_service.set_availability(session, submission, occurrence, color)
        # Garantit la couverture d'au moins un membre de chaque paire applicable.
        for occurrence_ids in pairs_occurrences:
            if occurrence_ids:
                occurrence = session.get(GardeOccurrence, sorted(occurrence_ids)[0])
                campaign_service.set_availability(
                    session, submission, occurrence, Color.VERT
                )
        campaign_service.validate_submission(session, submission)
    print(f"  {len(campaign.submissions) - 1} réponses validées · 1 non-répondant "
          f"({non_repondant.code})")

    # Les rappels cessent après validation.
    Clock.freeze(datetime(2026, 12, 20, 13, 0))
    sent = campaign_service.send_due_reminders(session, campaign)
    Clock.freeze(datetime(2026, 12, 25, 13, 0))
    sent += campaign_service.send_due_reminders(session, campaign)
    print(f"  rappels J-7 et J-2 : {sent} envoi(s), uniquement au non-répondant")

    Clock.freeze(datetime(2026, 12, 27, 12, 30))
    campaign_service.close_campaign(session, campaign, admin)
    print(f"  échéance atteinte → état {campaign.state.value} (génération bloquée)")

    blockers = planning_service.generation_blockers(session, quarter)
    for blocker in blockers:
        print(f"    blocage : {blocker}")

    ok, reasons = campaign_service.can_apply_default_availability(campaign)
    print(f"  conversion immédiate possible ? {ok} — {reasons}")

    Clock.freeze(datetime(2026, 12, 29, 13, 0))
    converted = campaign_service.apply_default_availability(session, campaign, admin)
    total = sum(converted.values())
    print(
        f"  après délai de grâce (48 h) : {total} date(s) passée(s) en "
        f"« disponible par défaut — non confirmé par la personne » pour "
        f"{', '.join(k for k, v in converted.items() if v)}"
    )
    print(f"  état de la campagne : {campaign.state.value}")
    return campaign


def _holiday_pair_occurrences(session, campaign: Campaign) -> list[set[int]]:
    out = []
    quarter = campaign.quarter
    for pair in campaign_service.applicable_pairs(session, campaign):
        for member in pair.members:
            if member.date_end < quarter.start_date or member.date_start > quarter.end_date:
                continue
            ids = {
                o.id
                for o in catalog_service.occurrences_for_member(session, member)
                if quarter.start_date <= o.local_date <= quarter.end_date
            }
            if ids:
                out.append(ids)
                break
    return out


# --------------------------------------------------------------------------- #
# 5. Génération, validation, publication
# --------------------------------------------------------------------------- #


def demo_planning(session, quarter: Quarter, admin: User):
    import json

    Clock.freeze(datetime(2026, 12, 29, 14, 0))
    run = planning_service.run_engine(
        session, quarter, admin=admin, seed=20260901, variants=3, min_diversity=0.08
    )
    print(f"  exécution {run.id} · statut {run.status.value} · "
          f"empreinte des entrées {run.input_snapshot_hash[:16]}…")
    print(f"  profil de règles : {run.rule_profile_label} · "
          f"version des règles : {run.ruleset_version} · moteur {run.engine_version}")
    for proposal in run.proposals:
        print(
            f"    variante {proposal.variant_index} : score {proposal.score_total:.0f} · "
            f"réalisable={proposal.feasible} · oranges={proposal.orange_count} · "
            f"dispo par défaut utilisées={proposal.default_availability_count} · "
            f"diversité min={proposal.diversity_min:.2f}"
        )
        if not proposal.feasible:
            for item in json.loads(proposal.unfilled_json)[:3]:
                print(f"      non pourvu : {item['date']} {item['ligne']} ({item['type']})")

    best = min(run.proposals, key=lambda p: (not p.feasible, p.score_total))
    version = planning_service.create_version_from_proposal(
        session, best, admin, note="Version issue de la meilleure variante."
    )
    example = version.assignments[0]
    explanation = json.loads(example.explanation_json)
    print(f"  explication d'une affectation : {explanation.get('texte')}")

    # Correction manuelle refusée sur une date rouge.
    _demo_red_refusal(session, version, admin)

    planning_service.set_lock(session, version, version.assignments[0].post_id, True, admin)
    planning_service.validate_version(session, version, admin)
    Clock.freeze(datetime(2026, 12, 30, 9, 0))
    planning_service.publish_version(session, version, admin)
    print(f"  planning publié : version {version.version_no}, "
          f"{len(version.assignments)} affectations")
    return version


def _demo_red_refusal(session, version, admin: User) -> None:
    for assignment in version.assignments:
        post = session.get(CoveragePost, assignment.post_id)
        for profile in session.execute(select(ProfessionalProfile)).scalars():
            if profile.status is not post.required_status:
                continue
            rejection = None
            from app.services import engine_bridge

            color = engine_bridge.current_color(
                session, profile.id, post.occurrence_id, post.line
            )
            if color is not Color.ROUGE:
                continue
            try:
                planning_service.manual_correction(
                    session, version, post, profile, admin, "tentative de démonstration"
                )
            except planning_service.HardConstraintError as exc:
                print(f"  correction manuelle sur rouge refusée : {exc}")
                return
    print("  (aucune date rouge candidate trouvée pour la démonstration de refus)")


# --------------------------------------------------------------------------- #
# 6. Reprises
# --------------------------------------------------------------------------- #


def _future_assignments(session, version, line: Line | None = None):
    rows = session.execute(
        select(Assignment, CoveragePost, GardeOccurrence)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .where(Assignment.schedule_version_id == version.id)
        .order_by(GardeOccurrence.start_at)
    ).all()
    out = []
    for assignment, post, occurrence in rows:
        if occurrence.start_at <= Clock.now():
            continue
        if line is not None and post.line is not line:
            continue
        out.append((assignment, post, occurrence))
    return out


def demo_handovers(session, version, admin: User) -> None:
    import json

    candidates = _future_assignments(session, version)

    # ---- Reprise verte avec plusieurs volontaires -------------------------- #
    played = 0
    for assignment, post, occurrence in candidates:
        requester = session.get(ProfessionalProfile, assignment.profile_id)
        try:
            request = handover_service.request_handover(
                session, assignment, requester, comment="Empêchement fictif."
            )
        except handover_service.HandoverError:
            continue
        wave = handover_service.open_wave(session, request, WaveKind.VERTE)
        if wave.solicited_count < 3:
            handover_service.cancel_request(session, request, admin)
            continue

        volunteers = sorted(
            s.profile_id
            for s in session.execute(
                select(handover_service.WaveSolicitation).where(
                    handover_service.WaveSolicitation.wave_id == wave.id
                )
            ).scalars()
        )
        solicited = volunteers
        for index, profile_id in enumerate(solicited):
            profile = session.get(ProfessionalProfile, profile_id)
            if index < 3:
                handover_service.submit_candidacy(session, wave, profile)
            else:
                handover_service.decline(session, wave, profile)
        print(
            f"  reprise verte : garde du {occurrence.local_date} ({post.line.value}) · "
            f"{wave.solicited_count} personnes sollicitées anonymement · 3 candidatures"
        )
        handover_service.advance(session, request)
        session.refresh(request)
        draw = session.execute(
            select(handover_service.Draw).where(handover_service.Draw.wave_id == wave.id)
        ).scalar_one_or_none()
        if draw is not None:
            proof = json.loads(draw.proof_json)
            winner = session.get(ProfessionalProfile, draw.winner_profile_id)
            print(
                f"    liste figée {proof['liste_figee']} · empreinte "
                f"{proof['empreinte_liste_valide'][:16]}…"
            )
            print(f"    engagement graine {proof['engagement_graine'][:16]}… → "
                  f"graine révélée {proof['graine_revelee'][:16]}…")
            print(f"    tirage : index {proof['index']} → {winner.code} · "
                  f"état de la demande : {request.state.value}")
            print(f"    la garde est désormais assurée par {winner.code} "
                  f"(anciennement {requester.code}) — attribution immédiatement officielle")
        played = 1
        break
    if not played:
        print("  (aucune garde éligible pour la démonstration de reprise verte)")

    # ---- Reprise orange : tout le monde refuse en vague verte --------------- #
    for assignment, post, occurrence in candidates:
        if assignment.busy_operation is not None:
            continue
        requester = session.get(ProfessionalProfile, assignment.profile_id)
        try:
            request = handover_service.request_handover(session, assignment, requester)
        except handover_service.HandoverError:
            continue
        green = handover_service.open_wave(session, request, WaveKind.VERTE)
        for solicitation in session.execute(
            select(handover_service.WaveSolicitation).where(
                handover_service.WaveSolicitation.wave_id == green.id
            )
        ).scalars():
            handover_service.decline(
                session, green, session.get(ProfessionalProfile, solicitation.profile_id)
            )
        handover_service.advance(session, request)
        session.refresh(request)
        orange = next((w for w in request.waves if w.kind is WaveKind.ORANGE), None)
        if orange is None or orange.solicited_count == 0:
            handover_service.cancel_request(session, request, admin)
            continue
        print(
            f"  reprise orange : aucune candidature verte → seconde vague ouverte à "
            f"{orange.solicited_count} personne(s) orange"
        )
        orange_solicited = sorted(
            s.profile_id
            for s in session.execute(
                select(handover_service.WaveSolicitation).where(
                    handover_service.WaveSolicitation.wave_id == orange.id
                )
            ).scalars()
        )
        for index, profile_id in enumerate(orange_solicited):
            profile = session.get(ProfessionalProfile, profile_id)
            if index < 2:
                handover_service.submit_candidacy(session, orange, profile)
            else:
                handover_service.decline(session, orange, profile)
        handover_service.advance(session, request)
        session.refresh(request)
        draw = session.execute(
            select(handover_service.Draw).where(handover_service.Draw.wave_id == orange.id)
        ).scalar_one_or_none()
        if draw is not None:
            winner = session.get(ProfessionalProfile, draw.winner_profile_id)
            print(f"    tirage orange → {winner.code} · état {request.state.value}")
        break

    # ---- Reprise échouée : refus dans les deux vagues ----------------------- #
    for assignment, post, occurrence in candidates:
        session.refresh(assignment)
        if assignment.busy_operation is not None:
            continue
        requester = session.get(ProfessionalProfile, assignment.profile_id)
        try:
            request = handover_service.request_handover(session, assignment, requester)
        except handover_service.HandoverError:
            continue
        for kind in (WaveKind.VERTE, WaveKind.ORANGE):
            wave = next((w for w in request.waves if w.kind is kind), None)
            if wave is None:
                handover_service.advance(session, request)
                session.refresh(request)
                wave = next((w for w in request.waves if w.kind is kind), None)
            if wave is None:
                continue
            for solicitation in session.execute(
                select(handover_service.WaveSolicitation).where(
                    handover_service.WaveSolicitation.wave_id == wave.id
                )
            ).scalars():
                handover_service.decline(
                    session, wave, session.get(ProfessionalProfile, solicitation.profile_id)
                )
            handover_service.advance(session, request)
            session.refresh(request)
        if request.state is HandoverState.ESCALADE:
            print(
                f"  reprise échouée : garde du {occurrence.local_date} · "
                "escalade vers les administrateurs, affectation initiale maintenue "
                f"({requester.code})"
            )
            break


# --------------------------------------------------------------------------- #
# 7. Échanges
# --------------------------------------------------------------------------- #


def demo_swaps(session, version, admin: User) -> None:
    rows = _future_assignments(session, version)
    by_signature: dict[tuple, list] = {}
    for assignment, post, occurrence in rows:
        session.refresh(assignment)
        if assignment.busy_operation is not None:
            continue
        garde_type = occurrence.garde_type
        signature = (
            post.line.value, garde_type.category_id, garde_type.count_weight,
            garde_type.exchange_class_id, garde_type.duration_class,
            occurrence.effective_mode.value,
        )
        by_signature.setdefault(signature, []).append(assignment)

    # Échange valide entre deux gardes de même nature.
    done = False
    for signature, assignments in by_signature.items():
        for i, a in enumerate(assignments):
            for b in assignments[i + 1:]:
                if a.profile_id == b.profile_id:
                    continue
                proposer = session.get(ProfessionalProfile, a.profile_id)
                other = session.get(ProfessionalProfile, b.profile_id)
                try:
                    proposal = swap_service.propose_swap(session, a, b, proposer)
                except swap_service.SwapError:
                    continue
                if proposal.state is not SwapState.PROPOSE:
                    continue
                swap_service.accept_swap(session, proposal, other)
                session.refresh(proposal)
                if proposal.state is SwapState.OFFICIEL:
                    print(
                        f"  échange valide : {a.post.occurrence.local_date} ↔ "
                        f"{b.post.occurrence.local_date} · {proposer.code} ↔ {other.code} · "
                        f"état {proposal.state.value} · compteurs inchangés"
                    )
                    done = True
                else:
                    print(f"  échange non abouti : {proposal.refusal_reason}")
                break
            if done:
                break
        if done:
            break
    if not done:
        print("  (aucun couple de gardes équivalentes disponible pour l'échange valide)")

    # Échange refusé entre deux gardes de nature différente.
    signatures = sorted(by_signature, key=lambda s: str(s))
    for i, sig_a in enumerate(signatures):
        for sig_b in signatures[i + 1:]:
            if sig_a[1] == sig_b[1] and sig_a[0] == sig_b[0] and sig_a[3] == sig_b[3]:
                continue
            list_a = [x for x in by_signature[sig_a] if x.busy_operation is None]
            list_b = [x for x in by_signature[sig_b] if x.busy_operation is None]
            if not list_a or not list_b:
                continue
            a, b = list_a[0], list_b[0]
            if a.profile_id == b.profile_id:
                continue
            proposer = session.get(ProfessionalProfile, a.profile_id)
            proposal = swap_service.propose_swap(session, a, b, proposer)
            print(f"  échange refusé : {proposal.refusal_reason}")
            return


# --------------------------------------------------------------------------- #


def main() -> None:
    Clock.reset()
    drop_all()
    create_all()
    session = SessionLocal()
    try:
        banner("1. SOCLE — comptes, catalogue, trimestre, quotas, exemptions")
        people = seed_people(session)
        year, quarter = seed_catalog(session)
        admin = people["admins"][0]
        seed_quotas(session, year, quarter, people, admin)
        session.commit()

        banner("2. PROJECTIONS — sans aucun effet opérationnel")
        demo_projections(session, admin, quarter)
        session.commit()

        banner("3-4. CAMPAGNE DE DÉSIDÉRATAS — non-répondant et disponibilité par défaut")
        demo_campaign(session, quarter, people, admin)
        session.commit()

        banner("5. GÉNÉRATION, CONTRÔLE HUMAIN ET PUBLICATION")
        version = demo_planning(session, quarter, admin)
        session.commit()

        banner("6. REPRISES — anonymat, collecte, tirage auditable")
        demo_handovers(session, version, admin)
        session.commit()

        banner("7. ÉCHANGES BILATÉRAUX")
        demo_swaps(session, version, admin)
        session.commit()

        banner("VÉRIFICATIONS FINALES")
        ok, problems = audit_service.verify_chain(session)
        print(f"  journal d'audit : chaîne intègre = {ok} ({len(problems)} anomalie(s))")
        from app.models import AuditEvent, Notification

        print(f"  {session.query(AuditEvent).count()} événements d'audit · "
              f"{session.query(Notification).count()} notifications simulées")
        summary = quota_service.summary(session, people["seniors"][0], year)
        print(f"  quotas {summary.profile_code} : cible {summary.total_target} · "
              f"réalisé+programmé {summary.total_done} · {summary.projection}")
        print("\nBase de démonstration prête : gardes.db")
    finally:
        session.close()
        Clock.reset()


if __name__ == "__main__":
    main()
