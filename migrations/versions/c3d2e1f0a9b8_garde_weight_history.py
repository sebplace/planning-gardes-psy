"""P1 : historique de pondération de garde (dixièmes, dates d'effet).

Revision ID: c3d2e1f0a9b8
Revises: b2f1a7c9d3e0
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d2e1f0a9b8"
down_revision = "b2f1a7c9d3e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "garde_weight_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("weight_tenths", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_garde_weight_profile", "garde_weight_history", ["profile_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_garde_weight_profile", table_name="garde_weight_history")
    op.drop_table("garde_weight_history")
