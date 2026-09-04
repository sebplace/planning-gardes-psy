"""Lot 2 — corrections métier canoniques.

Contre-audit du 04/09/2026, six points :

1. quota assistant 57/68 réellement opérationnel sur la période unique ;
2. bornes assistant selon la date de début de service ;
3. paires de jours fériés, seniors seulement, jour férié vert obligatoire ;
4. reprise simple versus échange selon le bucket exact catégorie × ligne ;
5. rappel T1 unique le 15/11, sans doublon ;
6. politique explicite de validation incomplète.

Données fictives.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.engine import cycle
from app.engine.types import H_INACTIF, H_QUOTA_PERIODE
from app.models import (
    ActivityPeriod,
    Availability,
    Campaign,
    Color,
    Enforcement,
    GardeOccurrence,
    Line,
    Notification,
    PeriodQuota,
    Status,
)
from app.services import (
    campaign_service,
    engine_bridge,
    handover_service,
    period_quota_service,
)
from app.services.clock import Clock
from tests.conftest import publish_plan

DEBUT = period_quota_service.ASSISTANTS_DEBUT
FIN = period_quota_service.ASSISTANTS_FIN


# --------------------------------------------------------------------------- #
# 2.1 — quota de période opérationnel
# --------------------------------------------------------------------------- #


def _quota_assistants(world, maximum: float, opposable: bool = True):
    return period_quota_service.set_period_quota(
        world.session,
        world.admin,
        code=period_quota_service.CODE_QUOTA_ASSISTANTS,
        label=f"Quota assistant {maximum:g} gardes",
        start_date=DEBUT,
        end_date=FIN,
        target=maximum,
        maximum=maximum,
        status=Status.ASSISTANT,
        enforcement=Enforcement.FERME if opposable else Enforcement.SOUPLE,
        institutionally_validated=opposable,
    )


def test_la_periode_est_unique_et_a_cheval_sur_deux_annees(world):
    quota = _quota_assistants(world, 57)
    assert quota.start_date.year == 2026
    assert quota.end_date.year == 2027
    assert quota.covers(DEBUT)
    assert quota.covers(FIN)
    assert not quota.covers(DEBUT - timedelta(days=1))
    assert not quota.covers(FIN + timedelta(days=1))


def test_un_quota_de_periode_exige_les_trois_verrous(world):
    informatif = _quota_assistants(world, 57, opposable=False)
    assert informatif.is_enforceable is False
    assert informatif.alert is not None
    opposable = _quota_assistants(world, 57, opposable=True)
    assert opposable.is_enforceable is True
    assert opposable.alert is None


def _poste_assistant(world):
    for occurrence in world.occurrences:
        for post in occurrence.posts:
            if post.line is Line.L1 and post.required_status is Status.ASSISTANT:
                return post
    pytest.skip("aucun poste assistant")


def _refus_quota_periode(maximum: float, deja_fait: float):
    """Évalue la contrainte ferme au niveau du moteur pur, sans base.

    Le contrôle porte sur la règle elle-même : ``deja_fait`` gardes sont déjà
    posées sur la période, on tente d'en ajouter une.
    """
    from datetime import datetime as dt

    from app.engine import (
        AvailabilityIn,
        Color as EColor,
        CoverageMode,
        EngineInput,
        Line as ELine,
        PeriodQuotaIn,
        PersonIn,
        PostIn,
        Status as EStatus,
        hard_violation,
    )
    from app.engine.context import Context, State

    jour = date(2027, 3, 15)
    personne = PersonIn(
        profile_id=1,
        code="ASS-01",
        status=EStatus.ASSISTANT,
        eligible_l1=True,
        eligible_l2=False,
    )
    poste = PostIn(
        post_id=1,
        occurrence_id=1,
        type_code="NUIT_SEMAINE",
        category_code="NUITS_LJ",
        line=ELine.L1,
        required_status=EStatus.ASSISTANT,
        start_at=dt(2027, 3, 15, 17, 0),
        end_at=dt(2027, 3, 16, 8, 0),
        local_date=jour,
        coverage_mode=CoverageMode.B,
    )
    inp = EngineInput(
        posts=[poste],
        people=[personne],
        availabilities=[AvailabilityIn(1, 1, EColor.VERT)],
        period_quotas=[
            PeriodQuotaIn(
                code=period_quota_service.CODE_QUOTA_ASSISTANTS,
                label=f"Quota assistant {maximum:g} gardes",
                start_date=DEBUT,
                end_date=FIN,
                status=EStatus.ASSISTANT,
                target=maximum,
                maximum=maximum,
                enforcement=Enforcement.FERME,
                institutionally_validated=True,
            )
        ],
        prior_period_load={1: float(deja_fait)},
    )
    ctx = Context(inp)
    return hard_violation(ctx, State(ctx), poste, personne)


@pytest.mark.parametrize(
    "maximum, deja_fait, refus_attendu",
    [
        (57, 56, False),  # 56 -> 57 : accepté
        (57, 57, True),   # 57 -> 58 : refusé
        (57, 58, True),   # au-delà : refusé
        (68, 67, False),  # 67 -> 68 : accepté
        (68, 68, True),   # 68 -> 69 : refusé
        (68, 69, True),
    ],
)
def test_les_bornes_57_et_68_sont_opposables(maximum, deja_fait, refus_attendu):
    """Le quota devient une contrainte ferme, pas un calcul de projection."""
    refus = _refus_quota_periode(maximum, deja_fait)
    if refus_attendu:
        assert refus is not None
        assert refus.constraint_code == H_QUOTA_PERIODE
        assert str(maximum) in refus.detail
    else:
        assert refus is None


def test_un_quota_de_periode_non_opposable_ne_bloque_pas():
    from datetime import datetime as dt

    from app.engine import (
        AvailabilityIn,
        Color as EColor,
        CoverageMode,
        EngineInput,
        Line as ELine,
        PeriodQuotaIn,
        PersonIn,
        PostIn,
        Status as EStatus,
        hard_violation,
    )
    from app.engine.context import Context, State

    personne = PersonIn(1, "ASS-01", EStatus.ASSISTANT, True, False)
    poste = PostIn(
        1, 1, "NUIT_SEMAINE", "NUITS_LJ", ELine.L1, EStatus.ASSISTANT,
        dt(2027, 3, 15, 17, 0), dt(2027, 3, 16, 8, 0), date(2027, 3, 15),
        CoverageMode.B,
    )
    inp = EngineInput(
        posts=[poste],
        people=[personne],
        availabilities=[AvailabilityIn(1, 1, EColor.VERT)],
        period_quotas=[
            PeriodQuotaIn(
                code="X", label="quota non valide", start_date=DEBUT, end_date=FIN,
                status=EStatus.ASSISTANT, target=57, maximum=57,
                enforcement=Enforcement.FERME, institutionally_validated=False,
            )
        ],
        prior_period_load={1: 999.0},
    )
    ctx = Context(inp)
    assert hard_violation(ctx, State(ctx), poste, personne) is None


def test_le_quota_de_periode_ignore_les_dates_hors_periode():
    """Une garde hors de la période n'est jamais comptée dans ce quota."""
    from datetime import datetime as dt

    from app.engine import (
        AvailabilityIn,
        Color as EColor,
        CoverageMode,
        EngineInput,
        Line as ELine,
        PeriodQuotaIn,
        PersonIn,
        PostIn,
        Status as EStatus,
        hard_violation,
    )
    from app.engine.context import Context, State

    hors_periode = FIN + timedelta(days=1)  # 04/10/2027
    personne = PersonIn(1, "ASS-01", EStatus.ASSISTANT, True, False)
    poste = PostIn(
        1, 1, "NUIT_SEMAINE", "NUITS_LJ", ELine.L1, EStatus.ASSISTANT,
        dt(2027, 10, 4, 17, 0), dt(2027, 10, 5, 8, 0), hors_periode,
        CoverageMode.B,
    )
    inp = EngineInput(
        posts=[poste],
        people=[personne],
        availabilities=[AvailabilityIn(1, 1, EColor.VERT)],
        period_quotas=[
            PeriodQuotaIn(
                code="X", label="quota 57", start_date=DEBUT, end_date=FIN,
                status=EStatus.ASSISTANT, target=57, maximum=57,
                enforcement=Enforcement.FERME, institutionally_validated=True,
            )
        ],
        prior_period_load={1: 57.0},
    )
    ctx = Context(inp)
    assert hard_violation(ctx, State(ctx), poste, personne) is None


