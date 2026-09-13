"""Supervisor — decompose -> delegate (parallel) -> synthesize (Phase 14c, SPEC §16.2).

Anti-patterns blocked by design (SPEC §16.2):
  God Supervisor  — the supervisor turn gets no sandbox tools, only delegation
  Chatty Workers  — a worker returns its final answer only (run_delegated)
  Implicit State  — each sub-task is composed to stand on its own
  No Fallback     — a worker error is an object the synthesiser must handle
"""

from __future__ import annotations

import asyncio

import structlog

from ._common import NodeLog, complete_json, simple_llm
from .base import RunContext, register

log = structlog.get_logger("pagi.patterns.supervisor")

_PLAN_SYS = (
    "You coordinate specialist agents. Split the user's request into independent "
    "sub-tasks, each assigned to one of the named worker agents. Each sub-task "
    "must stand on its own — the worker cannot see this conversation. "
    'Return JSON {"subtasks": [{"agent": "<name>", "task": "...", "context": "..."}]}'
)
_SYNTH_SYS = (
    "You are given the user's original request and the results returned by "
    "specialist agents (some may be errors). Write the single best final answer. "
    "If a worker failed, work around it or say what is missing."
)


class SupervisorStrategy:
    name = "supervisor"

    async def drive(self, rc: RunContext) -> None:
        from ...db.models import Agent, Message
        from ..delegation import run_delegated

        cfg = rc.cfg
        max_workers = int(cfg.get("max_workers") or 3)
        parallel = bool(cfg.get("parallel", True))
        emit_mid = cfg.get("emit_intermediate", True)
        task = await _first_user_text(rc)

        worker_ids = cfg.get("worker_agent_ids") or []
        workers = [w for w in [await rc.db.get(Agent, wid) for wid in worker_ids] if w]
        by_name = {w.name: w for w in workers}
        catalogue = "\n".join(
            f"- {w.name}: {w.delegate_description or w.name}" for w in workers
        )

        async with NodeLog(rc, node="plan", step_no=0) as nl:
            obj = await complete_json(
                rc,
                system=_PLAN_SYS,
                user=f"WORKERS:\n{catalogue}\n\nREQUEST:\n{task}",
                model=cfg.get("supervisor_model"),
                kind="plan",
            )
            subtasks = (obj.get("subtasks") if isinstance(obj, dict) else obj) or []
            subtasks = [s for s in subtasks if s.get("agent") in by_name][:max_workers]
            nl.payload = {"subtasks": subtasks}
            nl.output_summary = f"{len(subtasks)} subtasks"
        if emit_mid:
            await rc.emit_node({"type": "plan_created", "steps": subtasks})

        if not subtasks:
            # nothing to split — just answer directly
            from ._common import run_react_once

            await run_react_once(rc)
            return

        sem = asyncio.Semaphore(
            max(1, min(len(subtasks), _parallel_limit()))
        ) if parallel else asyncio.Semaphore(1)

        async def _one(idx: int, st: dict) -> dict:
            async with sem:
                # NodeLog is opened here so the concurrent workers each carry
                # their own run_id (rc.last_run_id is shared and racy under gather)
                async with NodeLog(rc, node="delegate", step_no=1 + idx) as nl:
                    rid = nl.row.id if nl.row is not None else None
                    if emit_mid:
                        await rc.emit(
                            {
                                "type": "worker_started",
                                "run_id": rid,
                                "agent_name": st["agent"],
                                "task": st.get("task", ""),
                            }
                        )
                    res = await run_delegated(
                        parent_session_id=rc.session_id,
                        tool_call_id=f"sup:{rc.session_id}:{idx}",
                        worker_name=st["agent"],
                        task=st.get("task", ""),
                        context=st.get("context"),
                        wait_for_approval=rc.wait_for_approval,
                        mode=rc.mode,
                        unattended_allowed_tools=rc.unattended_allowed_tools,
                    )
                    nl.payload = {"agent": st["agent"], "error": res.get("error")}
                    nl.output_summary = (res.get("answer") or res.get("message") or "")[:200]
                    if emit_mid:
                        await rc.emit(
                            {
                                "type": "worker_result",
                                "run_id": rid,
                                "agent_name": st["agent"],
                                "status": "error" if res.get("error") else "ok",
                            }
                        )
                return {"agent": st["agent"], **res}

        if parallel:
            results = await asyncio.gather(
                *[_one(i, st) for i, st in enumerate(subtasks)]
            )
        else:
            results = [await _one(i, st) for i, st in enumerate(subtasks)]

        async with NodeLog(rc, node="synthesize", step_no=1 + len(subtasks)) as nl:
            digest = "\n\n".join(
                f"[{r['agent']}] "
                + (f"ERROR {r['error']}: {r.get('message', '')}" if r.get("error")
                   else r.get("answer", ""))
                for r in results
            )
            final = await simple_llm(
                rc,
                system=_SYNTH_SYS,
                user=f"ORIGINAL REQUEST:\n{task}\n\nWORKER RESULTS:\n{digest}",
                model=cfg.get("synthesis_model") or cfg.get("supervisor_model"),
                kind="synthesize",
                max_tokens=1200,
            )
            nl.output_summary = final[:200]

        rc.db.add(Message(session_id=rc.session_id, role="assistant", content=final))
        await rc.db.commit()
        await rc.emit({"type": "message_done", "message_id": None, "tokens_in": 0, "tokens_out": 0})


def _parallel_limit() -> int:
    from ...config import get_settings

    return max(1, get_settings().subagent_parallel_limit)


async def _first_user_text(rc: RunContext) -> str:
    from sqlmodel import select

    from ...db.models import Message

    row = (
        await rc.db.exec(
            select(Message)
            .where(Message.session_id == rc.session_id, Message.role == "user")
            .order_by(Message.created_at)
        )
    ).first()
    return (row.content or "") if row else ""


register(SupervisorStrategy())
