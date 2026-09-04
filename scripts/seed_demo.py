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
    GardeWeightHistory,
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
from app.models import permissions  # noqa: E402
from app.services import (  # noqa: E402
    audit_service,
    campaign_service,
    catalog_service,
    counters_service,
    handover_service,
    period_quota_service,
    permission_service,
    planning_service,
    projection_service,
    quota_service,
    security,
    swap_service,
)
from app.services.clock import Clock  # noqa: E402

#: Quinze seniors fictifs. Effectif confirmé par le client le 03/09/2026.
SENIOR_NAMES = [
    "Alpha", "Bêta", "Gamma", "Delta", "Epsilon", "Zêta", "Êta",
    "Thêta", "Iota", "Kappa", "Lambda", "Mu", "Nu", "Xi", "Omicron",
]
#: Trois assistants fictifs, première garde 19/10/2026, dernière 03/10/2027 incluse.
ASSISTANT_NAMES = ["Pi", "Rhô", "Sigma"]

ASSISTANTS_DEBUT = date(2026, 10, 19)
ASSISTANTS_FIN = date(2027, 10, 3)  # inclus

# Jours fériés fictifs positionnés sur 2027 — administrables, jamais figés en dur.
HOLIDAYS_2027 = {
    date(2027, 1, 1), date(2027, 3, 29), date(2027, 5, 1), date(2027, 5, 6),
    date(2027, 5, 17), date(2027, 7, 21), date(2027, 8, 15), date(2027, 11, 1),
    date(2027, 11, 11), date(2027, 12, 25),
}

#: Quotité de temps de travail, en dixièmes. Donnée distincte de la pondération.
QUOTITES = [10, 10, 10, 10, 10, 10, 9, 9, 8, 8, 8, 6, 5, 5, 7]

#: Pondération **de garde**, en dixièmes, transmise par le client au 01/10/2026.
#: Somme attendue : 84/10. Les deux zéros correspondent à des seniors qui
#: n'assurent aucune garde sur la période.
PONDERATIONS_GARDE = [7, 6, 7, 8, 8, 0, 7, 8, 6, 0, 3, 6, 6, 7, 5]
PONDERATIONS_EFFET = date(2026, 10, 1)

#: Date d'effet des délégations de permissions dans le jeu de démonstration.
#: Volontairement antérieure, pour que les fonctions administratives soient en
#: vigueur quel que soit le jour de la présentation. C'est une donnée datée,
#: modifiable, sans rapport avec la date d'effet des pondérations.
PERMISSIONS_EFFET = date(2026, 1, 1)

