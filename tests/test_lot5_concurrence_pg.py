"""Lot 5.1 — concurrence **réelle** sous PostgreSQL, deux connexions distinctes.

Contre-audit du 04/09/2026 : des tests séquentiels sur une seule session SQLite
ne prouvent rien. Ces tests ouvrent **deux connexions indépendantes**, chacune
créée dans son propre fil d'exécution, avec deux transactions concurrentes, sur
un vrai serveur PostgreSQL.

Comment les exécuter
--------------------
Un serveur PostgreSQL doit être joignable et l'URL fournie par
``GARDES_TEST_PG_URL``. Sans cette variable, le module entier est sauté avec un
motif explicite : **aucun test n'est silencieusement neutralisé**.

Preuve locale réalisée avec le pilote **pur Python** ``pg8000``. La plateforme de
développement est en win-arm64, architecture pour laquelle ``psycopg`` ne publie
pas de roue binaire. Les propriétés testées ici (verrous de ligne, transactions,
contraintes uniques, savepoints, index uniques partiels) sont portées par
PostgreSQL lui-même et ne dépendent pas du pilote client.

Données exclusivement fictives.
"""

from __future__ import annotations

import os
import queue
import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

PG_URL = os.environ.get("GARDES_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason=(
        "GARDES_TEST_PG_URL non défini : la campagne de concurrence réelle exige "
        "un serveur PostgreSQL joignable. Ces tests ne sont jamais simulés."
    ),
)

DELAI = 20  # secondes


@pytest.fixture(scope="module")
def moteur():
    engine = create_engine(PG_URL)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield engine
    engine.dispose()


def _executer_dans_un_fil(moteur, travail, resultats: queue.Queue, etiquette: str):
    """Ouvre une connexion **propre au fil** et y exécute une transaction."""

    def cible():
        try:
            with moteur.connect() as conn:
                with conn.begin():
                    resultats.put((etiquette, travail(conn)))
        except IntegrityError:
            resultats.put((etiquette, "conflit_integrite"))
        except Exception as exc:  # pragma: no cover - filet
            resultats.put((etiquette, f"erreur:{type(exc).__name__}"))

    fil = threading.Thread(target=cible, daemon=True)
    fil.start()
    return fil


def _table(moteur, nom: str, definition: str, lignes: list[str] | None = None):
    with moteur.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {nom} CASCADE"))
        conn.execute(text(f"CREATE TABLE {nom} ({definition})"))
        for ligne in lignes or []:
            conn.execute(text(ligne))


def _nettoyer(moteur, *noms: str):
    with moteur.begin() as conn:
        for nom in noms:
            conn.execute(text(f"DROP TABLE IF EXISTS {nom} CASCADE"))


# --------------------------------------------------------------------------- #
# Preuve préalable : les connexions sont réellement distinctes
# --------------------------------------------------------------------------- #


def test_les_connexions_sont_reellement_distinctes(moteur):
    resultats: queue.Queue = queue.Queue()
    fils = [
        _executer_dans_un_fil(
            moteur,
            lambda c: c.execute(text("SELECT pg_backend_pid()")).scalar_one(),
            resultats,
            f"c{i}",
        )
        for i in range(2)
    ]
    for fil in fils:
        fil.join(timeout=DELAI)
    pids = {resultats.get(timeout=1)[1] for _ in range(2)}
    assert len(pids) == 2, f"les deux fils partagent le même backend : {pids}"


# --------------------------------------------------------------------------- #
# 1. Candidature contre gel
# --------------------------------------------------------------------------- #