# --------------------------------------------------------------------------- #
# 2.2 — bornes assistant selon la date de service
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "jour, dans_la_periode",
    [
        (date(2026, 10, 18), False),
        (date(2026, 10, 19), True),
        (date(2027, 10, 3), True),
        (date(2027, 10, 4), False),
    ],
)
def test_le_rattachement_suit_la_date_de_service(world, jour, dans_la_periode):
    quota = _quota_assistants(world, 57)
    assert quota.covers(jour) is dans_la_periode


def test_le_03_10_2027_est_accepte_meme_si_la_garde_finit_le_04(world):
    """Une garde du dimanche qui finit le lundi matin reste dans la période."""
    quota = _quota_assistants(world, 57)
    debut_de_service = date(2027, 10, 3)
    assert debut_de_service.weekday() == 6  # dimanche
    assert quota.covers(debut_de_service)
    # La fin de garde, le lundi, n'est jamais consultée pour le rattachement.
    assert not quota.covers(date(2027, 10, 4))


def test_la_periode_d_activite_borne_les_affectations(world):
    """Contrôle ferme complémentaire : hors période d'activité, refus."""
    poste = _poste_assistant(world)
    assistant = world.assistants[0]
    periode = world.session.execute(
        select(ActivityPeriod).where(ActivityPeriod.profile_id == assistant.id)
    ).scalar_one()
    periode.start_date = poste.occurrence.local_date + timedelta(days=1)
    world.session.flush()
    world.set_color(assistant, poste.occurrence, Color.VERT)
    refus = engine_bridge.check_assignment(world.session, poste, assistant)
    assert refus is not None
    assert refus.constraint_code == H_INACTIF