#: Campagne du premier trimestre, dates transmises par le client.
CAMPAGNE_T1_OUVERTURE = datetime(2026, 11, 1, 8, 0)
CAMPAGNE_T1_RAPPEL = datetime(2026, 11, 15, 8, 0)
CAMPAGNE_T1_CLOTURE = datetime(2026, 12, 1, 23, 59)
CAMPAGNE_T1_PUBLICATION = datetime(2026, 12, 7, 12, 0)


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
        # Pondération **de garde**, distincte de la quotité, datée du 01/10/2026.
        session.add(
            GardeWeightHistory(
                profile_id=profile.id,
                start_date=PONDERATIONS_EFFET,
                weight_tenths=PONDERATIONS_GARDE[index - 1],
                note=(
                    "Pondération de garde transmise par le client, en dixièmes. "
                    "Aucune formule n'est appliquée : c'est une donnée datée."
                ),
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
        # Période d'activité exacte transmise par le client : première garde le
        # 19/10/2026, dernière le 03/10/2027 incluse.
        session.add(
            ActivityPeriod(
                profile_id=profile.id,
                start_date=ASSISTANTS_DEBUT,
                end_date=ASSISTANTS_FIN,
                reason="Contrat d'assistanat fictif",
            )
        )
        session.add(
            QuotiteHistory(
                profile_id=profile.id, start_date=ASSISTANTS_DEBUT, tenths=10
            )
        )
        # Les assistants n'assurent que la première ligne.
        session.add(
            Eligibility(
                profile_id=profile.id, line=Line.L2, eligible=False,
                comment="Un assistant n'est jamais en deuxième ligne.",
            )
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

    # Trois fonctions ouvrant l'accès administratif (arbitrage du 04/09/2026) :
    # responsable des gardes 1, responsable des gardes 2, chef de service. Elles
    # sont attribuées séparément, datées et journalisées, et gardent des
    # périmètres de ligne distincts.
    fonctions = [
        (people["seniors"][1], permissions.RESP_L1),
        (people["seniors"][2], permissions.RESP_L2),
        (people["seniors"][3], permissions.CHEF_SERVICE),
    ]
    # Trois permissions complémentaires, indépendantes des fonctions ci-dessus et
    # qui n'ouvrent à elles seules aucun accès administratif.
    complementaires = [
        (people["seniors"][3], permissions.GESTION_COMPTES),
        (people["seniors"][3], permissions.PUBLICATION),
        (people["seniors"][4], permissions.CONSULTATION_AUDIT),
    ]
    for profil, code in fonctions + complementaires:
        permission_service.grant(
            session,
            session.get(User, profil.user_id),
            code,
            admin,
            # Date d'effet volontairement antérieure au jeu de démonstration, pour
            # que les délégations soient réellement en vigueur pendant une
            # présentation. Elle reste une donnée datée et modifiable.
            start_date=PERMISSIONS_EFFET,
            comment=f"Attribution fictive : {permissions.LIBELLES[code]}.",
        )
    session.flush()

    print(
        "  fonctions administratives : "
        + ", ".join(
            f"{profil.code} = {permissions.LIBELLES[code]}"
            for profil, code in fonctions
        )
    )
    print(
        "  permissions complementaires : "
        + ", ".join(
            f"{profil.code} = {permissions.LIBELLES[code]}"
            for profil, code in complementaires
        )
    )
    porteurs = [
        f"{profil.code} ({permissions.LIGNES_SUPERVISEES[code][0]}"
        + (f" et {permissions.LIGNES_SUPERVISEES[code][1]}"
           if len(permissions.LIGNES_SUPERVISEES[code]) > 1 else "")
        + ")"
        for profil, code in fonctions
    ]
    medecins = people["seniors"] + people["assistants"]
    avec_fonction = {profil.code for profil, _ in fonctions}
    cumul_admin = {
        p.code for p in medecins if session.get(User, p.user_id).is_admin
    }
    sans_acces = [
        p.code
        for p in medecins
        if p.code not in avec_fonction and p.code not in cumul_admin
    ]
    print(
        f"  acces administratif a partir du {PERMISSIONS_EFFET:%d/%m/%Y} : "
        + ", ".join(porteurs)
        + f" ; plus {', '.join(sorted(cumul_admin))} (cumul medecin/administrateur)"
    )
    print(
        f"  {len(sans_acces)} medecin(s) sur {len(medecins)} restent non "
        "administrateurs, dont les porteurs des trois permissions "
        "complementaires ci-dessus"
    )

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

    # Plafond mensuel : le client ne l'a pas chiffré (03/09/2026). On enregistre
    # donc une ligne **vide** pour chaque statut, afin que l'absence soit visible
    # et alertée, sans jamais inventer une valeur.
    for status in (Status.SENIOR, Status.ASSISTANT):
        quota_service.set_monthly_cap(
            session, year, admin, status=status, max_per_month=None,
            comment=(
                "Valeur institutionnelle attendue. Aucune valeur n'a été devinée : "
                "les 5, 6 ou 7 utilisés en projection restent des hypothèses de "
                "simulation."
            ),
        )
    for alerte in quota_service.monthly_cap_alerts(session, year):
        print(f"    alerte plafond mensuel : {alerte}")

    # Quota de période des assistants : la période est réelle et unique, mais le
    # client n'a pas tranché entre 57 et 68. Le quota est donc enregistré comme
    # cible **non opposable**, avec son alerte.
    period_quota_service.set_period_quota(
        session,
        admin,
        code=period_quota_service.CODE_QUOTA_ASSISTANTS,
        label="Quota assistant sur la période 19/10/2026 - 03/10/2027",
        start_date=period_quota_service.ASSISTANTS_DEBUT,
        end_date=period_quota_service.ASSISTANTS_FIN,
        target=57.0,
        maximum=None,
        status=Status.ASSISTANT,
        comment=(
            "Cible de simulation. Le client n'a pas tranché entre 57 et 68 : "
            "aucun maximum n'est opposable tant que la valeur n'est pas validée."
        ),
    )
    for alerte in period_quota_service.alerts(session):
        print(f"    alerte quota de periode : {alerte}")


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
        name="Trimestre T1 2027 — 3 assistants",
        description="Scénario de référence, hypothèses de démonstration.",
        categories=tuple(
            CategoryVolume(code, labels[code], n, 0.66) for code, n in sorted(counts.items())
        ),
        assistants=AssistantGroup(count=3, guards_per_assistant=15),
        seniors=SeniorGroup(
            quotite_tenths=tuple(PONDERATIONS_GARDE),
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
        name="Trimestre T1 2027 — 1 assistant seulement",
        description="Variante tendue, sans conversion du mode B en mode A.",
        categories=params.categories,
        assistants=AssistantGroup(count=1, guards_per_assistant=6),
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

    # Les trois comparaisons demandées par le client : quota global et plafond
    # mensuel restent deux paramètres distincts.
    print("  comparaison quota global / plafond mensuel (hypothèses de simulation) :")
    for ligne in projection_service.comparer_scenarios_assistants(
        params.categories, params.seniors, nb_assistants=3
    ):
        print(
            f"    {ligne['scenario']} → capacité quota {ligne['capacite_quota']:.0f}, "
            f"capacité plafond {ligne['capacite_plafond']:.0f}, "
            f"saturation {ligne['saturation'] * 100:.1f} %, "
            f"contrainte active : {ligne['contrainte_active']}"
        )
        for alerte in ligne["alertes"]:
            if "marge de manœuvre" in alerte:
                print(f"      {alerte}")

    try:
        projection_service.promote_to_configuration(session, tight_scenario, admin, confirmed=False)
    except projection_service.ProjectionError as exc:
        print(f"  promotion sans confirmation refusée : {exc}")


# --------------------------------------------------------------------------- #
# 3-4. Campagne de désidératas
# --------------------------------------------------------------------------- #


def demo_campaign(session, quarter: Quarter, people: dict, admin: User) -> Campaign:
    """Campagne du premier trimestre, aux dates transmises par le client.

    Ouverture le 01/11, rappel le 15/11, clôture le 01/12, publication le 07/12.
    """
    opens_at = CAMPAGNE_T1_OUVERTURE
    deadline = CAMPAGNE_T1_CLOTURE
    Clock.freeze(opens_at)

    # Décalage du rappel unique demandé : 16 jours après l'ouverture, soit le 15/11.
    rappel_offset = (deadline.date() - CAMPAGNE_T1_RAPPEL.date()).days
    campaign = campaign_service.create_campaign(
        session, quarter, opens_at=opens_at, deadline_at=deadline, admin=admin,
        grace_period_hours=48, requirement=HolidayRequirement.VERT_ORANGE,
        reminder_offsets_days=str(rappel_offset),
    )
    campaign_service.open_campaign(session, campaign, admin)
    print(
        f"  campagne T1 ouverte le {opens_at:%d/%m/%Y}, rappel le "
        f"{CAMPAGNE_T1_RAPPEL:%d/%m/%Y}, clôture le {deadline:%d/%m/%Y}, "
        f"publication visée le {CAMPAGNE_T1_PUBLICATION:%d/%m/%Y} "
        f"({len(campaign.submissions)} personnes)"
    )

    occurrences = list(
        session.execute(
            select(GardeOccurrence).where(GardeOccurrence.quarter_id == quarter.id)
        ).scalars()
    )
    pairs_occurrences = _holiday_pair_occurrences(session, campaign)

    Clock.freeze(CAMPAGNE_T1_RAPPEL.replace(hour=23, minute=59))
    sent = campaign_service.send_due_reminders(session, campaign)
    print(f"  rappel du 15/11 envoyé à {sent} personne(s) non finalisée(s)")

    non_repondant = people["seniors"][6]  # SEN-07 ne répondra jamais
    assistant_ids = {a.id for a in people["assistants"]}
    rng = random.Random(20260901)
    for submission in campaign.submissions:
        if submission.profile_id == non_repondant.id:
            continue
        # Les assistants ne déclarent que vert ou rouge (jamais orange).
        assistant = submission.profile_id in assistant_ids
        for occurrence in occurrences:
            draw = rng.random()
            if assistant:
                color = Color.ROUGE if draw < 0.15 else Color.VERT
            else:
                color = Color.ROUGE if draw < 0.15 else (
                    Color.ORANGE if draw < 0.35 else Color.VERT
                )
            campaign_service.set_availability(session, submission, occurrence, color)
        # L'obligation liée aux paires de jours fériés ne concerne que les seniors :
        # elle n'est pas étendue aux assistants (arbitrage du client du 03/09/2026).
        if not assistant:
            for occurrence_ids in pairs_occurrences:
                if occurrence_ids:
                    occurrence = session.get(GardeOccurrence, sorted(occurrence_ids)[0])
                    campaign_service.set_availability(
                        session, submission, occurrence, Color.VERT
                    )
        campaign_service.validate_submission(session, submission)
    print(f"  {len(campaign.submissions) - 1} réponses validées · 1 non-répondant "
          f"({non_repondant.code})")
    print("  obligation « paires de jours fériés » appliquée aux seniors uniquement")

    Clock.freeze(deadline + timedelta(minutes=30))
    campaign_service.close_campaign(session, campaign, admin)
    print(f"  clôture du 01/12 atteinte → état {campaign.state.value} "
          "(génération bloquée)")

    blockers = planning_service.generation_blockers(session, quarter)
    for blocker in blockers:
        print(f"    blocage : {blocker}")

    ok, reasons = campaign_service.can_apply_default_availability(campaign)
    print(f"  conversion immédiate possible ? {ok} — {reasons}")

    Clock.freeze(CAMPAGNE_T1_CLOTURE + timedelta(hours=50))
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
    """Jours **fériés** de chaque paire applicable, la veille étant facultative.

    L'obligation porte sur le jour férié lui-même : il doit être déclaré vert.
    """
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
                and o.garde_type.code == campaign_service.CODE_JOUR_FERIE
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

    Clock.freeze(CAMPAGNE_T1_CLOTURE + timedelta(hours=51))
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
    Clock.freeze(CAMPAGNE_T1_PUBLICATION)
    planning_service.publish_version(session, version, admin)
    print(f"  planning publié le {CAMPAGNE_T1_PUBLICATION:%d/%m/%Y} : "
          f"version {version.version_no}, {len(version.assignments)} affectations")
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

    # ---- Reprise L1 : verts déclarés uniquement ---------------------------- #
    played = 0
    for assignment, post, occurrence in candidates:
        if post.line is not Line.L1:
            continue
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
            f"  reprise L1 : garde du {occurrence.local_date} ({post.line.value}) · "
            f"{wave.solicited_count} personne(s) explicitement verte(s) sollicitée(s) "
            "anonymement · 3 candidatures"
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
        print("  (aucune garde éligible pour la démonstration de reprise L1)")

    # ---- Reprise L2 : collecte unique, priorité au vert au tirage ---------- #
    for assignment, post, occurrence in candidates:
        session.refresh(assignment)
        if assignment.busy_operation is not None or post.line is not Line.L2:
            continue
        requester = session.get(ProfessionalProfile, assignment.profile_id)
        try:
            request = handover_service.request_handover(session, assignment, requester)
        except handover_service.HandoverError:
            continue
        wave = handover_service.open_wave(session, request, WaveKind.UNIQUE)
        if wave.solicited_count < 2:
            handover_service.cancel_request(session, request, admin)
            continue
        solicites = sorted(
            s.profile_id
            for s in session.execute(
                select(handover_service.WaveSolicitation).where(
                    handover_service.WaveSolicitation.wave_id == wave.id
                )
            ).scalars()
        )
        couleurs = {
            pid: handover_service.engine_bridge.current_color(
                session, pid, occurrence.id, post.line
            )
            for pid in solicites
        }
        verts = [p for p, c in couleurs.items() if c is Color.VERT]
        oranges = [p for p, c in couleurs.items() if c is Color.ORANGE]
        print(
            f"  reprise L2 : collecte unique auprès de {wave.solicited_count} "
            f"personne(s) — {len(verts)} vert(s) et {len(oranges)} orange, "
            "sollicités en même temps"
        )
        for profile_id in solicites:
            handover_service.submit_candidacy(
                session, wave, session.get(ProfessionalProfile, profile_id)
            )
        handover_service.advance(session, request)
        session.refresh(request)
        draw = session.execute(
            select(handover_service.Draw).where(handover_service.Draw.wave_id == wave.id)
        ).scalar_one_or_none()
        if draw is not None:
            preuve = json.loads(draw.proof_json)
            winner = session.get(ProfessionalProfile, draw.winner_profile_id)
            print(
                f"    palier retenu : {preuve['palier_prioritaire']} · "
                f"tirage entre {len(preuve['liste_tirable'])} volontaire(s) → "
                f"{winner.code} · état {request.state.value}"
            )
        break

    # ---- Reprise échouée : tout le monde refuse la collecte unique --------- #
    for assignment, post, occurrence in candidates:
        session.refresh(assignment)
        if assignment.busy_operation is not None:
            continue
        requester = session.get(ProfessionalProfile, assignment.profile_id)
        try:
            request = handover_service.request_handover(session, assignment, requester)
        except handover_service.HandoverError:
            continue
        handover_service.advance(session, request)
        session.refresh(request)
        wave = handover_service.current_wave(request)
        if wave is not None:
            for solicitation in session.execute(
                select(handover_service.WaveSolicitation).where(
                    handover_service.WaveSolicitation.wave_id == wave.id
                )
            ).scalars():
                handover_service.decline(
                    session, wave, session.get(ProfessionalProfile, solicitation.profile_id)
                )
        handover_service.run_until_settled(session, request)
        session.refresh(request)
        if request.state is HandoverState.ESCALADE:
            print(
                f"  reprise échouée : garde du {occurrence.local_date} · "
                "aucune seconde vague, escalade immédiate, titulaire publié "
                f"maintenu responsable ({requester.code})"
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
    from app.services import environment as envsvc

    # Verrou absolu : le seed destructif (drop_all) n'est autorisé qu'en
    # environnement 'demonstration' et jamais sur une base contenant des comptes
    # non fictifs.
    envsvc.assert_destructive_seed_allowed()
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
