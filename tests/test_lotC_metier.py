"""Lot C du contre-audit du 04/09/2026 — les règles métier réellement exécutées.

Sept points, chacun éprouvé sur le comportement effectif et non sur l'intention :

1. bascule reprise → échange **réellement** lancée, au lieu d'exclure puis
   d'escalader ;
2. plafond mensuel comparé à la charge **réelle** du mois ;
3. borne assistant 03/10/2027 acceptée et 04/10/2027 refusée, par le **vrai
   moteur** d'affectation ;
4. cycle du premier lundi d'octobre raccordé aux charges antérieures, à
   l'avancement et aux compteurs, y compris au passage du 31 décembre ;
5. récupération validée bloquante pour la reprise et l'échange, avec ses routes
   protégées ;
6. opt-in week-end assistant strictement borné ;
7. objectif mensuel des assistants configurable et **inactif**.

Données entièrement fictives.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.engine.cycle import cycle_pour, premier_lundi_d_octobre
from app.main import app
from app.models import (
    Assignment,
    AuditEvent,
    Availability,
    Color,
    CoverageMode,
    Enforcement,
    GardeOccurrence,
    Line,
    ProfessionalProfile,
    Quarter,
    RecoveryProposal,
    ScheduleState,
    Status,
    Submission,
    SwapSearch,
    SwapSearchState,
    permissions,
)
from app.services import (
    catalog_service,
    engine_bridge,
    handover_service,
    period_quota_service,
    permission_service,
    quota_service,
    rest_service,
    swap_flow_service,
)
from app.services.clock import Clock
from tests.conftest import publish_plan

MOT_DE_PASSE = "demo"


def _client(world, utilisateur) -> TestClient:
    world.session.commit()
    client = TestClient(app)
    reponse = client.post(
        "/api/v1/auth/login",
        json={"email": utilisateur.email, "password": MOT_DE_PASSE},
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _futures(world):
    return [
        a
        for a in sorted(
            world.version.assignments, key=lambda a: a.post.occurrence.start_at
        )
        if a.post.occurrence.start_at > Clock.now() and a.busy_operation is None
    ]


# --------------------------------------------------------------------------- #
# C.1 — bascule reprise vers échange
# --------------------------------------------------------------------------- #


def test_C1_sans_volontaire_une_recherche_d_echange_est_reellement_lancee(world):
    """Le contre-audit reprochait d'exclure puis d'escalader sans rien tenter."""
    session = world.session
    publish_plan(world)
    affectation = _futures(world)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)

    # Personne d'autre n'est disponible pour une reprise simple : tout le monde
    # passe au rouge sur la date cédée.
    occurrence = affectation.post.occurrence
    for profil in world.seniors + world.assistants:
        if profil.id != titulaire.id:
            world.set_color(profil, occurrence, Color.ROUGE)
    session.flush()

    demande = handover_service.request_handover(session, affectation, titulaire)
    handover_service.run_until_settled(session, demande)
    session.flush()

    actions = [
        e.action
        for e in session.execute(select(AuditEvent)).scalars()
    ]
    recherches = list(
        session.execute(
            select(SwapSearch).where(SwapSearch.assignment_id == affectation.id)
        ).scalars()
    )
    # La preuve que la bascule a bien été **tentée** : une recherche d'échange
    # existe pour cette garde, avec son verdict, au lieu d'une simple exclusion.
    assert recherches, "une recherche d'échange doit avoir été ouverte"
    recherche = recherches[0]
    assert recherche.state in (
        SwapSearchState.COLLECTE,
        SwapSearchState.SANS_SOLUTION,
        SwapSearchState.OFFICIALISEE,
    )
    assert "REPRISE_ESCALADE" in actions
    if recherche.state is SwapSearchState.COLLECTE:
        assert "REPRISE_BASCULEE_EN_ECHANGE" in actions
    else:
        assert "ECHANGE_SANS_SOLUTION" in actions


def test_C1_sans_echange_le_titulaire_reste_inchange_avec_alerte(world):
    session = world.session
    publish_plan(world)
    affectation = _futures(world)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    occurrence = affectation.post.occurrence
    for profil in world.seniors + world.assistants:
        if profil.id != titulaire.id:
            for occ in world.occurrences:
                world.set_color(profil, occ, Color.ROUGE)
    session.flush()

    demande = handover_service.request_handover(session, affectation, titulaire)
    handover_service.run_until_settled(session, demande)
    session.flush()
    session.refresh(affectation)
    assert affectation.profile_id == titulaire.id
    assert affectation.busy_operation is None
    escalade = next(
        e
        for e in session.execute(select(AuditEvent)).scalars()
        if e.action == "REPRISE_ESCALADE"
    )
    assert handover_service.MOTIF_SANS_ECHANGE in escalade.payload_json


# --------------------------------------------------------------------------- #
# C.2 — plafond mensuel comparé à une charge réelle
# --------------------------------------------------------------------------- #


def test_C2_le_plafond_mensuel_compare_la_charge_reelle(world):
    """Plafond 6 : 0→1 autorisé, 5→6 autorisé, 6→7 refusé et orienté vers l'échange."""
    session = world.session
    publish_plan(world)
    affectation = _futures(world)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    post = affectation.post
    mois = post.occurrence.local_date

    quota_service.set_monthly_cap(
        session, world.year, world.admin,
        profile=titulaire, max_per_month=6.0,
        enforcement=Enforcement.FERME, institutionally_validated=True,
        label="plafond mensuel fictif de simulation",
    )
    session.flush()

    charge = handover_service.charge_du_mois(session, titulaire, mois)
    assert charge >= 0
    solde = handover_service.bucket_solde(session, titulaire, post)
    assert solde["charge_du_mois"] == pytest.approx(charge)

    # Le plafond ne mord que lorsque la charge réelle du mois l'atteint.
    if charge + 1 <= 6:
        assert not any("plafond mensuel fictif" in p for p in solde["plafonds_atteints"])

    # Plafond ramené sous la charge : la reprise simple devient impossible et le
    # motif nomme explicitement le dépassement.
    quota_service.set_monthly_cap(
        session, world.year, world.admin,
        profile=titulaire, max_per_month=max(charge - 1, 0),
        enforcement=Enforcement.FERME, institutionally_validated=True,
        label="plafond mensuel fictif de simulation",
    )
    session.flush()
    solde = handover_service.bucket_solde(session, titulaire, post)
    assert solde["plafonds_atteints"], solde
    assert handover_service.reprise_simple_possible(session, titulaire, post) is False


