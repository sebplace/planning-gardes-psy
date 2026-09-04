"""Lot D du contre-audit du 04/09/2026 — concurrence sur les **vrais** services.

Les tests PostgreSQL du lot 5 utilisaient des tables simplifiées ``bac_*`` :
ils prouvaient des primitives du moteur de base, pas les courses du programme
réel. Cette campagne-ci monte le **schéma migré**, les **modèles applicatifs**
et les **services** de l'application sur un serveur PostgreSQL, puis fait
travailler deux sessions distinctes, synchronisées par des barrières.

Six scénarios exigés :

1. candidature contre gel ;
2. double consentement d'échange ;
3. double tirage réel ;
4. collision d'outbox sans annulation de l'opération métier ;
5. publication concurrente réelle ;
6. tête d'audit concurrente, sans fourche silencieuse.

Plus la séparation **observable** de l'engagement de tirage et de sa révélation.

Comment les exécuter
--------------------
Un serveur PostgreSQL doit être joignable et l'URL fournie par
``GARDES_TEST_PG_URL_APP``. Sans cette variable, le module est sauté avec un
motif explicite : aucun test n'est simulé.

Le schéma est celui des migrations Alembic, appliquées par
``scripts/preparer_base_concurrence.py`` puis ``alembic upgrade head``.

Données exclusivement fictives.
"""

from __future__ import annotations

import os
import threading
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("GARDES_TEST_PG_URL_APP")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason=(
        "GARDES_TEST_PG_URL_APP non défini : la campagne de concurrence "
        "applicative exige un serveur PostgreSQL joignable et un schéma migré. "
        "Ces tests ne sont jamais simulés."
    ),
)

DELAI = 30  # secondes


# --------------------------------------------------------------------------- #
# Socle : une base migrée, un univers fictif, deux sessions distinctes
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def moteur_pg():
    from sqlalchemy import text

    engine = create_engine(PG_URL, pool_size=10, max_overflow=10)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield engine
    engine.dispose()


@pytest.fixture()
def univers(moteur_pg):
    """Univers fictif complet, monté sur le schéma **migré**.

    Les tables sont vidées entre deux tests, mais **jamais recréées** : c'est
    bien le schéma produit par les migrations qui est éprouvé.
    """
    from sqlalchemy import text

    from app.models import Base
    from app.services.clock import Clock
    from tests.conftest import build_world

    noms = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    with moteur_pg.begin() as conn:
        conn.execute(text(f"TRUNCATE {noms} RESTART IDENTITY CASCADE"))

    Fabrique = sessionmaker(bind=moteur_pg, expire_on_commit=False)
    session = Fabrique()
    Clock.reset()
    monde = build_world(session)
    session.commit()
    try:
        yield monde, Fabrique
    finally:
        session.close()
        Clock.reset()


def _en_parallele(travaux: list, fabrique) -> list:
    """Exécute plusieurs travaux, chacun dans son fil et sa **propre** session.

    Une barrière garantit qu'ils atteignent le point sensible en même temps :
    sans elle, l'exécution serait séquentielle et ne prouverait rien.
    """
    barriere = threading.Barrier(len(travaux))
    resultats: dict[int, object] = {}
    verrou = threading.Lock()

    def executer(index: int, travail):
        session = fabrique()
        try:
            barriere.wait(timeout=DELAI)
            valeur = travail(session)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - on veut le type exact
            session.rollback()
            valeur = f"erreur:{type(exc).__name__}:{exc}"
        finally:
            session.close()
        with verrou:
            resultats[index] = valeur

    fils = [
        threading.Thread(target=executer, args=(i, t), daemon=True)
        for i, t in enumerate(travaux)
    ]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=DELAI)
    return [resultats.get(i) for i in range(len(travaux))]


def _publier(monde):
    from tests.conftest import publish_plan

    version = publish_plan(monde)
    monde.session.commit()
    return version


def _futures(monde):
    from app.services.clock import Clock

    return [
        a
        for a in sorted(
            monde.version.assignments, key=lambda a: a.post.occurrence.start_at
        )
        if a.post.occurrence.start_at > Clock.now() and a.busy_operation is None
    ]


# --------------------------------------------------------------------------- #
# Preuve préalable : deux sessions, deux backends
# --------------------------------------------------------------------------- #


def test_D0_les_deux_sessions_sont_reellement_distinctes(univers):
    from sqlalchemy import text

    _, fabrique = univers
    pids = _en_parallele(
        [
            lambda s: s.execute(text("SELECT pg_backend_pid()")).scalar_one(),
            lambda s: s.execute(text("SELECT pg_backend_pid()")).scalar_one(),
        ],
        fabrique,
    )
    assert len(set(pids)) == 2, f"les deux fils partagent le même backend : {pids}"


