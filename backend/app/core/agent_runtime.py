"""Agent conversation loop + HITL gate (PLAN §3, SPEC §3 / §7).

The loop is *stateless between iterations*: every iteration rebuilds context from
the DB. That makes REST 202-then-approve and post-restart recovery both work by
simply calling `run_turn` again — there is no in-memory turn state to lose.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import structlog
from sqlalchemy import or_
from sqlmodel import select

from ..config import get_settings
from ..db.models import Agent, ChatSession, Message, ToolApproval, Trace
from ..db.session import SessionLocal
from ..observability.tracing import estimate_cost
from ..providers import (
    DoneEvent,
    TextDelta,
    ToolCallComplete,
    UsageEvent,
    get_provider,
    provider_supports_vision,
)
from ..tools import TOOL_REGISTRY, ToolContext, resolve_agent_tools
from ..tools.mcp_client import load_mcp_tools
from .hitl import (
    add_grant,
    create_waiter,
    drop_waiter,
    get_or_create_approval,
    has_grant,
    mark_resolved,
    resolve_waiter,
)
from . import context as context_mod
from . import memory_store
from .memory import load_messages, to_provider_messages, unresolved_tool_calls
from .security import scan_for_injection
from .ws_manager import manager

log = structlog.get_logger("pagi.runtime")

MAX_ITERATIONS = 24
TOOL_TIMEOUT_SECONDS = 150


class PendingApproval(Exception):
    """Raised in no-wait (REST) mode when a turn hits an `ask` tool."""

    def __init__(self, approval: ToolApproval) -> None:
        self.approval = approval
        super().__init__(f"approval required: {approval.id}")


class _TurnError(Exception):
    """Provider/tool failure already reported to the client — stop quietly."""


async def _emit(session_id: str, event: dict) -> None:
    """Broadcast to this session's listeners. If the session is a sub-agent
    (Phase 13, SPEC §15.4), wrap the event and bubble it to the tree root so the
    user — who only has the root's WebSocket open — actually sees it."""
    target = manager.bubble_target(session_id)
    if target is not None:
        await manager.broadcast(
            target["root"],
            {
                "type": "subagent",
                "sub_session_id": session_id,
                "agent_name": target.get("agent_name", ""),
                "depth": target.get("depth", 1),
                "event": event,
            },
        )
        return
    await manager.broadcast(session_id, event)


async def _set_turn_status(db, chat: ChatSession, status: str) -> None:
    """Persist the session's turn lifecycle state (Wave 2 crash-safe resume)."""
    try:
        chat.turn_status = status
        if status == "running":
            chat.turn_started_at = datetime.now(timezone.utc)
        db.add(chat)
        await db.commit()
    except Exception:  # pragma: no cover - never fail a turn over bookkeeping
        log.warning("turn_status_write_failed", session_id=chat.id, status=status)


async def clear_turn_status(session_id: str) -> None:
    """Best-effort reset to idle — called when a turn is aborted from outside."""
    with contextlib.suppress(Exception):
        async with SessionLocal() as db:
            chat = await db.get(ChatSession, session_id)
            if chat is not None and chat.turn_status != "idle":
                chat.turn_status = "idle"
                db.add(chat)
                await db.commit()


async def resume_interrupted_turns() -> int:
    """On boot, re-run turns that were cut off mid-stream.

    A row still marked ``running`` past ``resume_stale_after_seconds`` means the
    process died while an agent turn was in flight. agent_runtime rebuilds all
    state from the DB, so re-invoking ``run_turn`` is enough. Rows in
    ``awaiting_approval`` are left alone — the approval flow resumes those.

    Only root sessions are swept (Phase 13, SPEC §15.5): a sub-agent is woken by
    its parent turn re-running ``delegate_task``, never on its own — a sub-agent
    that ran alone would have no one to hand its answer back to.
    """
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.resume_stale_after_seconds)
    async with SessionLocal() as db:
        rows = list(
            (
                await db.exec(
                    select(ChatSession).where(
                        ChatSession.turn_status == "running",
                        ChatSession.parent_session_id == None,  # noqa: E711
                        or_(
                            ChatSession.turn_started_at == None,  # noqa: E711
                            ChatSession.turn_started_at < cutoff,
                        ),
                    )
                )
            ).all()
        )
    if not rows:
        return 0
    log.info("resuming_interrupted_turns", count=len(rows))
    sem = asyncio.Semaphore(max(1, settings.resume_sweep_concurrency))
    ids = [s.id for s in rows]

    async def _one(sid: str) -> None:
        async with sem:
            with contextlib.suppress(Exception):
                await run_turn(sid, wait_for_approval=True)

    for sid in ids:
        manager.set_task(sid, asyncio.create_task(_one(sid)))
    return len(rows)


