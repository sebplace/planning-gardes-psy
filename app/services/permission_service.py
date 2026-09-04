"""Attribution, révocation et vérification des six permissions distinctes.

Exigence P1.19. Principes :

* chaque permission est attribuée séparément, datée et journalisée ;
* une révocation pose une date de fin, elle n'efface rien ;
* un administrateur conserve l'ensemble des droits, ce qui préserve le
  fonctionnement actuel tout en permettant des délégations fines à des
  non-administrateurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    LIBELLES,
    LIGNES_SUPERVISEES,
    PERMISSIONS,
    ROLES_ADMINISTRATIFS,
    PermissionGrant,
    User,
)
from . import audit_service
from .clock import Clock


class PermissionError_(Exception):
    """Refus de droit, distinct des erreurs techniques."""


@dataclass
class LignePermission:
    code: str
    label: str
    accordee: bool
    origine: str  # "administrateur" ou "attribution datée"
    depuis: date | None = None


def grant(
    session: Session,
    user: User,
    code: str,
    granted_by: User | None,
    start_date: date | None = None,
    comment: str | None = None,
) -> PermissionGrant:
    if code not in PERMISSIONS:
        raise PermissionError_(f"Permission inconnue : {code}.")
    debut = start_date or Clock.now().date()

    existante = session.execute(
        select(PermissionGrant).where(
            PermissionGrant.user_id == user.id,
            PermissionGrant.code == code,
            PermissionGrant.start_date == debut,
        )
    ).scalar_one_or_none()
    if existante is not None:
        existante.active = True
        existante.end_date = None
        existante.comment = comment
        session.flush()
        ligne = existante
    else:
        ligne = PermissionGrant(
            user_id=user.id,
            code=code,
            start_date=debut,
            granted_by_id=granted_by.id if granted_by else None,
            granted_at=Clock.now(),
            comment=comment,
        )
        session.add(ligne)
        session.flush()

    audit_service.record(
        session,
        "PERMISSION_ACCORDEE",
        "permission_grant",
        ligne.id,
        {
            "beneficiaire": user.email,
            "permission": code,
            "libelle": LIBELLES[code],
            "depuis": debut.isoformat(),
        },
        actor=granted_by,
    )
    return ligne


def revoke(
    session: Session,
    user: User,
    code: str,
    revoked_by: User | None,
    end_date: date | None = None,
) -> int:
    """Pose une date de fin. Aucune ligne n'est supprimée."""
    fin = end_date or Clock.now().date()
    lignes = list(
        session.execute(
            select(PermissionGrant).where(
                PermissionGrant.user_id == user.id,
                PermissionGrant.code == code,
                PermissionGrant.active.is_(True),
            )
        ).scalars()
    )
    for ligne in lignes:
        ligne.end_date = fin
        ligne.active = False
    session.flush()
    if lignes:
        audit_service.record(
            session,
            "PERMISSION_RETIREE",
            "permission_grant",
            lignes[0].id,
            {
                "beneficiaire": user.email,
                "permission": code,
                "jusqu_au": fin.isoformat(),
                "lignes": [ligne.id for ligne in lignes],
            },
            actor=revoked_by,
        )
    return len(lignes)


def has_permission(
    session: Session, user: User | None, code: str, jour: date | None = None
) -> bool:
    """Vrai si le compte détient la permission à cette date.

    Un administrateur les détient toutes : c'est le comportement actuel, conservé
    pour ne pas rendre l'application inutilisable pendant la mise en place des
    délégations fines.
    """
    if user is None or not user.is_active:
        return False
    if user.is_admin:
        return True
    jour = jour or Clock.now().date()
    for ligne in session.execute(
        select(PermissionGrant).where(
            PermissionGrant.user_id == user.id, PermissionGrant.code == code
        )
    ).scalars():
        if ligne.covers(jour):
            return True
    return False


def require(session: Session, user: User | None, code: str) -> None:
    if not has_permission(session, user, code):
        raise PermissionError_(
            f"Droit « {LIBELLES.get(code, code)} » requis pour cette action."
        )


