"""P1.8 et P1.26 : repos, récupération et demandes explicites de bloc continu.

Arbitrages du client du 03/09/2026 :

* retrait de la règle ferme « 24 h entre deux gardes », qui n'a jamais été validée
  institutionnellement (elle est désactivée, pas supprimée, pour conserver la trace) ;
* demandes explicites et datées de bloc continu (week-end complet) ;
* déclarations de travail réellement effectué sur place, sans aucune présomption ;
* propositions de récupération, soumises à décision humaine.

Revision ID: f6a5b4c3d210
Revises: e5f4d3c2b109
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a5b4c3d210"
down_revision = "e5f4d3c2b109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekend_block_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "profile_id", "anchor_date", name="uq_weekend_block_request"
        ),
    )

    op.create_table(
        "on_site_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id", sa.Integer(), sa.ForeignKey("assignments.id"), nullable=False
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column("hours_on_site", sa.Float(), nullable=False, server_default="0"),
        sa.Column("moved_on_site", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("continuous", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("declared_at", sa.DateTime(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "recovery_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id", sa.Integer(), sa.ForeignKey("on_site_reports.id"), nullable=False
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column("hours", sa.Float(), nullable=False, server_default="12"),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="PROPOSEE"),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # La règle ferme des 24 h entre gardes n'a jamais été validée : elle est
    # désactivée, et conservée pour que l'historique reste lisible.
    op.get_bind().execute(
        sa.text(
            "UPDATE rest_rules SET active = false WHERE code = 'REPOS_MIN_24H'"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE rest_rules SET active = true WHERE code = 'REPOS_MIN_24H'")
    )
    op.drop_table("recovery_proposals")
    op.drop_table("on_site_reports")
    op.drop_table("weekend_block_requests")
