"""Lot 2.1 : quota de période opérationnel (assistants 57/68).

Le quota porte sur une période unique de dates de service, à cheval sur deux
années civiles et sur plusieurs trimestres. Il devient opposable au moteur, au
lieu de rester un calcul de projection.

Aucune valeur n'est écrite par défaut : le client n'a pas tranché entre 57 et 68.

Revision ID: c9d8e7f6a543
Revises: b8c7d6e5f432
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d8e7f6a543"
down_revision = "b8c7d6e5f432"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "period_quotas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=True,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("target", sa.Float(), nullable=False, server_default="0"),
        sa.Column("maximum", sa.Float(), nullable=True),
        sa.Column(
            "enforcement", sa.String(length=40), nullable=False, server_default="SOUPLE"
        ),
        sa.Column(
            "institutionally_validated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "code", "status", "profile_id", name="uq_period_quota_scope"
        ),
    )
    op.create_index(
        "ix_period_quota_dates", "period_quotas", ["start_date", "end_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_period_quota_dates", table_name="period_quotas")
    op.drop_table("period_quotas")