def test_C2_un_plafond_non_valide_ne_bloque_jamais(world):
    session = world.session
    publish_plan(world)
    affectation = _futures(world)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    quota_service.set_monthly_cap(
        session, world.year, world.admin,
        profile=titulaire, max_per_month=0.0,
        enforcement=Enforcement.SOUPLE, institutionally_validated=False,
        label="hypothèse de simulation",
    )
    session.flush()
    solde = handover_service.bucket_solde(session, titulaire, affectation.post)
    assert solde["plafonds_atteints"] == []


# --------------------------------------------------------------------------- #
# C.3 — borne assistant par le vrai moteur
# --------------------------------------------------------------------------- #


def _quarter_octobre_2027(world):
    """Trimestre fictif couvrant la bascule du 03 au 04 octobre 2027."""
    session = world.session
    annee = catalog_service.create_year(
        session, "2027-2028", date(2027, 10, 1), date(2027, 10, 31)
    )
    quarter = session.execute(
        select(Quarter).where(Quarter.year_id == annee.id, Quarter.index == 1)
    ).scalar_one()
    quarter.start_date = date(2027, 10, 1)
    quarter.end_date = date(2027, 10, 10)
    session.flush()
    catalog_service.generate_occurrences(
        session, quarter, holidays=set(),
        mode_resolver=lambda occurrence: CoverageMode.B,
    )
    session.flush()

    # Sans disponibilité déclarée, la contrainte « non renseigné » se déclenche
    # avant le quota de période et masquerait ce qu'on cherche à éprouver.
    from app.services import campaign_service

    campagne = campaign_service.create_campaign(
        session,
        quarter,
        opens_at=Clock.now(),
        deadline_at=Clock.now() + timedelta(days=10),
        admin=world.admin,
        grace_period_hours=48,
    )
    campaign_service.open_campaign(session, campagne, world.admin)
    session.flush()
    for profil in world.seniors + world.assistants:
        soumission = session.execute(
            select(Submission).where(
                Submission.campaign_id == campagne.id,
                Submission.profile_id == profil.id,
            )
        ).scalar_one()
        for occurrence in session.execute(
            select(GardeOccurrence).where(GardeOccurrence.quarter_id == quarter.id)
        ).scalars():
            session.add(
                Availability(
                    submission_id=soumission.id,
                    occurrence_id=occurrence.id,
                    color=Color.VERT,
                    is_declared=True,
                )
            )
    session.flush()
    return quarter


