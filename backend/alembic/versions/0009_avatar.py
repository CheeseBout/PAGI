"""agents.avatar_config — 2D avatar attachment (SPEC §20.2).

Defensive like 0002-0006: ``0001_baseline`` runs ``create_all`` so on a fresh DB
the column already exists and this is a no-op; on a DB stamped at an earlier
revision the column is actually added.

Uses a plain ``op.add_column`` rather than ``batch_alter_table`` — same reason
as 0006_vision: SQLite supports adding a column via a direct ``ALTER TABLE``,
and batch mode would recreate ``agents``, which fails with a FK error since
``sessions``/``agent_runs``/... hold a live FK into it.

Revision ID: 0009_avatar
Revises: 0008_project
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009_avatar"
down_revision = "0008_project"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.JSON(), "sqlite")


def _has_column(insp, table: str, col: str) -> bool:
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "agents", "avatar_config"):
        op.add_column("agents", sa.Column("avatar_config", _JSON, nullable=True))
        op.execute("UPDATE agents SET avatar_config = '{}' WHERE avatar_config IS NULL")


def downgrade() -> None:
    op.drop_column("agents", "avatar_config")
