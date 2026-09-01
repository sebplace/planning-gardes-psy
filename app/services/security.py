"""Authentification et permissions du prototype.

Rappel : comptes et données **entièrement fictifs**. Le hachage PBKDF2 est suffisant
pour une démonstration ; une production exigerait un facteur de travail mémoire
(Argon2id) et une revue de sécurité complète.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ProfessionalProfile, User

ITERATIONS = 120_000


class PermissionError_(Exception):
    """Droit insuffisant."""


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), expected)


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.execute(
        select(User).where(User.email == email.strip().lower())
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# --------------------------------------------------------------------------- #
# Permissions — les droits applicatifs sont indépendants du statut professionnel
# --------------------------------------------------------------------------- #


def require_admin(user: User | None) -> User:
    if user is None or not user.is_admin:
        raise PermissionError_("Cette action est réservée aux administrateurs.")
    return user


def require_medecin(user: User | None) -> User:
    if user is None or not user.is_medecin:
        raise PermissionError_("Cette action est réservée aux médecins.")
    return user


def require_authenticated(user: User | None) -> User:
    if user is None:
        raise PermissionError_("Authentification requise.")
    return user


def can_view_profile_details(user: User | None, profile: ProfessionalProfile) -> bool:
    """Un médecin ne voit que ses propres quotas, désidératas et historiques.

    Aucune comparaison nominative entre collègues n'est exposée à un non-administrateur.
    """
    if user is None:
        return False
    if user.is_admin:
        return True
    return profile.user_id == user.id


def assert_can_view_profile_details(user: User | None, profile: ProfessionalProfile) -> None:
    if not can_view_profile_details(user, profile):
        raise PermissionError_(
            "Les quotas et désidératas d'un collègue ne sont pas consultables."
        )
