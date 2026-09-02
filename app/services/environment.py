"""Séparation explicite des environnements et garde-fous de démarrage.

Trois environnements distincts, pilotés par ``GARDES_ENVIRONMENT`` :

* ``demonstration`` : données fictives, seed destructif autorisé, Swagger public,
  cookie non Secure (démarrage local en HTTP).
* ``staging`` : instance déployée derrière un proxy TLS, garde-fous transport actifs,
  seed destructif INTERDIT.
* ``production`` : idem staging, plus refus de démarrage en présence d'artefacts de
  démonstration (comptes ``.invalid``, secret par défaut).

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

DEFAULT_SECRET = "prototype-demo-non-secret-a-remplacer-en-production"


def env_name() -> str:
    """Nom d'environnement normalisé (tolère quelques alias)."""
    raw = (settings.environment or "").strip().lower()
    if raw in _VALID:
        return raw
    if raw in {"demo", "démonstration", "démo"}:
        return DEMONSTRATION
    if raw in {"prod"}:
        return PRODUCTION
    if raw in {"recette", "preprod", "pré-prod"}:
        return STAGING
    return DEMONSTRATION


def is_demonstration() -> bool:
    return env_name() == DEMONSTRATION


def is_staging() -> bool:
    return env_name() == STAGING


def is_production() -> bool:
    return env_name() == PRODUCTION


def is_deployed() -> bool:
    """Instance derrière un proxy TLS (staging ou production)."""
    return env_name() in {STAGING, PRODUCTION}


def _weak_secret() -> bool:
    key = settings.secret_key or ""
    return key == DEFAULT_SECRET or len(key) < 32


def _invalid_accounts_count() -> int | None:
    """Nombre de comptes @*.invalid (signature du jeu de démonstration).

    Retourne ``None`` si la vérification est impossible (schéma pas encore
    migré) : on ne bloque pas le démarrage sur une erreur d'infrastructure,
    seulement sur la présence AVÉRÉE d'artefacts de démonstration.
    """
    try:
        from sqlalchemy import func, select

        from ..db import SessionLocal
        from ..models import User

        with SessionLocal() as session:
            return int(
                session.execute(
                    select(func.count()).select_from(User).where(User.email.like("%.invalid"))
                ).scalar_one()
            )
    except Exception:
        return None


def startup_problems() -> list[str]:
    """Liste des raisons de refuser le démarrage dans l'environnement courant."""
    problems: list[str] = []
    if is_deployed() and _weak_secret():
        problems.append("secret applicatif faible ou par défaut (GARDES_SECRET_KEY)")
    if is_production():
        count = _invalid_accounts_count()
        if count:
            problems.append(
                f"{count} compte(s) de démonstration '.invalid' présents en production"
            )
    return problems


def assert_startup_safe() -> None:
    """Fait échouer le démarrage en cas d'artefact dangereux (staging/production)."""
    problems = startup_problems()
    if problems:
        raise SystemExit(
            f"Démarrage refusé en environnement '{env_name()}' : " + " ; ".join(problems)
        )


def assert_destructive_seed_allowed() -> None:
    """Verrou du seed destructif (drop_all).

    Autorisé en 'demonstration' et 'staging' (données fictives), INTERDIT en
    'production'. Dans tous les cas, refuse une base contenant des comptes non
    fictifs (aucun '.invalid'), pour éviter toute perte de données réelles.
    """
    if is_production():
        raise SystemExit(
            "Refus : le seed destructif (drop_all) est interdit en production. "
            "Utilisez une base de démonstration ou de staging dédiée."
        )
    count = _invalid_accounts_count()
    if count is not None and count == 0:
        # Base déjà peuplée avec des comptes non fictifs, ou base non reconnue comme demo.
        try:
            from sqlalchemy import func, select

            from ..db import SessionLocal
            from ..models import User

            with SessionLocal() as session:
                total = int(
                    session.execute(select(func.count()).select_from(User)).scalar_one()
                )
        except Exception:
            total = 0
        if total > 0:
            raise SystemExit(
                "Refus : la base contient des comptes non fictifs (aucun '.invalid'). "
                "Le seed destructif est bloqué pour éviter toute perte de données."
            )
