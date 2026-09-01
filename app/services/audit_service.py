"""Journal d'audit chaîné par empreinte.

``hash = sha256(prev_hash || charge utile canonique)``. Toute réécriture a
posteriori casse la chaîne et devient détectable (DECISIONS.md D-006).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditEvent, User
from .clock import Clock


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def last_hash(session: Session) -> str:
    row = session.execute(
        select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1)
    ).scalar_one_or_none()
    return row or ""


def record(
    session: Session,
    action: str,
    entity_type: str,
    entity_id: Any,
    payload: dict[str, Any] | None = None,
    actor: User | None = None,
    actor_label: str | None = None,
) -> AuditEvent:
    """Ajoute un événement au journal. Ne commite pas : l'appelant maîtrise sa transaction."""
    payload = payload or {}
    at = Clock.now()
    prev = last_hash(session)
    body = _canonical(
        {
            "at": at.isoformat(),
            "actor": actor.email if actor else (actor_label or "SYSTEME"),
            "action": action,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "payload": payload,
        }
    )
    digest = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
    event = AuditEvent(
        at=at,
        actor_user_id=actor.id if actor else None,
        actor_label=actor.email if actor else (actor_label or "SYSTEME"),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        payload_json=_canonical(payload),
        prev_hash=prev,
        hash=digest,
    )
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session) -> tuple[bool, list[str]]:
    """Vérifie l'intégrité de la chaîne. Utilisé par l'écran d'audit."""
    problems: list[str] = []
    prev = ""
    for event in session.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars():
        body = _canonical(
            {
                "at": event.at.isoformat(),
                "actor": event.actor_label,
                "action": event.action,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "payload": json.loads(event.payload_json or "{}"),
            }
        )
        expected = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
        if event.prev_hash != prev:
            problems.append(f"Événement {event.id} : chaînage rompu (prev_hash inattendu).")
        if event.hash != expected:
            problems.append(f"Événement {event.id} : empreinte non conforme au contenu.")
        prev = event.hash
    return (not problems), problems
