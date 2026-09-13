"""Synthesize agent-eval cases from an agent's own config (Phase 16, PLAN §16).

Given the agent's system prompt + tool set, ask a model for a spread of tasks:
some it should solve in a couple of steps, some that tempt an unnecessary tool,
some out-of-scope (expected outcome = a refusal). The user edits before running.
"""

from __future__ import annotations

import json

import structlog

from ...db.models import Agent
from ...rag.eval.judge import Judge

log = structlog.get_logger("pagi.eval.agent.synth")

_SYS = (
    "You design an evaluation set for an AI agent. Given its system prompt and "
    "available tools, produce {n} varied test cases. Mix: (a) tasks solvable in "
    "1-3 steps, (b) a task where a tempting tool is actually unnecessary, (c) a "
    "task outside the agent's scope (the agent should decline). "
    'Return JSON {"cases": [{"prompt": "...", "expected_outcome": "...", '
    '"optimal_steps": <int>, "forbidden_tools": ["..."]}]}. '
    "forbidden_tools lists tools that MUST NOT be used for that case (often []). "
    "optimal_steps is your estimate of the minimal number of steps."
)


async def synthesize(
    agent: Agent, *, n: int = 8, judge: Judge | None = None
) -> list[dict]:
    judge = judge or Judge()
    tools = ", ".join(agent.tools_allowed or []) or "(none)"
    user = (
        f"SYSTEM PROMPT:\n{agent.system_prompt or '(empty)'}\n\n"
        f"TOOLS: {tools}\n"
        f"DELEGATE DESCRIPTION: {agent.delegate_description or '(none)'}"
    )
    try:
        obj = await judge.complete_json(_SYS.format(n=n), user, max_tokens=1800)
    except Exception as exc:  # pragma: no cover - external
        log.warning("agent_case_synth_failed", error=str(exc))
        return []

    cases = obj.get("cases") if isinstance(obj, dict) else obj
    out: list[dict] = []
    for c in (cases or [])[:n]:
        if not isinstance(c, dict) or not c.get("prompt"):
            continue
        out.append(
            {
                "prompt": str(c["prompt"]),
                "expected_outcome": c.get("expected_outcome"),
                "optimal_steps": _int_or_none(c.get("optimal_steps")),
                "forbidden_tools": [
                    t for t in (c.get("forbidden_tools") or []) if isinstance(t, str)
                ],
            }
        )
    return out


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
