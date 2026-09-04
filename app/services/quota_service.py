"""Quotas : suivi annuel, ajustements après reprise, confidentialité.

Un médecin ne voit **que ses propres** quotas. Aucune comparaison nominative n'est
exposée à un non-administrateur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Assignment,
    CoveragePost,
    Enforcement,
    GardeOccurrence,
    GardeType,
    Line,
    MonthlyCap,
    ProfessionalProfile,
    Quarter,
    QuotaAdjustment,
    QuotaCategory,
    QuotaTarget,
    QuotaTargetHistory,
    ScheduleState,
    ScheduleVersion,
    Status,
    User,
    Year,
)
from . import audit_service
from .clock import Clock


@dataclass
class QuotaLine:
    category_code: str
    category_label: str
    line: str
    target: float
    minimum: float | None
    maximum: float | None
    hard_minimum: bool
    hard_maximum: bool
    realise: float = 0.0
    programme: float = 0.0
    ajustements: float = 0.0
    source: str = "MANUEL_ADMIN"

    @property
    def total(self) -> float:
        return round(self.realise + self.programme + self.ajustements, 3)

    @property
    def restant(self) -> float:
        return round(self.target - self.total, 3)

    @property
    def ecart(self) -> float:
        return round(self.total - self.target, 3)


@dataclass
class QuotaSummary:
    profile_code: str
    year_label: str
    profile_id: int | None = None
    lines: list[QuotaLine] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_target(self) -> float:
        return round(sum(line.target for line in self.lines), 2)

    @property
    def total_done(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @property
    def projection(self) -> str:
        gap = round(self.total_target - self.total_done, 2)
        if abs(gap) < 0.5:
            return "trajectoire conforme à la cible annuelle"
        if gap > 0:
            return f"{gap:.1f} garde(s) restant à programmer d'ici la fin de l'année"
        return f"{abs(gap):.1f} garde(s) au-dessus de la cible annuelle"


def summary(session: Session, profile: ProfessionalProfile, year: Year) -> QuotaSummary:
    """Cible, réalisé, programmé, restant, écart et projection — pour **une** personne."""
    categories = {c.id: c for c in session.execute(select(QuotaCategory)).scalars()}
    result = QuotaSummary(
        profile_code=profile.code, year_label=year.label, profile_id=profile.id
    )
    index: dict[tuple[str, str], QuotaLine] = {}

    for target in session.execute(
        select(QuotaTarget).where(
            QuotaTarget.profile_id == profile.id, QuotaTarget.year_id == year.id
        )
    ).scalars():
        category = categories[target.category_id]
        line = QuotaLine(
            category_code=category.code,
            category_label=category.label,
            line=target.line.value,
            target=target.target,
            minimum=target.minimum,
            maximum=target.maximum,
            hard_minimum=target.hard_minimum,
            hard_maximum=target.hard_maximum,
            source=target.source,
        )
        result.lines.append(line)
        index[(category.code, target.line.value)] = line

    now = Clock.now()
    rows = session.execute(
        select(Assignment, CoveragePost, GardeOccurrence, GardeType, QuotaCategory)
        .join(CoveragePost, Assignment.post_id == CoveragePost.id)
        .join(GardeOccurrence, CoveragePost.occurrence_id == GardeOccurrence.id)
        .join(GardeType, GardeOccurrence.garde_type_id == GardeType.id)
        .join(QuotaCategory, GardeType.category_id == QuotaCategory.id)
        .join(Quarter, GardeOccurrence.quarter_id == Quarter.id)
        .join(ScheduleVersion, Assignment.schedule_version_id == ScheduleVersion.id)
        .where(
            Assignment.profile_id == profile.id,
            ScheduleVersion.state == ScheduleState.PUBLIE,
            Quarter.year_id == year.id,
        )
    ).all()
    for _assignment, post, occurrence, garde_type, category in rows:
        key = (category.code, post.line.value)
        if key not in index:
            line = QuotaLine(
                category_code=category.code, category_label=category.label,
                line=post.line.value, target=0.0, minimum=None, maximum=None,
                hard_minimum=False, hard_maximum=False,
            )
            result.lines.append(line)
            index[key] = line
        if occurrence.end_at <= now:
            index[key].realise += garde_type.count_weight
        else:
            index[key].programme += garde_type.count_weight

    for adj in session.execute(
        select(QuotaAdjustment).where(
            QuotaAdjustment.profile_id == profile.id, QuotaAdjustment.year_id == year.id
        )
    ).scalars():
        category = categories[adj.category_id]
        key = (category.code, adj.line.value)
        if key not in index:
            line = QuotaLine(
                category_code=category.code, category_label=category.label,
                line=adj.line.value, target=0.0, minimum=None, maximum=None,
                hard_minimum=False, hard_maximum=False,
            )
            result.lines.append(line)
            index[key] = line
        index[key].ajustements += adj.delta

    result.lines.sort(key=lambda item: (item.category_code, item.line))
    if any(line.source == "MANUEL_ADMIN" for line in result.lines):
        result.notes.append(
            "Cibles saisies manuellement : la formule institutionnelle fondée sur la "
            "quotité/TIMA n'est pas stabilisée (OPEN_QUESTIONS.md Q-01)."
        )
    return result


def set_target(
    session: Session,
    profile: ProfessionalProfile,
    year: Year,
    category: QuotaCategory,
    line: Line,
    target: float,
    admin: User,
    minimum: float | None = None,
    maximum: float | None = None,
    hard_minimum: bool = False,
    hard_maximum: bool = False,
    comment: str | None = None,
) -> QuotaTarget:
    row = session.execute(
        select(QuotaTarget).where(
            QuotaTarget.profile_id == profile.id,
            QuotaTarget.year_id == year.id,
            QuotaTarget.category_id == category.id,
            QuotaTarget.line == line,
        )
    ).scalar_one_or_none()
    old = row.target if row else None
    if row is None:
        row = QuotaTarget(
            profile_id=profile.id, year_id=year.id, category_id=category.id, line=line
        )
        session.add(row)
    row.target = target
    row.minimum = minimum
    row.maximum = maximum
    row.hard_minimum = hard_minimum
    row.hard_maximum = hard_maximum
    row.comment = comment
    row.source = "MANUEL_ADMIN"
    session.flush()
    session.add(
        QuotaTargetHistory(
            quota_target_id=row.id, author_id=admin.id if admin else None,
            old_target=old, new_target=target, comment=comment,
        )
    )
    audit_service.record(
        session, "QUOTA_MODIFIE", "quota_target", row.id,
        {"profil": profile.code, "categorie": category.code, "ligne": line.value,
         "ancienne_cible": old, "nouvelle_cible": target}, actor=admin,
    )
    return row


# --------------------------------------------------------------------------- #
# Plafond mensuel — administrable, jamais inventé
# --------------------------------------------------------------------------- #


def set_monthly_cap(
    session: Session,
    year: Year,
    admin: User,
    status: Status | None = None,
    profile: ProfessionalProfile | None = None,
    max_per_month: float | None = None,
    enforcement: Enforcement = Enforcement.SOUPLE,
    institutionally_validated: bool = False,
    label: str = "plafond mensuel",
    comment: str | None = None,
) -> MonthlyCap:
    """Crée ou met à jour un plafond mensuel.

    Le plafond reste **non opposable** tant qu'il n'est pas à la fois chiffré,
    validé institutionnellement et déclaré ferme. Une valeur de simulation ne
    devient donc jamais une règle par simple saisie.
    """
    if status is None and profile is None:
        raise ValueError("Un plafond mensuel vise soit un statut, soit un profil.")

    row = session.execute(
        select(MonthlyCap).where(
            MonthlyCap.year_id == year.id,
            MonthlyCap.status == status,
            MonthlyCap.profile_id == (profile.id if profile else None),
        )
    ).scalar_one_or_none()
    ancien = row.max_per_month if row else None
    if row is None:
        row = MonthlyCap(
            year_id=year.id,
            status=status,
            profile_id=profile.id if profile else None,
        )
        session.add(row)
    row.max_per_month = max_per_month
    row.enforcement = enforcement
    row.institutionally_validated = institutionally_validated
    row.label = label
    row.comment = comment
    row.created_by_id = admin.id if admin else None
    session.flush()

    audit_service.record(
        session,
        "PLAFOND_MENSUEL_MODIFIE",
        "monthly_cap",
        row.id,
        {
            "portee": profile.code if profile else (status.value if status else None),
            "ancien_plafond": ancien,
            "nouveau_plafond": max_per_month,
            "caractere": enforcement.value,
            "valide_institutionnellement": institutionally_validated,
            "opposable": row.is_enforceable,
        },
        actor=admin,
    )
    return row


def monthly_caps(session: Session, year: Year) -> list[MonthlyCap]:
    return list(
        session.execute(
            select(MonthlyCap)
            .where(MonthlyCap.year_id == year.id)
            .order_by(MonthlyCap.id)
        ).scalars()
    )


def monthly_cap_alerts(session: Session, year: Year) -> list[str]:
    """Alertes à afficher avant tout planning officiel.

    Un statut sans aucun plafond enregistré produit également une alerte : la
    valeur institutionnelle est attendue, elle n'est jamais devinée.
    """
    rows = monthly_caps(session, year)
    alertes = [message for row in rows if (message := row.alert)]

    couverts = {row.status for row in rows if row.status is not None}
    for status in (Status.SENIOR, Status.ASSISTANT):
        présents = session.execute(
            select(ProfessionalProfile.id).where(ProfessionalProfile.status == status)
        ).first()
        if présents is not None and status not in couverts:
            alertes.append(
                f"Aucun plafond mensuel enregistré pour le statut {status.value}. "
                "Valeur institutionnelle attendue avant tout planning officiel."
            )
    alertes.append(OBJECTIF_MENSUEL_ASSISTANT.alerte)
    return alertes


# --------------------------------------------------------------------------- #
# Objectif mensuel de répartition des assistants — paramètre inactif
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ObjectifMensuelAssistant:
    """Répartition mensuelle souhaitée pour un assistant.

    Lot C, point 7 du contre-audit du 04/09/2026 : le caractère opposable de
    « un vendredi + deux jours de week-end par mois » n'a **pas** été tranché
    institutionnellement. Le paramètre existe donc, il est lisible et
    modifiable, mais il est **inactif** : ni le moteur ni les reprises ni les
    échanges ne le consultent. Le rendre opposable est une décision humaine.
    """

    vendredis_par_mois: float = 1.0
    jours_de_week_end_par_mois: float = 2.0
    #: Jamais vrai tant qu'aucune décision institutionnelle n'est enregistrée.
    actif: bool = False
    label: str = "objectif mensuel de répartition des assistants"

    @property
    def opposable(self) -> bool:
        """Toujours faux : un objectif inactif ne bloque jamais une affectation."""
        return False

    @property
    def alerte(self) -> str:
        return (
            f"{self.label} : hypothèse de simulation "
            f"({self.vendredis_par_mois:g} vendredi et "
            f"{self.jours_de_week_end_par_mois:g} jours de week-end par mois), "
            "inactive tant qu'aucune décision institutionnelle explicite n'est "
            "enregistrée. Décision humaine attendue."
        )

    def as_dict(self) -> dict:
        return {
            "libelle": self.label,
            "vendredis_par_mois": self.vendredis_par_mois,
            "jours_de_week_end_par_mois": self.jours_de_week_end_par_mois,
            "actif": self.actif,
            "opposable": self.opposable,
            "note": (
                "paramètre configurable et inactif ; aucune règle ferme n'en "
                "dérive tant que le client n'a pas tranché"
            ),
        }


#: Valeur de démonstration, explicitement inactive.
OBJECTIF_MENSUEL_ASSISTANT = ObjectifMensuelAssistant()


def apply_handover_adjustment(
    session: Session,
    replaced: ProfessionalProfile,
    taker: ProfessionalProfile,
    year: Year,
    category: QuotaCategory,
    line: Line,
    weight: float,
    source_ref: str,
) -> None:
    """Après une reprise : la garde est comptabilisée à la personne qui l'assure
    réellement et retirée du compteur de la personne remplacée.

    L'écart résiduel de la personne remplacée est **reporté dans le suivi** et pris
    en compte lors de la campagne suivante, sans remanier le planning publié
    (DECISIONS.md M-007). Le transfert d'affectation suffit au comptage courant ;
    l'ajustement enregistré ici sert au report inter-campagne.
    """
    session.add(
        QuotaAdjustment(
            profile_id=replaced.id, year_id=year.id, category_id=category.id, line=line,
            delta=0.0,
            reason=(
                f"Reprise : garde transférée à {taker.code}. Écart de {weight:g} garde "
                "reporté pour la campagne suivante."
            ),
            source_ref=source_ref, carried_to_next_campaign=True,
        )
    )
    session.flush()


def admin_overview(session: Session, year: Year) -> list[QuotaSummary]:
    """Vue administrative du pool complet, avec risques de rattrapage de fin d'année."""
    out = []
    for profile in session.execute(select(ProfessionalProfile)).scalars():
        item = summary(session, profile, year)
        if item.lines:
            gap = item.total_target - item.total_done
            if gap > 3:
                item.notes.append(
                    f"Risque de rattrapage : {gap:.1f} garde(s) à programmer d'ici la fin d'année."
                )
            out.append(item)
    return out