# --------------------------------------------------------------------------- #
# Accès administratif attaché à une fonction
# --------------------------------------------------------------------------- #


def administrative_roles(session: Session, user: User | None) -> list[str]:
    """Fonctions administratives réellement exercées par ce compte aujourd'hui."""
    if user is None or not user.is_active:
        return []
    return [
        code
        for code in ROLES_ADMINISTRATIFS
        if has_permission(session, user, code)
    ]


def has_administrative_access(session: Session, user: User | None) -> bool:
    """Vrai si le compte exerce une fonction ouvrant l'accès administratif.

    Confirmé par le client le 04/09/2026 : responsable des gardes 1, responsable
    des gardes 2 et chef de service disposent des droits administratifs
    nécessaires à leur fonction. Les autres médecins restent non administrateurs.
    """
    if user is None or not user.is_active:
        return False
    if user.is_admin:
        return True
    return bool(administrative_roles(session, user))


def supervised_lines(session: Session, user: User | None) -> set[str]:
    """Lignes de garde supervisées, ce qui distingue les trois fonctions.

    Responsable des gardes 1 : première ligne. Responsable des gardes 2 :
    deuxième ligne. Chef de service et administrateur global : les deux.
    """
    if user is None or not user.is_active:
        return set()
    if user.is_admin:
        return {"L1", "L2"}
    lignes: set[str] = set()
    for code in administrative_roles(session, user):
        lignes.update(LIGNES_SUPERVISEES.get(code, ()))
    return lignes


def supervises_line(session: Session, user: User | None, line: str) -> bool:
    return line in supervised_lines(session, user)


def require_administrative_access(session: Session, user: User | None) -> None:
    if not has_administrative_access(session, user):
        raise PermissionError_(
            "Accès administratif requis. Il est ouvert aux responsables des "
            "gardes de première ligne, aux responsables des gardes de deuxième "
            "ligne et au chef de service."
        )


def require_line_supervision(session: Session, user: User | None, line: str) -> None:
    """Garde **métier** du périmètre de ligne.

    Appelée par le service, donc par tous les chemins d'appel à la fois :
    interface web, API JSON et scripts. Un contrôle posé uniquement dans une
    couche de présentation serait contournable par l'autre.
    """
    require_administrative_access(session, user)
    if not supervises_line(session, user, line):
        raise PermissionError_(
            f"Cette opération porte sur la ligne {line}. Elle relève du "
            f"responsable des gardes de cette ligne ou du chef de service."
        )


def matrix(session: Session, user: User) -> list[LignePermission]:
    """Vue lisible des six permissions pour un compte donné."""
    jour = Clock.now().date()
    lignes = []
    for code in PERMISSIONS:
        if user.is_admin:
            lignes.append(
                LignePermission(
                    code=code,
                    label=LIBELLES[code],
                    accordee=True,
                    origine="administrateur",
                )
            )
            continue
        accordee = None
        for ligne in session.execute(
            select(PermissionGrant).where(
                PermissionGrant.user_id == user.id, PermissionGrant.code == code
            )
        ).scalars():
            if ligne.covers(jour):
                accordee = ligne
                break
        lignes.append(
            LignePermission(
                code=code,
                label=LIBELLES[code],
                accordee=accordee is not None,
                origine="attribution datée" if accordee else "aucune",
                depuis=accordee.start_date if accordee else None,
            )
        )
    return lignes


def holders(session: Session, code: str) -> list[User]:
    """Comptes détenant effectivement une permission aujourd'hui."""
    jour = Clock.now().date()
    out: list[User] = []
    for user in session.execute(select(User).order_by(User.email)).scalars():
        if not user.is_active:
            continue
        if user.is_admin or any(
            ligne.covers(jour)
            for ligne in session.execute(
                select(PermissionGrant).where(
                    PermissionGrant.user_id == user.id, PermissionGrant.code == code
                )
            ).scalars()
        ):
            out.append(user)
    return out
