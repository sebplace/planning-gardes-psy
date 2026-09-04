"""Dépendances FastAPI partagées : session utilisateur et contrôle des droits."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import ProfessionalProfile, User
from ..services import http_security, permission_service


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None

    # Durée de session : inactivité et durée absolue, plus courtes pour un
    # compte disposant d'un accès administratif (lot 5, point 3).
    acces_admin = permission_service.has_administrative_access(session, user)
    motif = http_security.session_expiree(request.session, acces_admin)
    if motif is not None:
        request.session.clear()
        return None
    http_security.marquer_activite(request.session)
    return user


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(401, "Authentification requise.")
    return user


def require_admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Cette action est réservée aux administrateurs.")
    return user


def require_medecin_user(user: User = Depends(current_user)) -> User:
    if not user.is_medecin:
        raise HTTPException(403, "Cette action est réservée aux médecins.")
    return user


def profile_medecin(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ProfessionalProfile:
    """Profil professionnel **actif** de l'utilisateur courant.

    Lot 5, point 7 du contre-audit : la révocation de ``is_medecin`` doit être
    appliquée à **tous** les points d'entrée métier, pas seulement à l'écran de
    connexion. Cette dépendance est le point unique de vérification.
    """
    if not user.is_medecin:
        raise HTTPException(
            403,
            "Ce compte n'est plus enregistré comme médecin : les opérations "
            "métier lui sont fermées.",
        )
    profil = profile_of(session, user)
    if profil is None:
        raise HTTPException(
            403, "Aucun profil professionnel actif rattaché à ce compte."
        )
    return profil


def profile_of(session: Session, user: User) -> ProfessionalProfile | None:
    """Profil professionnel rattaché à un compte.

    Retourne ``None`` si le compte n'est **plus** enregistré comme médecin :
    la révocation de ``is_medecin`` prend ainsi effet sur **tous** les points
    d'entrée métier, sans dépendre d'un contrôle répété route par route
    (lot 5, point 7 du contre-audit du 04/09/2026).
    """
    if not user.is_medecin:
        return None
    return session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()


def require_administrative_access(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> User:
    """Accès administratif ouvert aux trois fonctions confirmées par le client.

    Responsable des gardes de première ligne, responsable des gardes de deuxième
    ligne et chef de service. Les autres médecins restent non administrateurs.
    """
    if not permission_service.has_administrative_access(session, user):
        raise HTTPException(
            403,
            "Accès administratif requis. Il est ouvert aux responsables des "
            "gardes de première ligne, aux responsables des gardes de deuxième "
            "ligne et au chef de service.",
        )
    return user


def require_action(action: str):
    """Dépendance FastAPI exigeant une **action nommée** de la matrice.

    C'est le pendant en API de ce que fait l'interface : les deux couches
    consultent la même matrice action × rôle × ligne.
    """

    def _dependency(
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> User:
        if not permission_service.may(session, user, action):
            raise HTTPException(403, permission_service.refus(action))
        return user

    return _dependency


def require_permission(code: str):
    """Dépendance FastAPI exigeant une permission précise (P1.19).

    Un administrateur détient toutes les permissions ; une délégation datée
    suffit à un non-administrateur.
    """

    def _dependency(
        user: User = Depends(current_user),
        session: Session = Depends(get_session),
    ) -> User:
        if not permission_service.has_permission(session, user, code):
            raise HTTPException(
                403,
                f"Droit « {permission_service.LIBELLES.get(code, code)} » requis "
                "pour cette action.",
            )
        return user

    return _dependency
