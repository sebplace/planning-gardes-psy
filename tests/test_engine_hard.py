"""Tests du moteur et des contraintes fermes.

Couvre les exigences §22 : 1, 2, 3, 4, 5, 17, 18, 20, 21, 22, 29.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.engine import Color as EColor, CoverageMode, Line as ELine, Status as EStatus
from app.engine.solver import impossibility_report
from app.models import (
    ActivityPeriod,
    Color,
    CoveragePost,
    GardeOccurrence,
    GardeType,
    Line,
    Status,
)
from app.services import catalog_service, engine_bridge, planning_service
from app.services.clock import Clock, local_to_utc, wall_clock_window
from tests.conftest import close_and_prepare, publish_plan


def _solution_of(world, seed=4242, variants=1):
    close_and_prepare(world)
    Clock.freeze(datetime(2026, 12, 29, 14, 0))
    run = planning_service.run_engine(
        world.session, world.quarter, admin=world.admin, seed=seed, variants=variants
    )
    assert run.blocked_reason is None
    return run


# --------------------------------------------------------------------------- #
# Test 1 — aucune affectation automatique sur rouge
# --------------------------------------------------------------------------- #


def test_01_aucune_affectation_automatique_sur_rouge(world):
    occurrences = world.occurrences
    # Un rouge sur une personne pour la moitié des dates.
    rouges = set()
    for index, occurrence in enumerate(occurrences):
        if index % 2 == 0:
            world.set_color(world.seniors[0], occurrence, Color.ROUGE)
            rouges.add((world.seniors[0].id, occurrence.id))
        else:
            world.set_color(world.assistants[0], occurrence, Color.ROUGE)
            rouges.add((world.assistants[0].id, occurrence.id))
    run = _solution_of(world)
    for proposal in run.proposals:
        for item in proposal.items:
            post = world.session.get(CoveragePost, item.post_id)
            assert (item.profile_id, post.occurrence_id) not in rouges, (
                "Le moteur a affecté une personne sur une date rouge."
            )


# --------------------------------------------------------------------------- #
# Tests 2, 3, 5, 29 — lignes, statuts et modes de couverture
# --------------------------------------------------------------------------- #


def test_02_assistant_jamais_en_ligne_2(world):
    run = _solution_of(world)
    for proposal in run.proposals:
        for item in proposal.items:
            post = world.session.get(CoveragePost, item.post_id)
            profile = item.profile
            if post.line is Line.L2:
                assert profile.status is Status.SENIOR


def test_03_toute_ligne_2_est_assuree_par_un_senior(world):
    run = _solution_of(world)
    postes_l2 = [p for p in world.posts() if p.line is Line.L2]
    assert postes_l2, "Le jeu de test doit contenir des postes de deuxième ligne."
    for proposal in run.proposals:
        couverts = {i.post_id: i.profile.status for i in proposal.items}
        for post in postes_l2:
            assert couverts[post.id] is Status.SENIOR


def test_05_et_29_mode_a_sans_ligne_2_mode_b_complet(world):
    """Mode A : un seul poste L1 senior. Mode B : L1 assistant + L2 senior."""
    for occurrence in world.occurrences:
        lignes = sorted(p.line.value for p in occurrence.posts)
        statuts = {p.line: p.required_status for p in occurrence.posts}
        if occurrence.effective_mode is CoverageMode.A:
            assert lignes == ["L1"], "Le mode A ne doit jamais créer de deuxième ligne."
            assert statuts[Line.L1] is Status.SENIOR
        else:
            assert lignes == ["L1", "L2"]
            assert statuts[Line.L1] is Status.ASSISTANT
            assert statuts[Line.L2] is Status.SENIOR


def test_29_impossible_de_creer_une_l2_derriere_un_senior_de_l1(world):
    """La structure elle-même l'interdit : changer de mode reconstruit les postes."""
    occurrence = next(
        o for o in world.occurrences if o.effective_mode is CoverageMode.B
    )
    catalog_service.set_coverage_mode(world.session, occurrence, CoverageMode.A)
    world.session.refresh(occurrence)
    assert len(occurrence.posts) == 1
    assert occurrence.posts[0].line is Line.L1
    assert occurrence.posts[0].required_status is Status.SENIOR


