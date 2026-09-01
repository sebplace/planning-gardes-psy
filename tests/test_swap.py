"""Tests de l'échange bilatéral.

Couvre les exigences §22 : 34, 35, 46, 50.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import (
    AssignmentOrigin,
    AuditEvent,
    Color,
    ProfessionalProfile,
    SwapState,
)
from app.services import quota_service, swap_service
from app.services.clock import Clock
from tests.conftest import publish_plan


def _couple_equivalent(world, executable: bool = True):
    """Couple de gardes équivalentes, et si demandé réellement permutables
    (les contraintes fermes des deux médecins étant satisfaites)."""
    from app.services import engine_bridge

    session = world.session
    assignments = sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    )
    for i, a in enumerate(assignments):
        for b in assignments[i + 1:]:
            if a.profile_id == b.profile_id:
                continue
            if not swap_service.check_equivalence(a, b)[0]:
                continue
            if not executable:
                return a, b
            profil_a = session.get(ProfessionalProfile, a.profile_id)
            profil_b = session.get(ProfessionalProfile, b.profile_id)
            ignore = {a.id, b.id}
            if (
                engine_bridge.check_assignment(
                    session, b.post, profil_a, ignore_assignment_ids=ignore
                )
                is None
                and engine_bridge.check_assignment(
                    session, a.post, profil_b, ignore_assignment_ids=ignore
                )
                is None
            ):
                return a, b
    pytest.skip("Aucun couple de gardes équivalentes permutables dans ce jeu de test.")


def _couple_different(world):
    assignments = sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    )
    for i, a in enumerate(assignments):
        for b in assignments[i + 1:]:
            if a.profile_id == b.profile_id:
                continue
            ok, differences, _ = swap_service.check_equivalence(a, b)
            if not ok:
                return a, b, differences
    pytest.skip("Aucun couple de gardes de nature différente.")


# --------------------------------------------------------------------------- #
# Test 34 — échange valide entre gardes équivalentes
# --------------------------------------------------------------------------- #


def test_34_echange_officiel_apres_les_deux_accords_compteurs_inchanges(world):
    session = world.session
    publish_plan(world)
    a, b = _couple_equivalent(world)
    profil_a = session.get(ProfessionalProfile, a.profile_id)
    profil_b = session.get(ProfessionalProfile, b.profile_id)

    avant_a = quota_service.summary(session, profil_a, world.year).total_done
    avant_b = quota_service.summary(session, profil_b, world.year).total_done

    proposition = swap_service.propose_swap(session, a, b, profil_a)
    assert proposition.state is SwapState.PROPOSE, proposition.refusal_reason
    # Un seul accord ne suffit pas.
    assert a.profile_id == profil_a.id

    proposition = swap_service.accept_swap(session, proposition, profil_b)
    session.refresh(proposition)
    assert proposition.state is SwapState.OFFICIEL, proposition.refusal_reason
    assert proposition.executed_at is not None

    session.refresh(a)
    session.refresh(b)
    assert a.profile_id == profil_b.id
    assert b.profile_id == profil_a.id
    assert a.origin is AssignmentOrigin.ECHANGE
    assert a.busy_operation is None and b.busy_operation is None

    # Les compteurs restent inchangés puisque les deux gardes sont équivalentes.
    assert quota_service.summary(session, profil_a, world.year).total_done == avant_a
    assert quota_service.summary(session, profil_b, world.year).total_done == avant_b

    # La permutation des titulaires est journalisée.
    evenement = session.execute(
        select(AuditEvent).where(AuditEvent.action == "ECHANGE_OFFICIALISE")
    ).scalars().first()
    assert evenement is not None
    charge = json.loads(evenement.payload_json)
    assert charge["permutation"]["garde_a"]["avant"] == profil_a.code
    assert charge["permutation"]["garde_a"]["apres"] == profil_b.code
    assert charge["compteurs"] == "inchangés (gardes équivalentes)"


# --------------------------------------------------------------------------- #
# Test 35 — refus si la nature diffère
# --------------------------------------------------------------------------- #


def test_35_echange_refuse_si_la_nature_differe(world):
    session = world.session
    publish_plan(world)
    a, b, differences = _couple_different(world)
    profil_a = session.get(ProfessionalProfile, a.profile_id)

    proposition = swap_service.propose_swap(session, a, b, profil_a)
    assert proposition.state is SwapState.REFUSE
    assert "Gardes de nature différente" in proposition.refusal_reason
    assert "ouvrir volontairement une demande de reprise" in proposition.refusal_reason
    # Aucune reprise n'est déclenchée automatiquement.
    from app.models import HandoverRequest

    assert session.execute(select(HandoverRequest)).scalars().all() == []
    # Les titulaires n'ont pas changé.
    session.refresh(a)
    assert a.profile_id == profil_a.id


def test_35b_chaque_critere_d_equivalence_est_verifie(world):
    """La classe d'échange seule ne suffit jamais à déclarer une équivalence."""
    session = world.session
    publish_plan(world)
    a, b = _couple_equivalent(world)
    ok, _, payload = swap_service.check_equivalence(a, b)
    assert ok
    criteres = set(payload["garde_a"])
    assert criteres == {
        "ligne", "statut_exige", "categorie_comptable", "poids_de_decompte",
        "classe_echange", "classe_duree", "exigences_couverture", "mode_couverture",
    }

    # On casse un seul critère : l'échange devient impossible.
    # Un type jumeau, identique en tout sauf le poids de décompte.
    from app.models import GardeType

    origine = b.post.occurrence.garde_type
    jumeau = GardeType(
        code=origine.code + "_BIS",
        label=origine.label + " (variante de test)",
        module=origine.module,
        category_id=origine.category_id,
        default_coverage_mode=origine.default_coverage_mode,
        start_time=origine.start_time,
        end_time=origine.end_time,
        duration_hours=origine.duration_hours,
        duration_class=origine.duration_class,
        count_weight=origine.count_weight + 0.5,
        exchange_class_id=origine.exchange_class_id,
    )
    session.add(jumeau)
    session.flush()
    b.post.occurrence.garde_type_id = jumeau.id
    session.flush()
    session.refresh(b.post.occurrence)

    ok2, differences, _ = swap_service.check_equivalence(a, b)
    assert not ok2 and any("poids de décompte" in d for d in differences)

    b.post.occurrence.garde_type_id = origine.id
    session.flush()


