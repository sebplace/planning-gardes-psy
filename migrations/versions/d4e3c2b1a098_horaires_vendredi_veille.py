"""P1.9bis : horaires vendredi et veille de férié confirmés (17h -> 9h).

Le client a confirmé le 03/09/2026 que la nuit du vendredi se termine le samedi à
9 h et que la veille ouvrable d'un jour férié se termine le jour férié à 9 h, afin
de supprimer le trou de 8 h à 9 h avant la relève du matin.

Migration de données : seules les lignes encore marquées « horaires à valider »
sont réalignées, afin de ne jamais écraser un horaire saisi par un administrateur.

Revision ID: d4e3c2b1a098
Revises: c3d2e1f0a9b8
Create Date: 2026-09-03
"""

from __future__ import annotations

from datetime import time

import sqlalchemy as sa
from alembic import op

revision = "d4e3c2b1a098"
down_revision = "c3d2e1f0a9b8"
branch_labels = None
depends_on = None

CIBLES = ("NUIT_VENDREDI", "VEILLE_FERIE")

NOUVEAUX_LIBELLES = {
    "NUIT_VENDREDI": "Nuit du vendredi (vendredi 17 h au samedi 9 h)",
    "VEILLE_FERIE": "Veille ouvrable d'un jour férié (17 h au jour férié 9 h)",
}

ANCIENS_LIBELLES = {
    "NUIT_VENDREDI": "Nuit du vendredi",
    "VEILLE_FERIE": "Nuit précédant un jour férié",
}


def _garde_types() -> sa.Table:
    """Table minimale typée, pour que le pilote reçoive une vraie heure."""
    return sa.table(
        "garde_types",
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("end_time", sa.Time),
        sa.column("duration_hours", sa.Float),
        sa.column("duration_class", sa.String),
        sa.column("horaires_a_valider", sa.Boolean),
    )


def upgrade() -> None:
    table = _garde_types()
    for code in CIBLES:
        op.execute(
            table.update()
            .where(table.c.code == code)
            .where(table.c.horaires_a_valider.is_(True))
            .values(
                end_time=time(9, 0),
                duration_hours=16.0,
                duration_class="NUIT_16H",
                label=NOUVEAUX_LIBELLES[code],
                horaires_a_valider=False,
            )
        )
    # Les quatre autres horaires étaient déjà confirmés le 02/09/2026.
    op.execute(
        table.update()
        .where(table.c.code.in_(["NUIT_SEMAINE", "SAMEDI", "DIMANCHE", "JOUR_FERIE"]))
        .values(horaires_a_valider=False)
    )


def downgrade() -> None:
    table = _garde_types()
    for code in CIBLES:
        op.execute(
            table.update()
            .where(table.c.code == code)
            .values(
                end_time=time(8, 0),
                duration_hours=15.0,
                duration_class="NUIT_12H",
                label=ANCIENS_LIBELLES[code],
                horaires_a_valider=True,
            )
        )
    op.execute(
        table.update()
        .where(table.c.code.in_(["NUIT_SEMAINE", "SAMEDI", "DIMANCHE", "JOUR_FERIE"]))
        .values(horaires_a_valider=True)
    )