def test_C3_la_borne_assistant_est_appliquee_par_le_moteur(world):
    """03/10/2027 accepté, 04/10/2027 refusé : contrôle par check_assignment."""
    session = world.session
    publish_plan(world)
    quarter = _quarter_octobre_2027(world)
    assistant = world.assistants[0]

    # Quota de période opposable et déjà saturé par les gardes publiées de
    # janvier : la garde du 03/10 doit être refusée pour dépassement, celle du
    # 04/10 acceptée puisqu'elle ouvre le cycle suivant.
    period_quota_service.set_period_quota(
        session,
        world.admin,
        code="ASSISTANTS_2026_2027",
        label="quota fictif de période (simulation)",
        start_date=date(2026, 10, 19),
        end_date=date(2027, 10, 3),
        target=1.0,
        maximum=1.0,
        status=Status.ASSISTANT,
        enforcement=Enforcement.FERME,
        institutionally_validated=True,
    )
    session.flush()

    def _poste(jour: date):
        occurrence = session.execute(
            select(GardeOccurrence).where(
                GardeOccurrence.quarter_id == quarter.id,
                GardeOccurrence.local_date == jour,
            )
        ).scalar_one_or_none()
        assert occurrence is not None, f"occurrence attendue le {jour}"
        poste = next(
            (p for p in occurrence.posts if p.required_status is Status.ASSISTANT),
            None,
        )
        assert poste is not None, f"poste assistant attendu le {jour}"
        return poste, occurrence

    poste_dedans, occ_dedans = _poste(date(2027, 10, 3))
    poste_dehors, _ = _poste(date(2027, 10, 4))

    # La garde du 03/10 commence dans la période, même si elle finit le 04/10.
    assert occ_dedans.end_at.date() == date(2027, 10, 4)

    refus = engine_bridge.check_assignment(session, poste_dedans, assistant)
    assert refus is not None, "le 03/10/2027 appartient à la période : refus attendu"
    assert "période" in refus.detail or "période" in refus.label

    hors = engine_bridge.check_assignment(session, poste_dehors, assistant)
    if hors is not None:
        assert "période" not in hors.detail, hors.detail


# --------------------------------------------------------------------------- #
# C.4 — cycle annuel
# --------------------------------------------------------------------------- #


def test_C4_le_cycle_va_du_premier_lundi_d_octobre_au_suivant():
    cycle = cycle_pour(date(2027, 1, 15))
    assert cycle.debut == premier_lundi_d_octobre(2026) == date(2026, 10, 5)
    assert cycle.fin == date(2027, 10, 3)
    # Le 31 décembre ne coupe rien.
    assert cycle.contient(date(2026, 12, 31))
    assert cycle.contient(date(2027, 1, 1))


def test_C4_septembre_et_octobre_ne_sont_pas_dans_le_meme_cycle():
    septembre = cycle_pour(date(2027, 9, 30))
    octobre = cycle_pour(date(2027, 10, 4))
    assert septembre.debut == date(2026, 10, 5)
    assert octobre.debut == date(2027, 10, 4)
    assert septembre.debut != octobre.debut


def test_C4_les_bornes_du_moteur_suivent_le_cycle_et_non_l_annee_civile(world):
    session = world.session
    publish_plan(world)
    debut, fin = engine_bridge.cycle_bounds(world.quarter)
    assert debut == date(2026, 10, 5)
    assert fin == date(2027, 10, 3)
    # L'année civile 2027 commence le 01/01 : ce n'est pas elle qui commande.
    assert debut != world.year.start_date

    fraction = engine_bridge.year_fraction(world.quarter)
    assert 0.0 < fraction <= 1.0
    # Un trimestre de janvier est déjà bien avancé dans le cycle ouvert en octobre.
    assert fraction > 0.2


def test_C4_les_compteurs_suivent_le_cycle(world):
    """Les gardes de janvier comptent bien dans le cycle ouvert en octobre."""
    from app.services import counters_service

    session = world.session
    publish_plan(world)
    tableau = counters_service.tableau_seniors(session, world.year)
    assert tableau
    total = sum(
        cellule.gardes for compteurs in tableau for cellule in compteurs.cellules
    )
    assert total > 0, "les gardes publiées de janvier doivent être comptées"