def _approval_required_event(a: ToolApproval) -> dict:
    return {
        "type": "approval_required",
        "approval_id": a.id,
        "tool_call_id": a.tool_call_id,
        "tool_name": a.tool_name,
        "args": a.tool_args,
    }


_PREVIEW_TOOLS = {"write_file", "edit_file", "delete_file"}
_PREVIEW_MAX_CHARS = 8_000


async def _attach_preview(db, session_id: str, approval: ToolApproval) -> None:
    """For file-mutating tools, stash a before/after snapshot in the approval's
    args so the UI can show a diff instead of raw parameters (Wave 4b)."""
    if approval.tool_name not in _PREVIEW_TOOLS:
        return
    args = approval.tool_args or {}
    if "_preview" in args:
        return
    path = args.get("path")
    if not path:
        return

    from ..tools import sandbox_client

    old: str | None = None
    try:
        res = await sandbox_client.call(
            "/files/read", {"session_id": session_id, "path": path}, timeout=15.0
        )
        old = res.get("content")
    except Exception:  # new file, unreadable, or sandbox down — treat as absent
        old = None

    if approval.tool_name == "write_file":
        new = args.get("content")
        if args.get("mode") == "append" and old is not None:
            new = old + (new or "")
    elif approval.tool_name == "edit_file":
        find, repl = args.get("find"), args.get("replace")
        if old is not None and find is not None and repl is not None:
            count = -1 if args.get("occurrence") == "all" else 1
            new = old.replace(find, repl, count)
        else:
            new = None
    else:  # delete_file
        new = ""

    def _clip(s: str | None) -> tuple[str | None, bool]:
        if s is None:
            return None, False
        return (s[:_PREVIEW_MAX_CHARS], len(s) > _PREVIEW_MAX_CHARS)

    old_c, old_t = _clip(old)
    new_c, new_t = _clip(new)
    approval.tool_args = {
        **args,
        "_preview": {"path": path, "old": old_c, "new": new_c, "truncated": old_t or new_t},
    }
    db.add(approval)
    await db.commit()
    await db.refresh(approval)


