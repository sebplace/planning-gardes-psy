"""P1.19 : six permissions applicatives distinctes et traçables.

Une attribution n'est jamais supprimée : elle est révoquée en posant une date de
fin, afin que l'historique des droits reste lisible.

Revision ID: a7b6c5d4e321
Revises: f6a5b4c3d210
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b6c5d4e321"
down_revision = "f6a5b4c3d210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "permission_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("granted_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "code", "start_date", name="uq_permission_grant"),
    )
    op.create_index("ix_permission_grant_user", "permission_grants", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_permission_grant_user", table_name="permission_grants")
    op.drop_table("permission_grants")