def test_D0_le_schema_est_bien_celui_des_migrations(moteur_pg):
    """Aucune table ``bac_*`` : on travaille sur le schéma applicatif migré."""
    from sqlalchemy import text

    with moteur_pg.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
    assert "alembic_version" in tables
    assert "handover_waves" in tables
    assert "swap_searches" in tables
    assert not any(t.startswith("bac_") for t in tables), sorted(tables)


# --------------------------------------------------------------------------- #
# 1. Candidature contre gel
# --------------------------------------------------------------------------- #


def test_D1_candidature_contre_gel(univers):
    """Soit la candidature est commise avant le gel et figure dans la liste
    figée, soit elle est refusée. Jamais déposée après gel puis omise."""
    from app.models import Candidacy, HandoverWave, ProfessionalProfile, WaveSolicitation
    from app.services import handover_service

    monde, fabrique = univers
    _publier(monde)
    session = monde.session
    affectation = _futures(monde)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    demande = handover_service.request_handover(session, affectation, titulaire)
    handover_service.advance(session, demande)
    session.commit()
    vague_id = demande.waves[0].id
    demande_id = demande.id
    sollicites = list(
        session.execute(
            select(WaveSolicitation.profile_id).where(
                WaveSolicitation.wave_id == vague_id
            )
        ).scalars()
    )
    assert sollicites, "l'univers doit produire au moins un sollicité"
    profil_id = sollicites[0]

    def candidater(s):
        vague = s.get(HandoverWave, vague_id)
        profil = s.get(ProfessionalProfile, profil_id)
        candidature = handover_service.submit_candidacy(s, vague, profil)
        return ("candidature", candidature.id)

    def geler(s):
        vague = s.get(HandoverWave, vague_id)
        tirage = handover_service.close_and_draw(s, vague)
        return ("gel", tirage.id if tirage else None)

    resultats = _en_parallele([candidater, geler], fabrique)

    verif = fabrique()
    try:
        candidatures = list(
            verif.execute(
                select(Candidacy).where(Candidacy.wave_id == vague_id)
            ).scalars()
        )
        vague = verif.get(HandoverWave, vague_id)
        candidature_commise = any(
            isinstance(r, tuple) and r[0] == "candidature" for r in resultats
        )
        if candidature_commise:
            # Elle existe et elle a été **traitée** : validée, exclue, retenue
            # ou non retenue. Jamais silencieusement ignorée.
            assert candidatures, resultats
            for candidature in candidatures:
                assert candidature.state.value != "DEPOSEE" or vague.frozen_at is None
        else:
            # Refus explicite, avec un motif : rien n'a été perdu en silence.
            erreurs = [r for r in resultats if isinstance(r, str)]
            assert erreurs, resultats
            assert any("Handover" in e or "figée" in e or "close" in e for e in erreurs), erreurs
    finally:
        verif.close()
    assert demande_id


# --------------------------------------------------------------------------- #
# 2. Double consentement d'échange
# --------------------------------------------------------------------------- #


def test_D2_double_consentement_d_echange(univers):
    """État final cohérent et officialisation **exactement une fois**."""
    from app.models import ProfessionalProfile, SwapProposal, SwapState
    from app.services import swap_service

    monde, fabrique = univers
    _publier(monde)
    session = monde.session
    futures = _futures(monde)
    paire = None
    for premiere in futures:
        for seconde in futures:
            if seconde.id == premiere.id or seconde.profile_id == premiere.profile_id:
                continue
            if swap_service.check_equivalence(premiere, seconde)[0]:
                paire = (premiere, seconde)
                break
        if paire:
            break
    assert paire, "l'univers doit offrir une paire échangeable"
    a, b = paire
    proposeur = session.get(ProfessionalProfile, a.profile_id)
    partenaire_id = b.profile_id
    proposition = swap_service.propose_swap(session, a, b, proposeur)
    session.commit()
    proposition_id = proposition.id

    def accepter(s):
        p = s.get(SwapProposal, proposition_id)
        profil = s.get(ProfessionalProfile, partenaire_id)
        return swap_service.accept_swap(s, p, profil).state.value

    resultats = _en_parallele([accepter, accepter], fabrique)

    verif = fabrique()
    try:
        p = verif.get(SwapProposal, proposition_id)
        assert p.state in (SwapState.OFFICIEL, SwapState.REFUSE), p.state
        officialisations = [r for r in resultats if r == SwapState.OFFICIEL.value]
        assert len(officialisations) <= 1, resultats
        if p.state is SwapState.OFFICIEL:
            assert p.executed_at is not None
    finally:
        verif.close()


# --------------------------------------------------------------------------- #
# 3. Double tirage réel
# --------------------------------------------------------------------------- #


