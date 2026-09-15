"""Multi-day project development loop API (Phase 18, PLAN §18)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlmodel import delete as sqldelete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..core.orchestration import project_dev
from ..core.orchestration.project_dev import ProjectValidationError
from ..db.models import CronJob, ProjectIteration, ProjectRun, User
from ..scheduler.cron_jobs import reschedule_all
from ..schemas import ProjectRunCreate, ProjectRunPatch
from ..tools.sandbox_client import SandboxError
from .deps import APIError, get_current_user, get_db
from .serializers import project_iteration_out, project_run_out

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _valid_cron(expr: str) -> bool:
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(expr)
        return True
    except Exception:
        return False


async def _cron_job_for(db: AsyncSession, project_run_id: str) -> CronJob | None:
    return (
        await db.exec(select(CronJob).where(CronJob.project_run_id == project_run_id))
    ).first()


@router.get("")
async def list_projects(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    rows = (await db.exec(select(ProjectRun).order_by(ProjectRun.created_at.desc()))).all()
    return [project_run_out(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectRunCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.schedule and not _valid_cron(body.schedule):
        raise APIError(422, "invalid_schedule", "schedule is not a valid 5-field cron expression")
    try:
        run = await project_dev.create_project_run(
            db,
            name=body.name,
            planner_agent_id=body.planner_agent_id,
            developer_agent_id=body.developer_agent_id,
            qa_agent_id=body.qa_agent_id,
            user_id=user.id,
            max_iterations=body.max_iterations,
            budget_usd=body.budget_usd,
            schedule=body.schedule,
        )
    except ProjectValidationError as exc:
        raise APIError(422, exc.code, exc.message)
    except SandboxError as exc:
        raise APIError(502, "sandbox_unreachable", f"could not initialise the workspace: {exc}")

    if body.schedule:
        job = CronJob(
            agent_id=body.developer_agent_id,
            name=f"[project] {run.name}",
            schedule=body.schedule,
            prompt="",
            kind="project_iteration",
            project_run_id=run.id,
        )
        db.add(job)
        await db.commit()
        await reschedule_all()

    return project_run_out(run)


@router.get("/{project_id}")
async def get_project(
    project_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(ProjectRun, project_id)
    if run is None:
        raise APIError(404, "not_found", "project not found")
    iterations = (
        await db.exec(
            select(ProjectIteration)
            .where(ProjectIteration.project_run_id == project_id)
            .order_by(ProjectIteration.iteration_no)
        )
    ).all()
    return {
        "run": project_run_out(run),
        "iterations": [project_iteration_out(it) for it in iterations],
    }


@router.patch("/{project_id}")
async def patch_project(
    project_id: str,
    body: ProjectRunPatch,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    run = await db.get(ProjectRun, project_id)
    if run is None:
        raise APIError(404, "not_found", "project not found")
    data = body.model_dump(exclude_unset=True)
    if "schedule" in data and data["schedule"] and not _valid_cron(data["schedule"]):
        raise APIError(422, "invalid_schedule", "schedule is not a valid 5-field cron expression")

    schedule_changed = "schedule" in data
    for key, value in data.items():
        setattr(run, key, value)
    run.updated_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    if schedule_changed:
        job = await _cron_job_for(db, project_id)
        if run.schedule:
            if job is None:
                job = CronJob(
                    agent_id=run.developer_agent_id, name=f"[project] {run.name}",
                    schedule=run.schedule, prompt="", kind="project_iteration",
                    project_run_id=run.id,
                )
            else:
                job.schedule = run.schedule
            db.add(job)
            await db.commit()
        elif job is not None:
            await db.delete(job)
            await db.commit()
        await reschedule_all()

    return project_run_out(run)


@router.post("/{project_id}/pause")
async def pause_project(
    project_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(ProjectRun, project_id)
    if run is None:
        raise APIError(404, "not_found", "project not found")
    if run.status != "active":
        raise APIError(409, "not_active", f"project is {run.status}, not active")
    run.status = "paused"
    run.updated_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return project_run_out(run)


@router.post("/{project_id}/resume")
async def resume_project(
    project_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(ProjectRun, project_id)
    if run is None:
        raise APIError(404, "not_found", "project not found")
    if run.status != "paused":
        raise APIError(409, "not_paused", f"project is {run.status}, not paused")
    run.status = "active"
    run.updated_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return project_run_out(run)


@router.post("/{project_id}/iterate-now", status_code=status.HTTP_202_ACCEPTED)
async def iterate_now(
    project_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(ProjectRun, project_id)
    if run is None:
        raise APIError(404, "not_found", "project not found")
    asyncio.create_task(project_dev.run_iteration(project_id))
    return {"scheduled": True, "project_id": project_id}


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    run = await db.get(ProjectRun, project_id)
    if run is None:
        raise APIError(404, "not_found", "project not found")
    job = await _cron_job_for(db, project_id)
    if job is not None:
        await db.delete(job)
    await db.exec(sqldelete(ProjectIteration).where(ProjectIteration.project_run_id == project_id))
    await db.delete(run)
    await db.commit()
    if job is not None:
        await reschedule_all()
