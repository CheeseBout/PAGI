"""Cron job CRUD + run-now (SPEC §2.7, §10)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import Agent, CronJob, User
from ..schemas import CronJobCreate, CronJobPatch
from ..scheduler.cron_jobs import reschedule_all, trigger_now
from .deps import APIError, get_current_user, get_db
from .serializers import cron_out

router = APIRouter(prefix="/api/cron-jobs", tags=["cron"])


def _valid_cron(expr: str) -> bool:
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(expr)
        return True
    except Exception:
        return False


@router.get("")
async def list_jobs(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [cron_out(c) for c in (await db.exec(select(CronJob))).all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(
    body: CronJobCreate, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    if not _valid_cron(body.schedule):
        raise APIError(422, "invalid_schedule", "schedule is not a valid 5-field cron expression")
    if await db.get(Agent, body.agent_id) is None:
        raise APIError(422, "invalid_agent", "Agent does not exist")
    job = CronJob(**body.model_dump())
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await reschedule_all()
    return cron_out(job)


@router.patch("/{job_id}")
async def patch_job(
    job_id: str,
    body: CronJobPatch,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    job = await db.get(CronJob, job_id)
    if job is None:
        raise APIError(404, "not_found", "Cron job not found")
    data = body.model_dump(exclude_unset=True)
    if "schedule" in data and not _valid_cron(data["schedule"]):
        raise APIError(422, "invalid_schedule", "schedule is not a valid 5-field cron expression")
    for key, value in data.items():
        setattr(job, key, value)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await reschedule_all()
    return cron_out(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    job = await db.get(CronJob, job_id)
    if job is None:
        raise APIError(404, "not_found", "Cron job not found")
    await db.delete(job)
    await db.commit()
    await reschedule_all()


@router.post("/{job_id}/run-now", status_code=status.HTTP_202_ACCEPTED)
async def run_now(
    job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    job = await db.get(CronJob, job_id)
    if job is None:
        raise APIError(404, "not_found", "Cron job not found")
    session_id = await trigger_now(job_id, user.id)
    return {"session_id": session_id}
