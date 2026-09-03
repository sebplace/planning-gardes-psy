"""P1.6 : plafond mensuel administrable, jamais inventé.

Le client n'a pas chiffré le plafond institutionnel (03/09/2026). La table est donc
créée vide, avec une valeur nullable et deux verrous explicites
(``institutionally_validated`` et ``enforcement``) : une valeur de simulation ne
peut pas devenir silencieusement une règle ferme.

Revision ID: e5f4d3c2b109
Revises: d4e3c2b1a098
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f4d3c2b109"
down_revision = "d4e3c2b1a098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monthly_caps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("year_id", sa.Integer(), sa.ForeignKey("years.id"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=True,
        ),
        sa.Column("max_per_month", sa.Float(), nullable=True),
        sa.Column(
            "enforcement", sa.String(length=20), nullable=False, server_default="SOUPLE"
        ),
        sa.Column(
            "institutionally_validated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "label", sa.String(length=200), nullable=False, server_default="plafond mensuel"
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("year_id", "status", "profile_id", name="uq_monthly_cap_scope"),
    )


def downgrade() -> None:
    op.drop_table("monthly_caps")
