"""Tests P1.23, P1.24 et P2.10 : nouvelle logique de reprise.

Arbitrages du client du 03/09/2026 :

* reprise L1 : uniquement les personnes explicitement vertes et éligibles ;
* reprise L2 : une seule collecte verts + orange, revalidation à la clôture,
  tirage entre les verts valides, orange seulement si aucun vert valide ;
* les disponibilités par défaut non confirmées sont exclues de toutes les reprises ;
* sans solution, le titulaire publié reste responsable et les responsables sont
  alertés.

Données fictives.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import (
    Color,
    Draw,
    HandoverState,
    Line,
    ProfessionalProfile,
    WaveKind,
    WaveSolicitation,
)
from app.services import handover_service
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


def _demande_sur_ligne(world, ligne: Line):
    """Ouvre une demande de reprise sur la garde de cette ligne la plus « peuplée ».

    On retient l'affectation qui laisse le plus de personnes sollicitables une fois
    les contraintes fermes appliquées, afin que les tests portent sur la règle de
    couleur et non sur les hasards du petit univers de test.
    """
    session = world.session
    meilleur = None
    for affectation in sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    ):
        session.refresh(affectation)
        if affectation.busy_operation is not None:
            continue
        if affectation.post.line is not ligne:
            continue
        titulaire = session.get(ProfessionalProfile, affectation.profile_id)
        demande = handover_service.request_handover(session, affectation, titulaire)
        occurrence = affectation.post.occurrence
        for profil in world.seniors + world.assistants:
            if profil.id != titulaire.id:
                world.set_color(profil, occurrence, Color.VERT)
        session.flush()
        nombre = len(
            handover_service.eligible_profiles(session, demande, WaveKind.UNIQUE)
        )
        if meilleur is None or nombre > meilleur[2]:
            if meilleur is not None:
                handover_service.cancel_request(session, meilleur[0], world.admin)
            meilleur = (demande, titulaire, nombre)
        else:
            handover_service.cancel_request(session, demande, world.admin)
    if meilleur is None:
        pytest.skip(f"Aucune garde disponible sur la ligne {ligne.value}.")
    return meilleur[0], meilleur[1]


# --------------------------------------------------------------------------- #
# Choix du type de collecte selon la ligne
# --------------------------------------------------------------------------- #


def test_l1_ouvre_une_collecte_verte(world):
    publish_plan(world)
    demande, _ = _demande_sur_ligne(world, Line.L1)
    assert handover_service.wave_kind_for(demande.assignment.post) is WaveKind.VERTE
    handover_service.advance(world.session, demande)
    world.session.refresh(demande)
    assert demande.waves[0].kind is WaveKind.VERTE


def test_l2_ouvre_une_collecte_unique(world):
    publish_plan(world)
    demande, _ = _demande_sur_ligne(world, Line.L2)
    assert handover_service.wave_kind_for(demande.assignment.post) is WaveKind.UNIQUE
    handover_service.advance(world.session, demande)
    world.session.refresh(demande)
    assert demande.waves[0].kind is WaveKind.UNIQUE
    assert demande.state is HandoverState.COLLECTE_UNIQUE


# --------------------------------------------------------------------------- #
# Couleurs sollicitables
# --------------------------------------------------------------------------- #


def _peindre_sur_eligibles(world, demande, couleurs: list[Color]) -> dict[str, Color]:
    """Attribue les couleurs voulues à des personnes réellement sollicitables.

    On part d'un univers où tout le monde est vert, on relève qui est éligible une
    fois les contraintes fermes appliquées, puis on peint les couleurs sur ces
    personnes-là. Le test porte ainsi sur la couleur, jamais sur un hasard de
    planning.
    """
    session = world.session
    occurrence = demande.assignment.post.occurrence
    titulaire_id = demande.requester_profile_id
    for profil in world.seniors + world.assistants:
        if profil.id != titulaire_id:
            world.set_color(profil, occurrence, Color.VERT)
    session.flush()

    base = handover_service.eligible_profiles(session, demande, WaveKind.UNIQUE)
    if len(base) < len(couleurs):
        pytest.skip(
            f"{len(base)} personne(s) sollicitable(s) seulement, "
            f"{len(couleurs)} attendue(s)."
        )
    attribution: dict[str, Color] = {}
    for profil, couleur in zip(base, couleurs):
        world.set_color(profil, occurrence, couleur)
        attribution[profil.code] = couleur
    # Les personnes surnuméraires sont mises hors jeu, pour que la collecte
    # contienne exactement le mélange de couleurs voulu par le test.
    for profil in base[len(couleurs):]:
        world.set_color(profil, occurrence, Color.ROUGE)
    session.flush()
    return attribution


def test_l1_ne_sollicite_que_les_verts_explicites(world):
    session = world.session
    publish_plan(world)
    demande, titulaire = _demande_sur_ligne(world, Line.L1)
    occurrence = demande.assignment.post.occurrence

    for profil in world.seniors + world.assistants:
        if profil.id != titulaire.id:
            world.set_color(profil, occurrence, Color.VERT)
    session.flush()
    base = handover_service.eligible_profiles(session, demande, WaveKind.VERTE)
    if len(base) < 2:
        pytest.skip("univers trop petit")
    world.set_color(base[0], occurrence, Color.DISPO_DEFAUT)
    session.flush()

    eligibles = handover_service.eligible_profiles(session, demande, WaveKind.VERTE)
    codes = {p.code for p in eligibles}
    assert base[0].code not in codes
    assert base[1].code in codes
    for profil in eligibles:
        assert world.color_of(profil, occurrence) is Color.VERT


def test_l2_sollicite_verts_et_orange_mais_jamais_la_dispo_par_defaut(world):
    session = world.session
    publish_plan(world)
    demande, _ = _demande_sur_ligne(world, Line.L2)
    attribution = _peindre_sur_eligibles(
        world, demande, [Color.VERT, Color.ORANGE, Color.DISPO_DEFAUT]
    )
    par_couleur = {couleur: code for code, couleur in attribution.items()}

    codes = {
        p.code
        for p in handover_service.eligible_profiles(session, demande, WaveKind.UNIQUE)
    }
    assert par_couleur[Color.VERT] in codes
    assert par_couleur[Color.ORANGE] in codes
    assert par_couleur[Color.DISPO_DEFAUT] not in codes, (
        "La disponibilité par défaut non confirmée est exclue de toutes les reprises."
    )


def test_la_dispo_par_defaut_est_exclue_des_deux_types_de_collecte(world):
    session = world.session
    publish_plan(world)
    demande, titulaire = _demande_sur_ligne(world, Line.L2)
    occurrence = demande.assignment.post.occurrence
    for profil in world.seniors + world.assistants:
        if profil.id != titulaire.id:
            world.set_color(profil, occurrence, Color.DISPO_DEFAUT)
    session.flush()
    for kind in (WaveKind.VERTE, WaveKind.UNIQUE):
        assert handover_service.eligible_profiles(session, demande, kind) == []


# --------------------------------------------------------------------------- #
# Priorité au vert au moment du tirage
# --------------------------------------------------------------------------- #


def _collecte_unique_avec(world, couleurs: list[Color]):
    """Ouvre une collecte unique L2 après avoir posé les couleurs demandées."""
    session = world.session
    publish_plan(world)
    demande, _ = _demande_sur_ligne(world, Line.L2)
    attribution = _peindre_sur_eligibles(world, demande, couleurs)
    wave = handover_service.open_wave(session, demande, WaveKind.UNIQUE)
    return demande, wave, attribution


def test_le_tirage_ne_porte_que_sur_les_verts_quand_il_en_existe(world):
    session = world.session
    demande, wave, attribution = _collecte_unique_avec(
        world, [Color.ORANGE, Color.VERT, Color.ORANGE]
    )
    solicites = _solicites(session, wave)
    assert len(solicites) >= 2

    for profil in solicites:
        handover_service.submit_candidacy(session, wave, profil)
    tirage = handover_service.close_and_draw(session, wave)
    assert tirage is not None

    preuve = json.loads(tirage.proof_json)
    assert preuve["palier_prioritaire"] == "VERT"
    assert preuve["liste_tirable"] == preuve["verts_valides"]
    assert preuve["orange_valides"], "des orange existaient bien dans la collecte"
    gagnant = session.get(ProfessionalProfile, tirage.winner_profile_id)
    assert attribution[gagnant.code] is Color.VERT


def test_les_orange_ne_sont_tires_qu_en_l_absence_de_vert(world):
    session = world.session
    demande, wave, attribution = _collecte_unique_avec(
        world, [Color.ORANGE, Color.ORANGE]
    )
    solicites = _solicites(session, wave)
    for profil in solicites:
        handover_service.submit_candidacy(session, wave, profil)
    tirage = handover_service.close_and_draw(session, wave)
    assert tirage is not None

    preuve = json.loads(tirage.proof_json)
    assert preuve["palier_prioritaire"] == "ORANGE"
    assert preuve["verts_valides"] == []
    assert preuve["liste_tirable"] == preuve["orange_valides"]
    gagnant = session.get(ProfessionalProfile, tirage.winner_profile_id)
    assert attribution[gagnant.code] is Color.ORANGE


def test_une_seule_collecte_meme_avec_des_orange(world):
    """La priorité au vert ne réintroduit aucune seconde vague."""
    session = world.session
    demande, wave, _ = _collecte_unique_avec(world, [Color.VERT, Color.ORANGE])
    for profil in _solicites(session, wave):
        handover_service.submit_candidacy(session, wave, profil)
    handover_service.run_until_settled(session, demande)
    session.refresh(demande)
    assert len(demande.waves) == 1
    assert demande.state is HandoverState.ATTRIBUEE


def test_la_preuve_documente_la_regle_de_priorite(world):
    session = world.session
    demande, wave, _ = _collecte_unique_avec(world, [Color.VERT, Color.ORANGE])
    for profil in _solicites(session, wave):
        handover_service.submit_candidacy(session, wave, profil)
    tirage = handover_service.close_and_draw(session, wave)
    preuve = json.loads(tirage.proof_json)
    assert "collecte unique" in preuve["regle_de_priorite"]
    assert "absence totale de vert valide" in preuve["regle_de_priorite"]


# --------------------------------------------------------------------------- #
# Revalidation à la clôture
# --------------------------------------------------------------------------- #


def test_un_vert_devenu_orange_bascule_de_palier(world):
    """La couleur retenue est celle constatée **à la clôture**, pas au dépôt."""
    session = world.session
    demande, wave, _ = _collecte_unique_avec(world, [Color.VERT, Color.ORANGE])
    solicites = _solicites(session, wave)
    for profil in solicites:
        handover_service.submit_candidacy(session, wave, profil)

    occurrence = demande.assignment.post.occurrence
    for profil in solicites:
        world.set_color(profil, occurrence, Color.ORANGE)
    session.flush()

    tirage = handover_service.close_and_draw(session, wave)
    preuve = json.loads(tirage.proof_json)
    assert preuve["palier_prioritaire"] == "ORANGE"
    assert preuve["verts_valides"] == []


def test_un_candidat_devenu_dispo_par_defaut_est_exclu(world):
    session = world.session
    demande, wave, _ = _collecte_unique_avec(world, [Color.VERT, Color.VERT])
    solicites = _solicites(session, wave)
    assert len(solicites) >= 2
    for profil in solicites:
        handover_service.submit_candidacy(session, wave, profil)

    occurrence = demande.assignment.post.occurrence
    world.set_color(solicites[0], occurrence, Color.DISPO_DEFAUT)
    session.flush()

    tirage = handover_service.close_and_draw(session, wave)
    assert tirage is not None
    exclusions = json.loads(tirage.excluded_json)
    motifs = " ".join(e["motif"] for e in exclusions)
    assert "par défaut" in motifs
    assert tirage.winner_profile_id != solicites[0].id


# --------------------------------------------------------------------------- #
# Absence de solution
# --------------------------------------------------------------------------- #


def test_sans_volontaire_le_titulaire_reste_responsable(world):
    session = world.session
    publish_plan(world)
    demande, titulaire = _demande_sur_ligne(world, Line.L2)
    occurrence = demande.assignment.post.occurrence
    for profil in world.seniors + world.assistants:
        if profil.id != titulaire.id:
            world.set_color(profil, occurrence, Color.ROUGE)
    session.flush()

    handover_service.run_until_settled(session, demande)
    session.refresh(demande)
    assert demande.state is HandoverState.ESCALADE
    assert demande.assignment.profile_id == titulaire.id
    assert session.execute(select(Draw)).first() is None
