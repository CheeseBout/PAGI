"""Orchestration patterns: agents.orchestration + agent_runs (Phase 14, SPEC §16).

Adds ``agents.orchestration`` (JSON, {} = pattern "react") and the ``agent_runs``
table (one row per pattern node, SPEC §1.14).

Defensive like ``0002_rag`` / ``0003_subagent``.

Revision ID: 0004_orchestration
Revises: 0003_subagent
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004_orchestration"
down_revision = "0003_subagent"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.JSON(), "sqlite")


def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def _has_column(insp, table: str, col: str) -> bool:
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "agents", "orchestration"):
        with op.batch_alter_table("agents") as batch:
            batch.add_column(sa.Column("orchestration", _JSON, nullable=True))
        op.execute("UPDATE agents SET orchestration = '{}' WHERE orchestration IS NULL")

    if not _has_table(insp, "agent_runs"):
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "session_id", sa.String(), sa.ForeignKey("sessions.id"), nullable=False
            ),
            sa.Column("root_session_id", sa.String(), nullable=True),
            sa.Column("message_id", sa.String(length=64), nullable=True),
            sa.Column("pattern", sa.String(length=24), nullable=False),
            sa.Column("step_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("node", sa.String(length=32), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("input_summary", sa.Text(), nullable=True),
            sa.Column("output_summary", sa.Text(), nullable=True),
            sa.Column("payload", _JSON, nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])
        op.create_index(
            "ix_agent_runs_root_session_id", "agent_runs", ["root_session_id"]
        )
        op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("agent_runs")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("orchestration")
