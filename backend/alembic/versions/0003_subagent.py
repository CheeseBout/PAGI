"""Sub-agent / delegation: session tree + agents.is_delegatable (Phase 13, SPEC §15).

Adds to ``sessions``: ``parent_session_id``, ``root_session_id``, ``depth``,
``kind``, ``spawned_by_tool_call_id`` (UNIQUE), ``delegated_task``.
Adds to ``agents``: ``is_delegatable``, ``delegate_description``.

Defensive like ``0002_rag``: ``0001_baseline`` runs ``create_all`` so on a fresh
DB the columns already exist and each step is a no-op; on a DB stamped at an
earlier revision the steps actually run.

Revision ID: 0003_subagent
Revises: 0002_rag
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003_subagent"
down_revision = "0002_rag"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, col: str) -> bool:
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "sessions", "parent_session_id"):
        with op.batch_alter_table("sessions") as batch:
            batch.add_column(sa.Column("parent_session_id", sa.String(), nullable=True))
            batch.add_column(sa.Column("root_session_id", sa.String(), nullable=True))
            batch.add_column(
                sa.Column("depth", sa.Integer(), nullable=False, server_default="0")
            )
            batch.add_column(
                sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat")
            )
            batch.add_column(
                sa.Column("spawned_by_tool_call_id", sa.String(length=64), nullable=True)
            )
            batch.add_column(sa.Column("delegated_task", sa.Text(), nullable=True))
        op.create_index(
            "ix_sessions_parent_session_id", "sessions", ["parent_session_id"]
        )
        op.create_index("ix_sessions_root_session_id", "sessions", ["root_session_id"])
        op.create_index(
            "uq_sessions_spawned_by_tool_call_id",
            "sessions",
            ["spawned_by_tool_call_id"],
            unique=True,
        )

    if not _has_column(insp, "agents", "is_delegatable"):
        with op.batch_alter_table("agents") as batch:
            batch.add_column(
                sa.Column(
                    "is_delegatable", sa.Boolean(), nullable=False, server_default=sa.false()
                )
            )
            batch.add_column(
                sa.Column(
                    "delegate_description", sa.Text(), nullable=False, server_default=""
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("delegated_task")
        batch.drop_column("spawned_by_tool_call_id")
        batch.drop_column("kind")
        batch.drop_column("depth")
        batch.drop_column("root_session_id")
        batch.drop_column("parent_session_id")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("delegate_description")
        batch.drop_column("is_delegatable")
