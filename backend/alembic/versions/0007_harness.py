"""Self-improving harness tables (Phase 17, PLAN §17).

Adds ``weakness_reports`` + ``agent_config_versions``, and one column
(``agent_eval_cases.held_out``) to the existing Phase-16 case table. Defensive
like 0002-0006: on a fresh DB created via ``create_all`` these are no-ops; on a
DB stamped at an earlier revision they actually apply.

``held_out`` is added with plain ``op.add_column`` (not ``batch_alter_table``)
for the same reason ``0006_vision`` uses it on ``agents``: SQLite handles a
direct ``ALTER TABLE ... ADD COLUMN`` for a constant-default column fine, while
batch mode's recreate-the-table approach fails on tables with live incoming
FKs (``agent_eval_cases`` has none pointing at it today, but this keeps the
migration style uniform with the rest of the file).

Revision ID: 0007_harness
Revises: 0006_vision
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007_harness"
down_revision = "0006_vision"
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

    if not _has_column(insp, "agent_eval_cases", "held_out"):
        op.add_column(
            "agent_eval_cases",
            sa.Column("held_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if not _has_table(insp, "weakness_reports"):
        op.create_table(
            "weakness_reports",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column(
                "agent_eval_run_id", sa.String(), sa.ForeignKey("agent_eval_runs.id"),
                nullable=False,
            ),
            sa.Column("pattern", sa.Text(), nullable=False, server_default=""),
            sa.Column("example_case_ids", _JSON, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_weakness_reports_agent_id", "weakness_reports", ["agent_id"])

    if not _has_table(insp, "agent_config_versions"):
        op.create_table(
            "agent_config_versions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column(
                "parent_version_id", sa.String(),
                sa.ForeignKey("agent_config_versions.id"), nullable=True,
            ),
            sa.Column(
                "weakness_report_id", sa.String(),
                sa.ForeignKey("weakness_reports.id"), nullable=True,
            ),
            sa.Column("diff", _JSON, nullable=False),
            sa.Column("config_snapshot", _JSON, nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "source_eval_run_id", sa.String(), sa.ForeignKey("agent_eval_runs.id"),
                nullable=True,
            ),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="proposed"),
            sa.Column("held_in_score", sa.Float(), nullable=True),
            sa.Column("held_out_score", sa.Float(), nullable=True),
            sa.Column("reject_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("activated_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_agent_config_versions_agent_id", "agent_config_versions", ["agent_id"]
        )


def downgrade() -> None:
    op.drop_table("agent_config_versions")
    op.drop_table("weakness_reports")
    op.drop_column("agent_eval_cases", "held_out")