# --------------------------------------------------------------------------- #
# Test 4 — un senior est compatible avec n'importe quel assistant
# --------------------------------------------------------------------------- #


def test_04_senior_compatible_avec_tout_assistant(world):
    """Aucun binôme assistant–senior fixe : la L2 reste ouverte à tout senior,
    quel que soit l'assistant présent en L1."""
    occurrence = next(o for o in world.occurrences if o.effective_mode is CoverageMode.B)
    poste_l2 = next(p for p in occurrence.posts if p.line is Line.L2)
    autorises = [
        senior
        for senior in world.seniors
        if engine_bridge.check_assignment(world.session, poste_l2, senior) is None
    ]
    assert len(autorises) == len(world.seniors), (
        "Tous les seniors doivent pouvoir couvrir la deuxième ligne."
    )


# --------------------------------------------------------------------------- #
# Test 17 — règle de repos ferme jamais violée
#
# Arbitrage du client du 03/09/2026 : l'interdiction universelle de 24 h entre
# toutes les gardes est retirée. Ce qui reste ferme est la durée de service
# **continu**, dérogeable uniquement par une demande explicite et datée.
# --------------------------------------------------------------------------- #


def test_17_regle_de_repos_ferme_jamais_violee(world):
    from app.models import DUREE_CONTINUE_MAX_HEURES

    run = _solution_of(world)
    for proposal in run.proposals:
        par_personne: dict[int, list[GardeOccurrence]] = {}
        for item in proposal.items:
            post = world.session.get(CoveragePost, item.post_id)
            par_personne.setdefault(item.profile_id, []).append(post.occurrence)
        for occurrences in par_personne.values():
            occurrences.sort(key=lambda o: o.start_at)
            # Aucune demande explicite dans cet univers : aucun bloc de service
            # continu ne peut donc dépasser le maximum.
            debut = fin = None
            for occurrence in occurrences:
                if fin is None or occurrence.start_at > fin:
                    debut, fin = occurrence.start_at, occurrence.end_at
                else:
                    fin = max(fin, occurrence.end_at)
                duree = (fin - debut).total_seconds() / 3600.0
                assert duree <= DUREE_CONTINUE_MAX_HEURES + 1e-6, (
                    f"Service continu de {duree:.1f} h alors que le maximum ferme "
                    f"est de {DUREE_CONTINUE_MAX_HEURES:.0f} h sans demande explicite."
                )


def test_17b_plus_aucune_interdiction_universelle_de_24h(session):
    """La règle ferme « 24 h entre deux gardes » a été retirée sur décision du client."""
    from sqlalchemy import select

    from app.models import Enforcement, RestRule
    from app.services import catalog_service

    catalog_service.ensure_reference_data(session)
    fermes = [
        r
        for r in session.execute(select(RestRule).where(RestRule.active)).scalars()
        if r.enforcement is Enforcement.FERME
    ]
    assert fermes == []
    ancienne = session.execute(
        select(RestRule).where(RestRule.code == "REPOS_MIN_24H")
    ).scalar_one_or_none()
    assert ancienne is None or ancienne.active is False


# --------------------------------------------------------------------------- #
# Test 18 — rapport d'impossibilité explicite
# --------------------------------------------------------------------------- #


def test_18_rapport_d_impossibilite_explicite(world):
    """Toutes les personnes rouges sur une date : le poste ne peut pas être pourvu,
    et le rapport nomme la contrainte ferme responsable."""
    import json

    occurrence = world.occurrences[0]
    for profile in world.seniors + world.assistants:
        world.set_color(profile, occurrence, Color.ROUGE)
    run = _solution_of(world)
    proposal = run.proposals[0]
    assert not proposal.feasible, "Le planning ne peut pas être complet."
    unfilled = json.loads(proposal.unfilled_json)
    assert unfilled
    concerne = [u for u in unfilled if u["date"] == occurrence.local_date.isoformat()]
    assert concerne, "Le poste bloqué doit apparaître dans le rapport."
    libelles = {e["libelle"] for u in concerne for e in u["exclusions"]}
    assert "Indisponibilité rouge déclarée par la personne" in libelles

    rapport = planning_service.impossibility(world.session, proposal)
    assert "Aucune contrainte ferme n'a été relâchée" in rapport["note"]

    # La validation d'un planning incomplet est refusée.
    version = planning_service.create_version_from_proposal(
        world.session, proposal, world.admin
    )
    with pytest.raises(planning_service.PlanningError):
        planning_service.validate_version(world.session, version, world.admin)


