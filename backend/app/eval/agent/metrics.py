"""Trajectory metrics computed from a finished eval session (PLAN §16 / §12).

Reads the session's ``messages`` + ``traces`` (+ sub-agent tree) and returns a
dict of raw per-case numbers. The judge-based bits (resolved, parameter
hallucination) are done in the runner; this module is deterministic.
"""

from __future__ import annotations

import json

from sqlmodel import select

from ...db.models import ChatSession, Message, Trace


def _canon_args(args) -> str:
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(args)


async def trajectory_metrics(
    db, session_id: str, *, optimal_steps: int | None, forbidden_tools: list[str]
) -> dict:
    # every session in the tree (sub-agents count toward cost / calls)
    tree_ids = [session_id] + [
        s for s in (
            await db.exec(
                select(ChatSession.id).where(ChatSession.root_session_id == session_id)
            )
        ).all()
    ]

    msgs = (
        await db.exec(
            select(Message)
            .where(Message.session_id.in_(tree_ids))
            .order_by(Message.created_at, Message.id)
        )
    ).all()
    traces = (
        await db.exec(select(Trace).where(Trace.session_id.in_(tree_ids)))
    ).all()

    tool_calls: list[tuple[str, str]] = []  # (name, canonical args)
    forbidden = set(forbidden_tools or [])
    forbidden_used = False
    for m in msgs:
        if m.role == "assistant" and m.tool_calls:
            for tc in m.tool_calls:
                name = tc.get("name", "")
                tool_calls.append((name, _canon_args(tc.get("args", {}))))
                if name in forbidden:
                    forbidden_used = True

    # steps = distinct tool invocations + 1 final answer turn (rough, matches
    # KNOWLEDGE §12 "actual steps")
    steps_taken = len(tool_calls) + 1

    # redundant = a (name, args) pair issued more than once
    seen: set[tuple[str, str]] = set()
    redundant = 0
    for key in tool_calls:
        if key in seen:
            redundant += 1
        else:
            seen.add(key)

    llm_calls = sum(1 for t in traces if (t.kind or "chat") in {
        "chat", "plan", "evaluate", "reflect", "route", "worker", "judge",
        "synthesize", "summary", "transform",
    })
    cost = sum(t.cost_usd or 0.0 for t in traces)
    latency = sum(t.latency_ms or 0 for t in traces)

    step_eff = None
    if optimal_steps and steps_taken > 0:
        step_eff = round(min(1.0, optimal_steps / steps_taken), 4)

    final = ""
    for m in reversed(msgs):
        if m.session_id == session_id and m.role == "assistant" and (m.content or "").strip():
            final = m.content
            break

    return {
        "answer": final,
        "steps_taken": steps_taken,
        "redundant_tool_calls": redundant,
        "forbidden_tool_used": forbidden_used,
        "llm_calls": llm_calls,
        "cost_usd": round(cost, 6),
        "latency_ms": latency,
        "step_efficiency": step_eff,
        "_tool_calls": tool_calls,  # for the judge's parameter-hallucination check
    }
