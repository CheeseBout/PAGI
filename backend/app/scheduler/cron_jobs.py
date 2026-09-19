"""APScheduler wiring for cron jobs (PLAN §8, SPEC §10).

Unattended runs strip every `ask` tool from the agent unless the job lists it in
`unattended_allowed_tools` — nobody is around at 3am to approve.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import select

from ..config import get_settings
from ..core.agent_runtime import run_turn
from ..core.hitl import notify_approval
from ..core.notify import clip, notify
from ..core.ws_manager import manager
from ..db.models import ChatSession, CronJob, Message, ToolApproval, User
from ..db.session import SessionLocal

log = structlog.get_logger("pagi.cron")

_scheduler: AsyncIOScheduler | None = None
_EXPIRY_JOB_ID = "_pagi_approval_expiry_sweep"


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def start() -> None:
    sched = get_scheduler()
    if not sched.running:
        sched.start()
    sched.add_job(
        sweep_expired_approvals,
        trigger=IntervalTrigger(minutes=15),
        id=_EXPIRY_JOB_ID,
        replace_existing=True,
    )
    await reschedule_all()


async def shutdown() -> None:
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)


async def reschedule_all() -> None:
    sched = get_scheduler()
    if not sched.running:
        return
    for job in list(sched.get_jobs()):
        if job.id != _EXPIRY_JOB_ID:  # leave the approval-expiry sweep alone
            job.remove()
    async with SessionLocal() as db:
        rows = (await db.exec(select(CronJob).where(CronJob.enabled == True))).all()  # noqa: E712
        for job in rows:
            try:
                trigger = CronTrigger.from_crontab(job.schedule, timezone="UTC")
            except ValueError:
                log.warning("bad_cron_expr", job_id=job.id, schedule=job.schedule)
                continue
            sched.add_job(run_cron_job, trigger=trigger, args=[job.id], id=job.id, replace_existing=True)
            nxt = sched.get_job(job.id).next_run_time
            job.next_run_at = nxt.astimezone(timezone.utc).replace(tzinfo=timezone.utc) if nxt else None
            db.add(job)
        await db.commit()


async def sweep_expired_approvals() -> int:
    """SPEC §7.4: pending approvals older than APPROVAL_TIMEOUT_HOURS -> expired."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=get_settings().approval_timeout_hours)
    async with SessionLocal() as db:
        rows = (
            await db.exec(
                select(ToolApproval).where(
                    ToolApproval.status == "pending", ToolApproval.created_at < cutoff
                )
            )
        ).all()
        if not rows:
            return 0
        for a in rows:
            a.status = "expired"
            a.resolved_at = datetime.now(timezone.utc)
            db.add(a)
        await db.commit()
        for a in rows:
            await manager.broadcast(
                a.session_id, {"type": "approval_resolved", "approval_id": a.id, "status": "expired"}
            )
            await notify_approval(db, a, "approval_resolved")
    log.info("expired_approvals_swept", count=len(rows))
    return len(rows)


async def _seed_session(db, job: CronJob, user_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    s = ChatSession(user_id=user_id, agent_id=job.agent_id, title=f"[cron] {job.name} — {stamp}")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    db.add(Message(session_id=s.id, role="user", content=job.prompt))
    job.last_run_at = datetime.now(timezone.utc)
    db.add(job)
    await db.commit()
    return s.id


async def run_cron_job(job_id: str) -> None:
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        if job is None or not job.enabled:
            return
        if job.kind == "project_iteration":
            project_run_id = job.project_run_id
        else:
            project_run_id = None
            owner = (await db.exec(select(User).order_by(User.created_at))).first()
            if owner is None:
                log.warning("cron_no_user", job_id=job_id)
                return
            session_id = await _seed_session(db, job, owner.id)
            allowed = list(job.unattended_allowed_tools or [])
            user_id, job_name = owner.id, job.name

    if project_run_id is not None:
        log.info("cron_run_project_iteration", job_id=job_id, project_run_id=project_run_id)
        from ..core.orchestration import project_dev

        await project_dev.run_iteration(project_run_id)
        return

    log.info("cron_run", job_id=job_id, session_id=session_id)
    await _run_cron_turn(job_id, job_name, session_id, user_id, allowed)


async def _run_cron_turn(
    job_id: str, job_name: str, session_id: str, user_id: str, allowed: list[str]
) -> None:
    """Run the seeded cron session, then tell the user how it went (SPEC §21.7).

    ``status`` is "error" when the turn raised or produced no assistant reply —
    turn errors are only streamed over the (unwatched) chat socket, not stored,
    so an empty result is the best signal available here.
    """
    status = "ok"
    try:
        await run_turn(
            session_id,
            wait_for_approval=False,
            mode="unattended",
            unattended_allowed_tools=allowed,
        )
    except Exception:
        log.warning("cron_turn_failed", job_id=job_id, session_id=session_id, exc_info=True)
        status = "error"

    summary = ""
    try:
        async with SessionLocal() as db:
            last = (
                await db.exec(
                    select(Message)
                    .where(Message.session_id == session_id, Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                )
            ).first()
            summary = (last.content or "") if last else ""
        if status == "ok" and not summary:
            status = "error"
    except Exception:  # pragma: no cover - summary is best-effort
        pass

    await notify(
        user_id,
        {
            "type": "cron_run_finished",
            "job_id": job_id,
            "job_name": job_name,
            "session_id": session_id,
            "status": status,
            "summary": clip(summary),
        },
    )


async def trigger_now(job_id: str, user_id: str) -> str | None:
    async with SessionLocal() as db:
        job = await db.get(CronJob, job_id)
        if job is None:
            raise KeyError(job_id)
        if job.kind == "project_iteration":
            project_run_id = job.project_run_id
        else:
            project_run_id = None
            session_id = await _seed_session(db, job, user_id)
            allowed = list(job.unattended_allowed_tools or [])
            job_name = job.name

    import asyncio

    if project_run_id is not None:
        from ..core.orchestration import project_dev

        asyncio.create_task(project_dev.run_iteration(project_run_id))
        return None

    asyncio.create_task(_run_cron_turn(job_id, job_name, session_id, user_id, allowed))
    return session_id