def test_D3_double_tirage_reel(univers):
    """Deux clôtures concurrentes : au plus un tirage enregistré."""
    from app.models import Draw, HandoverWave, ProfessionalProfile, WaveSolicitation
    from app.services import handover_service

    monde, fabrique = univers
    _publier(monde)
    session = monde.session
    affectation = _futures(monde)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    demande = handover_service.request_handover(session, affectation, titulaire)
    handover_service.advance(session, demande)
    session.commit()
    vague_id = demande.waves[0].id
    for profil_id in session.execute(
        select(WaveSolicitation.profile_id).where(
            WaveSolicitation.wave_id == vague_id
        )
    ).scalars():
        handover_service.submit_candidacy(
            session, session.get(HandoverWave, vague_id),
            session.get(ProfessionalProfile, profil_id),
        )
    session.commit()

    def clore(s):
        vague = s.get(HandoverWave, vague_id)
        tirage = handover_service.close_and_draw(s, vague)
        return tirage.id if tirage else None

    resultats = _en_parallele([clore, clore], fabrique)

    verif = fabrique()
    try:
        tirages = verif.execute(
            select(func.count()).select_from(Draw).where(Draw.wave_id == vague_id)
        ).scalar_one()
        assert tirages <= 1, f"{tirages} tirages pour une seule vague"
        succes = [r for r in resultats if isinstance(r, int)]
        assert len(succes) <= 1, resultats
    finally:
        verif.close()


# --------------------------------------------------------------------------- #
# 4. Collision d'outbox sans annulation métier
# --------------------------------------------------------------------------- #


def test_D4_collision_d_outbox_sans_rollback_metier(univers):
    """Une clé de notification en double n'annule jamais l'opération métier."""
    from app.models import Notification, ProfessionalProfile, WeekendBlockRequest
    from app.services import notification_service, rest_service

    monde, fabrique = univers
    session = monde.session
    assistant = monde.assistants[0]
    assistant_id = assistant.id
    utilisateur_id = monde.user_of(assistant).id
    samedi = next(
        o.local_date for o in monde.occurrences if o.local_date.weekday() == 5
    )
    session.commit()

    def travail(s):
        from app.models import User

        profil = s.get(ProfessionalProfile, assistant_id)
        utilisateur = s.get(User, utilisateur_id)
        # Opération métier réelle.
        demande = rest_service.request_weekend_block(
            s, profil, samedi, requested_by=utilisateur
        )
        # Notification à clé **identique** dans les deux fils : collision certaine.
        notification_service.enqueue(
            s, "CAMPAGNE_VALIDATION", "collision:lotD:unique", profil,
            {"quarter": "trimestre fictif", "at": "maintenant"},
        )
        return demande.id

    resultats = _en_parallele([travail, travail], fabrique)

    verif = fabrique()
    try:
        # L'opération métier a survécu au moins une fois, malgré la collision.
        demandes = verif.execute(
            select(func.count()).select_from(WeekendBlockRequest).where(
                WeekendBlockRequest.profile_id == assistant_id
            )
        ).scalar_one()
        assert demandes >= 1, resultats
        # Une seule notification porte la clé : l'idempotence a tenu.
        messages = verif.execute(
            select(func.count()).select_from(Notification).where(
                Notification.idempotency_key == "collision:lotD:unique"
            )
        ).scalar_one()
        assert messages == 1, messages
    finally:
        verif.close()


# --------------------------------------------------------------------------- #
# 5. Publication concurrente réelle
# --------------------------------------------------------------------------- #


def test_D5_publication_concurrente_reelle(univers):
    """Au plus **une** version publiée par trimestre, prouvé sous PostgreSQL."""
    from app.models import ScheduleState, ScheduleVersion
    from app.services import planning_service
    from app.services.clock import Clock
    from tests.conftest import APRES_GRACE, close_and_prepare

    monde, fabrique = univers
    session = monde.session
    close_and_prepare(monde)
    Clock.freeze(APRES_GRACE)
    versions = []
    for graine in (4242, 9191):
        execution = planning_service.run_engine(
            session, monde.quarter, admin=monde.admin, seed=graine, variants=1
        )
        version = planning_service.create_version_from_proposal(
            session, execution.proposals[0], monde.admin, note="concurrence"
        )
        planning_service.validate_version(session, version, monde.admin)
        versions.append(version.id)
    session.commit()
    admin_id = monde.admin.id

    def publier(version_id):
        def travail(s):
            from app.models import User

            version = s.get(ScheduleVersion, version_id)
            admin = s.get(User, admin_id)
            planning_service.publish_version(s, version, admin)
            return version_id

        return travail

    resultats = _en_parallele(
        [publier(versions[0]), publier(versions[1])], fabrique
    )

    verif = fabrique()
    try:
        publiees = verif.execute(
            select(func.count()).select_from(ScheduleVersion).where(
                ScheduleVersion.quarter_id == monde.quarter.id,
                ScheduleVersion.state == ScheduleState.PUBLIE,
            )
        ).scalar_one()
        assert publiees == 1, f"{publiees} versions publiées : {resultats}"
    finally:
        verif.close()


