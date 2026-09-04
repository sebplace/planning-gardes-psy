"""Journal d'audit chaîné par empreinte.

``hash = sha256(prev_hash || charge utile canonique)``. Toute réécriture a
posteriori casse la chaîne et devient détectable (DECISIONS.md D-006).

Lot D du contre-audit du 04/09/2026 : deux écritures **réellement concurrentes**
lisaient la même tête de chaîne et produisaient une fourche silencieuse. Deux
protections cumulées :

1. **prévention** — la tête de chaîne est sérialisée par un verrou consultatif
   de transaction sous PostgreSQL ; la seconde écriture attend la validation de
   la première puis relit la tête réelle ;
2. **détection** — un index unique sur ``prev_hash`` interdit à deux événements
   de partager le même prédécesseur. Même si le verrou était contourné, la
   fourche serait refusée par la base, jamais commise en silence.

La chaîne n'est pas qualifiée d'inviolable pour autant : elle détecte une
réécriture et refuse une fourche, mais un ancrage externe reste à mettre en
place avant toute donnée réelle.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import AuditEvent, User
from .clock import Clock

#: Clé du verrou consultatif de transaction protégeant la tête de chaîne.
VERROU_TETE_AUDIT = 748213


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def _serialiser_la_tete(session: Session) -> None:
    """Sérialise l'accès à la tête de chaîne.

    Sous PostgreSQL, un verrou consultatif de transaction fait attendre la
    seconde écriture jusqu'à la validation de la première : elle lit alors la
    tête réelle et chaîne correctement. Sous SQLite, les écritures sont déjà
    sérialisées par le verrou de base.
    """
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:cle)"), {"cle": VERROU_TETE_AUDIT}
        )


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
    _serialiser_la_tete(session)
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