# ── public entrypoint ───────────────────────────────────────────────────
async def run_turn(
    session_id: str,
    *,
    wait_for_approval: bool,
    mode: str = "interactive",
    unattended_allowed_tools: list[str] | None = None,
    restrict_tools: list[str] | None = None,
) -> None:
    """Run one agent turn for ``session_id``.

    ``restrict_tools`` (Phase 13, SPEC §15.5): when set, the effective builtin
    tool set is intersected with it — a sub-agent never gets a tool its parent
    lacks. ``rag_search`` (read-only) is always allowed through.
    """
    async with SessionLocal() as db:
        chat = await db.get(ChatSession, session_id)
        if chat is None:
            return
        agent = await db.get(Agent, chat.agent_id)
        if agent is None:
            await _emit(session_id, {"type": "error", "code": "config_error", "message": "agent not found"})
            return

        try:
            mcp_tools = await load_mcp_tools(db)
        except Exception as exc:  # pragma: no cover - external
            log.warning("mcp_load_failed", error=str(exc))
            mcp_tools = []

        # Wave 6 (RAG): expose rag_search when the agent's mode is tool/auto,
        # even if it isn't in tools_allowed. `always` injects context instead.
        from ..rag import service as rag_service

        try:
            rag_mode = await rag_service.mode_for(agent)
        except Exception as exc:  # pragma: no cover - never block a turn
            log.warning("rag_mode_failed", error=str(exc))
            rag_mode = "off"
        rag_service.reset_rounds(session_id)  # agentic RAG: fresh round budget per turn
        extra_specs = list(mcp_tools)
        if rag_service.wants_tool(rag_mode) and "rag_search" in TOOL_REGISTRY:
            extra_specs.append(TOOL_REGISTRY["rag_search"])

        specs, policy = resolve_agent_tools(
            agent.tools_allowed,
            agent.tool_policy,
            extra=extra_specs,
            unattended=(mode == "unattended"),
            unattended_allowed=unattended_allowed_tools,
        )
        if restrict_tools is not None:
            allowed = set(restrict_tools) | {"rag_search"}
            specs = [s for s in specs if s.name in allowed]
            policy = {k: v for k, v in policy.items() if k in allowed}
        specs_by_name = {s.name: s for s in specs}
        ctx = ToolContext(
            session_id=session_id, db=db,
            wait_for_approval=wait_for_approval, mode=mode,
            unattended_allowed_tools=list(unattended_allowed_tools or []),
            agent_id=agent.id, tools_allowed=list(agent.tools_allowed or []),
            depth=getattr(chat, "depth", 0) or 0,
        )

        # Phase 14a: resolve the orchestration pattern and dispatch to a Strategy.
        # {} = pattern "react" — identical behaviour to before this refactor.
        from .orchestration.config import resolve_config as _resolve_orch
        from .patterns import RunContext, StrategyUnavailable, get_strategy

        try:
            orch_cfg = _resolve_orch(agent=getattr(agent, "orchestration", None) or {})
        except Exception as exc:  # pragma: no cover - validated on agent save
            log.warning("orch_config_failed", error=str(exc))
            orch_cfg = _resolve_orch()

        # in-traffic A/B (SPEC §16.6): with probability ab_split, swap in the
        # challenger pattern for this turn. The chosen arm is what agent_runs
        # records, so comparison is a group-by on agent_runs.pattern.
        _ab_p = orch_cfg.get("ab_pattern")
        _ab_split = float(orch_cfg.get("ab_split") or 0.0)
        if _ab_p and _ab_split > 0 and random.random() < _ab_split:
            orch_cfg = {**orch_cfg, "pattern": _ab_p}
            await _emit(
                session_id,
                {"type": "ab_arm", "pattern": _ab_p, "challenger": True},
            )

        rc = RunContext(
            db=db, session_id=session_id, chat=chat, agent=agent,
            specs=specs, specs_by_name=specs_by_name, policy=policy, ctx=ctx,
            wait_for_approval=wait_for_approval, mode=mode, rag_mode=rag_mode,
            cfg=orch_cfg, emit=lambda ev: _emit(session_id, ev),
            unattended_allowed_tools=list(unattended_allowed_tools or []),
        )

        # per-turn A/B marker (SPEC §16.6) — records which pattern arm this turn
        # ran, plus its cost/latency/llm_calls once done. Re-used on resume.
        from .orchestration.runs import close_turn_marker, open_turn_marker

        with contextlib.suppress(Exception):
            await open_turn_marker(
                db, session_id,
                pattern=orch_cfg.get("pattern", "react"),
                root_id=getattr(chat, "root_session_id", None),
            )

        await _set_turn_status(db, chat, "running")

        async def _drive() -> None:
            strategy = get_strategy(orch_cfg.get("pattern"))
            await strategy.drive(rc)

        turn_timeout = get_settings().turn_max_seconds
        try:
            if wait_for_approval:
                # interactive: a turn may legitimately sit for a long time waiting
                # for a human to approve a tool — cap the LLM call instead
                # (see _call_llm), not the whole turn.
                await _drive()
            else:
                # unattended (cron / REST): nobody is watching — hard-cap the turn.
                await asyncio.wait_for(_drive(), timeout=turn_timeout)
        except StrategyUnavailable as exc:
            await _set_turn_status(db, chat, "idle")
            await _emit(
                session_id,
                {"type": "error", "code": "orchestration_unavailable", "message": str(exc)},
            )
            return
        except PendingApproval:
            await _set_turn_status(db, chat, "awaiting_approval")
            raise
        except asyncio.TimeoutError:
            await _set_turn_status(db, chat, "idle")
            await _emit(
                session_id,
                {
                    "type": "error",
                    "code": "turn_timeout",
                    "message": f"the turn ran past {turn_timeout}s and was stopped",
                },
            )
            return
        except _TurnError:
            await _set_turn_status(db, chat, "idle")
            return
        except asyncio.CancelledError:
            await _set_turn_status(db, chat, "idle")
            await _emit(
                session_id,
                {"type": "error", "code": "aborted", "message": "generation stopped by user"},
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("turn_failed", session_id=session_id)
            await _set_turn_status(db, chat, "idle")
            await _emit(session_id, {"type": "error", "code": "runtime_error", "message": str(exc)})
            return
        await _set_turn_status(db, chat, "idle")
        await close_turn_marker(session_id)


# ── LLM step ─────────────────────────────────────────────────────────────
async def _call_llm(
    db, session_id: str, chat: ChatSession, agent: Agent, specs, messages: list[Message],
    *, rag_mode: str = "off",
) -> bool:
    provider = get_provider(agent.provider)

    has_images = any(
        m.role == "user" and m.attachments
        and any(a.get("kind") == "image" for a in m.attachments)
        for m in messages
    )
    include_images = bool(getattr(agent, "vision_enabled", True))
    if include_images and has_images and not provider_supports_vision(agent.provider, agent.model):
        include_images = False
        await _emit(
            session_id,
            {
                "type": "error",
                "code": "model_no_vision",
                "message": f"{agent.model} has no image support — attachments were ignored.",
            },
        )
    last_user_text = next(
        (m.content for m in reversed(messages) if m.role == "user" and m.content), ""
    )

    # id of the assistant message this call will produce — allocated up front so
    # the RAG query log can be linked to it (SPEC §14.7 / Playground).
    msg_id = str(uuid4())

    extra_system_parts: list[str] = []
    rag_reserve = 0

    # Wave 6 (RAG): retrieve knowledge-base context *before* trimming history, so
    # its token cost is reserved from the window (SPEC §14.6).
    if rag_mode in ("always", "auto") and last_user_text:
        from ..rag import service as rag_service

        full_budget = context_mod.budget_tokens(agent.model, agent.max_tokens)
        cfg = await rag_service.resolved_config(agent)
        pct = float(cfg.get("context_budget_pct") or 0.6)
        rag_budget = max(256, int(full_budget * pct))
        if cfg.get("context_max_tokens"):
            rag_budget = min(rag_budget, int(cfg["context_max_tokens"]))
        res = await rag_service.retrieve_for_turn(
            agent, session_id=session_id, query=last_user_text,
            budget_tokens=rag_budget, message_id=msg_id,
        )
        if res is not None and res.packed.text:
            extra_system_parts.append(res.packed.text)
            if res.packed.system_note:
                extra_system_parts.append(res.packed.system_note)
            rag_reserve = res.packed.token_count
            await _emit(
                session_id,
                {
                    "type": "rag_retrieval",
                    "query_log_id": res.query_log_id,
                    "chunk_count": len(res.packed.used_chunk_ids),
                    "total_ms": res.total_ms,
                    "citations": res.packed.citations,
                },
            )
        elif res is not None and res.no_context:
            await _emit(
                session_id,
                {
                    "type": "rag_no_context",
                    "query_log_id": res.query_log_id,
                    "reason": res.reason or "no_hits",
                },
            )

    # Wave 3c: trim history to the model's context window (+ rolling summary).
    fitted, summary = await context_mod.prepare(
        db, chat, agent, messages, reserve_tokens=rag_reserve
    )

    # Wave 3d: pull in semantically-relevant slices of earlier conversations.
    if summary:
        extra_system_parts.insert(0, "Summary of earlier conversation:\n" + summary)
    if memory_store.enabled() and last_user_text:
        recalled = await memory_store.retrieve(chat.user_id, session_id, last_user_text)
        if recalled:
            extra_system_parts.append(recalled)
    extra_system = "\n\n".join(p for p in extra_system_parts if p) or None

    llm_messages = to_provider_messages(
        agent.system_prompt,
        fitted,
        session_id=session_id,
        include_images=include_images,
        provider=agent.provider,
        model=agent.model,
        cache_system=True,
        extra_system=extra_system,
    )
    tool_schemas = [s.llm_schema() for s in specs]

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    tokens_in = tokens_out = 0
    cached_tokens = cache_write_tokens = 0
    cache_hit: bool | None = None
    finish_reason = "stop"
    error_text: str | None = None
    started = time.perf_counter()
    llm_timeout = get_settings().llm_call_timeout_seconds

    async def _consume() -> None:
        nonlocal tokens_in, tokens_out, cached_tokens, cache_write_tokens, cache_hit, finish_reason
        async for ev in provider.stream_chat(
            messages=llm_messages,
            tools=tool_schemas,
            model=agent.model,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
        ):
            if isinstance(ev, TextDelta):
                text_parts.append(ev.content)
                await _emit(session_id, {"type": "token", "message_id": msg_id, "content": ev.content})
            elif isinstance(ev, ToolCallComplete):
                tool_calls.append({"id": ev.tool_call_id, "name": ev.name, "args": ev.args})
            elif isinstance(ev, UsageEvent):
                tokens_in, tokens_out, cache_hit = ev.tokens_in, ev.tokens_out, ev.cache_hit
                cached_tokens, cache_write_tokens = ev.cached_tokens, ev.cache_write_tokens
            elif isinstance(ev, DoneEvent):
                finish_reason = ev.finish_reason

    try:
        await asyncio.wait_for(_consume(), timeout=llm_timeout)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        error_text = f"provider did not respond within {llm_timeout}s"
    except Exception as exc:
        error_text = str(exc)

    latency_ms = int((time.perf_counter() - started) * 1000)

    if error_text is not None:
        db.add(
            Trace(
                session_id=session_id, provider=agent.provider, model=agent.model,
                latency_ms=latency_ms, error=error_text,
            )
        )
        await db.commit()
        await _emit(session_id, {"type": "error", "code": "provider_error", "message": error_text})
        raise _TurnError(error_text)

    content = "".join(text_parts) or None
    db.add(
        Message(
            id=msg_id, session_id=session_id, role="assistant", content=content,
            tool_calls=tool_calls or None,
            tokens_in=tokens_in or None, tokens_out=tokens_out or None,
        )
    )
    chat.updated_at = datetime.now(timezone.utc)
    db.add(chat)
    db.add(
        Trace(
            session_id=session_id, message_id=msg_id, provider=agent.provider, model=agent.model,
            latency_ms=latency_ms, tokens_in=tokens_in or None, tokens_out=tokens_out or None,
            cache_hit=cache_hit,
            cached_tokens=cached_tokens or None,
            cache_write_tokens=cache_write_tokens or None,
            cost_usd=estimate_cost(
                agent.model, tokens_in, tokens_out, provider=agent.provider,
                cached_tokens=cached_tokens, cache_write_tokens=cache_write_tokens,
            ),
        )
    )
    await db.commit()

    if content and memory_store.enabled():
        asyncio.create_task(
            memory_store.index_message(session_id, chat.user_id, msg_id, "assistant", content)
        )

    if tool_calls:
        return True
    await _emit(
        session_id,
        {
            "type": "message_done",
            "message_id": msg_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached_tokens": cached_tokens,
        },
    )
    return False


# ── tool / HITL step ────────────────────────────────────────────────────
async def _resolve_tools(
    db, session_id: str, assistant_msg: Message, pending: list[dict], specs_by_name: dict,
    policy: dict, ctx: ToolContext, wait_for_approval: bool,
) -> None:
    for tc in pending:
        tc_id, name = tc["id"], tc["name"]
        args = tc.get("args") or {}
        spec = specs_by_name.get(name)
        if spec is None:
            await _add_tool_result(
                db, session_id, tc_id, name, {"error": f"unknown or disallowed tool: {name}"}
            )
            continue

        needs_approval = policy.get(name, "ask") == "ask"
        if needs_approval and await has_grant(db, session_id, name):
            needs_approval = False  # user chose "always allow" for this chat (Wave 4a)

        if needs_approval:
            approval = await get_or_create_approval(
                db, session_id=session_id, message_id=assistant_msg.id, tool_call_id=tc_id,
                tool_name=name, tool_args=args,
            )
            if approval.status == "pending":
                await _attach_preview(db, session_id, approval)
                await _emit(session_id, _approval_required_event(approval))
                if not wait_for_approval:
                    raise PendingApproval(approval)
                fut = create_waiter(approval.id)
                _wait_t0 = time.monotonic()
                try:
                    await fut
                finally:
                    drop_waiter(approval.id)
                    from .hitl import note_approval_wait

                    note_approval_wait(session_id, time.monotonic() - _wait_t0)

            # The decision was committed by a *different* session (REST/WS handler);
            # re-read this row from the DB before trusting its status.
            await db.refresh(approval)
            if approval.status != "approved":
                await _add_tool_result(db, session_id, tc_id, name, {"error": "user_denied"})
                continue

        await _emit(
            session_id,
            {"type": "tool_call_start", "tool_call_id": tc_id, "tool_name": name, "args": args},
        )
        # let a tool that spawns its own work (delegate_task) latch on this id
        ctx._tool_call_id = tc_id
        # delegate_task manages its own timeout (a sub-agent turn is long-lived)
        tool_timeout = None if name == "delegate_task" else TOOL_TIMEOUT_SECONDS
        try:
            result = await asyncio.wait_for(spec.handler(ctx, args), timeout=tool_timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            result = {"error": "tool execution timed out"}
        except Exception as exc:  # pragma: no cover - defensive
            result = {"error": f"tool failed: {exc}"}

        await _emit(
            session_id, {"type": "tool_call_result", "tool_call_id": tc_id, "result": result}
        )
        await _add_tool_result(db, session_id, tc_id, name, result)


_SCAN_MAX_DEPTH = 3
_SCAN_MAX_CHARS = 20_000


def _iter_scannable_strings(value: object, depth: int = 0, budget: list[int] | None = None):
    """Yield strings nested in *value* (dict/list/str), depth- and size-bounded.

    Tool results come in whatever shape the tool (or, for MCP servers, an
    arbitrary external server) chose — a fixed field-name allowlist misses
    anything shaped differently. Walking the structure instead covers MCP and
    future tools too; the depth/char caps keep a huge or deeply-nested result
    from making every tool call expensive to scan.
    """
    if budget is None:
        budget = [_SCAN_MAX_CHARS]
    if budget[0] <= 0 or depth > _SCAN_MAX_DEPTH:
        return
    if isinstance(value, str):
        budget[0] -= len(value)
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_scannable_strings(v, depth + 1, budget)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_scannable_strings(v, depth + 1, budget)


def _audit_tool_output(session_id: str, tool_name: str, tool_call_id: str, result: dict) -> None:
    """Heuristic prompt-injection check on untrusted tool output (PLAN §10)."""
    if not isinstance(result, dict):
        return
    for value in _iter_scannable_strings(result):
        hits = scan_for_injection(value)
        if hits:
            log.warning(
                "possible_prompt_injection_in_tool_output",
                session_id=session_id, tool_name=tool_name, tool_call_id=tool_call_id,
                markers=hits,
            )


async def _add_tool_result(
    db, session_id: str, tool_call_id: str, tool_name: str, result: dict
) -> None:
    if isinstance(result, dict):
        _audit_tool_output(session_id, tool_name, tool_call_id, result)
    db.add(
        Message(
            session_id=session_id, role="tool", tool_call_id=tool_call_id,
            content=json.dumps(result, ensure_ascii=False),
        )
    )
    chat = await db.get(ChatSession, session_id)
    if chat is not None:
        chat.updated_at = datetime.now(timezone.utc)
        db.add(chat)
    await db.commit()


# ── approval resolution (called by REST + WS) ──────────────────────────
async def resolve_approval(
    db, approval_id: str, decision: str, user_id: str | None, *, remember: str | None = None
) -> ToolApproval:
    stmt = (
        select(ToolApproval)
        .where(ToolApproval.id == approval_id)
        .execution_options(populate_existing=True)
    )
    approval = (await db.exec(stmt)).first()
    if approval is None:
        raise KeyError("not_found")
    if approval.status != "pending":
        raise ValueError("already_resolved")

    status = "approved" if decision in ("approve", "approved") else "denied"
    await mark_resolved(db, approval, status=status, user_id=user_id)
    if status == "approved" and remember == "session":
        await add_grant(
            db, session_id=approval.session_id, tool_name=approval.tool_name, user_id=user_id
        )
    await _emit(
        approval.session_id,
        {"type": "approval_resolved", "approval_id": approval.id, "status": status},
    )

    if not resolve_waiter(approval.id, status):
        # No in-process waiter: REST 202 flow, or orphaned after a restart.
        # agent_runtime rebuilds everything from the DB, so just resume.
        task = asyncio.create_task(run_turn(approval.session_id, wait_for_approval=True))
        manager.set_task(approval.session_id, task)

    return approval


async def cancel_session_waiters(session_id: str) -> None:  # pragma: no cover - helper
    with contextlib.suppress(Exception):
        manager.abort(session_id)


# ── regenerate / edit-message (PLAN §7.8) ───────────────────────────────
class NotFound(Exception):
    pass


async def truncate_after(db, session_id: str, keep_message_id: str) -> None:
    """Delete every message strictly after `keep_message_id`, plus the
    tool_approvals/traces that reference them, in FK-safe order."""
    ordered = await load_messages(db, session_id)
    idx = next((i for i, m in enumerate(ordered) if m.id == keep_message_id), None)
    if idx is None:
        raise NotFound(keep_message_id)
    doomed = ordered[idx + 1 :]
    if not doomed:
        return
    doomed_ids = [m.id for m in doomed]
    for m in doomed:
        if m.attachments:
            from . import attachments as att_store

            att_store.delete_many(session_id, m.attachments)
    for a in (
        await db.exec(select(ToolApproval).where(ToolApproval.message_id.in_(doomed_ids)))
    ).all():
        await db.delete(a)
    for t in (await db.exec(select(Trace).where(Trace.message_id.in_(doomed_ids)))).all():
        await db.delete(t)
    # Flush the child-row deletes before touching messages: Message/ToolApproval/
    # Trace have no ORM `relationship()` between them (plain FK columns only),
    # so SQLAlchemy's unit-of-work has no dependency info to order these deletes
    # itself — without this flush it can (and does) emit the messages DELETE
    # first and violate the FK.
    await db.flush()
    for m in doomed:
        await db.delete(m)
    chat = await db.get(ChatSession, session_id)
    if chat is not None:
        chat.updated_at = datetime.now(timezone.utc)
        db.add(chat)
    await db.commit()
    # regenerate / edit dropped a turn's output — a dissatisfaction signal for
    # the A/B report (SPEC §16.6).
    with contextlib.suppress(Exception):
        from .orchestration.runs import mark_turn_regenerated

        await mark_turn_regenerated(db, session_id)


async def regenerate_last(session_id: str) -> None:
    """Drop the last assistant turn (and any tool traffic that produced it) and
    re-run from the last user message."""
    async with SessionLocal() as db:
        messages = await load_messages(db, session_id)
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        if last_user is None:
            return
        await truncate_after(db, session_id, last_user.id)
    await run_turn(session_id, wait_for_approval=True)


async def edit_message_and_regenerate(session_id: str, message_id: str, new_content: str) -> None:
    """Rewrite a previously-sent user message, drop everything after it, and
    re-run the turn from there."""
    async with SessionLocal() as db:
        msg = await db.get(Message, message_id)
        if msg is None or msg.session_id != session_id or msg.role != "user":
            raise NotFound(message_id)
        msg.content = new_content
        db.add(msg)
        await db.commit()
        await truncate_after(db, session_id, msg.id)
    await run_turn(session_id, wait_for_approval=True)
