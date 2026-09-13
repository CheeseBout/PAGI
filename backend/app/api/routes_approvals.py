"""HITL approval routes (SPEC §2.5, §7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..core.agent_runtime import resolve_approval
from ..db.models import Agent, ChatSession, ToolApproval, User
from .deps import APIError, get_current_user, get_db
from .serializers import approval_out

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


async def _approval_with_subagent(db: AsyncSession, a: ToolApproval) -> dict:
    """approval_out, tagged with the sub-agent name when the approval's session
    is a sub-agent (Phase 13, SPEC §15.4) so ApprovalCard can label it."""
    s = await db.get(ChatSession, a.session_id)
    if s is not None and getattr(s, "kind", "chat") == "subagent":
        agent = await db.get(Agent, s.agent_id)
        return approval_out(
            a, sub_session_id=s.id, agent_name=agent.name if agent else None
        )
    return approval_out(a)


@router.get("")
async def list_approvals(
    status: str = Query(default="pending"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(ToolApproval)
        .join(ChatSession, ChatSession.id == ToolApproval.session_id)
        .where(ChatSession.user_id == user.id)
        .order_by(ToolApproval.created_at.desc())
    )
    if status != "all":
        stmt = stmt.where(ToolApproval.status == status)
    return [await _approval_with_subagent(db, a) for a in (await db.exec(stmt)).all()]


async def _decide(
    approval_id: str, decision: str, db: AsyncSession, user: User, remember: str | None = None
) -> dict:
    try:
        approval = await resolve_approval(db, approval_id, decision, user.id, remember=remember)
    except KeyError:
        raise APIError(404, "not_found", "Approval not found")
    except ValueError:
        raise APIError(409, "already_resolved", "Approval already resolved")
    return approval_out(approval)


@router.post("/{approval_id}/approve")
async def approve(
    approval_id: str,
    remember: str | None = Query(default=None, description="'session' = allow this tool for the rest of the chat"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _decide(approval_id, "approve", db, user, remember=remember)


@router.post("/{approval_id}/deny")
async def deny(
    approval_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return await _decide(approval_id, "deny", db, user)