def test_candidature_contre_gel(moteur):
    """Le gel gagne ou la candidature gagne, jamais les deux.

    Transposition fidèle du verrou applicatif : ``UPDATE ... WHERE state = attendu``
    avec contrôle du nombre de lignes touchées. Le second gel doit être **bloqué**
    par le verrou de ligne, puis constater que l'état a changé.
    """
    _table(
        moteur,
        "bac_gel",
        "id integer PRIMARY KEY, etat text NOT NULL",
        ["INSERT INTO bac_gel (id, etat) VALUES (1, 'OUVERTE')"],
    )
    demarre = threading.Event()
    resultats: queue.Queue = queue.Queue()

    def premier(conn):
        r = conn.execute(
            text(
                "UPDATE bac_gel SET etat = 'FIGEE' "
                "WHERE id = 1 AND etat = 'OUVERTE'"
            )
        )
        demarre.set()
        # Transaction maintenue ouverte pour que le second gel se heurte
        # réellement au verrou de ligne.
        conn.execute(text("SELECT pg_sleep(1.5)"))
        return r.rowcount

    def second(conn):
        demarre.wait(timeout=DELAI)
        r = conn.execute(
            text(
                "UPDATE bac_gel SET etat = 'FIGEE' "
                "WHERE id = 1 AND etat = 'OUVERTE'"
            )
        )
        return r.rowcount

    f1 = _executer_dans_un_fil(moteur, premier, resultats, "premier")
    f2 = _executer_dans_un_fil(moteur, second, resultats, "second")
    f1.join(timeout=DELAI)
    f2.join(timeout=DELAI)

    obtenus = dict(resultats.get(timeout=1) for _ in range(2))
    assert obtenus["premier"] == 1
    assert obtenus["second"] == 0, (
        "le second gel a réussi : le verrou applicatif ne tient pas sous "
        "concurrence réelle"
    )
    with moteur.connect() as conn:
        assert conn.execute(text("SELECT etat FROM bac_gel")).scalar_one() == "FIGEE"
    _nettoyer(moteur, "bac_gel")


# --------------------------------------------------------------------------- #
# 2. Double réponse d'échange
# --------------------------------------------------------------------------- #


def test_double_reponse_d_echange(moteur):
    """Deux acceptations simultanées : une seule officialisation."""
    _table(
        moteur,
        "bac_echange",
        "id integer PRIMARY KEY, etat text NOT NULL",
        ["INSERT INTO bac_echange (id, etat) VALUES (1, 'PROPOSE')"],
    )
    resultats: queue.Queue = queue.Queue()

    def accepter(conn):
        return conn.execute(
            text(
                "UPDATE bac_echange SET etat = 'OFFICIEL' "
                "WHERE id = 1 AND etat = 'PROPOSE'"
            )
        ).rowcount

    fils = [
        _executer_dans_un_fil(moteur, accepter, resultats, f"accord{i}")
        for i in range(2)
    ]
    for fil in fils:
        fil.join(timeout=DELAI)

    rowcounts = sorted(resultats.get(timeout=1)[1] for _ in range(2))
    assert rowcounts == [0, 1], f"officialisations concurrentes : {rowcounts}"
    _nettoyer(moteur, "bac_echange")


# --------------------------------------------------------------------------- #
# 3. Double tirage
# --------------------------------------------------------------------------- #


def test_double_tirage_impossible(moteur):
    """L'unicité d'un tirage par vague est garantie **en base**, pas en Python."""
    _table(
        moteur,
        "bac_tirage",
        "id serial PRIMARY KEY, wave_id integer NOT NULL, "
        "CONSTRAINT uq_bac_tirage UNIQUE (wave_id)",
    )
    resultats: queue.Queue = queue.Queue()

    def tirer(conn):
        conn.execute(text("INSERT INTO bac_tirage (wave_id) VALUES (42)"))
        return "insere"

    fils = [
        _executer_dans_un_fil(moteur, tirer, resultats, f"tirage{i}")
        for i in range(2)
    ]
    for fil in fils:
        fil.join(timeout=DELAI)

    valeurs = [resultats.get(timeout=1)[1] for _ in range(2)]
    assert valeurs.count("insere") == 1, f"deux tirages ont abouti : {valeurs}"
    assert valeurs.count("conflit_integrite") == 1, valeurs

    with moteur.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bac_tirage")).scalar_one() == 1
    _nettoyer(moteur, "bac_tirage")


