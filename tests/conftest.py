"""Fixtures de test.

Base SQLite dédiée, données fictives réduites pour la rapidité.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["GARDES_DATABASE_URL"] = f"sqlite:///{(ROOT / 'tests' / 'test_gardes.db').as_posix()}"

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, create_all, drop_all  # noqa: E402
from app.models import (  # noqa: E402
    ActivityPeriod,
    Availability,
    Campaign,
    Color,
    CoverageMode,
    CoveragePost,
    GardeOccurrence,
    HolidayRequirement,
    Line,
    ProfessionalProfile,
    Quarter,
    QuotaCategory,
    QuotiteHistory,
    Status,
    Submission,
    User,
    Year,
)
from app.services import (  # noqa: E402
    campaign_service,
    catalog_service,
    planning_service,
    quota_service,
    security,
)
from app.services.clock import Clock  # noqa: E402

QUARTER_DAYS = 21
CAMPAIGN_OPEN = datetime(2026, 12, 2, 8, 0)
CAMPAIGN_DEADLINE = datetime(2026, 12, 27, 12, 0)
SAISIE_MOMENT = datetime(2026, 12, 5, 10, 0)
APRES_GRACE = datetime(2026, 12, 29, 14, 0)


class World:
    """Petit univers fictif complet, manipulable par les tests."""

    def __init__(self, session):
        self.session = session
        self.seniors: list[ProfessionalProfile] = []
        self.assistants: list[ProfessionalProfile] = []
        self.admin: User | None = None
        self.year: Year | None = None
        self.quarter: Quarter | None = None
        self.campaign: Campaign | None = None
        self.version = None

    # -- accès pratiques ------------------------------------------------ #

    @property
    def occurrences(self) -> list[GardeOccurrence]:
        return list(
            self.session.execute(
                select(GardeOccurrence)
                .where(GardeOccurrence.quarter_id == self.quarter.id)
                .order_by(GardeOccurrence.local_date)
            ).scalars()
        )

    def posts(self) -> list[CoveragePost]:
        return list(
            self.session.execute(
                select(CoveragePost)
                .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
                .where(GardeOccurrence.quarter_id == self.quarter.id)
                .order_by(GardeOccurrence.start_at, CoveragePost.line)
            ).scalars()
        )

    def submission(self, profile: ProfessionalProfile) -> Submission:
        return self.session.execute(
            select(Submission).where(
                Submission.campaign_id == self.campaign.id,
                Submission.profile_id == profile.id,
            )
        ).scalar_one()

    def set_color(self, profile, occurrence, color: Color) -> None:
        submission = self.submission(profile)
        entry = self.session.execute(
            select(Availability).where(
                Availability.submission_id == submission.id,
                Availability.occurrence_id == occurrence.id,
            )
        ).scalar_one_or_none()
        if entry is None:
            entry = Availability(
                submission_id=submission.id, occurrence_id=occurrence.id, color=color
            )
            self.session.add(entry)
        entry.color = color
        entry.is_declared = color is not Color.DISPO_DEFAUT
        self.session.flush()

    def color_of(self, profile, occurrence) -> Color | None:
        submission = self.submission(profile)
        entry = self.session.execute(
            select(Availability).where(
                Availability.submission_id == submission.id,
                Availability.occurrence_id == occurrence.id,
            )
        ).scalar_one_or_none()
        return entry.color if entry else None

    def user_of(self, profile) -> User:
        return self.session.get(User, profile.user_id)


def _add_person(session, code: str, status: Status, index: int, admin: bool = False):
    user = User(
        email=f"{code.lower()}@demo.invalid",
        display_name=f"Dr {code} (fictif)",
        password_hash=security.hash_password("demo"),
        is_medecin=True,
        is_admin=admin,
    )
    session.add(user)
    session.flush()
    profile = ProfessionalProfile(user_id=user.id, code=code, status=status)
    session.add(profile)
    session.flush()
    session.add(ActivityPeriod(profile_id=profile.id, start_date=date(2020, 1, 1)))
    session.add(
        QuotiteHistory(profile_id=profile.id, start_date=date(2020, 1, 1), tenths=10)
    )
    session.flush()
    return profile


@pytest.fixture()
def session():
    drop_all()
    create_all()
    Clock.reset()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        Clock.reset()


@pytest.fixture(autouse=True)
def _durcissement_http(monkeypatch):
    """Rend le durcissement HTTP compatible avec les tests, sans le neutraliser.

    Deux ajustements, tous deux **explicites** :

    1. le limiteur de débit est vidé entre les tests, sinon les connexions
       répétées d'une suite complète déclencheraient un blocage légitime mais
       hors sujet ;
    2. les requêtes non sûres de ``TestClient`` vers l'interface reçoivent
       automatiquement le jeton anti-rejeu, comme le ferait un navigateur qui a
       chargé la page. Le contrôle CSRF reste **actif** : les tests qui le
       visent explicitement passent un jeton absent ou faux.
    """
    from starlette.testclient import TestClient

    from app.services import http_security

    http_security.limiteur.vider()

    original = TestClient.request

    def request(self, method, url, *args, **kwargs):
        chemin = str(url)
        non_sure = method.upper() in ("POST", "PUT", "PATCH", "DELETE")
        interface = not chemin.startswith("/api/") and not chemin.startswith(
            "/health/"
        )
        entetes = kwargs.get("headers") or {}
        deja_fourni = any(
            k.lower() == http_security.ENTETE_CSRF.lower() for k in entetes
        )
        donnees = kwargs.get("data") or {}
        dans_le_formulaire = (
            isinstance(donnees, dict) and http_security.CHAMP_CSRF in donnees
        )
        if non_sure and interface and not deja_fourni and not dans_le_formulaire:
            jeton = getattr(self, "_jeton_csrf", None)
            if jeton is None:
                page = original(self, "GET", "/connexion")
                trouve = re.search(
                    rf'name="{http_security.CHAMP_CSRF}" value="([^"]+)"', page.text
                )
                jeton = trouve.group(1) if trouve else ""
                self._jeton_csrf = jeton
            entetes = dict(entetes)
            entetes[http_security.ENTETE_CSRF] = jeton
            kwargs["headers"] = entetes
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "request", request)
    yield
    http_security.limiteur.vider()


@pytest.fixture()
def world(session) -> World:
    """Univers fictif : 4 seniors, 2 assistants, 1 administrateur, 21 jours de gardes."""
    w = World(session)
    Clock.freeze(CAMPAIGN_OPEN)

    for i in range(1, 5):
        w.seniors.append(_add_person(session, f"SEN-{i:02d}", Status.SENIOR, i))
    for i in range(1, 3):
        w.assistants.append(_add_person(session, f"ASS-{i:02d}", Status.ASSISTANT, i))

    admin = User(
        email="admin@demo.invalid",
        display_name="Administration (fictive)",
        password_hash=security.hash_password("demo"),
        is_medecin=False,
        is_admin=True,
    )
    session.add(admin)
    session.flush()
    w.admin = admin

    catalog_service.ensure_reference_data(session)
    w.year = catalog_service.create_year(
        session, "2027", date(2027, 1, 1), date(2027, 12, 31)
    )
    quarter = session.execute(
        select(Quarter).where(Quarter.year_id == w.year.id, Quarter.index == 1)
    ).scalar_one()
    quarter.end_date = quarter.start_date + timedelta(days=QUARTER_DAYS - 1)
    session.flush()
    w.quarter = quarter

    def mode_resolver(occurrence):
        return (
            CoverageMode.B
            if (occurrence.local_date - quarter.start_date).days % 2 == 0
            else CoverageMode.A
        )

    catalog_service.generate_occurrences(
        session, quarter, holidays=set(), mode_resolver=mode_resolver
    )

    categories = {c.code: c for c in session.execute(select(QuotaCategory)).scalars()}
    for profile in w.seniors:
        for code in ("NUITS_LJ", "WEEKENDS_VEILLES"):
            for line in (Line.L1, Line.L2):
                quota_service.set_target(
                    session, profile, w.year, categories[code], line, 12.0, admin
                )
    for profile in w.assistants:
        for code in ("NUITS_LJ", "WEEKENDS_VEILLES"):
            quota_service.set_target(
                session, profile, w.year, categories[code], Line.L1, 16.0, admin
            )

    w.campaign = campaign_service.create_campaign(
        session,
        quarter,
        opens_at=CAMPAIGN_OPEN,
        deadline_at=CAMPAIGN_DEADLINE,
        admin=admin,
        grace_period_hours=48,
        requirement=HolidayRequirement.VERT_ORANGE,
    )
    campaign_service.open_campaign(session, w.campaign, admin)

    Clock.freeze(SAISIE_MOMENT)
    for profile in w.seniors + w.assistants:
        submission = w.submission(profile)
        for occurrence in w.occurrences:
            w.set_color(profile, occurrence, Color.VERT)
        submission.state = submission.state.__class__.BROUILLON
    session.flush()
    session.commit()
    return w


def validate_all(w: World) -> None:
    for profile in w.seniors + w.assistants:
        submission = w.submission(profile)
        if not submission.is_finalised:
            campaign_service.validate_submission(w.session, submission)
    w.session.flush()


def close_and_prepare(w: World) -> None:
    """Valide toutes les réponses, clôture la campagne et la rend prête."""
    validate_all(w)
    Clock.freeze(CAMPAIGN_DEADLINE + timedelta(minutes=30))
    campaign_service.close_campaign(w.session, w.campaign, w.admin)
    w.session.flush()


def publish_plan(w: World, seed: int = 4242):
    """Génère, valide et publie un planning. Retourne la version publiée."""
    close_and_prepare(w)
    Clock.freeze(APRES_GRACE)
    run = planning_service.run_engine(
        w.session, w.quarter, admin=w.admin, seed=seed, variants=1
    )
    assert run.blocked_reason is None, run.blocked_reason
    proposal = run.proposals[0]
    version = planning_service.create_version_from_proposal(
        w.session, proposal, w.admin, note="test"
    )
    planning_service.validate_version(w.session, version, w.admin)
    planning_service.publish_version(w.session, version, w.admin)
    w.session.flush()
    w.version = version
    return version
