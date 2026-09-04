"""P2.10 : une candidature retirée n'est jamais tirable.

Ajoute l'état ``RETIREE`` à la contrainte CHECK de ``candidacies.state``.

Revision ID: b8c7d6e5f432
Revises: a7b6c5d4e321
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c7d6e5f432"
down_revision = "a7b6c5d4e321"
branch_labels = None
depends_on = None

ANCIENS = ("DEPOSEE", "VALIDE", "EXCLUE", "RETENUE", "NON_RETENUE")
NOUVEAUX = ("DEPOSEE", "RETIREE", "VALIDE", "EXCLUE", "RETENUE", "NON_RETENUE")
CONTRAINTE = "candidacystate"


def _recreer_contrainte(valeurs: tuple[str, ...]) -> None:
    """Remplace la contrainte CHECK de l'énumération portable.

    SQLite ne sait pas modifier une contrainte en place : on passe par le mode
    batch, qui reconstruit la table. PostgreSQL applique directement.
    """
    with op.batch_alter_table("candidacies") as batch:
        batch.alter_column(
            "state",
            existing_type=sa.String(length=40),
            type_=sa.Enum(
                *valeurs, name=CONTRAINTE, native_enum=False, length=40
            ),
            existing_nullable=False,
        )


def upgrade() -> None:
    _recreer_contrainte(NOUVEAUX)


def downgrade() -> None:
    # Aucune candidature retirée ne doit subsister avant de restreindre la
    # contrainte : on les repositionne en EXCLUE, sémantiquement le plus proche.
    op.get_bind().execute(
        sa.text("UPDATE candidacies SET state = 'EXCLUE' WHERE state = 'RETIREE'")
    )
    _recreer_contrainte(ANCIENS)