# --------------------------------------------------------------------------- #
# 4. Collision de notification
# --------------------------------------------------------------------------- #


def test_collision_de_notification_n_annule_pas_l_operation_metier(moteur):
    """Le point exact du contre-audit : pas de ``rollback()`` global.

    L'opération métier doit survivre à une collision d'idempotence sur la
    notification. C'est ce que garantit un **savepoint** : seule l'insertion en
    conflit est annulée, la transaction métier se poursuit.
    """
    _table(moteur, "bac_metier", "id serial PRIMARY KEY, valeur text")
    _table(
        moteur,
        "bac_notif",
        "id serial PRIMARY KEY, cle text NOT NULL UNIQUE",
        ["INSERT INTO bac_notif (cle) VALUES ('deja-envoyee')"],
    )

    with moteur.connect() as conn:
        with conn.begin():
            conn.execute(
                text("INSERT INTO bac_metier (valeur) VALUES ('operation-metier')")
            )
            point = conn.begin_nested()  # SAVEPOINT
            try:
                conn.execute(
                    text("INSERT INTO bac_notif (cle) VALUES ('deja-envoyee')")
                )
                point.commit()
            except IntegrityError:
                point.rollback()  # seule la notification est annulée

    with moteur.connect() as conn:
        metier = conn.execute(text("SELECT count(*) FROM bac_metier")).scalar_one()
        notifs = conn.execute(text("SELECT count(*) FROM bac_notif")).scalar_one()
    assert metier == 1, "l'opération métier a été annulée par la collision"
    assert notifs == 1, "la notification a été dupliquée"
    _nettoyer(moteur, "bac_metier", "bac_notif")


def test_un_rollback_global_perdrait_l_operation_metier(moteur):
    """Contre-épreuve : montre ce que faisait l'ancien comportement."""
    _table(moteur, "bac_metier2", "id serial PRIMARY KEY, valeur text")
    _table(
        moteur,
        "bac_notif2",
        "id serial PRIMARY KEY, cle text NOT NULL UNIQUE",
        ["INSERT INTO bac_notif2 (cle) VALUES ('deja-envoyee')"],
    )

    conn = moteur.connect()
    transaction = conn.begin()
    conn.execute(text("INSERT INTO bac_metier2 (valeur) VALUES ('operation-metier')"))
    try:
        conn.execute(text("INSERT INTO bac_notif2 (cle) VALUES ('deja-envoyee')"))
        transaction.commit()
    except IntegrityError:
        transaction.rollback()  # ancien comportement : rollback global
    conn.close()

    with moteur.connect() as c:
        metier = c.execute(text("SELECT count(*) FROM bac_metier2")).scalar_one()
    assert metier == 0, (
        "la contre-épreuve devrait montrer la perte de l'opération métier"
    )
    _nettoyer(moteur, "bac_metier2", "bac_notif2")


# --------------------------------------------------------------------------- #
# 5. Transitions concurrentes de version
# --------------------------------------------------------------------------- #


