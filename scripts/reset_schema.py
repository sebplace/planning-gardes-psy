"""Réinitialise le schéma PostgreSQL (DÉMONSTRATION uniquement).

Supprime puis recrée le schéma ``public`` afin de repartir d'une base vierge
avant ``alembic upgrade head`` puis le seed fictif. Refuse de s'exécuter sur
SQLite (base locale) pour éviter toute suppression accidentelle.
"""

from __future__ import annotations

from sqlalchemy import text

from app.config import settings
from app.db import engine


def main() -> None:
    from app.services import environment as envsvc

    if settings.database_url.startswith("sqlite"):
        raise SystemExit("Refus : ce script ne s'exécute que sur une base PostgreSQL.")
    # Verrou destructif complet et fail-closed : environnement déterminable, hors
    # production, base explicitement déclarée fictive, et aucun compte non fictif.
    envsvc.assert_destructive_seed_allowed()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    print("Schéma public réinitialisé (base de démonstration).")


if __name__ == "__main__":
    main()
