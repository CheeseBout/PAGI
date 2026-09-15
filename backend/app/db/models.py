"""SQLModel table definitions — mirrors SPEC.md §1.

IDs are UUID v4 strings; timestamps are timezone-aware UTC datetimes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=_uuid, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=64)
    password_hash: str = Field(max_length=255)
    is_admin: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_now)


class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(max_length=128)
    system_prompt: str = Field(default="")
    provider: str = Field(max_length=16)  # openai | anthropic | gemini | openrouter
    model: str = Field(max_length=128)
    tools_allowed: list = Field(default_factory=list, sa_column=Column(JSON))
    tool_policy: dict = Field(default_factory=dict, sa_column=Column(JSON))
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=4096)
    is_default: bool = Field(default=False)
    # can this agent see images (chat uploads, future screenshots)? off = strip image parts before the LLM call.
    vision_enabled: bool = Field(default=True)
    # ── RAG (Phase 12) ───────────────────────────────────────────────
    # collections this agent may retrieve from; empty = agent doesn't use RAG.
    kb_collection_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    # per-agent override of RagConfig (SPEC §14.2) — only the fields to change.
    rag_config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # ── delegation (Phase 13, SPEC §15.3) ────────────────────────────
    # can another agent call this one as a worker via delegate_task?
    is_delegatable: bool = Field(default=False)
    # one-line "this agent is good at X" — fed into the delegate_task schema.
    delegate_description: str = Field(default="")
    # ── orchestration (Phase 14, SPEC §16) — {} = pattern "react" ─────
    orchestration: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ChatSession(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    agent_id: str = Field(foreign_key="agents.id")
    title: str | None = Field(default=None, max_length=255)
    archived: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now, index=True)
    # ── turn lifecycle (Wave 2: crash-safe resume) ────────────────────
    # idle | running | awaiting_approval — set on entry to run_turn, cleared
    # in its finally. On boot, a stale "running" row means a turn was cut off
    # mid-stream and is re-run from the DB.
    turn_status: str = Field(default="idle", max_length=16)
    turn_started_at: datetime | None = Field(default=None)
    # ── rolling context summary (Wave 3) ─────────────────────────────
    summary: str | None = Field(default=None)
    summary_upto_message_id: str | None = Field(default=None, max_length=64)
    # ── session tree (Phase 13, SPEC §15.1) ─────────────────────────
    # parent = the session that delegated this one out; NULL for a user chat.
    parent_session_id: str | None = Field(default=None, foreign_key="sessions.id", index=True)
    # root of the tree — denormalised so bubbling / cost-rollup need one read,
    # never a recursive walk. NULL for the root session itself.
    root_session_id: str | None = Field(default=None, foreign_key="sessions.id", index=True)
    depth: int = Field(default=0)
    kind: str = Field(default="chat", max_length=16)  # chat | subagent
    # tool_call id of the delegate_task that spawned this session. UNIQUE — this
    # is the idempotency latch: a resumed parent turn re-uses the child instead
    # of spawning a second tree (SPEC §15.5).
    spawned_by_tool_call_id: str | None = Field(default=None, max_length=64, unique=True)
    delegated_task: str | None = Field(default=None)
    # ── project workspace override (Phase 18, PLAN §18) ──────────────
    # when set, sandbox tool calls address THIS id's directory instead of the
    # session's own id — lets several sessions (Planner/Developer/QA) share one
    # persistent workspace. None everywhere outside Phase 18 (default behaviour
    # unchanged: a session's own sandbox workspace is keyed by its own id).
    workspace_id: str | None = Field(default=None, max_length=64)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    role: str = Field(max_length=16)  # user | assistant | tool | system
    content: str | None = Field(default=None)
    tool_calls: list | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    tool_call_id: str | None = Field(default=None, max_length=64)
    # user-message file attachments: [{id, kind, filename, content_type,
    # size_bytes, stored_path, workspace_path?}] — see core/attachments.py
    attachments: list | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    tokens_in: int | None = Field(default=None)
    tokens_out: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now, index=True)


class McpServer(SQLModel, table=True):
    __tablename__ = "mcp_servers"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(index=True, unique=True, max_length=64)
    transport: str = Field(max_length=16)  # stdio | sse | http
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_now)


class ToolApproval(SQLModel, table=True):
    __tablename__ = "tool_approvals"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    message_id: str = Field(foreign_key="messages.id")
    tool_call_id: str = Field(max_length=64, index=True)
    tool_name: str = Field(max_length=64)
    tool_args: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="pending", max_length=16)  # pending|approved|denied|expired
    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = Field(default=None)
    resolved_by: str | None = Field(default=None, foreign_key="users.id")


class SessionToolGrant(SQLModel, table=True):
    """"Always allow this tool for the rest of this conversation" (Wave 4a).

    Consulted before creating a ToolApproval: a matching row makes an
    otherwise-`ask` tool run as if its policy were `auto`, scoped to one
    session. Cleared when the conversation is deleted.
    """

    __tablename__ = "session_tool_grants"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    tool_name: str = Field(max_length=64, index=True)
    created_at: datetime = Field(default_factory=_now)
    created_by: str | None = Field(default=None, foreign_key="users.id")


class MemoryChunk(SQLModel, table=True):
    """Embedded slice of conversation history for semantic recall (Wave 3d).

    Only written when EMBEDDING_MODEL is configured. `embedding` is a JSON
    array of floats; nearest-neighbour search is done in Python (single-tenant
    scale) unless a vector extension is available.
    """

    __tablename__ = "memory_chunks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    message_id: str | None = Field(default=None, max_length=64)
    role: str = Field(max_length=16)
    text: str
    embedding: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now, index=True)


class CronJob(SQLModel, table=True):
    __tablename__ = "cron_jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    name: str = Field(max_length=128)
    schedule: str = Field(max_length=64)  # 5-field cron expression
    prompt: str
    unattended_allowed_tools: list = Field(default_factory=list, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    last_run_at: datetime | None = Field(default=None)
    next_run_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now)
    # ── project iteration jobs (Phase 18, PLAN §18c) ─────────────────
    # "prompt" (default, existing behaviour) | "project_iteration" — the latter
    # ignores `prompt` and calls project_dev.run_iteration(project_run_id)
    # instead of seeding a fresh session from `prompt`.
    kind: str = Field(default="prompt", max_length=24)
    project_run_id: str | None = Field(default=None, foreign_key="project_runs.id")


class Trace(SQLModel, table=True):
    __tablename__ = "traces"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str | None = Field(default=None, foreign_key="sessions.id", index=True)
    message_id: str | None = Field(default=None, foreign_key="messages.id")
    provider: str = Field(max_length=16)
    model: str = Field(max_length=128)
    latency_ms: int = Field(default=0)
    tokens_in: int | None = Field(default=None)
    tokens_out: int | None = Field(default=None)
    cache_hit: bool | None = Field(default=None)
    # prompt-cache accounting (Wave 3a) — cached_tokens is the portion of
    # tokens_in served from cache (read); cache_write_tokens is what was
    # written into the cache this call (Anthropic only).
    cached_tokens: int | None = Field(default=None)
    cache_write_tokens: int | None = Field(default=None)
    cost_usd: float | None = Field(default=None)
    # which kind of LLM call this was, so the Usage tab can split cost
    # (Phase 12): chat | embedding | rerank | transform | eval | summary
    # (Phase 14): plan | evaluate | reflect | route | worker | judge
    kind: str = Field(default="chat", max_length=16)
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now)


class AgentRun(SQLModel, table=True):
    """One row per pattern node (Phase 14, SPEC §1.14).

    A log, not a source of truth: used to skip already-done nodes on resume.
    Wiping it must make a turn re-run from scratch, never run wrong.
    """

    __tablename__ = "agent_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    root_session_id: str | None = Field(default=None, index=True)
    message_id: str | None = Field(default=None, max_length=64)
    pattern: str = Field(max_length=24)
    step_no: int = Field(default=0)
    node: str = Field(max_length=32)
    attempt: int = Field(default=1)
    input_summary: str | None = Field(default=None)
    output_summary: str | None = Field(default=None)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="running", max_length=16)  # running|done|failed|skipped
    latency_ms: int = Field(default=0)
    cost_usd: float | None = Field(default=None)
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now, index=True)


# ── Knowledge Base / RAG (Phase 12, SPEC §1.9–1.13) ─────────────────────
class KbCollection(SQLModel, table=True):
    __tablename__ = "kb_collections"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(index=True, unique=True, max_length=128)
    description: str = Field(default="")
    # RagConfig fields to override for this collection (SPEC §14.2).
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # chosen at creation, immutable — changing it invalidates every vector.
    embedding_model: str = Field(max_length=128)
    embedding_dim: int | None = Field(default=None)
    doc_count: int = Field(default=0)
    chunk_count: int = Field(default=0)
    status: str = Field(default="ready", max_length=16)  # ready|indexing|error
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class KbDocument(SQLModel, table=True):
    __tablename__ = "kb_documents"

    id: str = Field(default_factory=_uuid, primary_key=True)
    collection_id: str = Field(foreign_key="kb_collections.id", index=True)
    source_type: str = Field(max_length=16)  # upload|url|workspace|text
    source_uri: str = Field(default="")
    title: str = Field(max_length=512)
    content_hash: str = Field(default="", max_length=64, index=True)
    version: int = Field(default=1)
    status: str = Field(default="pending", max_length=16)
    error: str | None = Field(default=None)
    token_count: int | None = Field(default=None)
    injection_flagged: bool = Field(default=False)
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class KbChunk(SQLModel, table=True):
    __tablename__ = "kb_chunks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    document_id: str = Field(foreign_key="kb_documents.id", index=True)
    collection_id: str = Field(foreign_key="kb_collections.id", index=True)
    parent_id: str | None = Field(default=None, max_length=64)
    ordinal: int = Field(default=0)
    text: str
    text_embedded: str = Field(default="")
    embedding: list = Field(default_factory=list, sa_column=Column(JSON))
    token_count: int = Field(default=0)
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class KbQueryLog(SQLModel, table=True):
    __tablename__ = "kb_query_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    session_id: str | None = Field(default=None, foreign_key="sessions.id", index=True)
    message_id: str | None = Field(default=None, max_length=64)
    collection_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    query_raw: str = Field(default="")
    queries_used: list = Field(default_factory=list, sa_column=Column(JSON))
    config_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    stages: list = Field(default_factory=list, sa_column=Column(JSON))
    picked_chunk_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    context_tokens: int = Field(default=0)
    crag_verdict: str | None = Field(default=None, max_length=16)
    total_ms: int = Field(default=0)
    created_at: datetime = Field(default_factory=_now, index=True)


class KbEvalRun(SQLModel, table=True):
    __tablename__ = "kb_eval_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    collection_id: str = Field(foreign_key="kb_collections.id", index=True)
    name: str = Field(default="", max_length=128)
    config_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    agent_id: str | None = Field(default=None, max_length=64)
    judge_model: str | None = Field(default=None, max_length=128)
    case_count: int = Field(default=0)
    faithfulness: float | None = Field(default=None)
    answer_relevancy: float | None = Field(default=None)
    context_precision: float | None = Field(default=None)
    context_recall: float | None = Field(default=None)
    status: str = Field(default="running", max_length=16)
    created_at: datetime = Field(default_factory=_now)


class KbEvalCase(SQLModel, table=True):
    __tablename__ = "kb_eval_cases"

    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str = Field(foreign_key="kb_eval_runs.id", index=True)
    question: str
    ground_truth: str | None = Field(default=None)
    answer: str | None = Field(default=None)
    contexts: list = Field(default_factory=list, sa_column=Column(JSON))
    faithfulness: float | None = Field(default=None)
    answer_relevancy: float | None = Field(default=None)
    context_precision: float | None = Field(default=None)
    context_recall: float | None = Field(default=None)
    judge_rationale: dict = Field(default_factory=dict, sa_column=Column(JSON))


# ── Agent / trajectory evaluation (Phase 16, PLAN §16) ─────────────────
class AgentEvalRun(SQLModel, table=True):
    """One pass of a case set against one agent + pattern (PLAN §16).

    Aggregates trajectory metrics, not just final-answer quality: step
    efficiency, redundant tool calls, parameter hallucination, task resolution.
    """

    __tablename__ = "agent_eval_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(default="", max_length=128)
    agent_id: str = Field(foreign_key="agents.id")
    pattern: str | None = Field(default=None, max_length=24)  # override; None = agent's own
    judge_model: str | None = Field(default=None, max_length=128)
    case_count: int = Field(default=0)
    # aggregates
    task_resolution_rate: float | None = Field(default=None)
    step_efficiency: float | None = Field(default=None)  # mean(optimal/actual)
    redundant_tool_rate: float | None = Field(default=None)
    parameter_hallucination_rate: float | None = Field(default=None)
    forbidden_tool_rate: float | None = Field(default=None)
    avg_llm_calls: float | None = Field(default=None)
    avg_cost_usd: float | None = Field(default=None)
    avg_latency_ms: float | None = Field(default=None)
    status: str = Field(default="running", max_length=16)
    created_at: datetime = Field(default_factory=_now)


class AgentEvalCase(SQLModel, table=True):
    __tablename__ = "agent_eval_cases"

    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str = Field(foreign_key="agent_eval_runs.id", index=True)
    prompt: str
    expected_outcome: str | None = Field(default=None)
    optimal_steps: int | None = Field(default=None)
    forbidden_tools: list = Field(default_factory=list, sa_column=Column(JSON))
    # results
    answer: str | None = Field(default=None)
    resolved: bool | None = Field(default=None)
    steps_taken: int | None = Field(default=None)
    redundant_tool_calls: int | None = Field(default=None)
    forbidden_tool_used: bool | None = Field(default=None)
    parameter_hallucination: bool | None = Field(default=None)
    llm_calls: int | None = Field(default=None)
    cost_usd: float | None = Field(default=None)
    latency_ms: int | None = Field(default=None)
    judge_rationale: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # held out of Weakness Miner's failure pool (Phase 17) — scored like any
    # other case, but never used to detect a weakness, so it's a fair check
    # that a proposed patch didn't just overfit the cases that flagged it.
    held_out: bool = Field(default=False)


# ── Self-improving harness (Phase 17, PLAN §17) ─────────────────────────
class WeaknessReport(SQLModel, table=True):
    """One recurring failure pattern mined from one agent's own eval history.

    Groups several failing ``agent_eval_cases`` (same agent, same eval run,
    ``held_out=False``) into a single human-readable pattern instead of one
    report per case — the input to Harness Proposal.
    """

    __tablename__ = "weakness_reports"

    id: str = Field(default_factory=_uuid, primary_key=True)
    agent_id: str = Field(foreign_key="agents.id", index=True)
    agent_eval_run_id: str = Field(foreign_key="agent_eval_runs.id")
    pattern: str = Field(default="")
    example_case_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class AgentConfigVersion(SQLModel, table=True):
    """One candidate (or historical) patch to an agent's harness config.

    ``diff`` is the minimal ``{field: new_value}`` patch this version applies
    on top of its parent (exactly one of ``system_prompt``/``tools_allowed``/
    ``tool_policy``/``orchestration``) — kept for audit/display. ``config_snapshot``
    is the *full* 4-field state at this version, so rollback to any row in the
    chain doesn't require replaying every diff since the root. ``agents`` is
    only ever written from two places: the regression gate (17c) turning a
    ``proposed`` row ``active``, or an explicit rollback — never directly from
    a Harness Proposal (PLAN §2 principle 13).
    """

    __tablename__ = "agent_config_versions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    agent_id: str = Field(foreign_key="agents.id", index=True)
    parent_version_id: str | None = Field(default=None, foreign_key="agent_config_versions.id")
    weakness_report_id: str | None = Field(default=None, foreign_key="weakness_reports.id")
    diff: dict = Field(default_factory=dict, sa_column=Column(JSON))
    config_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    rationale: str = Field(default="")
    source_eval_run_id: str | None = Field(default=None, foreign_key="agent_eval_runs.id")
    # proposed | rejected | active | superseded
    status: str = Field(default="proposed", max_length=16)
    held_in_score: float | None = Field(default=None)
    held_out_score: float | None = Field(default=None)
    reject_reason: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now)
    activated_at: datetime | None = Field(default=None)


# ── Multi-day project development loop (Phase 18, PLAN §18) ─────────────
class ProjectRun(SQLModel, table=True):
    """One long-running "build this project" loop (PLAN §18).

    Each iteration is Planner -> Developer -> QA, three independent
    ``delegate_task``-style calls (``core/delegation.py::run_delegated``)
    against ``root_session_id``, sharing one sandbox workspace
    (``workspace_path``, addressed via ``ChatSession.workspace_id`` — see
    core/orchestration/project_dev.py) that accumulates one git commit per
    iteration. ``planner_agent_id``/``developer_agent_id``/``qa_agent_id`` are
    not in the original PLAN §6 table — there is no LLM "conductor" deciding
    which named agent to delegate to each iteration (project_dev.run_iteration
    is a plain function, not a chat Strategy), so the three roles must be
    fixed config on the row.
    """
    # NOTE: root_session_id's own agent is a dedicated internal "driver" agent
    # (created alongside the row, tools_allowed mirroring the developer's) —
    # never one of the three role agents themselves, since a role's own
    # delegate call would then be rejected as self-delegation.

    __tablename__ = "project_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(default="", max_length=128)
    planner_agent_id: str = Field(foreign_key="agents.id")
    developer_agent_id: str = Field(foreign_key="agents.id")
    qa_agent_id: str = Field(foreign_key="agents.id")
    # anchors the delegation tree (parent_session_id/depth/tool-intersection
    # bookkeeping in run_delegated) — its agent_id is a dedicated internal
    # "driver" agent (see the class docstring), never one of the three role
    # agents; it never runs a turn itself.
    root_session_id: str = Field(foreign_key="sessions.id")
    # a workspace *identifier* (== this row's id in practice), not a literal
    # filesystem path — same sense a chat session_id already is one.
    workspace_path: str = Field(default="", max_length=64)
    status: str = Field(default="active", max_length=16)  # active | paused | done
    max_iterations: int = Field(default=10)
    iterations_done: int = Field(default=0)
    budget_usd: float = Field(default=0.0)  # 0 = off
    spent_usd: float = Field(default=0.0)
    qa_fail_streak: int = Field(default=0)
    qa_fail_pause_threshold: int = Field(default=3)
    schedule: str | None = Field(default=None, max_length=64)  # cron expr; None = manual only
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ProjectIteration(SQLModel, table=True):
    """One Planner -> Developer -> QA round of a ``ProjectRun`` (PLAN §18b).

    ``status`` and the unique ``(project_run_id, iteration_no)`` pairing are
    not in PLAN §6's literal column list — needed so a crashed/errored
    iteration is safely retried by the next cron firing without double
    counting toward ``iterations_done`` or spawning a duplicate iteration
    (the three ``delegate_task`` calls are already individually idempotent
    via ``spawned_by_tool_call_id``; this is the same property one level up).
    """

    __tablename__ = "project_iterations"
    __table_args__ = (UniqueConstraint("project_run_id", "iteration_no", name="uq_project_iteration_no"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    project_run_id: str = Field(foreign_key="project_runs.id", index=True)
    iteration_no: int = Field(default=1)
    planner_session_id: str | None = Field(default=None, foreign_key="sessions.id")
    developer_session_id: str | None = Field(default=None, foreign_key="sessions.id")
    qa_session_id: str | None = Field(default=None, foreign_key="sessions.id")
    workspace_commit_sha: str | None = Field(default=None, max_length=64)
    qa_verdict: str | None = Field(default=None, max_length=8)  # pass | fail
    qa_reason: str | None = Field(default=None)
    cost_usd: float = Field(default=0.0)
    # running | done | qa_failed | error
    status: str = Field(default="running", max_length=16)
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = Field(default=None)
