"""Horloge et fuseau horaire.

Les instants absolus (`start_at`, `end_at`, échéances) sont stockés en **UTC naïf**.
L'affichage et la saisie se font en heure locale Europe/Brussels. Ce choix rend
correctes la détection de chevauchement, les durées de repos et les gardes qui
traversent minuit, y compris lors des changements d'heure.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import settings

LOCAL_TZ = ZoneInfo(settings.timezone)
UTC = timezone.utc


class Clock:
    """Horloge injectable : indispensable pour tester rappels, fenêtres et délais."""

    _override: datetime | None = None

    @classmethod
    def now(cls) -> datetime:
        """Instant courant en UTC naïf."""
        if cls._override is not None:
            return cls._override
        return datetime.now(UTC).replace(tzinfo=None)

    @classmethod
    def now_local(cls) -> datetime:
        return to_local(cls.now())

    @classmethod
    def freeze(cls, moment: datetime) -> None:
        cls._override = moment.replace(tzinfo=None) if moment.tzinfo else moment

    @classmethod
    def freeze_local(cls, moment: datetime) -> None:
        cls._override = local_to_utc(moment)

    @classmethod
    def advance(cls, **kwargs) -> None:
        cls._override = cls.now() + timedelta(**kwargs)

    @classmethod
    def reset(cls) -> None:
        cls._override = None


def local_to_utc(naive_local: datetime) -> datetime:
    """Heure locale naïve → UTC naïf. ``fold=0`` lève l'ambiguïté du recul d'heure."""
    aware = naive_local.replace(tzinfo=LOCAL_TZ)
    return aware.astimezone(UTC).replace(tzinfo=None)


def to_local(naive_utc: datetime) -> datetime:
    aware = naive_utc.replace(tzinfo=UTC)
    return aware.astimezone(LOCAL_TZ).replace(tzinfo=None)


def wall_clock_window(
    local_date: date, start: time, end: time, crosses_midnight: bool
) -> tuple[datetime, datetime, float]:
    """Fenêtre exprimée en horloge murale → bornes UTC naïves + durée réelle.

    La durée réelle vaut 23 h ou 25 h lors des changements d'heure, ce qui est le
    comportement attendu pour une garde définie par des horaires muraux.
    """
    start_local = datetime.combine(local_date, start)
    end_day = local_date + timedelta(days=1) if crosses_midnight else local_date
    end_local = datetime.combine(end_day, end)
    start_utc = local_to_utc(start_local)
    end_utc = local_to_utc(end_local)
    duration = (end_utc - start_utc).total_seconds() / 3600.0
    return start_utc, end_utc, round(duration, 4)


def format_local(naive_utc: datetime | None, pattern: str = "%d/%m/%Y %H:%M") -> str:
    if naive_utc is None:
        return "—"
    return to_local(naive_utc).strftime(pattern)


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def format_date_fr(day: date) -> str:
    return f"{JOURS[day.weekday()]} {day.day} {MOIS[day.month - 1]} {day.year}"
