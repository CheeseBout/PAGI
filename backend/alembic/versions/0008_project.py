"""Multi-day project development loop tables (Phase 18, PLAN §18).

Adds ``project_runs`` + ``project_iterations``, plus ``sessions.workspace_id``
and ``cron_jobs.kind``/``cron_jobs.project_run_id``. Defensive like 0002-0007:
on a fresh DB created via ``create_all`` these are no-ops; on a DB stamped at
an earlier revision they actually apply.

New columns on existing tables use plain ``op.add_column`` (not
``batch_alter_table``) for the same reason ``0006_vision`` does: SQLite
supports a direct ``ALTER TABLE ... ADD COLUMN``, while batch mode's
recreate-the-table approach fails on tables other tables hold a live FK into
(``sessions`` is referenced by ``messages``/``agent_runs``/``tool_approvals``/
etc.; ``cron_jobs`` isn't referenced by anything today, but the same style is
used for consistency).

Revision ID: 0008_project
Revises: 0007_harness
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008_project"
down_revision = "0007_harness"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def _has_column(insp, table: str, col: str) -> bool:
    if not insp.has_table(table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if not _has_column(insp, "sessions", "workspace_id"):
        op.add_column("sessions", sa.Column("workspace_id", sa.String(length=64), nullable=True))

    if not _has_column(insp, "cron_jobs", "kind"):
        op.add_column(
            "cron_jobs",
            sa.Column("kind", sa.String(length=24), nullable=False, server_default="prompt"),
        )
    if not _has_column(insp, "cron_jobs", "project_run_id"):
        # FK added without a server-side constraint reference in the ALTER
        # itself (SQLite ADD COLUMN can't add a new FK constraint on an
        # existing table) — the ORM-level foreign_key on the model is enough
        # for fresh databases (create_all), and this column is nullable so a
        # pre-Phase-18 DB just gets NULLs here, matching every existing row's
        # "prompt" kind which never uses it.
        op.add_column("cron_jobs", sa.Column("project_run_id", sa.String(), nullable=True))

    if not _has_table(insp, "project_runs"):
        op.create_table(
            "project_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("planner_agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("developer_agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("qa_agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("root_session_id", sa.String(), sa.ForeignKey("sessions.id"), nullable=False),
            sa.Column("workspace_path", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("max_iterations", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("iterations_done", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("budget_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("spent_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("qa_fail_streak", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("qa_fail_pause_threshold", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("schedule", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table(insp, "project_iterations"):
        op.create_table(
            "project_iterations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("project_run_id", sa.String(), sa.ForeignKey("project_runs.id"), nullable=False),
            sa.Column("iteration_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("planner_session_id", sa.String(), sa.ForeignKey("sessions.id"), nullable=True),
            sa.Column("developer_session_id", sa.String(), sa.ForeignKey("sessions.id"), nullable=True),
            sa.Column("qa_session_id", sa.String(), sa.ForeignKey("sessions.id"), nullable=True),
            sa.Column("workspace_commit_sha", sa.String(length=64), nullable=True),
            sa.Column("qa_verdict", sa.String(length=8), nullable=True),
            sa.Column("qa_reason", sa.Text(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_project_iterations_project_run_id", "project_iterations", ["project_run_id"]
        )
        op.create_index(
            "uq_project_iteration_no", "project_iterations",
            ["project_run_id", "iteration_no"], unique=True,
        )


def downgrade() -> None:
    op.drop_table("project_iterations")
    op.drop_table("project_runs")
    op.drop_column("cron_jobs", "project_run_id")
    op.drop_column("cron_jobs", "kind")
    op.drop_column("sessions", "workspace_id")
