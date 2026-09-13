"""Plan & Execute — plan -> (execute -> verify)* -> replan (Phase 14b, SPEC §16.2).

The plan is structured ``[{step, goal, done_when}]``; ``done_when`` is a checkable
criterion so the verifier isn't just guessing. Steps and their status go into
``agent_runs`` so a crashed turn resumes from the last unfinished step.
"""

from __future__ import annotations

import structlog

from ._common import (
    NodeLog,
    append_user,
    complete_json,
    done_nodes,
    last_assistant_text,
    run_react_once,
    simple_llm,
)
from .base import RunContext, register

log = structlog.get_logger("pagi.patterns.plan_execute")

_PLAN_SYS = (
    "Break the user's task into an ordered list of at most {n} concrete steps. "
    'Return JSON: {"steps": [{"step": 1, "goal": "...", "done_when": "..."}]}'
)
_VERIFY_SYS = (
    "Given a step goal, its done-when criterion, and the work log, reply with JSON "
    '{"met": true|false, "why": "..."}.'
)


class PlanExecuteStrategy:
    name = "plan_execute"

    async def drive(self, rc: RunContext) -> None:
        cfg = rc.cfg
        max_steps = int(cfg.get("max_steps") or 6)
        verify_each = bool(cfg.get("verify_each_step", True))
        replan_on_fail = bool(cfg.get("replan_on_failure", True))
        max_replans = int(cfg.get("max_replans") or 1)
        emit_mid = cfg.get("emit_intermediate", True)

        task = await _first_user_text(rc)
        already = await done_nodes(rc)

        replans = 0
        step_base = 0
        while True:
            plan_key = ("plan", step_base)
            if plan_key in already:
                steps = already[plan_key].get("steps", [])
            else:
                async with NodeLog(rc, node="plan", step_no=step_base) as nl:
                    obj = await complete_json(
                        rc,
                        system=_PLAN_SYS.format(n=max_steps),
                        user=task,
                        model=cfg.get("planner_model"),
                        kind="plan",
                    )
                    steps = (obj.get("steps") if isinstance(obj, dict) else obj) or []
                    steps = steps[:max_steps]
                    nl.payload = {"steps": steps}
                    nl.output_summary = f"{len(steps)} steps"
                if emit_mid:
                    await rc.emit_node({"type": "plan_created", "steps": steps})

            failed_step = None
            for i, st in enumerate(steps):
                ex_step = step_base + 1 + i * 2
                if ("execute", ex_step) in already:
                    continue
                goal = st.get("goal") or f"step {i + 1}"
                done_when = st.get("done_when") or ""
                if emit_mid:
                    await rc.emit_node({"type": "step_started", "step": i + 1, "goal": goal})
                async with NodeLog(rc, node="execute", step_no=ex_step) as nl:
                    await append_user(
                        rc,
                        f"Work on this step now: {goal}\n"
                        f"You are done with this step when: {done_when}",
                    )
                    out = await run_react_once(rc)
                    nl.output_summary = out[:200]

                if verify_each and done_when:
                    async with NodeLog(rc, node="verify", step_no=ex_step + 1) as nl:
                        v = await complete_json(
                            rc,
                            system=_VERIFY_SYS,
                            user=f"GOAL: {goal}\nDONE_WHEN: {done_when}\nWORK: {out}",
                            model=cfg.get("planner_model"),
                            kind="evaluate",
                        )
                        met = bool(v.get("met")) if isinstance(v, dict) else True
                        nl.payload = {"met": met, "why": (v or {}).get("why", "") if isinstance(v, dict) else ""}
                        nl.output_summary = "met" if met else "NOT met"
                    if emit_mid:
                        await rc.emit_node({"type": "step_done", "step": i + 1, "status": "done" if met else "failed"})
                    if not met:
                        failed_step = (i, goal, (v or {}).get("why", "") if isinstance(v, dict) else "")
                        break
                elif emit_mid:
                    await rc.emit_node({"type": "step_done", "step": i + 1, "status": "done"})

            if failed_step is None or not replan_on_fail or replans >= max_replans:
                break
            replans += 1
            step_base += 1 + len(steps) * 2
            already = {}  # after a replan, redo everything from the new plan
            await append_user(
                rc,
                f"Step '{failed_step[1]}' did not meet its criterion ({failed_step[2]}). "
                "Re-plan the remaining work and continue.",
            )

        # a final synthesis pass so the last assistant message is a full answer
        if not (await last_assistant_text(rc)).strip():
            await append_user(rc, "Now give the complete final answer to the original task.")
            await run_react_once(rc)


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


register(PlanExecuteStrategy())
