"""Service de notifications — découplé du moteur métier.

Canal unique du prototype : **e-mail simulé**, écrit dans une boîte locale
consultable dans l'application. Aucun message réel n'est envoyé.

La file est **idempotente** : la clé métier interdit tout doublon, y compris après
un redémarrage ou une nouvelle tentative technique (DECISIONS.md D-009).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Notification, ProfessionalProfile
from .clock import Clock

# Modèles de message, modifiables sans toucher au moteur.
TEMPLATES: dict[str, tuple[str, str]] = {
    "CAMPAGNE_OUVERTURE": (
        "Campagne de désidératas ouverte — {quarter}",
        "La campagne de désidératas pour {quarter} est ouverte jusqu'au {deadline}.\n"
        "Encodez vos disponibilités (vert, orange, rouge) date par date.",
    ),
    "CAMPAGNE_RAPPEL": (
        "Rappel — désidératas {quarter} (J-{days})",
        "Votre réponse pour {quarter} n'est pas finalisée. Échéance : {deadline}.",
    ),
    "CAMPAGNE_VALIDATION": (
        "Réponse enregistrée — {quarter}",
        "Votre réponse pour {quarter} a été validée le {at}. Vous ne recevrez plus de rappel.",
    ),
    "CAMPAGNE_ECHEANCE": (
        "Échéance atteinte — {quarter}",
        "L'échéance de la campagne {quarter} est atteinte.",
    ),
    "ADMIN_NON_REPONDANTS": (
        "Alerte administrateur — réponses manquantes ({quarter})",
        "{count} personne(s) n'ont pas finalisé leur réponse. "
        "La génération est bloquée tant que la situation n'est pas résolue.",
    ),
    "DISPO_PAR_DEFAUT": (
        "Disponibilité par défaut appliquée — {quarter}",
        "Après relances et délai de grâce, vos dates non renseignées ont été marquées "
        "« disponible par défaut — non confirmé par la personne ». "
        "Ce statut n'est pas une réponse volontaire et reste distinct d'un vert déclaré.",
    ),
    "PLANNING_PUBLIE": (
        "Planning publié — {quarter}",
        "Le planning {quarter} (version {version}) est publié. Consultez vos affectations.",
    ),
    "PLANNING_MODIFIE": (
        "Planning modifié — {quarter}",
        "Une nouvelle version du planning {quarter} a été publiée : version {version}.",
    ),
    "REPRISE_SOLLICITATION": (
        "Garde à reprendre — {date} ({line})",
        "Une garde cherche preneur : {date}, {type_label}, {line}.\n"
        "Fenêtre de réponse jusqu'au {closes_at}.\n"
        "Toutes les réponses favorables sont collectées puis départagées par tirage au sort : "
        "répondre plus vite ne procure aucun avantage.",
    ),
    "REPRISE_RAPPEL": (
        "Rappel — garde à reprendre {date} ({line})",
        "Rappel {index} : la fenêtre de réponse se clôt le {closes_at}.",
    ),
    "REPRISE_CLOTURE_COLLECTE": (
        "Collecte close — garde du {date}",
        "La liste des candidatures est figée. Le tirage au sort va être exécuté.",
    ),
    "REPRISE_TIRAGE_GAGNANT": (
        "Garde attribuée — {date}",
        "Le tirage au sort vous a désigné·e pour la garde du {date} ({line}). "
        "L'attribution est officielle.",
    ),
    "REPRISE_TIRAGE_NON_RETENU": (
        "Garde du {date} — attribuée à une autre personne",
        "La garde du {date} a été attribuée par tirage au sort. Merci de votre disponibilité.",
    ),
    "REPRISE_ECHEC": (
        "Reprise sans candidature — {date}",
        "Aucune candidature valide après les vagues verte et orange. "
        "L'affectation initiale est maintenue et les responsables sont alertés.",
    ),
    "ECHANGE_PROPOSE": (
        "Proposition d'échange — {date_a} contre {date_b}",
        "Une proposition d'échange bilatéral vous est adressée.",
    ),
    "ECHANGE_OFFICIEL": (
        "Échange officialisé — {date_a} / {date_b}",
        "L'échange est officiel. Les compteurs restent inchangés (gardes équivalentes).",
    ),
    "ECHANGE_REFUSE": (
        "Échange refusé — {date_a} / {date_b}",
        "L'échange a été refusé : {reason}",
    ),
    "ECHANGE_SOLLICITATION": (
        "Échange possible — votre garde du {date_reprise}",
        "Une garde du {date_cedee} ({type_label}, {line}) cherche un échange "
        "contre votre garde du {date_reprise}.\n"
        "Fenêtre de réponse jusqu'au {closes_at} (palier : {palier}).\n"
        "Tous les partenaires possibles sont sollicités en même temps : répondre "
        "plus vite ne procure aucun avantage. Aucun motif n'est communiqué.",
    ),
    "ECHANGE_RECHERCHE_OFFICIELLE": (
        "Échange officialisé — {date_cedee} contre {date_reprise}",
        "L'échange est officiel après accord des deux parties et revalidation. "
        "Les compteurs restent inchangés (gardes équivalentes).",
    ),
    "ECHANGE_NON_RETENU": (
        "Échange du {date_cedee} — une autre solution a été retenue",
        "Merci de votre accord. Le classement a retenu une autre permutation. "
        "Votre garde reste inchangée.",
    ),
    "ECHANGE_SANS_SOLUTION": (
        "Échange sans solution — garde du {date_cedee}",
        "Aucun échange praticable n'a abouti : {reason}\n"
        "Votre garde reste à votre charge.",
    ),
}


def render(kind: str, context: dict[str, Any]) -> tuple[str, str]:
    subject, body = TEMPLATES.get(
        kind, ("Notification — {kind}", "Événement : {kind}")
    )
    safe = dict(context)
    safe.setdefault("kind", kind)
    try:
        return subject.format(**safe), body.format(**safe)
    except KeyError:
        return subject, body


def enqueue(
    session: Session,
    kind: str,
    idempotency_key: str,
    recipient: ProfessionalProfile | None,
    context: dict[str, Any] | None = None,
    recipient_label: str | None = None,
    anonymised: bool = False,
) -> Notification | None:
    """Ajoute un message. Retourne ``None`` si la clé existe déjà (aucun doublon)."""
    context = context or {}
    existing = session.execute(
        select(Notification).where(Notification.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return None

    subject, body = render(kind, context)
    label = recipient_label or (
        recipient.user.display_name if recipient and recipient.user else "destinataire fictif"
    )
    body = f"{body}\n\n---\n{settings.demo_banner}\n{settings.patient_data_warning}"
    notification = Notification(
        idempotency_key=idempotency_key,
        kind=kind,
        recipient_profile_id=recipient.id if recipient else None,
        recipient_label=label,
        subject=subject,
        body=body,
        payload_json=json.dumps(context, ensure_ascii=False, default=str),
        created_at=Clock.now(),
        sent_at=Clock.now(),  # « envoi » simulé immédiat dans la boîte locale
        anonymised=anonymised,
    )
    # Point de contre-audit (04/09/2026) : une collision d'idempotence ne doit
    # **jamais** annuler l'opération métier en cours. On isole donc l'insertion
    # dans un savepoint : seule la notification en conflit est annulée, la
    # transaction métier se poursuit intacte.
    point = session.begin_nested()
    session.add(notification)
    try:
        point.commit()
    except IntegrityError:
        point.rollback()
        return None
    return notification


def inbox(session: Session, profile_id: int | None = None, limit: int = 200):
    query = select(Notification).order_by(Notification.id.desc()).limit(limit)
    if profile_id is not None:
        query = query.where(Notification.recipient_profile_id == profile_id)
    return list(session.execute(query).scalars())
