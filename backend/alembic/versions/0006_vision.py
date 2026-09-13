"""agents.vision_enabled toggle — lets an operator turn image input off per agent.

Defensive like 0002-0005: ``0001_baseline`` runs ``create_all`` so on a fresh
DB the column already exists and this is a no-op; on a DB stamped at an
earlier revision the column is actually added.

Uses a plain ``op.add_column`` rather than ``batch_alter_table``: SQLite
supports adding a NOT NULL column with a constant default via a direct
``ALTER TABLE ... ADD COLUMN`` (see ``db/session.py::_ensure_added_columns``),
and batch mode would instead recreate the table (rename/copy/drop/rename) —
which fails with ``FOREIGN KEY constraint failed`` on ``agents`` since other
tables (``sessions``, ``agent_runs``, ...) hold a live FK into it.

Revision ID: 0006_vision
Revises: 0005_agent_eval
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006_vision"
down_revision = "0005_agent_eval"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, col: str) -> bool:
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "agents", "vision_enabled"):
        op.add_column(
            "agents",
            sa.Column("vision_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    op.drop_column("agents", "vision_enabled")
