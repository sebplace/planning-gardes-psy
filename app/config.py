"""Configuration applicative. Toutes les valeurs sont surchargeables par variables
d'environnement (préfixe ``GARDES_``)."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GARDES_", env_file=".env", extra="ignore")

    app_name: str = "Planification des gardes psychiatriques"
    environment: str = "demonstration"
    #: Bases explicitement déclarées exclusivement fictives, séparées par des
    #: virgules. Complète l'allowlist intégrée. Une opération destructive n'est
    #: possible que sur une base de cette liste.
    demo_database_allowlist: str = ""
    # SQLite par défaut : la démonstration locale tourne sans serveur de base.
    database_url: str = f"sqlite:///{(BASE_DIR / 'gardes.db').as_posix()}"
    secret_key: str = "prototype-demo-non-secret-a-remplacer-en-production"
    timezone: str = "Europe/Brussels"

    # Bannière obligatoire : aucune donnée patient (§4.5 du cahier des charges).
    patient_data_warning: str = (
        "Ne pas encoder d'information concernant un patient dans cette application."
    )
    demo_banner: str = (
        "Prototype de démonstration — données entièrement fictives. "
        "Aucun message réel n'est envoyé. Ceci n'est pas un outil institutionnel."
    )
    free_text_max_length: int = 500

    # Valeurs de démonstration — voir OPEN_QUESTIONS.md
    default_grace_period_hours: int = 48  # Q-08
    default_holiday_pair_requirement: str = "VERT_ORANGE"  # Q-05
    default_reminder_offsets_days: str = "30,14,7,2"  # §9.1

    @model_validator(mode="after")
    def _resolve_database_url(self) -> "Settings":
        """Rend l'URL de base compatible avec un hébergeur PaaS (ex. Scalingo).

        1. En production, la base managée est exposée via une variable HORS préfixe
           GARDES_ (SCALINGO_POSTGRESQL_URL, sinon DATABASE_URL). On la reprend
           seulement si aucune URL explicite GARDES_DATABASE_URL n'a été fournie
           (c.-à-d. si l'on est resté sur le SQLite par défaut).
        2. SQLAlchemy 2.0 avec le pilote psycopg v3 exige le schéma
           ``postgresql+psycopg://`` : on normalise ``postgres://`` et
           ``postgresql://``.
        3. Les bases managées imposent TLS : on ajoute ``sslmode=require`` au besoin.
        """
        if self.database_url.startswith("sqlite"):
            external = os.environ.get("SCALINGO_POSTGRESQL_URL") or os.environ.get("DATABASE_URL")
            if external:
                self.database_url = external
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        if url.startswith("postgresql+psycopg://") and "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
        self.database_url = url
        return self


settings = Settings()