# --------------------------------------------------------------------------- #
# Test 46 — état des gardes au moment de l'officialisation
# --------------------------------------------------------------------------- #


def test_46_refus_si_la_garde_n_est_plus_future(world):
    session = world.session
    publish_plan(world)
    a, b = _couple_equivalent(world)
    profil_a = session.get(ProfessionalProfile, a.profile_id)
    profil_b = session.get(ProfessionalProfile, b.profile_id)

    proposition = swap_service.propose_swap(session, a, b, profil_a)
    assert proposition.state is SwapState.PROPOSE

    # La garde A commence avant le second accord.
    Clock.freeze(a.post.occurrence.start_at + timedelta(hours=1))
    proposition = swap_service.accept_swap(session, proposition, profil_b)
    session.refresh(proposition)
    assert proposition.state is SwapState.REFUSE
    assert "n'est plus future" in proposition.refusal_reason
    session.refresh(a)
    assert a.profile_id == profil_a.id


def test_46b_refus_si_la_garde_change_de_titulaire(world):
    session = world.session
    publish_plan(world)
    a, b = _couple_equivalent(world)
    profil_a = session.get(ProfessionalProfile, a.profile_id)
    profil_b = session.get(ProfessionalProfile, b.profile_id)

    proposition = swap_service.propose_swap(session, a, b, profil_a)
    # Un autre processus change le titulaire de la garde A entre-temps.
    autre = next(
        p for p in world.seniors + world.assistants
        if p.id not in (profil_a.id, profil_b.id)
    )
    a.profile_id = autre.id
    session.flush()

    proposition = swap_service.accept_swap(session, proposition, profil_b)
    session.refresh(proposition)
    assert proposition.state is SwapState.REFUSE
    assert "détenue par la personne annoncée" in proposition.refusal_reason


# --------------------------------------------------------------------------- #
# Test 50 — revérification séparée pour chacun des deux médecins
# --------------------------------------------------------------------------- #


def test_50_reverification_separee_des_deux_medecins(world):
    session = world.session
    publish_plan(world)
    a, b = _couple_equivalent(world)
    profil_a = session.get(ProfessionalProfile, a.profile_id)
    profil_b = session.get(ProfessionalProfile, b.profile_id)

    proposition = swap_service.propose_swap(session, a, b, profil_a)
    # Le médecin A déclare un rouge sur la garde qu'il recevrait.
    world.set_color(profil_a, b.post.occurrence, Color.ROUGE)

    proposition = swap_service.accept_swap(session, proposition, profil_b)
    session.refresh(proposition)
    assert proposition.state is SwapState.REFUSE
    assert profil_a.code in proposition.refusal_reason
    assert "Indisponibilité rouge" in proposition.refusal_reason
    # L'autre médecin n'est pas mis en cause à tort.
    session.refresh(a)
    session.refresh(b)
    assert a.profile_id == profil_a.id and b.profile_id == profil_b.id
