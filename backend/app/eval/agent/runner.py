"""Run an agent-eval case set (PLAN §16). Background, like the RAG eval runner.

Per case: spin a throwaway ChatSession for the agent (optionally overriding the
orchestration pattern), seed the prompt, run one turn, then score the trajectory
(deterministic metrics) + judge (resolved? parameter hallucination?).
"""

from __future__ import annotations

import asyncio

import structlog
from sqlmodel import select

from ...db.models import Agent, AgentEvalCase, AgentEvalRun, ChatSession, Message
from ...db.session import SessionLocal
from ...rag.eval.judge import Judge
from .metrics import trajectory_metrics

log = structlog.get_logger("pagi.eval.agent")

_JUDGE_SYS = (
    "You grade an AI agent's transcript against a task. Return JSON: "
    '{"resolved": true|false, "parameter_hallucination": true|false, '
    '"reason": "..."}. `parameter_hallucination` is true if any tool call used a '
    "clearly invented / nonsensical argument value."
)


def _mean(vals) -> float | None:
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 4) if nums else None


def _rate(vals) -> float | None:
    bools = [bool(v) for v in vals if v is not None]
    return round(sum(bools) / len(bools), 4) if bools else None


async def run(run_id: str, *, judge: Judge | None = None) -> None:
    async with SessionLocal() as db:
        run_row = await db.get(AgentEvalRun, run_id)
        if run_row is None:
            return
        agent = await db.get(Agent, run_row.agent_id)
        cases = list(
            (await db.exec(select(AgentEvalCase).where(AgentEvalCase.run_id == run_id))).all()
        )
        pattern_override = run_row.pattern
        judge = judge or Judge(model=run_row.judge_model)
        owner_id = None
        from ...db.models import User

        owner = (await db.exec(select(User).order_by(User.created_at))).first()
        owner_id = owner.id if owner else None

    if agent is None or owner_id is None:
        async with SessionLocal() as db:
            r = await db.get(AgentEvalRun, run_id)
            if r:
                r.status = "error"
                db.add(r)
                await db.commit()
        return

    from ...core.agent_runtime import run_turn

    for case in cases:
        session_id = None
        try:
            async with SessionLocal() as db:
                sess = ChatSession(user_id=owner_id, agent_id=agent.id, title="[eval] case")
                db.add(sess)
                await db.commit()
                await db.refresh(sess)
                session_id = sess.id
                db.add(Message(session_id=session_id, role="user", content=case.prompt))
                await db.commit()

            # optional pattern override for A/B between patterns
            if pattern_override:
                async with SessionLocal() as db:
                    ov_agent = await db.get(Agent, agent.id)
                    # don't persist — only for this run's sessions; simplest is to
                    # temporarily patch the agent row, run, then restore.
                    original = dict(ov_agent.orchestration or {})
                    ov_agent.orchestration = {**original, "pattern": pattern_override}
                    db.add(ov_agent)
                    await db.commit()

            await asyncio.wait_for(
                run_turn(session_id, wait_for_approval=False, mode="unattended"),
                timeout=300,
            )

            if pattern_override:
                async with SessionLocal() as db:
                    ov_agent = await db.get(Agent, agent.id)
                    ov_agent.orchestration = original
                    db.add(ov_agent)
                    await db.commit()

            async with SessionLocal() as db:
                m = await trajectory_metrics(
                    db, session_id,
                    optimal_steps=case.optimal_steps,
                    forbidden_tools=case.forbidden_tools or [],
                )
            tool_lines = "\n".join(f"- {n}({a})" for n, a in m["_tool_calls"]) or "(no tool calls)"
            try:
                verdict = await judge.complete_json(
                    _JUDGE_SYS,
                    f"TASK:\n{case.prompt}\n\nEXPECTED OUTCOME:\n{case.expected_outcome or '(unspecified)'}"
                    f"\n\nAGENT FINAL ANSWER:\n{m['answer']}\n\nTOOL CALLS:\n{tool_lines}",
                )
            except Exception as exc:  # pragma: no cover - external
                verdict = {"resolved": None, "parameter_hallucination": None, "reason": str(exc)}

            async with SessionLocal() as db:
                row = await db.get(AgentEvalCase, case.id)
                row.answer = m["answer"]
                row.steps_taken = m["steps_taken"]
                row.redundant_tool_calls = m["redundant_tool_calls"]
                row.forbidden_tool_used = m["forbidden_tool_used"]
                row.llm_calls = m["llm_calls"]
                row.cost_usd = m["cost_usd"]
                row.latency_ms = m["latency_ms"]
                row.resolved = _as_bool(verdict.get("resolved"))
                row.parameter_hallucination = _as_bool(verdict.get("parameter_hallucination"))
                row.judge_rationale = verdict if isinstance(verdict, dict) else {}
                db.add(row)
                await db.commit()
        except Exception as exc:  # pragma: no cover - keep the run going
            log.warning("agent_eval_case_failed", run_id=run_id, error=str(exc))
            async with SessionLocal() as db:
                row = await db.get(AgentEvalCase, case.id)
                if row:
                    row.judge_rationale = {"error": str(exc)}
                    db.add(row)
                    await db.commit()

    # aggregate
    async with SessionLocal() as db:
        run_row = await db.get(AgentEvalRun, run_id)
        rows = list(
            (await db.exec(select(AgentEvalCase).where(AgentEvalCase.run_id == run_id))).all()
        )
        run_row.task_resolution_rate = _rate([r.resolved for r in rows])
        run_row.step_efficiency = _mean(
            [
                (r.optimal_steps / r.steps_taken)
                for r in rows
                if r.optimal_steps and r.steps_taken
            ]
        )
        run_row.redundant_tool_rate = _mean([r.redundant_tool_calls for r in rows])
        run_row.parameter_hallucination_rate = _rate([r.parameter_hallucination for r in rows])
        run_row.forbidden_tool_rate = _rate([r.forbidden_tool_used for r in rows])
        run_row.avg_llm_calls = _mean([r.llm_calls for r in rows])
        run_row.avg_cost_usd = _mean([r.cost_usd for r in rows])
        run_row.avg_latency_ms = _mean([r.latency_ms for r in rows])
        run_row.status = "done"
        db.add(run_row)
        await db.commit()
    log.info("agent_eval_run_done", run_id=run_id, cases=len(cases))

    # Phase 17 — opt-in autopilot: mine/propose/validate a harness patch off
    # the back of this run. Off by default (HARNESS_AUTO_RUN_ON_EVAL); always
    # available on demand via POST /api/agent-eval/runs/{id}/harness/run.
    from ...config import get_settings as _get_settings

    if _get_settings().harness_auto_run_on_eval:
        from ...harness import schedule as _schedule_harness

        _schedule_harness(run_id)


def _as_bool(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "1"}
    return bool(v)


def schedule(run_id: str) -> None:
    asyncio.create_task(run(run_id))
