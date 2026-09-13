"""Agent / trajectory evaluation tables (Phase 16, PLAN §16).

Adds ``agent_eval_runs`` + ``agent_eval_cases``. Defensive like 0002-0004.

Revision ID: 0005_agent_eval
Revises: 0004_orchestration
Create Date: 2026-09-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_agent_eval"
down_revision = "0004_orchestration"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.JSON(), "sqlite")


def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def upgrade() -> None:
    insp = inspect(op.get_bind())

    if not _has_table(insp, "agent_eval_runs"):
        op.create_table(
            "agent_eval_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("agent_id", sa.String(), sa.ForeignKey("agents.id"), nullable=False),
            sa.Column("pattern", sa.String(length=24), nullable=True),
            sa.Column("judge_model", sa.String(length=128), nullable=True),
            sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("task_resolution_rate", sa.Float(), nullable=True),
            sa.Column("step_efficiency", sa.Float(), nullable=True),
            sa.Column("redundant_tool_rate", sa.Float(), nullable=True),
            sa.Column("parameter_hallucination_rate", sa.Float(), nullable=True),
            sa.Column("forbidden_tool_rate", sa.Float(), nullable=True),
            sa.Column("avg_llm_calls", sa.Float(), nullable=True),
            sa.Column("avg_cost_usd", sa.Float(), nullable=True),
            sa.Column("avg_latency_ms", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if not _has_table(insp, "agent_eval_cases"):
        op.create_table(
            "agent_eval_cases",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("run_id", sa.String(), sa.ForeignKey("agent_eval_runs.id"), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("expected_outcome", sa.Text(), nullable=True),
            sa.Column("optimal_steps", sa.Integer(), nullable=True),
            sa.Column("forbidden_tools", _JSON, nullable=False),
            sa.Column("answer", sa.Text(), nullable=True),
            sa.Column("resolved", sa.Boolean(), nullable=True),
            sa.Column("steps_taken", sa.Integer(), nullable=True),
            sa.Column("redundant_tool_calls", sa.Integer(), nullable=True),
            sa.Column("forbidden_tool_used", sa.Boolean(), nullable=True),
            sa.Column("parameter_hallucination", sa.Boolean(), nullable=True),
            sa.Column("llm_calls", sa.Integer(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("judge_rationale", _JSON, nullable=False),
        )
        op.create_index("ix_agent_eval_cases_run_id", "agent_eval_cases", ["run_id"])


def downgrade() -> None:
    op.drop_table("agent_eval_cases")
    op.drop_table("agent_eval_runs")
