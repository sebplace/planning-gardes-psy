"""Tests P1.8 et P1.26 : repos, récupération et demandes explicites.

Arbitrages du client du 03/09/2026. Données fictives.
Voir docs/AUDIT_DIFFERENTIEL.md.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.engine.types import H_DUREE_CONTINUE
from app.models import (
    DUREE_CONTINUE_MAX_HEURES,
    Assignment,
    Color,
    CoveragePost,
    Enforcement,
    GardeOccurrence,
    Line,
    RecoveryProposal,
    RestRule,
    Status,
)
from app.services import catalog_service, engine_bridge, rest_service
from app.services.clock import Clock
from conftest import publish_plan


# --------------------------------------------------------------------------- #
# Aucune interdiction universelle de 24 h entre deux gardes
# --------------------------------------------------------------------------- #


def test_espacement_ordinaire_reste_souple(session):
    catalog_service.ensure_reference_data(session)
    regle = session.execute(
        select(RestRule).where(RestRule.code == "ESPACEMENT_7J")
    ).scalar_one()
    assert regle.enforcement is Enforcement.SOUPLE
    assert regle.is_demo_hypothesis is True


def test_deux_gardes_rapprochees_restent_possibles(world):
    """Sans règle ferme d'espacement, deux gardes proches ne sont plus interdites."""
    occurrences = world.occurrences
    premiere = occurrences[0]
    suivante = next(
        o for o in occurrences if o.start_at >= premiere.end_at + timedelta(hours=1)
    )
    senior = world.seniors[0]
    poste_a = next(p for p in premiere.posts if p.required_status is Status.SENIOR)
    poste_b = next(p for p in suivante.posts if p.required_status is Status.SENIOR)
    world.set_color(senior, premiere, Color.VERT)
    world.set_color(senior, suivante, Color.VERT)
    assert engine_bridge.check_assignment(world.session, poste_a, senior) is None
    assert engine_bridge.check_assignment(world.session, poste_b, senior) is None


# --------------------------------------------------------------------------- #
# Durée de service continu : ferme, dérogeable seulement sur demande explicite
# --------------------------------------------------------------------------- #


def _weekend_contigu(world, mode=None, samedi_requis: bool = False):
    """Deux occurrences contiguës formant plus de 24 h de service continu.

    ``mode`` force le mode de couverture des deux occurrences, afin que le poste
    voulu (assistant en première ligne, ou senior) existe réellement.
    ``samedi_requis`` restreint aux blocs qui commencent effectivement un samedi,
    seul ancrage accepté pour une demande de week-end complet.
    """
    from app.services import catalog_service

    occurrences = world.occurrences
    for avant, apres in zip(occurrences, occurrences[1:]):
        if apres.start_at != avant.end_at:
            continue
        duree = (apres.end_at - avant.start_at).total_seconds() / 3600.0
        if duree <= DUREE_CONTINUE_MAX_HEURES:
            continue
        if samedi_requis and avant.local_date.weekday() != 5:
            continue
        if mode is not None:
            catalog_service.set_coverage_mode(world.session, avant, mode)
            catalog_service.set_coverage_mode(world.session, apres, mode)
            world.session.flush()
            # Les postes ont été supprimés puis recréés : il faut relire les
            # collections, sinon on référencerait des postes qui n'existent plus.
            world.session.refresh(avant)
            world.session.refresh(apres)
        return avant, apres
    pytest.skip("aucun bloc contigu de plus de 24 h dans cet univers de test")


def test_bloc_continu_d_assistant_refuse_sans_demande_explicite(world):
    from app.models import CoverageMode

    avant, apres = _weekend_contigu(world, mode=CoverageMode.B)
    assistant = world.assistants[0]
    poste_a = next(
        p for p in avant.posts if p.line is Line.L1 and p.required_status is Status.ASSISTANT
    )
    poste_b = next(
        p for p in apres.posts if p.line is Line.L1 and p.required_status is Status.ASSISTANT
    )
    world.set_color(assistant, avant, Color.VERT)
    world.set_color(assistant, apres, Color.VERT)

    assert engine_bridge.check_assignment(world.session, poste_a, assistant) is None
    _affecter(world, poste_a, assistant)
    refus = engine_bridge.check_assignment(world.session, poste_b, assistant)
    assert refus is not None
    assert refus.constraint_code == H_DUREE_CONTINUE
    assert "demande explicite" in refus.detail
    assert "assistant" in refus.detail


