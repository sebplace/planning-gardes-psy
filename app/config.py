"""Configuration applicative. Toutes les valeurs sont surchargeables par variables
d'environnement (préfixe ``GARDES_``)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GARDES_", env_file=".env", extra="ignore")

    app_name: str = "Planification des gardes psychiatriques"
    environment: str = "demonstration"
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


settings = Settings()