# --------------------------------------------------------------------------- #
# Cycle canonique du quota
# --------------------------------------------------------------------------- #


def test_le_cycle_va_du_premier_lundi_d_octobre_au_suivant():
    c = cycle.cycle_commencant_en(2026)
    assert c.debut == date(2026, 10, 5)
    assert c.fin == date(2027, 10, 3)
    assert c.fin_exclue == date(2027, 10, 4)
    assert c.jours == 364
    assert c.semaines == 52.0


@pytest.mark.parametrize(
    "jour, libelle",
    [
        (date(2026, 10, 4), "2025-2026"),
        (date(2026, 10, 5), "2026-2027"),
        (date(2027, 10, 3), "2026-2027"),
        (date(2027, 10, 4), "2027-2028"),
    ],
)
def test_le_rattachement_au_cycle_suit_la_date_de_service(jour, libelle):
    assert cycle.cycle_pour(jour).label == libelle


def test_une_garde_du_dimanche_appartient_au_cycle_du_dimanche():
    """Commencée le 03/10/2027, terminée le 04/10 : cycle du dimanche."""
    debut_de_service = date(2027, 10, 3)
    assert cycle.cycle_pour(debut_de_service).label == "2026-2027"
    assert cycle.cycle_pour(date(2027, 10, 4)).label == "2027-2028"


def test_les_cycles_s_enchainent_sans_trou_ni_recouvrement():
    a = cycle.cycle_commencant_en(2026)
    b = cycle.cycle_commencant_en(2027)
    assert a.fin_exclue == b.debut
    assert not a.contient(b.debut)
    assert b.contient(b.debut)


# --------------------------------------------------------------------------- #
# 2.4 — reprise simple versus échange
# --------------------------------------------------------------------------- #


def _demande(world, ligne: Line = Line.L2):
    session = world.session
    for affectation in sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    ):
        session.refresh(affectation)
        if affectation.busy_operation is not None or affectation.post.line is not ligne:
            continue
        titulaire = session.get(type(world.seniors[0]), affectation.profile_id)
        return handover_service.request_handover(session, affectation, titulaire)
    pytest.skip("aucune garde disponible")


def test_sous_la_cible_la_reprise_simple_est_possible(world):
    publish_plan(world)
    demande = _demande(world)
    poste = demande.assignment.post
    volontaire = next(
        p for p in world.seniors if p.id != demande.requester_profile_id
    )
    solde = handover_service.bucket_solde(world.session, volontaire, poste)
    assert solde["categorie"]
    assert solde["ligne"] == poste.line.value
    if solde["sous_la_cible"] and not solde["plafonds_atteints"]:
        assert solde["reprise_simple_possible"] is True


def test_cible_atteinte_interdit_la_reprise_simple(world):
    """Pas de surcharge : il faut alors chercher un échange équivalent."""
    from app.services import quota_service

    publish_plan(world)
    demande = _demande(world)
    poste = demande.assignment.post
    volontaire = next(
        p for p in world.seniors if p.id != demande.requester_profile_id
    )
    categorie = poste.occurrence.garde_type.category
    quota_service.set_target(
        world.session,
        volontaire,
        world.year,
        categorie,
        poste.line,
        0.0,
        world.admin,
    )
    solde = handover_service.bucket_solde(world.session, volontaire, poste)
    assert solde["sous_la_cible"] is False
    assert solde["reprise_simple_possible"] is False


def test_le_bucket_est_exact_categorie_fois_ligne(world):
    """Une marge dans un autre bucket ne débloque pas celui-ci."""
    from app.services import quota_service

    publish_plan(world)
    demande = _demande(world)
    poste = demande.assignment.post
    volontaire = next(
        p for p in world.seniors if p.id != demande.requester_profile_id
    )
    categorie = poste.occurrence.garde_type.category
    autre_ligne = Line.L1 if poste.line is Line.L2 else Line.L2

    quota_service.set_target(
        world.session, volontaire, world.year, categorie, poste.line, 0.0, world.admin
    )
    quota_service.set_target(
        world.session, volontaire, world.year, categorie, autre_ligne, 99.0, world.admin
    )
    solde = handover_service.bucket_solde(world.session, volontaire, poste)
    assert solde["reprise_simple_possible"] is False


