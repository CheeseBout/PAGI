"""Manual rollback — apply a historical version's full config_snapshot.

No regression gate: rollback is an explicit user action on a version that
(for the active target) already cleared the gate once, or that the user
deliberately wants back regardless (PLAN §17c: "rollback về bản bất kỳ bằng
1 cú bấm"). The one invariant preserved is history: rows are never deleted,
only ``status``/``activated_at`` change.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..core.agent_config import apply_patch
from ..db.models import Agent, AgentConfigVersion


class RollbackError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


async def rollback(db: AsyncSession, agent_id: str, version_id: str) -> AgentConfigVersion:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise RollbackError("not_found", "agent not found")
    target = await db.get(AgentConfigVersion, version_id)
    if target is None or target.agent_id != agent_id:
        raise RollbackError("not_found", "config version not found for this agent")

    prev_active = (
        await db.exec(
            select(AgentConfigVersion).where(
                AgentConfigVersion.agent_id == agent_id,
                AgentConfigVersion.status == "active",
            )
        )
    ).first()
    if prev_active is not None and prev_active.id != target.id:
        prev_active.status = "superseded"
        db.add(prev_active)

    apply_patch(agent, dict(target.config_snapshot or {}))
    db.add(agent)

    target.status = "active"
    target.activated_at = datetime.now(timezone.utc)
    db.add(target)

    await db.commit()
    await db.refresh(target)
    return target
