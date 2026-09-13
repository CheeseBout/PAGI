"""Agent CRUD (SPEC §2.2)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from pydantic import ValidationError

from ..core.orchestration.config import DELEGATION_PATTERNS, OrchestrationConfig
from ..db.models import Agent, ChatSession, User
from ..rag.config import RagConfig
from ..schemas import AgentCreate, AgentUpdate
from .deps import APIError, get_current_user, get_db
from .serializers import agent_out

router = APIRouter(prefix="/api/agents", tags=["agents"])

_PROVIDERS = {"openai", "anthropic", "gemini", "openrouter"}


def _validate_configs(data: dict) -> None:
    """422 on a bad rag_config / orchestration — a typo must not silently no-op."""
    if data.get("rag_config") is not None:
        try:
            RagConfig.model_validate(data["rag_config"] or {})
        except ValidationError as exc:
            raise APIError(422, "invalid_rag_config", f"invalid rag_config: {exc.errors()}")
    if data.get("orchestration") is not None:
        try:
            OrchestrationConfig.model_validate(data["orchestration"] or {})
        except ValidationError as exc:
            raise APIError(422, "invalid_orchestration", f"invalid orchestration: {exc.errors()}")
        orch = data["orchestration"] or {}
        for pattern in {orch.get("pattern"), orch.get("ab_pattern")}:
            if pattern in DELEGATION_PATTERNS and pattern != "router":
                key = "debater_agent_ids" if pattern == "debate" else "worker_agent_ids"
                if not orch.get(key):
                    raise APIError(
                        422, "missing_workers",
                        f"pattern '{pattern}' needs a non-empty {key}",
                    )


async def _unset_other_defaults(db: AsyncSession, keep_id: str | None) -> None:
    rows = (await db.exec(select(Agent).where(Agent.is_default == True))).all()  # noqa: E712
    for a in rows:
        if a.id != keep_id:
            a.is_default = False
            db.add(a)


@router.get("")
async def list_agents(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    rows = (await db.exec(select(Agent).order_by(Agent.created_at))).all()
    return [agent_out(a) for a in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    if body.provider not in _PROVIDERS:
        raise APIError(422, "invalid_provider", f"provider must be one of {sorted(_PROVIDERS)}")
    _validate_configs(body.model_dump())
    agent = Agent(**body.model_dump())
    if agent.is_default:
        await _unset_other_defaults(db, None)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent_out(agent)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise APIError(404, "not_found", "Agent not found")
    return agent_out(agent)


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise APIError(404, "not_found", "Agent not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("provider") and data["provider"] not in _PROVIDERS:
        raise APIError(422, "invalid_provider", f"provider must be one of {sorted(_PROVIDERS)}")
    _validate_configs(data)
    for key, value in data.items():
        setattr(agent, key, value)
    agent.updated_at = datetime.now(timezone.utc)
    if data.get("is_default"):
        await _unset_other_defaults(db, agent.id)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent_out(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise APIError(404, "not_found", "Agent not found")
    in_use = (await db.exec(select(ChatSession).where(ChatSession.agent_id == agent_id))).first()
    if in_use is not None:
        raise APIError(409, "agent_in_use", "Agent is referenced by conversations; archive instead")
    await db.delete(agent)
    await db.commit()
