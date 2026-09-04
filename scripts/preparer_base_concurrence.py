"""Prépare la base PostgreSQL applicative des tests de concurrence (lot D).

Données exclusivement fictives. À exécuter avant la campagne de concurrence.
"""

from __future__ import annotations

import pg8000.dbapi as db

CONN = dict(
    host="127.0.0.1", port=55432, user="postgres", password="gardes_test_local"
)
BASE = "gardes_applicatif"


def main() -> None:
    connexion = db.connect(database="postgres", **CONN)
    connexion.autocommit = True
    curseur = connexion.cursor()
    curseur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (BASE,),
    )
    curseur.execute(f'DROP DATABASE IF EXISTS "{BASE}"')
    curseur.execute(f'CREATE DATABASE "{BASE}"')
    print(f"base {BASE} recreee")
    connexion.close()


if __name__ == "__main__":
    main()
