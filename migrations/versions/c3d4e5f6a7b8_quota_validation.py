"""Lot E : validation institutionnelle explicite d'une cible de quota.

Le contre-audit demandait de vraies routes d'écriture de quotas, avec un
périmètre objet × ligne, et de distinguer la saisie de la validation. Une cible
saisie reste une valeur de simulation tant que le chef de service ne l'a pas
validée : cette colonne matérialise cette distinction.

Aucune cible existante n'est validée par la migration : la valeur par défaut est
explicitement fausse. Réversible.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quota_targets",
        sa.Column(
            "institutionally_validated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("quota_targets", "institutionally_validated")