# --------------------------------------------------------------------------- #
# Test 20 — reproductibilité à graine identique
# --------------------------------------------------------------------------- #


def test_20_resultat_reproductible_avec_meme_graine(world):
    run_a = _solution_of(world, seed=777)
    resultat_a = {i.post_id: i.profile_id for i in run_a.proposals[0].items}
    empreinte_a = run_a.input_snapshot_hash

    run_b = planning_service.run_engine(
        world.session, world.quarter, admin=world.admin, seed=777, variants=1
    )
    resultat_b = {i.post_id: i.profile_id for i in run_b.proposals[0].items}

    assert resultat_a == resultat_b
    assert empreinte_a == run_b.input_snapshot_hash
    assert run_a.proposals[0].score_total == run_b.proposals[0].score_total

    run_c = planning_service.run_engine(
        world.session, world.quarter, admin=world.admin, seed=778, variants=1
    )
    assert run_c.input_snapshot_hash != empreinte_a, (
        "La graine fait partie de l'instantané reproductible."
    )


# --------------------------------------------------------------------------- #
# Test 21 — compte assistant expiré exclu
# --------------------------------------------------------------------------- #


def test_21_compte_assistant_expire_exclu(world):
    expire = world.assistants[0]
    periode = world.session.execute(
        select(ActivityPeriod).where(ActivityPeriod.profile_id == expire.id)
    ).scalar_one()
    periode.end_date = date(2026, 12, 31)  # expire avant le trimestre
    world.session.flush()

    run = _solution_of(world)
    for proposal in run.proposals:
        assert all(i.profile_id != expire.id for i in proposal.items), (
            "Un compte expiré ne doit jamais être affecté."
        )

    poste = next(p for p in world.posts() if p.required_status is Status.ASSISTANT)
    rejet = engine_bridge.check_assignment(world.session, poste, expire)
    assert rejet is not None
    assert rejet.constraint_code.startswith("H07")


# --------------------------------------------------------------------------- #
# Test 22 — minuit, changement d'heure, année bissextile
# --------------------------------------------------------------------------- #


def test_22a_garde_traversant_minuit(world):
    occurrence = world.occurrences[0]
    assert occurrence.end_at > occurrence.start_at
    assert occurrence.end_at.date() > occurrence.start_at.date() or (
        occurrence.duration_hours >= 12
    )


def test_22b_changement_d_heure_de_printemps_et_d_automne():
    """Une garde définie en horloge murale dure 23 h ou 25 h les jours de bascule."""
    # Passage à l'heure d'été : dernier dimanche de mars 2027 = 28 mars.
    _, _, printemps = wall_clock_window(date(2027, 3, 27), time(8, 0), time(8, 0), True)
    assert printemps == 23.0, printemps
    # Retour à l'heure d'hiver : dernier dimanche d'octobre 2027 = 31 octobre.
    _, _, automne = wall_clock_window(date(2027, 10, 30), time(8, 0), time(8, 0), True)
    assert automne == 25.0, automne
    # Journée ordinaire.
    _, _, normale = wall_clock_window(date(2027, 6, 12), time(8, 0), time(8, 0), True)
    assert normale == 24.0


def test_22c_annee_bissextile(world):
    """29 février 2028 : l'occurrence est générée et correctement bornée."""
    from app.models import Quarter

    annee = catalog_service.create_year(
        world.session, "2028", date(2028, 1, 1), date(2028, 12, 31)
    )
    trimestre = world.session.execute(
        select(Quarter).where(Quarter.year_id == annee.id, Quarter.index == 1)
    ).scalar_one()
    trimestre.start_date = date(2028, 2, 27)
    trimestre.end_date = date(2028, 3, 1)
    world.session.flush()
    créées = catalog_service.generate_occurrences(world.session, trimestre, holidays=set())
    dates = {o.local_date for o in créées}
    assert date(2028, 2, 29) in dates, "Le 29 février d'une année bissextile doit exister."
    bissextile = next(o for o in créées if o.local_date == date(2028, 2, 29))
    assert bissextile.end_at > bissextile.start_at
    assert bissextile.duration_hours > 0