def test_une_personne_sans_marge_n_est_pas_sollicitee(world):
    """Le contrôle a lieu **avant** la sollicitation."""
    from app.services import quota_service

    publish_plan(world)
    demande = _demande(world)
    poste = demande.assignment.post
    categorie = poste.occurrence.garde_type.category
    for profil in world.seniors + world.assistants:
        quota_service.set_target(
            world.session, profil, world.year, categorie, poste.line, 0.0, world.admin
        )
    eligibles = handover_service.eligible_profiles(
        world.session, demande, handover_service.wave_kind_for(poste)
    )
    assert eligibles == []


# --------------------------------------------------------------------------- #
# 2.5 — rappel T1 unique
# --------------------------------------------------------------------------- #


def _rappels(session, campaign_id):
    return list(
        session.execute(
            select(Notification).where(
                Notification.kind == "CAMPAGNE_RAPPEL",
            )
        ).scalars()
    )


def test_un_seul_decalage_produit_exactement_un_rappel(world):
    """Cas de la campagne T1 : un rappel le 15/11, aucun avant."""
    campagne = world.campaign
    campagne.reminder_offsets_days = "16"
    world.session.flush()
    assert campagne.reminder_offsets == [16]

    # Avant l'échéance du rappel : rien.
    Clock.freeze(campagne.deadline_at - timedelta(days=17))
    assert campaign_service.send_due_reminders(world.session, campagne) == 0
    assert _rappels(world.session, campagne.id) == []

    # À l'échéance : exactement un envoi par personne non finalisée.
    Clock.freeze(campagne.deadline_at - timedelta(days=16, hours=-1))
    premier = campaign_service.send_due_reminders(world.session, campagne)
    assert premier == len(campagne.submissions)

    # Nouvelle exécution : aucun doublon.
    second = campaign_service.send_due_reminders(world.session, campagne)
    assert second == 0
    Clock.freeze(campagne.deadline_at - timedelta(days=1))
    assert campaign_service.send_due_reminders(world.session, campagne) == 0


def test_le_message_d_ouverture_ne_consomme_aucun_index(world):
    """Il est envoyé séparément et n'est pas compté comme un rappel."""
    ouvertures = list(
        world.session.execute(
            select(Notification).where(Notification.kind == "CAMPAGNE_OUVERTURE")
        ).scalars()
    )
    assert ouvertures
    for soumission in world.campaign.submissions:
        assert soumission.last_reminder_index == -1


# --------------------------------------------------------------------------- #
# 2.6 — validation incomplète
# --------------------------------------------------------------------------- #


def test_une_reponse_incomplete_ne_peut_pas_etre_validee(world):
    session = world.session
    profil = world.seniors[0]
    soumission = world.submission(profil)
    # On efface une seule date : la réponse devient incomplète.
    entree = session.execute(
        select(Availability).where(Availability.submission_id == soumission.id).limit(1)
    ).scalar_one()
    session.delete(entree)
    session.flush()

    with pytest.raises(campaign_service.CampaignError) as exc:
        campaign_service.validate_submission(session, soumission)
    assert "ne sont pas renseignées" in str(exc.value)
    assert "partielle" in str(exc.value)


def test_le_message_nomme_les_dates_manquantes(world):
    session = world.session
    soumission = world.submission(world.seniors[0])
    entrees = list(
        session.execute(
            select(Availability).where(Availability.submission_id == soumission.id).limit(2)
        ).scalars()
    )
    dates = []
    for entree in entrees:
        occurrence = session.get(GardeOccurrence, entree.occurrence_id)
        dates.append(occurrence.local_date.isoformat())
        session.delete(entree)
    session.flush()

    with pytest.raises(campaign_service.CampaignError) as exc:
        campaign_service.validate_submission(session, soumission)
    for jour in dates:
        assert jour in str(exc.value)


def test_une_reponse_complete_reste_validable(world):
    soumission = world.submission(world.seniors[0])
    assert campaign_service.occurrences_non_renseignees(world.session, soumission) == []
    campaign_service.validate_submission(world.session, soumission)


def test_les_dates_hors_periode_d_activite_ne_sont_pas_exigees(world):
    """On ne peut pas exiger une réponse là où la personne n'exerce pas."""
    session = world.session
    assistant = world.assistants[0]
    soumission = world.submission(assistant)
    session.query(Availability).filter(
        Availability.submission_id == soumission.id
    ).delete()
    session.flush()
    assert campaign_service.occurrences_non_renseignees(session, soumission)

    periode = session.execute(
        select(ActivityPeriod).where(ActivityPeriod.profile_id == assistant.id)
    ).scalar_one()
    periode.start_date = date(2030, 1, 1)
    periode.end_date = date(2030, 12, 31)
    session.flush()
    assert campaign_service.occurrences_non_renseignees(session, soumission) == []
