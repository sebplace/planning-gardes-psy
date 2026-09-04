"""Séparation explicite des environnements et garde-fous de démarrage.

Trois environnements distincts, pilotés par ``GARDES_ENVIRONMENT`` :

* ``demonstration`` : données fictives, seed destructif autorisé, Swagger public,
  cookie non Secure (démarrage local en HTTP).
* ``staging`` : instance déployée derrière un proxy TLS, garde-fous transport actifs,
  seed destructif autorisé sur une base explicitement déclarée fictive.
* ``production`` : idem staging, plus refus de démarrage en présence d'artefacts de
  démonstration, et seed destructif toujours interdit.

**Principe de conception, corrigé après le contre-audit du 04/09/2026 : tous les
garde-fous destructifs sont fail-closed.** Une valeur d'environnement inconnue,
une base impossible à contrôler ou un contexte ambigu arrêtent l'exécution.
Aucune opération destructive n'est jamais autorisée « parce qu'une vérification a
échoué ».

Aucune valeur institutionnelle n'est inventée ici : uniquement des garde-fous
techniques. La base de production doit être une base DISTINCTE, jamais la base de
démonstration transformée.
"""

from __future__ import annotations

from ..config import settings

DEMONSTRATION = "demonstration"
STAGING = "staging"
PRODUCTION = "production"
_VALID = {DEMONSTRATION, STAGING, PRODUCTION}

_ALIASES = {
    "demo": DEMONSTRATION,
    "démonstration": DEMONSTRATION,
    "démo": DEMONSTRATION,
    "prod": PRODUCTION,
    "recette": STAGING,
    "preprod": STAGING,
    "pré-prod": STAGING,
}

DEFAULT_SECRET = "prototype-demo-non-secret-a-remplacer-en-production"

#: Bases explicitement déclarées **exclusivement fictives**. Une opération
#: destructive n'est possible que sur une base de cette liste. Complétable par
#: ``GARDES_DEMO_DATABASE_ALLOWLIST`` (valeurs séparées par des virgules), mais
#: jamais devinée.
ALLOWLIST_PAR_DEFAUT = frozenset({"gardes.db", "test_gardes.db", "scratch.db"})


class EnvironmentError_(Exception):
    """Contexte d'exécution non déterminable ou refusé."""


# --------------------------------------------------------------------------- #
# Nom d'environnement — fail-closed
# --------------------------------------------------------------------------- #


def env_name() -> str:
    """Nom d'environnement normalisé.

    **Fail-closed** : une valeur inconnue lève une exception au lieu de retomber
    silencieusement sur « démonstration », ce qui autoriserait des opérations
    destructives sur une instance mal configurée.
    """
    raw = (settings.environment or "").strip().lower()
    if raw in _VALID:
        return raw
    if raw in _ALIASES:
        return _ALIASES[raw]
    raise EnvironmentError_(
        f"Valeur GARDES_ENVIRONMENT inconnue : {settings.environment!r}. "
        f"Valeurs acceptées : {', '.join(sorted(_VALID))}."
    )


def env_name_or_none() -> str | None:
    """Variante tolérante, réservée aux affichages purement informatifs."""
    try:
        return env_name()
    except EnvironmentError_:
        return None


def is_demonstration() -> bool:
    return env_name() == DEMONSTRATION


def is_staging() -> bool:
    return env_name() == STAGING


def is_production() -> bool:
    return env_name() == PRODUCTION


def is_deployed() -> bool:
    """Instance derrière un proxy TLS (staging ou production)."""
    return env_name() in {STAGING, PRODUCTION}


# --------------------------------------------------------------------------- #
# Identification de la base et allowlist
# --------------------------------------------------------------------------- #


def database_fingerprint() -> str:
    """Identifiant lisible de la base visée, **sans aucun secret**.

    Pour SQLite : le nom du fichier. Pour PostgreSQL : le nom de la base.
    L'identifiant ne contient jamais d'utilisateur, de mot de passe ni d'hôte.
    """
    from sqlalchemy.engine import make_url

    try:
        url = make_url(settings.database_url)
    except Exception as exc:  # configuration illisible : fail-closed
        raise EnvironmentError_(f"URL de base de données illisible : {exc}") from None
    nom = url.database or ""
    if not nom:
        raise EnvironmentError_(
            "Impossible d'identifier la base de données visée : aucune opération "
            "destructive n'est autorisée."
        )
    return nom.replace("\\", "/").rsplit("/", 1)[-1]


def demo_database_allowlist() -> frozenset[str]:
    """Bases explicitement déclarées fictives."""
    brut = getattr(settings, "demo_database_allowlist", "") or ""
    supplementaires = {item.strip() for item in brut.split(",") if item.strip()}
    return frozenset(ALLOWLIST_PAR_DEFAUT | supplementaires)


def database_is_allowlisted() -> bool:
    return database_fingerprint() in demo_database_allowlist()


# --------------------------------------------------------------------------- #
# Contrôle des comptes — fail-closed
# --------------------------------------------------------------------------- #


def _weak_secret() -> bool:
    key = settings.secret_key or ""
    return key == DEFAULT_SECRET or len(key) < 32


