"""Quotas de période : création, suivi et alertes.

Lot 2, point 1 du contre-audit du 04/09/2026. Le quota assistant 57/68 devient
un compteur **opérationnel**, opposable au moteur, et non plus un simple calcul
de projection.

Principes conservés du reste de l'application :

* la période est explicite et à cheval sur deux années civiles si nécessaire ;
* le rattachement se fait sur la **date de service** ;
* un maximum n'est opposable qu'après trois verrous cumulés (chiffré, validé
  institutionnellement, déclaré ferme) ;
* rien n'est inventé : le client n'a pas tranché entre 57 et 68, donc aucune
  valeur par défaut n'est écrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Assignment,
    CoveragePost,
    Enforcement,
    GardeOccurrence,
    GardeType,
    PeriodQuota,
    ProfessionalProfile,
    ScheduleState,
    ScheduleVersion,
    Status,
    User,
)
from . import audit_service

#: Période d'assistanat transmise par le client, bornes de dates de service.
ASSISTANTS_DEBUT = date(2026, 10, 19)
ASSISTANTS_FIN = date(2027, 10, 3)  # incluse
CODE_QUOTA_ASSISTANTS = "ASSISTANTS_2026_2027"


class PeriodQuotaError(Exception):
    pass


@dataclass
class SuiviPeriode:
    code: str
    label: str
    profile_code: str
    debut: date
    fin: date
    cible: float
    maximum: float | None
    opposable: bool
    realise: float
    programme: float

    @property
    def total(self) -> float:
        return round(self.realise + self.programme, 3)

    @property
    def restant_cible(self) -> float:
        return round(self.cible - self.total, 3)

    @property
    def restant_maximum(self) -> float | None:
        if self.maximum is None:
            return None
        return round(self.maximum - self.total, 3)

    @property
    def depasse_le_maximum(self) -> bool:
        return self.maximum is not None and self.total > self.maximum + 1e-9


# --------------------------------------------------------------------------- #
# Administration
# --------------------------------------------------------------------------- #


def set_period_quota(
    session: Session,
    admin: User | None,
    code: str,
    label: str,
    start_date: date,
    end_date: date,
    target: float,
    status: Status | None = None,
    profile: ProfessionalProfile | None = None,
    maximum: float | None = None,
    enforcement: Enforcement = Enforcement.SOUPLE,
    institutionally_validated: bool = False,
    comment: str | None = None,
) -> PeriodQuota:
    if status is None and profile is None:
        raise PeriodQuotaError(
            "Un quota de période vise soit un statut, soit un profil précis."
        )
    if end_date < start_date:
        raise PeriodQuotaError("La fin de période précède son début.")

    row = session.execute(
        select(PeriodQuota).where(
            PeriodQuota.code == code,
            PeriodQuota.status == status,
            PeriodQuota.profile_id == (profile.id if profile else None),
        )
    ).scalar_one_or_none()
    ancien = row.maximum if row else None
    if row is None:
        row = PeriodQuota(
            code=code,
            status=status,
            profile_id=profile.id if profile else None,
        )
        session.add(row)
    row.label = label
    row.start_date = start_date
    row.end_date = end_date
    row.target = target
    row.maximum = maximum
    row.enforcement = enforcement
    row.institutionally_validated = institutionally_validated
    row.comment = comment
    row.created_by_id = admin.id if admin else None
    session.flush()

    audit_service.record(
        session,
        "QUOTA_PERIODE_MODIFIE",
        "period_quota",
        row.id,
        {
            "code": code,
            "portee": profile.code if profile else (status.value if status else None),
            "periode": f"{start_date.isoformat()} → {end_date.isoformat()}",
            "cible": target,
            "ancien_maximum": ancien,
            "nouveau_maximum": maximum,
            "opposable": row.is_enforceable,
        },
        actor=admin,
    )
    return row


def period_quotas(session: Session) -> list[PeriodQuota]:
    return list(
        session.execute(select(PeriodQuota).order_by(PeriodQuota.id)).scalars()
    )


def alerts(session: Session) -> list[str]:
    return [message for row in period_quotas(session) if (message := row.alert)]


# --------------------------------------------------------------------------- #
# Suivi
# --------------------------------------------------------------------------- #


def _charge_publiee(
    session: Session, profile: ProfessionalProfile, debut: date, fin: date, now
) -> tuple[float, float]:
    """Retourne ``(réalisé, programmé)`` sur la période, en poids de décompte."""
    rows = session.execute(
        select(GardeType.count_weight, GardeOccurrence.end_at)
        .select_from(Assignment)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            Assignment.profile_id == profile.id,
            ScheduleVersion.state == ScheduleState.PUBLIE,
            GardeOccurrence.local_date >= debut,
            GardeOccurrence.local_date <= fin,
        )
    ).all()
    realise = sum(float(w) for w, fin_at in rows if fin_at <= now)
    programme = sum(float(w) for w, fin_at in rows if fin_at > now)
    return realise, programme


def suivi(
    session: Session, profile: ProfessionalProfile, now=None
) -> list[SuiviPeriode]:
    """Suivi de tous les quotas de période applicables à une personne."""
    from .clock import Clock

    now = now or Clock.now()
    out: list[SuiviPeriode] = []
    for row in period_quotas(session):
        vise = (
            row.profile_id == profile.id
            if row.profile_id is not None
            else row.status is profile.status
        )
        if not vise:
            continue
        realise, programme = _charge_publiee(
            session, profile, row.start_date, row.end_date, now
        )
        out.append(
            SuiviPeriode(
                code=row.code,
                label=row.label,
                profile_code=profile.code,
                debut=row.start_date,
                fin=row.end_date,
                cible=row.target,
                maximum=row.maximum,
                opposable=row.is_enforceable,
                realise=round(realise, 3),
                programme=round(programme, 3),
            )
        )
    return out
