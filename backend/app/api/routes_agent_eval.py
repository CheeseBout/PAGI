"""Agent / trajectory evaluation API (Phase 16, PLAN §16).

Create a run against one agent (+ optional pattern override), it executes the
cases in the background, then read back the aggregates and per-case results to
compare patterns on quality ↔ cost ↔ latency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from datetime import datetime, timezone
from typing import get_args

from ..core.orchestration.config import Pattern
from ..core.orchestration.runs import TURN_NODE
from ..db.models import Agent, AgentEvalCase, AgentEvalRun, AgentRun, ChatSession, User
from ..eval.agent import runner, synthesize
from ..schemas import AgentEvalRunCreate, AgentEvalSynth
from .deps import APIError, get_current_user, get_db
from .serializers import agent_eval_case_out, agent_eval_run_out

router = APIRouter(prefix="/api/agent-eval", tags=["agent-eval"])

_PATTERNS = set(get_args(Pattern))


@router.get("/runs")
async def list_runs(
    agent_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(AgentEvalRun).order_by(AgentEvalRun.created_at.desc())
    if agent_id:
        stmt = stmt.where(AgentEvalRun.agent_id == agent_id)
    return [agent_eval_run_out(r) for r in (await db.exec(stmt)).all()]


@router.post("/synth", status_code=status.HTTP_202_ACCEPTED)
async def synth_cases(
    body: AgentEvalSynth,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    agent = await db.get(Agent, body.agent_id)
    if agent is None:
        raise APIError(404, "not_found", "agent not found")
    from ..rag.eval.judge import Judge

    cases = await synthesize(agent, n=body.n, judge=Judge(model=body.model))
    return {"cases": cases}


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    body: AgentEvalRunCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    agent = await db.get(Agent, body.agent_id)
    if agent is None:
        raise APIError(404, "not_found", "agent not found")
    if not body.cases:
        raise APIError(422, "no_cases", "at least one case is required")
    if body.pattern and body.pattern not in _PATTERNS:
        raise APIError(422, "bad_pattern", f"unknown pattern {body.pattern!r}")

    run = AgentEvalRun(
        name=body.name,
        agent_id=body.agent_id,
        pattern=body.pattern,
        judge_model=body.judge_model,
        case_count=len(body.cases),
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    for c in body.cases:
        db.add(
            AgentEvalCase(
                run_id=run.id,
                prompt=c.prompt,
                expected_outcome=c.expected_outcome,
                optimal_steps=c.optimal_steps,
                forbidden_tools=c.forbidden_tools,
                held_out=c.held_out,
            )
        )
    await db.commit()

    runner.schedule(run.id)
    return agent_eval_run_out(run)


@router.get("/ab-report")
async def ab_report(
    agent_id: str = Query(...),
    since: str | None = Query(default=None, description="ISO lower bound"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """In-traffic A/B outcome signal (SPEC §16.6): per pattern arm, aggregate the
    per-turn markers written by run_turn — turns, avg cost / latency / LLM calls,
    and regenerate-rate (a dissatisfaction proxy)."""
    sess_ids = list(
        (
            await db.exec(
                select(ChatSession.id).where(ChatSession.agent_id == agent_id)
            )
        ).all()
    )
    if not sess_ids:
        return {"agent_id": agent_id, "arms": []}

    stmt = select(AgentRun).where(
        AgentRun.session_id.in_(sess_ids),
        AgentRun.node == TURN_NODE,
        AgentRun.status == "done",
    )
    if since:
        try:
            lb = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if lb.tzinfo:
                lb = lb.astimezone(timezone.utc).replace(tzinfo=None)
            stmt = stmt.where(AgentRun.created_at >= lb)
        except ValueError:
            raise APIError(400, "bad_request", "`since` must be ISO-8601")

    rows = (await db.exec(stmt)).all()
    arms: dict[str, dict] = {}
    for r in rows:
        a = arms.setdefault(
            r.pattern,
            {"pattern": r.pattern, "turns": 0, "_cost": 0.0, "_lat": 0,
             "_calls": 0, "_regen": 0},
        )
        a["turns"] += 1
        a["_cost"] += r.cost_usd or 0.0
        a["_lat"] += r.latency_ms or 0
        a["_calls"] += int((r.payload or {}).get("llm_calls") or 0)
        a["_regen"] += 1 if (r.payload or {}).get("regenerated") else 0

    out = []
    for a in arms.values():
        n = max(1, a["turns"])
        out.append(
            {
                "pattern": a["pattern"],
                "turns": a["turns"],
                "avg_cost_usd": round(a["_cost"] / n, 6),
                "avg_latency_ms": round(a["_lat"] / n, 1),
                "avg_llm_calls": round(a["_calls"] / n, 2),
                "regenerate_rate": round(a["_regen"] / n, 4),
            }
        )
    out.sort(key=lambda x: x["pattern"])
    return {"agent_id": agent_id, "arms": out}


@router.post("/runs/{run_id}/harness/run", status_code=status.HTTP_202_ACCEPTED)
async def run_harness_cycle(
    run_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    """Manually trigger the Phase 17 mine -> propose -> validate pipeline for
    this eval run (poll ``GET /api/agents/{agent_id}/config-versions`` for the
    outcome — the same fire-and-poll idiom as creating an eval run)."""
    run = await db.get(AgentEvalRun, run_id)
    if run is None:
        raise APIError(404, "not_found", "run not found")
    if run.status != "done":
        raise APIError(409, "run_not_done", "eval run has not finished yet")

    from ..harness import schedule as schedule_harness

    schedule_harness(run_id)
    return {"scheduled": True, "run_id": run_id}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(AgentEvalRun, run_id)
    if run is None:
        raise APIError(404, "not_found", "run not found")
    cases = (
        await db.exec(select(AgentEvalCase).where(AgentEvalCase.run_id == run_id))
    ).all()
    return {"run": agent_eval_run_out(run), "cases": [agent_eval_case_out(c) for c in cases]}


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(AgentEvalRun, run_id)
    if run is None:
        raise APIError(404, "not_found", "run not found")
    from sqlmodel import delete as sqldelete

    await db.exec(sqldelete(AgentEvalCase).where(AgentEvalCase.run_id == run_id))
    await db.exec(sqldelete(AgentEvalRun).where(AgentEvalRun.id == run_id))
    await db.commit()
