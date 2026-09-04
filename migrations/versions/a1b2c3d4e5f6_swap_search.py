"""Lot B : parcours nominal d'échange (recherche, sollicitations, accords).

Deux tables nouvelles :

* ``swap_searches`` — une recherche ouverte à partir de la **seule** garde à
  céder, avec sa fenêtre de collecte, son gel de liste, son engagement sur la
  graine, son classement et son éventuelle preuve de tirage ;
* ``swap_candidates`` — les partenaires sollicités, un enregistrement par couple
  (personne, garde de contrepartie).

Aucune donnée n'est écrite : la migration crée la structure, le parcours la
remplit. Réversible.

Revision ID: a1b2c3d4e5f6
Revises: c9d8e7f6a543
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "c9d8e7f6a543"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "swap_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assignment_id", sa.Integer(), sa.ForeignKey("assignments.id"), nullable=False
        ),
        sa.Column(
            "requester_profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column("comment", sa.String(length=300), nullable=True),
        sa.Column(
            "state", sa.String(length=40), nullable=False, server_default="BROUILLON"
        ),
        sa.Column("opens_at", sa.DateTime(), nullable=True),
        sa.Column("closes_at", sa.DateTime(), nullable=True),
        sa.Column("window_label", sa.String(length=60), nullable=True),
        sa.Column("urgent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("frozen_at", sa.DateTime(), nullable=True),
        sa.Column("list_hash", sa.String(length=80), nullable=True),
        sa.Column("seed_commitment", sa.String(length=80), nullable=True),
        sa.Column("solicited_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "retained_proposal_id",
            sa.Integer(),
            sa.ForeignKey("swap_proposals.id"),
            nullable=True,
        ),
        sa.Column("ranking_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("draw_json", sa.Text(), nullable=True),
        sa.Column("outcome_reason", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_swap_search_assignment", "swap_searches", ["assignment_id"]
    )
    op.create_index("ix_swap_search_state", "swap_searches", ["state"])

    op.create_table(
        "swap_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "search_id", sa.Integer(), sa.ForeignKey("swap_searches.id"), nullable=False
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "assignment_id", sa.Integer(), sa.ForeignKey("assignments.id"), nullable=False
        ),
        sa.Column(
            "state", sa.String(length=40), nullable=False, server_default="SOLLICITE"
        ),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("exclusion_reason", sa.String(length=300), nullable=True),
        sa.Column("ranking_key_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("search_id", "assignment_id", name="uq_swap_candidate"),
    )
    op.create_index("ix_swap_candidate_profile", "swap_candidates", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_swap_candidate_profile", table_name="swap_candidates")
    op.drop_table("swap_candidates")
    op.drop_index("ix_swap_search_state", table_name="swap_searches")
    op.drop_index("ix_swap_search_assignment", table_name="swap_searches")
    op.drop_table("swap_searches")