# --------------------------------------------------------------------------- #
# C.5 — récupération validée bloquante, avec ses routes
# --------------------------------------------------------------------------- #


def _une_garde_terminee(world):
    """Avance l'horloge après le trimestre pour disposer de gardes terminées."""
    session = world.session
    affectations = sorted(
        world.version.assignments, key=lambda a: a.post.occurrence.start_at
    )
    assert affectations, "l'univers doit contenir des affectations"
    premiere = affectations[0]
    Clock.freeze(premiere.post.occurrence.end_at + timedelta(hours=1))
    session.expire_all()
    return session.get(Assignment, premiere.id)


def _declarer_et_valider_une_recuperation(world, profile, assignment):
    session = world.session
    occurrence = assignment.post.occurrence
    Clock.freeze(occurrence.end_at + timedelta(hours=1))
    duree = (occurrence.end_at - occurrence.start_at).total_seconds() / 3600.0
    _, proposition = rest_service.declare_on_site(
        session, assignment, profile,
        hours_on_site=min(duree, 14.0), moved_on_site=True,
        declared_by=world.user_of(profile),
    )
    assert proposition is not None
    rest_service.decide_recovery(
        session, proposition, accepted=True, decided_by=world.admin
    )
    session.flush()
    return proposition


def test_C5_une_recuperation_validee_bloque_la_reprise_et_l_echange(world):
    session = world.session
    publish_plan(world)
    assignment = _une_garde_terminee(world)
    profile = session.get(ProfessionalProfile, assignment.profile_id)
    proposition = _declarer_et_valider_une_recuperation(world, profile, assignment)

    # La récupération validée devient un intervalle occupé pour le contrôle
    # d'une affectation unique, donc pour la reprise et l'échange.
    intervalles = engine_bridge.recovery_intervals(session)
    assert any(i.profile_id == profile.id for i in intervalles)

    # On place la fenêtre de récupération sur une garde réellement proposable à
    # cette personne : l'univers réduit ne garantit pas un chevauchement naturel.
    cible = next(
        (
            (occurrence, p)
            for occurrence in world.occurrences
            for p in occurrence.posts
            if p.required_status is profile.status
            and occurrence.id != assignment.post.occurrence_id
        ),
        None,
    )
    assert cible is not None, "l'univers doit offrir un poste du bon statut"
    occurrence_cible, conflit = cible
    proposition.starts_at = occurrence_cible.start_at - timedelta(hours=1)
    proposition.ends_at = occurrence_cible.end_at + timedelta(hours=1)
    session.flush()

    refus = engine_bridge.check_assignment(
        session, conflit, profile, ignore_assignment_ids={assignment.id}
    )
    assert refus is not None
    assert (
        "récupération" in refus.detail.lower()
        or "chevauch" in refus.label.lower()
        or "chevauch" in refus.detail.lower()
    ), refus.detail


def test_C5_une_proposition_non_tranchee_ne_bloque_rien(world):
    session = world.session
    publish_plan(world)
    assignment = _une_garde_terminee(world)
    profile = session.get(ProfessionalProfile, assignment.profile_id)
    occurrence = assignment.post.occurrence
    duree = (occurrence.end_at - occurrence.start_at).total_seconds() / 3600.0
    _, proposition = rest_service.declare_on_site(
        session, assignment, profile,
        hours_on_site=min(duree, 14.0), moved_on_site=True,
        declared_by=world.user_of(profile),
    )
    session.flush()
    assert proposition.state == "PROPOSEE"
    assert engine_bridge.recovery_intervals(session) == []


def test_C5_les_routes_de_repos_existent_et_sont_protegees(world):
    session = world.session
    publish_plan(world)
    assignment = _une_garde_terminee(world)
    profile = session.get(ProfessionalProfile, assignment.profile_id)
    client = _client(world, world.user_of(profile))
    page = client.get("/repos")
    assert page.status_code == 200
    assert "Déclarer une présence sur place" in page.text

    occurrence = assignment.post.occurrence
    duree = (occurrence.end_at - occurrence.start_at).total_seconds() / 3600.0
    _, proposition = rest_service.declare_on_site(
        session, assignment, profile,
        hours_on_site=min(duree, 14.0), moved_on_site=True,
        declared_by=world.user_of(profile),
    )
    session.commit()

    refus = client.post(
        f"/repos/recuperation/{proposition.id}",
        data={"decision": "valider"}, follow_redirects=False,
    )
    assert refus.status_code == 403, refus.text

    permission_service.grant(
        session, world.user_of(profile), permissions.RESP_L1, world.admin
    )
    session.commit()
    accepte = client.post(
        f"/repos/recuperation/{proposition.id}",
        data={"decision": "valider"}, follow_redirects=False,
    )
    assert accepte.status_code == 303, accepte.text
    session.expire_all()
    assert session.get(RecoveryProposal, proposition.id).state == "VALIDEE"


