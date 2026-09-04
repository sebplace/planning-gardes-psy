"""Lot D : la chaîne d'audit refuse une fourche, au lieu de l'accepter en silence.

Deux écritures réellement concurrentes lisaient la même tête de chaîne. Un
index unique sur ``prev_hash`` interdit désormais à deux événements de partager
le même prédécesseur : la seconde écriture est refusée par la base, jamais
commise silencieusement. Le verrou consultatif applicatif reste la protection
de premier rang ; cet index est le filet de détection.

Réversible.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_audit_prev_hash", "audit_events", ["prev_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_audit_prev_hash", table_name="audit_events")