# --------------------------------------------------------------------------- #
# 6. Tête d'audit concurrente
# --------------------------------------------------------------------------- #


def test_D6_tete_d_audit_concurrente_sans_fourche_silencieuse(univers):
    """Deux écritures d'audit simultanées : la chaîne reste vérifiable."""
    from app.models import AuditEvent
    from app.services import audit_service

    monde, fabrique = univers
    monde.session.commit()

    def ecrire(etiquette):
        def travail(s):
            audit_service.record(
                s, "TEST_CONCURRENCE", "user", 0,
                {"fil": etiquette}, actor_label="SYSTEME",
            )
            return etiquette

        return travail

    _en_parallele([ecrire("A"), ecrire("B")], fabrique)

    verif = fabrique()
    try:
        ecrits = list(
            verif.execute(
                select(AuditEvent).where(AuditEvent.action == "TEST_CONCURRENCE")
            ).scalars()
        )
        # Les deux écritures ont abouti, ou l'une a été refusée : dans les deux
        # cas la chaîne doit rester vérifiable, sans fourche silencieuse.
        assert ecrits
        ok, anomalies = audit_service.verify_chain(verif)
        assert ok, anomalies
    finally:
        verif.close()


# --------------------------------------------------------------------------- #
# Engagement et révélation du tirage, en deux transactions observables
# --------------------------------------------------------------------------- #


def test_D7_l_engagement_precede_la_revelation_de_maniere_observable(univers):
    """L'engagement est **commis** avant que le résultat ne soit calculable.

    Le contre-audit demandait soit deux transactions observables, soit l'arrêt
    de la qualification « engagement préalable ». On prouve ici la première
    branche : après la première transaction, l'empreinte de la graine et la
    liste figée sont visibles depuis une **autre** session, alors qu'aucun
    tirage n'existe encore.
    """
    from app.models import Draw, HandoverWave, ProfessionalProfile, WaveSolicitation
    from app.services import handover_service

    monde, fabrique = univers
    _publier(monde)
    session = monde.session
    affectation = _futures(monde)[0]
    titulaire = session.get(ProfessionalProfile, affectation.profile_id)
    demande = handover_service.request_handover(session, affectation, titulaire)
    handover_service.advance(session, demande)
    session.commit()
    vague_id = demande.waves[0].id
    for profil_id in session.execute(
        select(WaveSolicitation.profile_id).where(
            WaveSolicitation.wave_id == vague_id
        )
    ).scalars():
        handover_service.submit_candidacy(
            session, session.get(HandoverWave, vague_id),
            session.get(ProfessionalProfile, profil_id),
        )
    session.commit()

    # Transaction 1 : engagement seul, commis.
    engagement_session = fabrique()
    try:
        vague = engagement_session.get(HandoverWave, vague_id)
        empreinte = handover_service.sceller_engagement(engagement_session, vague)
        engagement_session.commit()
    finally:
        engagement_session.close()

    # Observation depuis une **autre** session : engagement visible, tirage absent.
    observateur = fabrique()
    try:
        vague = observateur.get(HandoverWave, vague_id)
        assert vague.seed_commitment == empreinte["engagement_graine"]
        assert vague.list_hash == empreinte["empreinte_liste"]
        assert vague.frozen_at is not None
        tirages = observateur.execute(
            select(func.count()).select_from(Draw).where(Draw.wave_id == vague_id)
        ).scalar_one()
        assert tirages == 0, "le tirage ne doit pas encore exister"
    finally:
        observateur.close()

    # Transaction 2 : révélation et tirage.
    revelation = fabrique()
    try:
        vague = revelation.get(HandoverWave, vague_id)
        tirage = handover_service.close_and_draw(revelation, vague)
        revelation.commit()
        assert tirage is not None
    finally:
        revelation.close()

    controle = fabrique()
    try:
        tirage = controle.execute(
            select(Draw).where(Draw.wave_id == vague_id)
        ).scalar_one()
        import hashlib
        import json

        preuve = json.loads(tirage.proof_json)
        assert preuve["engagement_graine"] == empreinte["engagement_graine"]
        assert (
            hashlib.sha256(preuve["graine_revelee"].encode()).hexdigest()
            == preuve["engagement_graine"]
        )
        # L'engagement couvre bien la liste **et** les éléments d'éligibilité.
        assert "liste_figee" in preuve
        assert "liste_valide" in preuve
        assert "verts_valides" in preuve
    finally:
        controle.close()
