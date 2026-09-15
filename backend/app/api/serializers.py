"""Model -> JSON-safe dict helpers (ISO-8601 'Z' timestamps, SPEC preamble)."""

from __future__ import annotations

from datetime import datetime

from ..db.models import (
    Agent,
    ChatSession,
    CronJob,
    KbChunk,
    KbCollection,
    KbDocument,
    KbEvalCase,
    KbEvalRun,
    KbQueryLog,
    McpServer,
    Message,
    SessionToolGrant,
    ToolApproval,
    Trace,
)


def _z(dt: datetime | None) -> str | None:
    """ISO-8601 with a 'Z' suffix for UTC (SPEC preamble).

    SQLite drops tzinfo on write, so values read back are naive UTC — treat them
    as such rather than emitting an offset-less string.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat().replace("+00:00", "Z")


def agent_out(a: Agent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "system_prompt": a.system_prompt,
        "provider": a.provider,
        "model": a.model,
        "tools_allowed": a.tools_allowed,
        "tool_policy": a.tool_policy,
        "temperature": a.temperature,
        "max_tokens": a.max_tokens,
        "is_default": a.is_default,
        "vision_enabled": bool(getattr(a, "vision_enabled", True)),
        "kb_collection_ids": a.kb_collection_ids or [],
        "rag_config": a.rag_config or {},
        "is_delegatable": bool(getattr(a, "is_delegatable", False)),
        "delegate_description": getattr(a, "delegate_description", "") or "",
        "orchestration": getattr(a, "orchestration", None) or {},
        "created_at": _z(a.created_at),
        "updated_at": _z(a.updated_at),
    }


def session_out(s: ChatSession) -> dict:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "agent_id": s.agent_id,
        "title": s.title,
        "archived": s.archived,
        "kind": getattr(s, "kind", "chat"),
        "parent_session_id": getattr(s, "parent_session_id", None),
        "depth": getattr(s, "depth", 0),
        "created_at": _z(s.created_at),
        "updated_at": _z(s.updated_at),
    }


def agent_eval_run_out(r) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "agent_id": r.agent_id,
        "pattern": r.pattern,
        "judge_model": r.judge_model,
        "case_count": r.case_count,
        "task_resolution_rate": r.task_resolution_rate,
        "step_efficiency": r.step_efficiency,
        "redundant_tool_rate": r.redundant_tool_rate,
        "parameter_hallucination_rate": r.parameter_hallucination_rate,
        "forbidden_tool_rate": r.forbidden_tool_rate,
        "avg_llm_calls": r.avg_llm_calls,
        "avg_cost_usd": r.avg_cost_usd,
        "avg_latency_ms": r.avg_latency_ms,
        "status": r.status,
        "created_at": _z(r.created_at),
    }


def agent_eval_case_out(c) -> dict:
    return {
        "id": c.id,
        "run_id": c.run_id,
        "prompt": c.prompt,
        "expected_outcome": c.expected_outcome,
        "optimal_steps": c.optimal_steps,
        "forbidden_tools": c.forbidden_tools,
        "held_out": bool(getattr(c, "held_out", False)),
        "answer": c.answer,
        "resolved": c.resolved,
        "steps_taken": c.steps_taken,
        "redundant_tool_calls": c.redundant_tool_calls,
        "forbidden_tool_used": c.forbidden_tool_used,
        "parameter_hallucination": c.parameter_hallucination,
        "llm_calls": c.llm_calls,
        "cost_usd": c.cost_usd,
        "latency_ms": c.latency_ms,
        "judge_rationale": c.judge_rationale,
    }


# ── Self-improving harness (Phase 17) ──────────────────────────────────
def weakness_report_out(w) -> dict:
    return {
        "id": w.id,
        "agent_id": w.agent_id,
        "agent_eval_run_id": w.agent_eval_run_id,
        "pattern": w.pattern,
        "example_case_ids": w.example_case_ids,
        "created_at": _z(w.created_at),
    }


def project_run_out(r) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "planner_agent_id": r.planner_agent_id,
        "developer_agent_id": r.developer_agent_id,
        "qa_agent_id": r.qa_agent_id,
        "root_session_id": r.root_session_id,
        "workspace_path": r.workspace_path,
        "status": r.status,
        "max_iterations": r.max_iterations,
        "iterations_done": r.iterations_done,
        "budget_usd": r.budget_usd,
        "spent_usd": r.spent_usd,
        "qa_fail_streak": r.qa_fail_streak,
        "qa_fail_pause_threshold": r.qa_fail_pause_threshold,
        "schedule": r.schedule,
        "created_at": _z(r.created_at),
        "updated_at": _z(r.updated_at),
    }


def project_iteration_out(it) -> dict:
    return {
        "id": it.id,
        "project_run_id": it.project_run_id,
        "iteration_no": it.iteration_no,
        "planner_session_id": it.planner_session_id,
        "developer_session_id": it.developer_session_id,
        "qa_session_id": it.qa_session_id,
        "workspace_commit_sha": it.workspace_commit_sha,
        "qa_verdict": it.qa_verdict,
        "qa_reason": it.qa_reason,
        "cost_usd": it.cost_usd,
        "status": it.status,
        "error": it.error,
        "created_at": _z(it.created_at),
        "finished_at": _z(it.finished_at),
    }


def agent_config_version_out(v) -> dict:
    return {
        "id": v.id,
        "agent_id": v.agent_id,
        "parent_version_id": v.parent_version_id,
        "weakness_report_id": v.weakness_report_id,
        "diff": v.diff,
        "config_snapshot": v.config_snapshot,
        "rationale": v.rationale,
        "source_eval_run_id": v.source_eval_run_id,
        "status": v.status,
        "held_in_score": v.held_in_score,
        "held_out_score": v.held_out_score,
        "reject_reason": v.reject_reason,
        "created_at": _z(v.created_at),
        "activated_at": _z(v.activated_at),
    }


def agent_run_out(r) -> dict:
    return {
        "id": r.id,
        "session_id": r.session_id,
        "root_session_id": r.root_session_id,
        "message_id": r.message_id,
        "pattern": r.pattern,
        "step_no": r.step_no,
        "node": r.node,
        "attempt": r.attempt,
        "input_summary": r.input_summary,
        "output_summary": r.output_summary,
        "payload": r.payload,
        "status": r.status,
        "latency_ms": r.latency_ms,
        "cost_usd": r.cost_usd,
        "error": r.error,
        "created_at": _z(r.created_at),
    }


def message_out(
    m: Message,
    *,
    rag: dict | None = None,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
) -> dict:
    out = {
        "id": m.id,
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content,
        "tool_calls": m.tool_calls,
        "tool_call_id": m.tool_call_id,
        "attachments": m.attachments,
        "tokens_in": m.tokens_in,
        "tokens_out": m.tokens_out,
        "created_at": _z(m.created_at),
    }
    if rag is not None:
        out["rag"] = rag  # {query_log_id, citations, no_context, reason} — Phase 12e
    # From the `traces` row that produced this assistant message (Phase 4 UI
    # observability) — optional because a message can predate tracing (Trace
    # rows didn't always carry message_id) or belong to a non-chat role.
    if cost_usd is not None:
        out["cost_usd"] = cost_usd
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    return out


def approval_out(a: ToolApproval, *, sub_session_id: str | None = None,
                 agent_name: str | None = None) -> dict:
    out = {
        "id": a.id,
        "session_id": a.session_id,
        "message_id": a.message_id,
        "tool_call_id": a.tool_call_id,
        "tool_name": a.tool_name,
        "tool_args": a.tool_args,
        "status": a.status,
        "created_at": _z(a.created_at),
        "resolved_at": _z(a.resolved_at),
        "resolved_by": a.resolved_by,
    }
    if sub_session_id is not None:
        # Phase 13: approval belongs to a sub-agent — the card must say which one
        out["sub_session_id"] = sub_session_id
        out["agent_name"] = agent_name or "agent"
    return out


def mcp_out(m: McpServer) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "transport": m.transport,
        "config": m.config,
        "enabled": m.enabled,
        "created_at": _z(m.created_at),
    }


def cron_out(c: CronJob) -> dict:
    return {
        "id": c.id,
        "agent_id": c.agent_id,
        "name": c.name,
        "schedule": c.schedule,
        "prompt": c.prompt,
        "unattended_allowed_tools": c.unattended_allowed_tools,
        "enabled": c.enabled,
        "last_run_at": _z(c.last_run_at),
        "next_run_at": _z(c.next_run_at),
        "created_at": _z(c.created_at),
    }


def trace_out(t: Trace) -> dict:
    return {
        "id": t.id,
        "session_id": t.session_id,
        "message_id": t.message_id,
        "provider": t.provider,
        "model": t.model,
        "latency_ms": t.latency_ms,
        "tokens_in": t.tokens_in,
        "tokens_out": t.tokens_out,
        "cache_hit": t.cache_hit,
        "cached_tokens": t.cached_tokens,
        "cache_write_tokens": t.cache_write_tokens,
        "cost_usd": t.cost_usd,
        "error": t.error,
        "created_at": _z(t.created_at),
    }


def grant_out(g: SessionToolGrant) -> dict:
    return {
        "id": g.id,
        "session_id": g.session_id,
        "tool_name": g.tool_name,
        "created_at": _z(g.created_at),
    }


# ── RAG / Knowledge Base (Phase 12) ────────────────────────────────────
def kb_collection_out(c: KbCollection) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "config": c.config,
        "embedding_model": c.embedding_model,
        "embedding_dim": c.embedding_dim,
        "doc_count": c.doc_count,
        "chunk_count": c.chunk_count,
        "status": c.status,
        "created_at": _z(c.created_at),
        "updated_at": _z(c.updated_at),
    }


def kb_document_out(d: KbDocument) -> dict:
    return {
        "id": d.id,
        "collection_id": d.collection_id,
        "source_type": d.source_type,
        "source_uri": d.source_uri,
        "title": d.title,
        "content_hash": d.content_hash,
        "version": d.version,
        "status": d.status,
        "error": d.error,
        "token_count": d.token_count,
        "injection_flagged": d.injection_flagged,
        "meta": d.meta,
        "created_at": _z(d.created_at),
        "updated_at": _z(d.updated_at),
    }


def kb_chunk_out(c: KbChunk) -> dict:
    return {
        "id": c.id,
        "document_id": c.document_id,
        "ordinal": c.ordinal,
        "text": c.text,
        "text_embedded": c.text_embedded,
        "token_count": c.token_count,
        "meta": c.meta,
    }


def kb_query_log_out(q: KbQueryLog) -> dict:
    return {
        "id": q.id,
        "session_id": q.session_id,
        "message_id": q.message_id,
        "collection_ids": q.collection_ids,
        "query_raw": q.query_raw,
        "queries_used": q.queries_used,
        "config_snapshot": q.config_snapshot,
        "stages": q.stages,
        "picked_chunk_ids": q.picked_chunk_ids,
        "context_tokens": q.context_tokens,
        "crag_verdict": q.crag_verdict,
        "total_ms": q.total_ms,
        "created_at": _z(q.created_at),
    }


def kb_eval_run_out(r: KbEvalRun) -> dict:
    return {
        "id": r.id,
        "collection_id": r.collection_id,
        "name": r.name,
        "config_snapshot": r.config_snapshot,
        "agent_id": r.agent_id,
        "judge_model": r.judge_model,
        "case_count": r.case_count,
        "faithfulness": r.faithfulness,
        "answer_relevancy": r.answer_relevancy,
        "context_precision": r.context_precision,
        "context_recall": r.context_recall,
        "status": r.status,
        "created_at": _z(r.created_at),
    }


def kb_eval_case_out(c: KbEvalCase) -> dict:
    return {
        "id": c.id,
        "run_id": c.run_id,
        "question": c.question,
        "ground_truth": c.ground_truth,
        "answer": c.answer,
        "contexts": c.contexts,
        "faithfulness": c.faithfulness,
        "answer_relevancy": c.answer_relevancy,
        "context_precision": c.context_precision,
        "context_recall": c.context_recall,
        "judge_rationale": c.judge_rationale,
    }