def account_census() -> tuple[int, int]:
    """Retourne ``(total_comptes, comptes_fictifs)``.

    **Fail-closed** : si le recensement est impossible, une exception est levée.
    Un appelant destructif doit alors s'arrêter, jamais poursuivre.

    Cas distingué explicitement : une base dont la table des comptes n'existe pas
    encore est une base **vide**, pas une base ambiguë. Elle est donc recensée
    comme ``(0, 0)``, ce qui permet le premier peuplement d'une base neuve.
    """
    from sqlalchemy import func, inspect, select

    from ..db import SessionLocal, engine
    from ..models import User

    try:
        inspecteur = inspect(engine)
        tables = set(inspecteur.get_table_names())
    except Exception as exc:
        raise EnvironmentError_(
            f"Base de données injoignable ou illisible ({type(exc).__name__}). "
            "Le contexte est ambigu : aucune opération destructive n'est autorisée."
        ) from None

    if User.__tablename__ not in tables:
        return 0, 0

    try:
        with SessionLocal() as session:
            total = int(
                session.execute(select(func.count()).select_from(User)).scalar_one()
            )
            fictifs = int(
                session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.email.like("%.invalid"))
                ).scalar_one()
            )
            return total, fictifs
    except Exception as exc:
        raise EnvironmentError_(
            f"Recensement des comptes impossible ({type(exc).__name__}). Le "
            "contexte est ambigu : aucune opération destructive n'est autorisée."
        ) from None


def _invalid_accounts_count() -> int | None:
    """Nombre de comptes fictifs, ou ``None`` si le recensement est impossible.

    Réservé aux contrôles **non destructifs** de démarrage.
    """
    try:
        _, fictifs = account_census()
        return fictifs
    except EnvironmentError_:
        return None


# --------------------------------------------------------------------------- #
# Démarrage
# --------------------------------------------------------------------------- #


def startup_problems() -> list[str]:
    """Liste des raisons de refuser le démarrage dans l'environnement courant."""
    try:
        deployed = is_deployed()
        production = is_production()
    except EnvironmentError_ as exc:
        return [str(exc)]

    problems: list[str] = []
    if deployed and _weak_secret():
        problems.append("secret applicatif faible ou par défaut (GARDES_SECRET_KEY)")
    if production:
        count = _invalid_accounts_count()
        if count:
            problems.append(
                f"{count} compte(s) de démonstration '.invalid' présents en production"
            )
    return problems


def assert_startup_safe() -> None:
    """Fait échouer le démarrage en cas d'artefact dangereux ou de contexte ambigu."""
    problems = startup_problems()
    if problems:
        nom = env_name_or_none() or "inconnu"
        raise SystemExit(
            f"Démarrage refusé en environnement '{nom}' : " + " ; ".join(problems)
        )


# --------------------------------------------------------------------------- #
# Verrou des opérations destructives — entièrement fail-closed
# --------------------------------------------------------------------------- #


def destructive_seed_problems() -> list[str]:
    """Toutes les raisons de refuser une opération destructive.

    Quatre verrous cumulés, dans cet ordre :

    1. l'environnement doit être déterminable ;
    2. il ne doit pas s'agir de la production ;
    3. la base doit figurer dans l'allowlist explicite des bases fictives ;
    4. le recensement des comptes doit **réussir**, et **tous** les comptes
       existants doivent être fictifs. Une base vide est acceptée ; une base
       contenant ne serait-ce qu'un seul compte non fictif est refusée.
    """
    try:
        nom = env_name()
    except EnvironmentError_ as exc:
        return [str(exc)]

    problems: list[str] = []
    if nom == PRODUCTION:
        problems.append(
            "opération destructive interdite en production ; utilisez une base de "
            "démonstration ou de préproduction dédiée"
        )

    try:
        empreinte = database_fingerprint()
    except EnvironmentError_ as exc:
        problems.append(str(exc))
        return problems

    if empreinte not in demo_database_allowlist():
        problems.append(
            f"la base « {empreinte} » ne figure pas dans l'allowlist des bases "
            "exclusivement fictives ; déclarez-la explicitement via "
            "GARDES_DEMO_DATABASE_ALLOWLIST si, et seulement si, elle ne contient "
            "aucune donnée réelle"
        )

    try:
        total, fictifs = account_census()
    except EnvironmentError_ as exc:
        problems.append(str(exc))
        return problems

    non_fictifs = total - fictifs
    if non_fictifs > 0:
        problems.append(
            f"la base contient {non_fictifs} compte(s) non fictif(s) sur {total} ; "
            "la coexistence de comptes fictifs et non fictifs ne vaut pas "
            "autorisation"
        )
    return problems


def assert_destructive_seed_allowed() -> None:
    """Verrou du seed destructif (drop_all). Fail-closed en toutes circonstances."""
    problems = destructive_seed_problems()
    if problems:
        nom = env_name_or_none() or "inconnu"
        raise SystemExit(
            f"Refus d'une opération destructive en environnement '{nom}' : "
            + " ; ".join(problems)
        )