def test_un_senior_n_est_pas_bloque_par_la_duree_continue(world):
    """Portée restreinte aux assistants (arbitrage du client du 04/09/2026).

    Le même enchaînement, refusé pour un assistant, ne crée **aucun** blocage
    supplémentaire pour un senior.
    """
    from app.models import CoverageMode

    avant, apres = _weekend_contigu(world, mode=CoverageMode.A)
    senior = world.seniors[0]
    poste_a = next(p for p in avant.posts if p.required_status is Status.SENIOR)
    poste_b = next(p for p in apres.posts if p.required_status is Status.SENIOR)
    world.set_color(senior, avant, Color.VERT)
    world.set_color(senior, apres, Color.VERT)

    assert engine_bridge.check_assignment(world.session, poste_a, senior) is None
    _affecter(world, poste_a, senior)
    # Aucune demande explicite n'a été formulée, et pourtant rien ne bloque.
    assert engine_bridge.check_assignment(world.session, poste_b, senior) is None


def test_la_regle_ne_vise_que_les_assistants(session):
    """Contrôle direct de la portée déclarée de la règle."""
    from app.services import engine_bridge as bridge

    regle = bridge.continuous_duty_rule(session)
    assert regle.applies_to_statuses == frozenset({Status.ASSISTANT})


def test_bloc_continu_autorise_apres_demande_datee(world):
    from app.models import CoverageMode

    avant, apres = _weekend_contigu(world, mode=CoverageMode.B, samedi_requis=True)
    assistant = world.assistants[0]
    poste_a = next(
        p for p in avant.posts if p.line is Line.L1 and p.required_status is Status.ASSISTANT
    )
    poste_b = next(
        p for p in apres.posts if p.line is Line.L1 and p.required_status is Status.ASSISTANT
    )
    world.set_color(assistant, avant, Color.VERT)
    world.set_color(assistant, apres, Color.VERT)

    # La demande n'est valable que si elle émane de la personne elle-même.
    demandeur = world.user_of(assistant)
    demande = rest_service.request_weekend_block(
        world.session, assistant, avant.local_date, demandeur
    )
    assert demande.requested_at is not None

    _affecter(world, poste_a, assistant)
    assert engine_bridge.check_assignment(world.session, poste_b, assistant) is None

    # Retirée, la dérogation cesse immédiatement de produire effet.
    rest_service.withdraw_weekend_block(world.session, demande, world.admin)
    refus = engine_bridge.check_assignment(world.session, poste_b, assistant)
    assert refus is not None
    assert refus.constraint_code == H_DUREE_CONTINUE


def test_seule_la_personne_peut_demander_son_week_end(world):
    """Lot 5, point 9 : l'opt-in est restreint à son propriétaire."""
    from app.models import CoverageMode

    avant, _ = _weekend_contigu(world, mode=CoverageMode.B, samedi_requis=True)
    assistant = world.assistants[0]
    with pytest.raises(rest_service.RestError) as exc:
        rest_service.request_weekend_block(
            world.session, assistant, avant.local_date, world.admin
        )
    assert "elle-même" in str(exc.value)


def test_la_demande_s_ancre_obligatoirement_un_samedi(world):
    """Elle couvre exactement les deux occurrences samedi et dimanche."""
    assistant = world.assistants[0]
    un_mercredi = next(
        o.local_date for o in world.occurrences if o.local_date.weekday() == 2
    )
    with pytest.raises(rest_service.RestError) as exc:
        rest_service.request_weekend_block(
            world.session, assistant, un_mercredi, world.user_of(assistant)
        )
    assert "samedi" in str(exc.value)


def _affecter(world, post, profile):
    """Pose une affectation dans une version de travail, pour le contrôle ferme."""
    from app.models import AssignmentOrigin, ScheduleState, ScheduleVersion

    version = ScheduleVersion(
        quarter_id=world.quarter.id,
        version_no=90 + world.session.query(ScheduleVersion).count(),
        state=ScheduleState.GENERE,
    )
    world.session.add(version)
    world.session.flush()
    world.session.add(
        Assignment(
            schedule_version_id=version.id,
            post_id=post.id,
            profile_id=profile.id,
            origin=AssignmentOrigin.MOTEUR,
        )
    )
    world.session.flush()
    return version


# --------------------------------------------------------------------------- #
# Récupération : proposée, jamais déclenchée
# --------------------------------------------------------------------------- #


def _une_affectation(world):
    """Une affectation **terminée**, détenue par son titulaire.

    Les contrôles de cohérence exigent une garde passée : l'horloge est donc
    avancée après la fin de la garde retenue.
    """
    version = publish_plan(world)
    affectation = world.session.execute(
        select(Assignment).where(Assignment.schedule_version_id == version.id).limit(1)
    ).scalar_one()
    fin = affectation.post.occurrence.end_at
    Clock.freeze(fin + timedelta(hours=1))
    return affectation


def _titulaire(world, affectation):
    from app.models import ProfessionalProfile

    return world.session.get(ProfessionalProfile, affectation.profile_id)


def test_aucune_presomption_sans_declaration(world):
    """Être de garde ne vaut aucune heure travaillée : rien n'est présumé."""
    _une_affectation(world)
    assert rest_service.pending_recoveries(world.session) == []


