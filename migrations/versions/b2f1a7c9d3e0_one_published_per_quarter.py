"""P2.2 : au plus une version publiée par trimestre (index unique partiel).

Revision ID: b2f1a7c9d3e0
Revises: 241581395051
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2f1a7c9d3e0"
down_revision = "241581395051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_one_published_per_quarter",
        "schedule_versions",
        ["quarter_id"],
        unique=True,
        postgresql_where=sa.text("state = 'PUBLIE'"),
        sqlite_where=sa.text("state = 'PUBLIE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_published_per_quarter", table_name="schedule_versions")
