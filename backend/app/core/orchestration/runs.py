"""agent_runs helpers: retention purge + timeline read + the per-turn A/B marker
(Phase 14 / §16.4, §16.6)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import structlog
from sqlmodel import delete, select

from ...db.models import AgentRun, Message, Trace
from ...db.session import SessionLocal

log = structlog.get_logger("pagi.orch.runs")

# node used for the per-turn marker row (SPEC §16.6). step_no=-1 so it sorts
# first; PatternTrace filters it out of the node timeline.
TURN_NODE = "turn"


async def open_turn_marker(db, session_id: str, *, pattern: str, root_id: str | None) -> AgentRun:
    """Get-or-create the running 'turn' marker for this session. Re-used on an
    approval-resume so a turn is counted once, not twice."""
    existing = (
        await db.exec(
            select(AgentRun).where(
                AgentRun.session_id == session_id,
                AgentRun.node == TURN_NODE,
                AgentRun.status == "running",
            )
        )
    ).first()
    if existing is not None:
        return existing
    row = AgentRun(
        session_id=session_id, root_session_id=root_id or session_id,
        pattern=pattern, step_no=-1, node=TURN_NODE, status="running",
        payload={"started_monotonic": time.monotonic()},
    )
    db.add(row)
    try:
        await db.commit()
        await db.refresh(row)
    except Exception:  # pragma: no cover
        pass
    return row


async def close_turn_marker(session_id: str) -> None:
    """Finalize the running 'turn' marker: latency, cost, llm_calls, message_id.
    Best-effort — never fail a turn over analytics bookkeeping."""
    try:
        async with SessionLocal() as db:
            row = (
                await db.exec(
                    select(AgentRun).where(
                        AgentRun.session_id == session_id,
                        AgentRun.node == TURN_NODE,
                        AgentRun.status == "running",
                    )
                )
            ).first()
            if row is None:
                return
            started = (row.payload or {}).get("started_monotonic")
            traces = (
                await db.exec(
                    select(Trace).where(
                        Trace.session_id == session_id, Trace.created_at >= row.created_at
                    )
                )
            ).all()
            last_asst = (
                await db.exec(
                    select(Message)
                    .where(Message.session_id == session_id, Message.role == "assistant")
                    .order_by(Message.created_at.desc(), Message.id.desc())
                )
            ).first()
            row.status = "done"
            row.latency_ms = int((time.monotonic() - started) * 1000) if started else 0
            row.cost_usd = round(sum(t.cost_usd or 0.0 for t in traces), 6)
            row.message_id = last_asst.id if last_asst else None
            row.payload = {
                **(row.payload or {}),
                "llm_calls": len(traces),
                "regenerated": (row.payload or {}).get("regenerated", False),
            }
            db.add(row)
            await db.commit()
    except Exception:  # pragma: no cover
        log.warning("close_turn_marker_failed", session_id=session_id)


async def mark_turn_regenerated(db, session_id: str) -> None:
    """Flag the most recent finished 'turn' marker as regenerated/edited — a
    dissatisfaction signal for the A/B report (SPEC §16.6)."""
    row = (
        await db.exec(
            select(AgentRun)
            .where(AgentRun.session_id == session_id, AgentRun.node == TURN_NODE)
            .order_by(AgentRun.created_at.desc())
        )
    ).first()
    if row is None:
        return
    row.payload = {**(row.payload or {}), "regenerated": True}
    db.add(row)
    await db.commit()


async def purge_old_runs(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with SessionLocal() as db:
        rows = (
            await db.exec(select(AgentRun.id).where(AgentRun.created_at < cutoff))
        ).all()
        if not rows:
            return 0
        await db.exec(delete(AgentRun).where(AgentRun.created_at < cutoff))
        await db.commit()
    log.info("agent_runs_purged", count=len(rows))
    return len(rows)
