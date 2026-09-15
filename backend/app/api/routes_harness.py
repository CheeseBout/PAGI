"""Self-improving harness API (Phase 17, PLAN §17): history + rollback.

Triggering a cycle lives in ``routes_agent_eval.py`` (``POST
/api/agent-eval/runs/{run_id}/harness/run``) since it's keyed off an eval run,
not an agent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import Agent, AgentConfigVersion, User, WeaknessReport
from ..harness import RollbackError
from ..harness import rollback as do_rollback
from .deps import APIError, get_current_user, get_db
from .serializers import agent_config_version_out, weakness_report_out

router = APIRouter(prefix="/api/agents", tags=["harness"])


@router.get("/{agent_id}/config-versions")
async def list_config_versions(
    agent_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise APIError(404, "not_found", "agent not found")
    rows = (
        await db.exec(
            select(AgentConfigVersion)
            .where(AgentConfigVersion.agent_id == agent_id)
            .order_by(AgentConfigVersion.created_at.desc())
        )
    ).all()
    return [agent_config_version_out(v) for v in rows]


@router.get("/{agent_id}/weakness-reports")
async def list_weakness_reports(
    agent_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise APIError(404, "not_found", "agent not found")
    rows = (
        await db.exec(
            select(WeaknessReport)
            .where(WeaknessReport.agent_id == agent_id)
            .order_by(WeaknessReport.created_at.desc())
        )
    ).all()
    return [weakness_report_out(w) for w in rows]


@router.post("/{agent_id}/config-versions/{version_id}/rollback")
async def rollback_config_version(
    agent_id: str,
    version_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        version = await do_rollback(db, agent_id, version_id)
    except RollbackError as exc:
        raise APIError(404, exc.code, exc.message)
    return agent_config_version_out(version)
