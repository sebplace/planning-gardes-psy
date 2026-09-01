"""Comptes, profils professionnels, périodes d'activité, quotités, éligibilités.

Quatre notions strictement séparées (§5 du cahier des charges) :
statut professionnel · droits applicatifs · éligibilité métier · période d'activité.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Line, Status, TimestampMixin, enum_column


class User(Base, TimestampMixin):
    """Compte applicatif. Les **droits** (`is_medecin`, `is_admin`) sont indépendants
    du **statut professionnel**, et cumulables."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_medecin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    profile: Mapped["ProfessionalProfile | None"] = relationship(
        back_populates="user", uselist=False
    )


class ProfessionalProfile(Base, TimestampMixin):
    __tablename__ = "professional_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    status: Mapped[Status] = enum_column(Status, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="profile")
    activity_periods: Mapped[list["ActivityPeriod"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    quotite_history: Mapped[list["QuotiteHistory"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    eligibilities: Mapped[list["Eligibility"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )

    # ------------------------------------------------------------------ #

    def is_active_on(self, day: date) -> bool:
        """L'expiration d'un assistant est **dérivée** des périodes, jamais figée."""
        if not self.user.is_active:
            return False
        if not self.activity_periods:
            return True
        return any(
            period.start_date <= day and (period.end_date is None or day <= period.end_date)
            for period in self.activity_periods
        )

    def activity_bounds(self) -> tuple[date | None, date | None]:
        if not self.activity_periods:
            return None, None
        starts = [p.start_date for p in self.activity_periods]
        ends = [p.end_date for p in self.activity_periods]
        return min(starts), (None if any(e is None for e in ends) else max(e for e in ends if e))

    def quotite_on(self, day: date) -> int:
        for entry in sorted(self.quotite_history, key=lambda q: q.start_date, reverse=True):
            if entry.start_date <= day and (entry.end_date is None or day <= entry.end_date):
                return entry.tenths
        return 10

    def eligible_for(self, line: Line, garde_type_id: int | None, day: date) -> bool:
        """Une éligibilité explicite prime ; à défaut, la règle structurelle s'applique :
        un assistant n'est jamais éligible à la deuxième ligne."""
        specific = None
        generic = None
        for e in self.eligibilities:
            if e.line is not line:
                continue
            if e.start_date and day < e.start_date:
                continue
            if e.end_date and day > e.end_date:
                continue
            if e.garde_type_id is not None and e.garde_type_id == garde_type_id:
                specific = e
            elif e.garde_type_id is None:
                generic = e
        entry = specific or generic
        if entry is not None:
            value = entry.eligible
        else:
            value = True
        if line is Line.L2 and self.status is not Status.SENIOR:
            return False
        return value

    def excluded_type_ids(self, line: Line, day: date) -> set[int]:
        return {
            e.garde_type_id
            for e in self.eligibilities
            if e.garde_type_id is not None
            and e.line is line
            and not e.eligible
            and (e.start_date is None or e.start_date <= day)
            and (e.end_date is None or day <= e.end_date)
        }


class ActivityPeriod(Base, TimestampMixin):
    __tablename__ = "activity_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    profile: Mapped[ProfessionalProfile] = relationship(back_populates="activity_periods")


class QuotiteHistory(Base, TimestampMixin):
    """Quotité / TIMA en dixièmes. **Donnée**, jamais transformée en quota par le moteur
    tant que la formule institutionnelle n'est pas validée (OPEN_QUESTIONS.md Q-01)."""

    __tablename__ = "quotite_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tenths: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    tima_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped[ProfessionalProfile] = relationship(back_populates="quotite_history")


class Eligibility(Base, TimestampMixin):
    __tablename__ = "eligibilities"
    __table_args__ = (
        UniqueConstraint("profile_id", "line", "garde_type_id", name="uq_eligibility_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    line: Mapped[Line] = enum_column(Line, nullable=False)
    garde_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("garde_types.id"), nullable=True
    )
    eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    comment: Mapped[str | None] = mapped_column(String(300), nullable=True)

    profile: Mapped[ProfessionalProfile] = relationship(back_populates="eligibilities")
