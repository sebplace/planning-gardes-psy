"""Dépendances FastAPI partagées : session utilisateur et contrôle des droits."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import ProfessionalProfile, User
from ..services import permission_service


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
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


def profile_of(session: Session, user: User) -> ProfessionalProfile | None:
    return session.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user.id)
    ).scalar_one_or_none()


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