def test_transitions_concurrentes_de_version(moteur):
    """Deux publications simultanées du même trimestre : une seule aboutit.

    C'est l'index unique partiel qui tranche, pas le code applicatif.
    """
    _table(
        moteur,
        "bac_versions",
        "id serial PRIMARY KEY, quarter_id integer NOT NULL, state text NOT NULL",
        [
            "INSERT INTO bac_versions (quarter_id, state) VALUES (1, 'VALIDE')",
            "INSERT INTO bac_versions (quarter_id, state) VALUES (1, 'VALIDE')",
        ],
    )
    with moteur.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_bac_une_publiee ON bac_versions (quarter_id) "
                "WHERE state = 'PUBLIE'"
            )
        )
    resultats: queue.Queue = queue.Queue()

    def publier(offset: int):
        def travail(conn):
            conn.execute(
                text(
                    "UPDATE bac_versions SET state = 'PUBLIE' WHERE id = ("
                    "SELECT id FROM bac_versions WHERE quarter_id = 1 "
                    "AND state = 'VALIDE' ORDER BY id LIMIT 1 OFFSET :n)"
                ),
                {"n": offset},
            )
            return "publie"

        return travail

    fils = [
        _executer_dans_un_fil(moteur, publier(i), resultats, f"publication{i}")
        for i in range(2)
    ]
    for fil in fils:
        fil.join(timeout=DELAI)

    with moteur.connect() as conn:
        publiees = conn.execute(
            text("SELECT count(*) FROM bac_versions WHERE state = 'PUBLIE'")
        ).scalar_one()
    assert publiees == 1, f"{publiees} versions publiées pour le même trimestre"
    _nettoyer(moteur, "bac_versions")


# --------------------------------------------------------------------------- #
# Le schéma réellement migré sous PostgreSQL
# --------------------------------------------------------------------------- #


def test_l_index_une_publication_par_trimestre_existe(moteur):
    with moteur.connect() as conn:
        present = conn.execute(
            text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE indexname = 'uq_one_published_per_quarter'"
            )
        ).scalar_one()
    assert present == 1, (
        "l'index unique partiel « une seule version publiée par trimestre » est "
        "absent du schéma PostgreSQL migré"
    )


def test_le_schema_migre_est_complet(moteur):
    attendues = {
        "users",
        "professional_profiles",
        "garde_occurrences",
        "coverage_posts",
        "assignments",
        "schedule_versions",
        "candidacies",
        "draws",
        "swap_proposals",
        "audit_events",
        "notifications",
        "monthly_caps",
        "period_quotas",
        "permission_grants",
        "weekend_block_requests",
        "on_site_reports",
        "recovery_proposals",
        "garde_weight_history",
    }
    with moteur.connect() as conn:
        presentes = {
            r[0]
            for r in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
    manquantes = attendues - presentes
    assert not manquantes, f"tables manquantes : {sorted(manquantes)}"


def test_ou_est_reellement_validee_l_enumeration_de_candidature(moteur):
    """Constat honnête : les énumérations ne sont **pas** contraintes en base.

    ``enum_column`` construit un ``Enum(native_enum=False)``. Depuis SQLAlchemy
    2.0, cette forme ne crée **aucune** contrainte CHECK : la colonne est un
    simple ``VARCHAR`` et la validation est faite côté Python par
    ``validate_strings=True``.

    Conséquence à connaître : une écriture SQL directe, hors application, peut
    déposer une valeur inconnue. Le contrôle applicatif reste efficace pour tout
    ce qui passe par le service, mais ce n'est pas une garantie de base.

    Ce test **documente** l'état réel plutôt que de laisser croire à une
    contrainte inexistante.
    """
    from app.models import CandidacyState

    with moteur.connect() as conn:
        contraintes = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'candidacies'::regclass AND contype = 'c'"
                )
            )
        ]
        type_colonne = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'candidacies' AND column_name = 'state'"
            )
        ).scalar_one()

    # État constaté : pas de CHECK, une colonne texte.
    assert contraintes == [], (
        "une contrainte CHECK existe : mettre ce test à jour, la garantie a changé"
    )
    assert type_colonne in ("character varying", "text")

    # La valeur RETIREE existe bien côté application ; la colonne l'accepte.
    assert CandidacyState.RETIREE.value == "RETIREE"
    valeurs_acceptees = {e.value for e in CandidacyState}
    assert "RETIREE" in valeurs_acceptees
    # Contrôle de longueur : la colonne doit pouvoir stocker la valeur.
    with moteur.connect() as conn:
        longueur = conn.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'candidacies' AND column_name = 'state'"
            )
        ).scalar_one()
    assert longueur is None or longueur >= len("RETIREE")
