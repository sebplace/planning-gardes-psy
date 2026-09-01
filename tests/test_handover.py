"""Tests des reprises : anonymat, vagues, collecte, gel, tirage, atomicité.

Couvre les exigences §22 : 12, 13, 14, 15, 32, 33, 36, 40, 41, 42, 43, 44, 45,
48, 49, 51, 52.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    Assignment,
    AssignmentOrigin,
    CandidacyState,
    Color,
    Draw,
    HandoverState,
    Notification,
    ProfessionalProfile,
    QuotaAdjustment,
    WaveKind,
    WaveSolicitation,
    WaveState,
)
from app.services import handover_service, swap_service
from app.services.clock import Clock
from tests.conftest import publish_plan


def _solicites(session, wave):
    return [
        session.get(ProfessionalProfile, s.profile_id)
        for s in session.execute(
            select(WaveSolicitation)
            .where(WaveSolicitation.wave_id == wave.id)
            .order_by(WaveSolicitation.profile_id)
        ).scalars()
    ]


def _ouvrir(world, assignment=None, minimum_solicites: int = 1):
    """Ouvre une demande de reprise sur une garde future et sa vague verte."""
    session = world.session
    candidates = sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    )
    for candidate in candidates:
        session.refresh(candidate)
        if assignment is not None and candidate.id != assignment.id:
            continue
        if candidate.busy_operation is not None:
            continue
        titulaire = session.get(ProfessionalProfile, candidate.profile_id)
        demande = handover_service.request_handover(session, candidate, titulaire)
        wave = handover_service.open_wave(session, demande, WaveKind.VERTE)
        if wave.solicited_count >= minimum_solicites:
            return demande, wave, titulaire
        handover_service.cancel_request(session, demande, world.admin)
    pytest.skip("Aucune garde ne dispose d'assez de personnes sollicitables.")


# --------------------------------------------------------------------------- #
# Tests 12, 49, 52 — anonymat et exclusion du demandeur
# --------------------------------------------------------------------------- #


def test_12_49_52_anonymat_et_exclusion_du_demandeur(world):
    session = world.session
    publish_plan(world)
    demande, wave, titulaire = _ouvrir(world, minimum_solicites=2)

    # 49 — le demandeur est exclu de sa propre vague.
    solicites = _solicites(session, wave)
    assert titulaire not in solicites

    # 12 — la sollicitation est anonyme.
    messages = list(
        session.execute(
            select(Notification).where(Notification.kind == "REPRISE_SOLLICITATION")
        ).scalars()
    )
    assert messages
    for message in messages:
        assert message.anonymised is True
        assert titulaire.code not in message.body
        assert titulaire.code not in message.subject

    # 52 — identité masquée pour un tiers tant que l'attribution n'est pas officialisée.
    tiers = next(p for p in solicites if p.id != titulaire.id)
    assert handover_service.requester_visible_to(world.user_of(tiers), demande) is False
    assert handover_service.requester_visible_to(world.admin, demande) is True

    for profile in solicites:
        handover_service.submit_candidacy(session, wave, profile)
    handover_service.advance(session, demande)
    session.refresh(demande)
    assert demande.state is HandoverState.ATTRIBUEE
    # Après l'attribution officialisée, l'identité redevient visible.
    assert handover_service.requester_visible_to(world.user_of(tiers), demande) is True


# --------------------------------------------------------------------------- #
# Tests 14, 15, 33, 40, 43, 44, 45 — collecte, gel, tirage, effets
# --------------------------------------------------------------------------- #


def test_14_40_44_45_collecte_gel_tirage_et_effets(world):
    session = world.session
    publish_plan(world)
    demande, wave, titulaire = _ouvrir(world, minimum_solicites=2)
    solicites = _solicites(session, wave)
    volontaires = solicites[:2]

    for profile in volontaires:
        handover_service.submit_candidacy(session, wave, profile)
    for profile in solicites[2:]:
        handover_service.decline(session, wave, profile)

    # 40 — le titulaire initial reste officiellement responsable pendant la collecte.
    session.refresh(demande.assignment)
    assert demande.assignment.profile_id == titulaire.id
    assert demande.state is HandoverState.COLLECTE_VERTE

    handover_service.advance(session, demande)
    session.refresh(demande)

    draw = session.execute(select(Draw).where(Draw.wave_id == wave.id)).scalar_one()
    preuve = json.loads(draw.proof_json)

    # 14 — toutes les candidatures sont collectées et figées avant l'attribution.
    assert sorted(preuve["liste_figee"]) == sorted(
        c.id for c in wave.candidacies
    )
    assert wave.frozen_at is not None and wave.frozen_at <= draw.executed_at

    # 33 — tirage côté serveur, résultat appartenant exactement à la liste figée.
    assert draw.winner_candidacy_id in preuve["liste_valide"]
    assert hashlib.sha256(draw.server_seed.encode()).hexdigest() == draw.seed_commitment
    recompute = hmac.new(
        draw.server_seed.encode(), preuve["empreinte_liste_valide"].encode(), hashlib.sha256
    ).hexdigest()
    assert recompute == preuve["hmac"]
    assert sorted(preuve["liste_valide"])[preuve["index"]] == draw.winner_candidacy_id

    # 44 — planning, quota, historique et clôture mis à jour ensemble.
    session.refresh(demande.assignment)
    assert demande.assignment.profile_id == draw.winner_profile_id
    assert demande.assignment.origin is AssignmentOrigin.REPRISE
    assert demande.state is HandoverState.ATTRIBUEE
    assert demande.closed_at is not None
    assert demande.assignment.busy_operation is None
    ajustements = list(
        session.execute(
            select(QuotaAdjustment).where(QuotaAdjustment.profile_id == titulaire.id)
        ).scalars()
    )
    assert ajustements and "reporté" in ajustements[0].reason

    # 45 — une seule notification de clôture par candidat non retenu.
    perdants = [
        c for c in wave.candidacies if c.id != draw.winner_candidacy_id
    ]
    for candidacy in perdants:
        assert candidacy.state is CandidacyState.NON_RETENUE
        messages = list(
            session.execute(
                select(Notification).where(
                    Notification.kind == "REPRISE_TIRAGE_NON_RETENU",
                    Notification.recipient_profile_id == candidacy.profile_id,
                )
            ).scalars()
        )
        assert len(messages) == 1


def test_14b_candidature_unique_passe_par_le_meme_evenement(world):
    """Avec une seule candidature valide, l'attribution résulte du même
    événement auditable : il n'y a simplement pas d'aléa utile."""
    session = world.session
    publish_plan(world)
    demande, wave, titulaire = _ouvrir(world, minimum_solicites=2)
    solicites = _solicites(session, wave)
    handover_service.submit_candidacy(session, wave, solicites[0])
    for profile in solicites[1:]:
        handover_service.decline(session, wave, profile)

    handover_service.advance(session, demande)
    draw = session.execute(select(Draw).where(Draw.wave_id == wave.id)).scalar_one()
    assert draw.single_candidate is True
    assert draw.winner_profile_id == solicites[0].id
    assert draw.executed_at is not None
    session.refresh(demande)
    assert demande.state is HandoverState.ATTRIBUEE


def test_33_un_seul_tirage_officiel_possible(world):
    session = world.session
    publish_plan(world)
    demande, wave, _ = _ouvrir(world, minimum_solicites=2)
    for profile in _solicites(session, wave):
        handover_service.submit_candidacy(session, wave, profile)
    handover_service.advance(session, demande)

    # Toute relance est refusée : la vague n'est plus ouverte.
    with pytest.raises(handover_service.HandoverError):
        handover_service.close_and_draw(session, wave)
    assert (
        len(list(session.execute(select(Draw).where(Draw.wave_id == wave.id)).scalars()))
        == 1
    )


def test_15_43_vitesse_de_reponse_sans_effet_et_resultat_immediatement_officiel(world):
    """L'ordre et la vitesse des réponses n'influencent pas le tirage,
    et le résultat est officiel sans validation administrative."""
    session = world.session
    publish_plan(world)

    # Pour disposer d'assez de tirages exploitables, on neutralise la règle de repos
    # (elle limite fortement le nombre de personnes sollicitables dans ce petit univers).
    # La propriété testée ici — l'équité du tirage — n'en dépend pas.
    from app.models import RestRule

    for rule in session.execute(select(RestRule)).scalars():
        rule.active = False
    session.flush()

    premiers_gagnants = 0
    essais = 0
    for assignment in sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    ):
        if essais >= 16:
            break
        session.refresh(assignment)
        if assignment.busy_operation is not None:
            continue
        titulaire = session.get(ProfessionalProfile, assignment.profile_id)
        demande = handover_service.request_handover(session, assignment, titulaire)
        wave = handover_service.open_wave(session, demande, WaveKind.VERTE)
        solicites = _solicites(session, wave)
        if len(solicites) < 2:
            handover_service.cancel_request(session, demande, world.admin)
            continue
        premier, second = solicites[0], solicites[1]
        handover_service.submit_candidacy(session, wave, premier)
        handover_service.submit_candidacy(session, wave, second)
        for profile in solicites[2:]:
            handover_service.decline(session, wave, profile)
        handover_service.advance(session, demande)
        session.refresh(demande)
        assert demande.state is HandoverState.ATTRIBUEE, (
            "Le résultat du tirage est immédiatement officiel."
        )
        draw = session.execute(select(Draw).where(Draw.wave_id == wave.id)).scalar_one()
        # La liste utilisée par le tirage est triée : l'ordre de dépôt n'y figure pas.
        preuve = json.loads(draw.proof_json)
        assert preuve["liste_valide"] == sorted(preuve["liste_valide"])
        if draw.winner_profile_id == premier.id:
            premiers_gagnants += 1
        essais += 1

    assert essais >= 8, "Trop peu d'essais exploitables pour conclure."
    assert 0 < premiers_gagnants < essais, (
        f"Le premier à répondre a gagné {premiers_gagnants}/{essais} fois : "
        "la vitesse de réponse semble procurer un avantage."
    )


# --------------------------------------------------------------------------- #
# Tests 13, 41, 42 — vagues, revérification, candidature tardive
# --------------------------------------------------------------------------- #


def test_13_42_vague_verte_avant_orange(world):
    session = world.session
    publish_plan(world)
    demande, wave, titulaire = _ouvrir(world, minimum_solicites=2)
    solicites = _solicites(session, wave)

    # Une personne passe en orange : elle quitte la vague verte.
    orange = solicites[-1]
    occurrence = demande.assignment.post.occurrence
    world.set_color(orange, occurrence, Color.ORANGE)

    # Tout le monde refuse en vague verte.
    for profile in solicites:
        handover_service.decline(session, wave, profile)
    handover_service.advance(session, demande)
    session.refresh(demande)

    kinds = [w.kind for w in sorted(demande.waves, key=lambda w: w.id)]
    assert kinds[0] is WaveKind.VERTE
    if len(kinds) > 1:
        assert kinds[1] is WaveKind.ORANGE
        assert demande.state in (HandoverState.COLLECTE_ORANGE, HandoverState.ESCALADE)
        orange_wave = demande.waves[-1]
        if orange_wave.state is WaveState.OUVERTE:
            assert orange in _solicites(session, orange_wave)
    else:
        assert demande.state is HandoverState.ESCALADE

    # 42 — candidature tardive rejetée sur une vague déjà close.
    with pytest.raises(handover_service.HandoverError):
        handover_service.submit_candidacy(session, wave, solicites[0])


def test_42b_pas_de_vague_orange_si_une_candidature_verte_est_valide(world):
    session = world.session
    publish_plan(world)
    demande, wave, _ = _ouvrir(world, minimum_solicites=2)
    solicites = _solicites(session, wave)
    handover_service.submit_candidacy(session, wave, solicites[0])
    for profile in solicites[1:]:
        handover_service.decline(session, wave, profile)
    handover_service.advance(session, demande)
    session.refresh(demande)
    assert [w.kind for w in demande.waves] == [WaveKind.VERTE]
    assert demande.state is HandoverState.ATTRIBUEE


def test_41_reverification_apres_gel_un_rouge_exclut(world):
    session = world.session
    publish_plan(world)
    demande, wave, _ = _ouvrir(world, minimum_solicites=2)
    solicites = _solicites(session, wave)
    a, b = solicites[0], solicites[1]
    handover_service.submit_candidacy(session, wave, a)
    handover_service.submit_candidacy(session, wave, b)
    for profile in solicites[2:]:
        handover_service.decline(session, wave, profile)

    # a déclare un rouge après avoir candidaté : exclusion immédiate au moment du gel.
    world.set_color(a, demande.assignment.post.occurrence, Color.ROUGE)

    handover_service.advance(session, demande)
    draw = session.execute(select(Draw).where(Draw.wave_id == wave.id)).scalar_one()
    exclusions = json.loads(draw.excluded_json)
    assert any(e["profil"] == a.code and "Rouge" in e["motif"] for e in exclusions)
    assert draw.winner_profile_id == b.id
    candidature_a = next(c for c in wave.candidacies if c.profile_id == a.id)
    assert candidature_a.state is CandidacyState.EXCLUE


# --------------------------------------------------------------------------- #
# Test 32 — fenêtres et rappels adaptatifs
# --------------------------------------------------------------------------- #


def test_32_fenetres_adaptatives_selon_la_proximite(world):
    session = world.session
    publish_plan(world)
    debut = datetime(2027, 1, 10, 20, 0)

    Clock.freeze(debut - timedelta(hours=6))
    proche = handover_service.urgency_tier(session, debut)
    Clock.freeze(debut - timedelta(hours=24))
    moyen = handover_service.urgency_tier(session, debut)
    Clock.freeze(debut - timedelta(days=4))
    lointain = handover_service.urgency_tier(session, debut)
    Clock.freeze(debut - timedelta(days=30))
    tres_lointain = handover_service.urgency_tier(session, debut)

    fenetres = [
        proche["window_minutes"], moyen["window_minutes"],
        lointain["window_minutes"], tres_lointain["window_minutes"],
    ]
    assert fenetres == sorted(fenetres), (
        "La fenêtre doit se raccourcir à mesure que la garde approche."
    )
    assert max(proche["reminders_minutes"]) < max(tres_lointain["reminders_minutes"])


def test_32b_rappels_sans_doublon(world):
    session = world.session
    publish_plan(world)
    demande, wave, _ = _ouvrir(world, minimum_solicites=1)
    plan = json.loads(wave.reminder_plan_json)
    assert plan
    Clock.freeze(wave.opens_at + timedelta(minutes=plan[0] + 1))
    premier = handover_service.send_due_reminders(session, wave)
    second = handover_service.send_due_reminders(session, wave)
    assert premier > 0
    assert second == 0, "Un rappel déjà envoyé ne doit jamais être dupliqué."


# --------------------------------------------------------------------------- #
# Tests 36, 48, 51 — atomicité et opérations concurrentes
# --------------------------------------------------------------------------- #


def test_36_deux_reprises_concurrentes_sur_la_meme_garde(world):
    session = world.session
    publish_plan(world)
    demande, wave, titulaire = _ouvrir(world, minimum_solicites=1)
    with pytest.raises(handover_service.HandoverError):
        handover_service.request_handover(session, demande.assignment, titulaire)


def test_48_concurrence_entre_reprise_et_echange(world):
    """Une même garde ne peut pas participer simultanément à une reprise et à un échange."""
    session = world.session
    publish_plan(world)
    assignments = sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    )
    couple = None
    for i, a in enumerate(assignments):
        for b in assignments[i + 1:]:
            if a.profile_id == b.profile_id:
                continue
            if swap_service.check_equivalence(a, b)[0]:
                couple = (a, b)
                break
        if couple:
            break
    assert couple is not None, "Le jeu de test doit contenir deux gardes équivalentes."
    a, b = couple
    titulaire = session.get(ProfessionalProfile, a.profile_id)

    handover_service.request_handover(session, a, titulaire)
    with pytest.raises(swap_service.SwapError) as exc:
        swap_service.propose_swap(session, a, b, titulaire)
    assert "autre opération" in str(exc.value)


def test_51_annulation_et_tirage_concurrents_un_seul_etat_final(world):
    session = world.session
    publish_plan(world)

    # (a) Annulation avant le tirage : la demande est annulée, aucun tirage possible.
    demande, wave, _ = _ouvrir(world, minimum_solicites=2)
    solicites = _solicites(session, wave)
    handover_service.submit_candidacy(session, wave, solicites[0])
    assert handover_service.cancel_request(session, demande, world.admin) is True
    session.refresh(demande)
    assert demande.state is HandoverState.ANNULEE
    with pytest.raises(handover_service.HandoverError):
        handover_service.close_and_draw(session, wave)
    session.refresh(demande)
    assert demande.state is HandoverState.ANNULEE

    # (b) Annulation après le tirage : refusée, l'attribution reste le seul état final.
    demande2, wave2, _ = _ouvrir(world, minimum_solicites=2)
    solicites2 = _solicites(session, wave2)
    for profile in solicites2:
        handover_service.submit_candidacy(session, wave2, profile)
    handover_service.advance(session, demande2)
    session.refresh(demande2)
    assert demande2.state is HandoverState.ATTRIBUEE
    assert handover_service.cancel_request(session, demande2, world.admin) is False
    session.refresh(demande2)
    assert demande2.state is HandoverState.ATTRIBUEE