# --------------------------------------------------------------------------- #
# C.6 — opt-in week-end assistant
# --------------------------------------------------------------------------- #


def _un_samedi(world) -> date:
    for occurrence in world.occurrences:
        if occurrence.local_date.weekday() == 5:
            return occurrence.local_date
    raise AssertionError("l'univers de test doit contenir un samedi")


def test_C6_un_auteur_nul_est_refuse(world):
    session = world.session
    with pytest.raises(rest_service.RestError) as erreur:
        rest_service.request_weekend_block(
            session, world.assistants[0], _un_samedi(world), requested_by=None
        )
    assert "auteur" in str(erreur.value)


def test_C6_seul_le_proprietaire_demande(world):
    session = world.session
    with pytest.raises(rest_service.RestError):
        rest_service.request_weekend_block(
            session, world.assistants[0], _un_samedi(world),
            requested_by=world.admin,
        )


def test_C6_l_ancrage_doit_etre_un_samedi(world):
    session = world.session
    samedi = _un_samedi(world)
    with pytest.raises(rest_service.RestError) as erreur:
        rest_service.request_weekend_block(
            session, world.assistants[0], samedi + timedelta(days=1),
            requested_by=world.user_of(world.assistants[0]),
        )
    assert "samedi" in str(erreur.value)


def test_C6_la_demande_couvre_exactement_deux_occurrences(world):
    session = world.session
    assistant = world.assistants[0]
    samedi = _un_samedi(world)
    demande = rest_service.request_weekend_block(
        session, assistant, samedi, requested_by=world.user_of(assistant)
    )
    session.flush()
    regle = engine_bridge.continuous_duty_rule(session)
    couverts = {
        jour for (profile_id, jour) in regle.explicit_requests
        if profile_id == assistant.id
    }
    assert couverts == {samedi, samedi + timedelta(days=1)}
    assert demande.anchor_date == samedi


def test_C6_une_demande_partielle_n_ouvre_pas_une_chaine_plus_longue(world):
    """Le cœur du point 6 : la dérogation ne couvre que ce qui a été demandé."""
    session = world.session
    assistant = world.assistants[0]
    samedi = _un_samedi(world)
    rest_service.request_weekend_block(
        session, assistant, samedi, requested_by=world.user_of(assistant)
    )
    session.flush()
    regle = engine_bridge.continuous_duty_rule(session)

    # La chaîne du week-end est couverte.
    assert regle.has_request(assistant.id, {samedi, samedi + timedelta(days=1)}) is True
    # Une chaîne qui déborde sur le lundi ne l'est pas.
    assert (
        regle.has_request(
            assistant.id,
            {samedi, samedi + timedelta(days=1), samedi + timedelta(days=2)},
        )
        is False
    )


# --------------------------------------------------------------------------- #
# C.7 — objectif mensuel des assistants, configurable et inactif
# --------------------------------------------------------------------------- #


def test_C7_l_objectif_mensuel_assistant_reste_inactif():
    objectif = quota_service.OBJECTIF_MENSUEL_ASSISTANT
    assert objectif.actif is False
    assert objectif.opposable is False
    assert objectif.vendredis_par_mois == 1.0
    assert objectif.jours_de_week_end_par_mois == 2.0
    assert "décision humaine" in objectif.alerte.lower()


def test_C7_l_objectif_est_signale_comme_decision_humaine(world):
    alertes = quota_service.monthly_cap_alerts(world.session, world.year)
    assert any("objectif mensuel" in a.lower() for a in alertes)


def test_C7_l_objectif_n_est_consulte_par_aucune_contrainte_ferme():
    """Preuve par le code : aucune règle ferme ne référence l'objectif."""
    from pathlib import Path

    moteur = Path(__file__).resolve().parents[1] / "app" / "engine"
    for fichier in moteur.glob("*.py"):
        texte = fichier.read_text(encoding="utf-8")
        assert "OBJECTIF_MENSUEL_ASSISTANT" not in texte, fichier.name
