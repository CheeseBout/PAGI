"""sessions.origin — desktop overlay's continuous chat marker (SPEC §21.5).

Defensive like 0002-0009: ``0001_baseline`` runs ``create_all`` so on a fresh DB
the column already exists and this is a no-op; on a DB stamped at an earlier
revision the column is actually added (existing rows read as ``'web'``).

Plain ``op.add_column`` rather than ``batch_alter_table`` for the same reason as
0006/0009: SQLite can ALTER TABLE ADD COLUMN directly, and batch mode would
recreate ``sessions``, which fails on the live FKs pointing at it.

Revision ID: 0010_overlay
Revises: 0009_avatar
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0010_overlay"
down_revision = "0009_avatar"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, col: str) -> bool:
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "sessions", "origin"):
        op.add_column(
            "sessions",
            sa.Column("origin", sa.String(16), nullable=False, server_default="web"),
        )


def downgrade() -> None:
    op.drop_column("sessions", "origin")