def test_simple_appel_sans_deplacement_n_ouvre_aucun_droit(world):
    affectation = _une_affectation(world)
    profil = _titulaire(world, affectation)
    rapport, proposition = rest_service.declare_on_site(
        world.session,
        affectation,
        profil,
        hours_on_site=14.0,
        moved_on_site=False,
        declared_by=world.admin,
    )
    assert rapport.opens_recovery is False
    assert proposition is None
    assert rest_service.pending_recoveries(world.session) == []


def test_heures_fractionnees_n_ouvrent_aucun_droit(world):
    affectation = _une_affectation(world)
    profil = _titulaire(world, affectation)
    _, proposition = rest_service.declare_on_site(
        world.session,
        affectation,
        profil,
        hours_on_site=14.0,
        moved_on_site=True,
        continuous=False,
        declared_by=world.admin,
    )
    assert proposition is None


def test_sous_le_seuil_aucune_proposition(world):
    affectation = _une_affectation(world)
    profil = _titulaire(world, affectation)
    _, proposition = rest_service.declare_on_site(
        world.session,
        affectation,
        profil,
        hours_on_site=11.5,
        moved_on_site=True,
        declared_by=world.admin,
    )
    assert proposition is None


def test_douze_heures_sur_place_proposent_douze_heures_de_recuperation(world):
    affectation = _une_affectation(world)
    profil = _titulaire(world, affectation)
    _, proposition = rest_service.declare_on_site(
        world.session,
        affectation,
        profil,
        hours_on_site=13.0,
        moved_on_site=True,
        declared_by=world.admin,
    )
    assert proposition is not None
    assert proposition.state == "PROPOSEE"
    assert proposition.hours == 12.0
    duree = (proposition.ends_at - proposition.starts_at).total_seconds() / 3600.0
    assert duree == pytest.approx(12.0)
    assert "validation humaine" in proposition.rationale
    assert rest_service.pending_recoveries(world.session) == [proposition]


def test_la_recuperation_exige_une_decision_humaine(world):
    affectation = _une_affectation(world)
    profil = _titulaire(world, affectation)
    _, proposition = rest_service.declare_on_site(
        world.session, affectation, profil, 13.0, True, declared_by=world.admin
    )
    tranchee = rest_service.decide_recovery(
        world.session, proposition, accepted=True, decided_by=world.admin
    )
    assert tranchee.state == "VALIDEE"
    assert tranchee.decided_by_id == world.admin.id
    assert tranchee.decided_at is not None
    # Une proposition déjà tranchée ne peut pas l'être une seconde fois.
    with pytest.raises(rest_service.RestError):
        rest_service.decide_recovery(
            world.session, proposition, accepted=False, decided_by=world.admin
        )


def test_refus_de_recuperation_trace(world):
    affectation = _une_affectation(world)
    profil = _titulaire(world, affectation)
    _, proposition = rest_service.declare_on_site(
        world.session, affectation, profil, 14.0, True, declared_by=world.admin
    )
    rest_service.decide_recovery(
        world.session,
        proposition,
        accepted=False,
        decided_by=world.admin,
        comment="situation intermédiaire appréciée par le chef de service",
    )
    ligne = world.session.execute(
        select(RecoveryProposal).where(RecoveryProposal.id == proposition.id)
    ).scalar_one()
    assert ligne.state == "REFUSEE"
    assert "chef de service" in ligne.decision_comment


# --------------------------------------------------------------------------- #
# Concentration : une alerte, jamais une règle
# --------------------------------------------------------------------------- #


def test_concentration_produit_une_alerte_sans_bloquer(world):
    version = publish_plan(world)
    alertes = rest_service.concentration_alerts(
        world.session, version.id, window_days=14, threshold=2
    )
    assert alertes, "l'univers de test doit produire au moins une concentration"
    for alerte in alertes:
        assert "appréciation humaine" in alerte.message
        assert "aucune règle ferme" in alerte.message
    # Le planning reste publié : l'alerte n'a rien empêché.
    world.session.refresh(version)
    assert version.state.value == "PUBLIE"


def test_seuil_de_concentration_configurable(world):
    version = publish_plan(world)
    beaucoup = rest_service.concentration_alerts(
        world.session, version.id, window_days=30, threshold=2
    )
    peu = rest_service.concentration_alerts(
        world.session, version.id, window_days=30, threshold=99
    )
    assert len(beaucoup) > len(peu)
    assert peu == []


def test_occurrences_non_utilisees_pour_presumer(world):
    """Aucune ligne de travail sur place n'est créée par la publication."""
    version = publish_plan(world)
    postes = world.session.execute(
        select(CoveragePost)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .where(GardeOccurrence.quarter_id == world.quarter.id)
    ).scalars()
    assert list(postes)
    assert version is not None
    from app.models import OnSiteReport

    assert world.session.execute(select(OnSiteReport)).first() is None
