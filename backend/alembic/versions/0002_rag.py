"""RAG / Knowledge Base schema (Phase 12, SPEC §1.9-1.13, §14)

Adds the five ``kb_*`` tables plus ``agents.kb_collection_ids`` /
``agents.rag_config`` and ``traces.kind``.

Defensive by design: ``0001_baseline`` runs ``SQLModel.metadata.create_all``,
which on a *fresh* database already creates everything the current models
declare (including the objects below). So each step here checks the live schema
first and is a no-op when the object is already present. On a database that was
stamped at ``0001`` before this revision existed, the same steps actually create
the tables / add the columns.

Revision ID: 0002_rag
Revises: 0001_baseline
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_rag"
down_revision = "0001_baseline"
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

    if not _has_table(insp, "kb_collections"):
        op.create_table(
            "kb_collections",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("config", _JSON, nullable=False),
            sa.Column("embedding_model", sa.String(length=128), nullable=False),
            sa.Column("embedding_dim", sa.Integer(), nullable=True),
            sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ready"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_kb_collections_name", "kb_collections", ["name"], unique=True)

    if not _has_table(insp, "kb_documents"):
        op.create_table(
            "kb_documents",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("collection_id", sa.String(), sa.ForeignKey("kb_collections.id"), nullable=False),
            sa.Column("source_type", sa.String(length=16), nullable=False),
            sa.Column("source_uri", sa.Text(), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("token_count", sa.Integer(), nullable=True),
            sa.Column("injection_flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("meta", _JSON, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_kb_documents_collection_id", "kb_documents", ["collection_id"])
        op.create_index("ix_kb_documents_content_hash", "kb_documents", ["content_hash"])

    if not _has_table(insp, "kb_chunks"):
        op.create_table(
            "kb_chunks",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("document_id", sa.String(), sa.ForeignKey("kb_documents.id"), nullable=False),
            sa.Column("collection_id", sa.String(), sa.ForeignKey("kb_collections.id"), nullable=False),
            sa.Column("parent_id", sa.String(length=64), nullable=True),
            sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("text_embedded", sa.Text(), nullable=False, server_default=""),
            sa.Column("embedding", _JSON, nullable=False),
            sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("meta", _JSON, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_kb_chunks_document_id", "kb_chunks", ["document_id"])
        op.create_index("ix_kb_chunks_collection_id", "kb_chunks", ["collection_id"])

    if not _has_table(insp, "kb_query_logs"):
        op.create_table(
            "kb_query_logs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("session_id", sa.String(), sa.ForeignKey("sessions.id"), nullable=True),
            sa.Column("message_id", sa.String(length=64), nullable=True),
            sa.Column("collection_ids", _JSON, nullable=False),
            sa.Column("query_raw", sa.Text(), nullable=False, server_default=""),
            sa.Column("queries_used", _JSON, nullable=False),
            sa.Column("config_snapshot", _JSON, nullable=False),
            sa.Column("stages", _JSON, nullable=False),
            sa.Column("picked_chunk_ids", _JSON, nullable=False),
            sa.Column("context_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("crag_verdict", sa.String(length=16), nullable=True),
            sa.Column("total_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_kb_query_logs_session_id", "kb_query_logs", ["session_id"])
        op.create_index("ix_kb_query_logs_created_at", "kb_query_logs", ["created_at"])

    if not _has_table(insp, "kb_eval_runs"):
        op.create_table(
            "kb_eval_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("collection_id", sa.String(), sa.ForeignKey("kb_collections.id"), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("config_snapshot", _JSON, nullable=False),
            sa.Column("agent_id", sa.String(length=64), nullable=True),
            sa.Column("judge_model", sa.String(length=128), nullable=True),
            sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("faithfulness", sa.Float(), nullable=True),
            sa.Column("answer_relevancy", sa.Float(), nullable=True),
            sa.Column("context_precision", sa.Float(), nullable=True),
            sa.Column("context_recall", sa.Float(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_kb_eval_runs_collection_id", "kb_eval_runs", ["collection_id"])

    if not _has_table(insp, "kb_eval_cases"):
        op.create_table(
            "kb_eval_cases",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("run_id", sa.String(), sa.ForeignKey("kb_eval_runs.id"), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("ground_truth", sa.Text(), nullable=True),
            sa.Column("answer", sa.Text(), nullable=True),
            sa.Column("contexts", _JSON, nullable=False),
            sa.Column("faithfulness", sa.Float(), nullable=True),
            sa.Column("answer_relevancy", sa.Float(), nullable=True),
            sa.Column("context_precision", sa.Float(), nullable=True),
            sa.Column("context_recall", sa.Float(), nullable=True),
            sa.Column("judge_rationale", _JSON, nullable=False),
        )
        op.create_index("ix_kb_eval_cases_run_id", "kb_eval_cases", ["run_id"])

    if not _has_column(insp, "agents", "kb_collection_ids"):
        with op.batch_alter_table("agents") as batch:
            batch.add_column(sa.Column("kb_collection_ids", _JSON, nullable=True))
            batch.add_column(sa.Column("rag_config", _JSON, nullable=True))
        op.execute("UPDATE agents SET kb_collection_ids = '[]' WHERE kb_collection_ids IS NULL")
        op.execute("UPDATE agents SET rag_config = '{}' WHERE rag_config IS NULL")

    if not _has_column(insp, "traces", "kind"):
        with op.batch_alter_table("traces") as batch:
            batch.add_column(
                sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat")
            )


def downgrade() -> None:
    for tbl in (
        "kb_eval_cases",
        "kb_eval_runs",
        "kb_query_logs",
        "kb_chunks",
        "kb_documents",
        "kb_collections",
    ):
        op.drop_table(tbl)
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("rag_config")
        batch.drop_column("kb_collection_ids")
    with op.batch_alter_table("traces") as batch:
        batch.drop_column("kind")
