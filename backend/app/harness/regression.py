"""17c — Proposal Validation / regression gate (PLAN §17c).

Re-runs the candidate's one-field patch against the held-in (failing) cases
and the held-out cases of the source eval run, scores both, and compares
against the baseline already stored on those cases from the original run (no
need to re-run the baseline — it was already scored). Only activates
(supersedes the previous active version and writes the live ``Agent`` row)
if *both* scores are non-regressions; otherwise rejects but keeps the row for
history. This is the only place besides ``harness/rollback.py`` allowed to
call ``apply_patch`` (PLAN §2 principle 13 / §8 checklist).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from sqlmodel import select

from ..config import get_settings
from ..core.agent_config import apply_patch
from ..db.models import Agent, AgentConfigVersion, AgentEvalCase, ChatSession, Message, User
from ..db.session import SessionLocal
from ..eval.agent.metrics import trajectory_metrics
from ..rag.eval.judge import Judge
from .miner import failing_case_ids

log = structlog.get_logger("pagi.harness.regression")

_JUDGE_SYS = (
    "You grade an AI agent's transcript against a task. Return JSON: "
    '{"resolved": true|false, "reason": "..."}.'
)


def _as_bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "1"}
    return bool(v)


def _rate(vals) -> float | None:
    bools = [bool(v) for v in vals if v is not None]
    return round(sum(bools) / len(bools), 4) if bools else None


async def _reject(version_id: str, reason: str) -> None:
    async with SessionLocal() as db:
        v = await db.get(AgentConfigVersion, version_id)
        if v is None:
            return
        v.status = "rejected"
        v.reject_reason = reason
        db.add(v)
        await db.commit()
    log.info("harness_regression_rejected_early", version_id=version_id, reason=reason)


async def _run_case(agent_id: str, owner_id: str, case: AgentEvalCase, judge: Judge) -> bool | None:
    """Run one case against the agent's *current* (temporarily patched) live
    config and return the judge's resolved verdict. Ephemeral — nothing here
    is persisted as a new AgentEvalRun/AgentEvalCase row."""
    from ..core.agent_runtime import run_turn

    async with SessionLocal() as db:
        sess = ChatSession(user_id=owner_id, agent_id=agent_id, title="[harness] regression")
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        session_id = sess.id
        db.add(Message(session_id=session_id, role="user", content=case.prompt))
        await db.commit()

    await asyncio.wait_for(
        run_turn(session_id, wait_for_approval=False, mode="unattended"),
        timeout=300,
    )

    async with SessionLocal() as db:
        m = await trajectory_metrics(
            db, session_id, optimal_steps=case.optimal_steps,
            forbidden_tools=case.forbidden_tools or [],
        )
    tool_lines = "\n".join(f"- {n}({a})" for n, a in m["_tool_calls"]) or "(no tool calls)"
    verdict = await judge.complete_json(
        _JUDGE_SYS,
        f"TASK:\n{case.prompt}\n\nEXPECTED OUTCOME:\n{case.expected_outcome or '(unspecified)'}"
        f"\n\nAGENT FINAL ANSWER:\n{m['answer']}\n\nTOOL CALLS:\n{tool_lines}",
    )
    return _as_bool(verdict.get("resolved"))


async def validate(version_id: str) -> None:
    async with SessionLocal() as db:
        version = await db.get(AgentConfigVersion, version_id)
        if version is None or version.status != "proposed":
            return
        agent = await db.get(Agent, version.agent_id)
        run_id = version.source_eval_run_id

    if agent is None:
        await _reject(version_id, "agent_missing")
        return
    if run_id is None:
        await _reject(version_id, "no_source_eval_run")
        return

    async with SessionLocal() as db:
        held_in_ids = await failing_case_ids(db, run_id)
        held_in_rows = (
            list((await db.exec(select(AgentEvalCase).where(AgentEvalCase.id.in_(held_in_ids)))).all())
            if held_in_ids
            else []
        )
        held_out_rows = list(
            (
                await db.exec(
                    select(AgentEvalCase).where(
                        AgentEvalCase.run_id == run_id, AgentEvalCase.held_out == True  # noqa: E712
                    )
                )
            ).all()
        )
        owner = (await db.exec(select(User).order_by(User.created_at))).first()
        owner_id = owner.id if owner else None

    if not held_out_rows:
        await _reject(version_id, "no_held_out_cases")
        return

    baseline_held_in = _rate([r.resolved for r in held_in_rows])
    baseline_held_out = _rate([r.resolved for r in held_out_rows])
    if baseline_held_out is None:
        await _reject(version_id, "held_out_unscored")
        return
    if owner_id is None:
        await _reject(version_id, "no_owner_user")
        return

    field, new_value = next(iter(version.diff.items()))
    async with SessionLocal() as db:
        live = await db.get(Agent, agent.id)
        original_value = getattr(live, field)

    candidate: dict[str, bool | None] = {}
    settings = get_settings()
    judge = Judge(model=settings.harness_judge_model)
    try:
        async with SessionLocal() as db:
            live = await db.get(Agent, agent.id)
            setattr(live, field, new_value)
            db.add(live)
            await db.commit()

        for case in held_in_rows + held_out_rows:
            try:
                candidate[case.id] = await _run_case(agent.id, owner_id, case, judge)
            except Exception as exc:  # pragma: no cover - keep validating the rest
                log.warning(
                    "harness_regression_case_failed",
                    version_id=version_id, case_id=case.id, error=str(exc),
                )
                candidate[case.id] = None
    finally:
        async with SessionLocal() as db:
            live = await db.get(Agent, agent.id)
            setattr(live, field, original_value)
            db.add(live)
            await db.commit()

    cand_held_in = _rate([candidate.get(c.id) for c in held_in_rows]) if held_in_rows else None
    cand_held_out = _rate([candidate.get(c.id) for c in held_out_rows])

    held_in_ok = baseline_held_in is None or (
        cand_held_in is not None and cand_held_in >= baseline_held_in
    )
    held_out_ok = cand_held_out is not None and cand_held_out >= baseline_held_out
    not_regressed = held_in_ok and held_out_ok

    async with SessionLocal() as db:
        version = await db.get(AgentConfigVersion, version_id)
        version.held_in_score = cand_held_in
        version.held_out_score = cand_held_out
        if not_regressed:
            prev_active = (
                await db.exec(
                    select(AgentConfigVersion).where(
                        AgentConfigVersion.agent_id == agent.id,
                        AgentConfigVersion.status == "active",
                    )
                )
            ).first()
            if prev_active is not None and prev_active.id != version.id:
                prev_active.status = "superseded"
                db.add(prev_active)
            live_agent = await db.get(Agent, agent.id)
            apply_patch(live_agent, {field: new_value})
            db.add(live_agent)
            version.status = "active"
            version.activated_at = datetime.now(timezone.utc)
        else:
            version.status = "rejected"
            version.reject_reason = (
                f"held_in {cand_held_in} vs baseline {baseline_held_in}; "
                f"held_out {cand_held_out} vs baseline {baseline_held_out}"
            )
        db.add(version)
        await db.commit()
    log.info(
        "harness_regression_done", version_id=version_id, status=version.status,
        held_in=cand_held_in, held_out=cand_held_out,
    )
