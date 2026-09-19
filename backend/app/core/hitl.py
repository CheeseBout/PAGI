"""Human-in-the-loop: approval rows + an in-memory waiter registry (SPEC §7).

Known limitation (SPEC §7.3): waiters live in process memory. If the backend
restarts while an approval is pending, the Future is lost. We recover by letting
`resolve_approval` spawn a fresh background turn (agent_runtime rebuilds all
state from the DB), rather than returning 410.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import ChatSession, Message, SessionToolGrant, ToolApproval
from .notify import clip, notify

_waiters: dict[str, asyncio.Future[str]] = {}

# per-session seconds spent blocked on a human approval — a sub-agent timeout
# watchdog subtracts this so "đi pha cà phê" doesn't kill the job (SPEC §15.5).
_approval_wait_seconds: dict[str, float] = {}


def note_approval_wait(session_id: str, seconds: float) -> None:
    _approval_wait_seconds[session_id] = _approval_wait_seconds.get(session_id, 0.0) + seconds


def approval_wait_seconds(session_id: str) -> float:
    return _approval_wait_seconds.get(session_id, 0.0)


def reset_approval_wait(session_id: str) -> None:
    _approval_wait_seconds.pop(session_id, None)


def create_waiter(approval_id: str) -> asyncio.Future[str]:
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    _waiters[approval_id] = fut
    return fut


def resolve_waiter(approval_id: str, decision: str) -> bool:
    fut = _waiters.pop(approval_id, None)
    if fut is not None and not fut.done():
        fut.set_result(decision)
        return True
    return False


def drop_waiter(approval_id: str) -> None:
    _waiters.pop(approval_id, None)


def has_waiter(approval_id: str) -> bool:
    return approval_id in _waiters


async def get_or_create_approval(
    db: AsyncSession, *, session_id: str, message_id: str, tool_call_id: str,
    tool_name: str, tool_args: dict,
) -> ToolApproval:
    existing = (
        await db.exec(select(ToolApproval).where(ToolApproval.tool_call_id == tool_call_id))
    ).first()
    if existing is not None:
        return existing
    approval = ToolApproval(
        session_id=session_id,
        message_id=message_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_args=tool_args,
        status="pending",
    )
    db.add(approval)
    await db.commit()
    await db.refresh(approval)
    # SPEC §21.7: only a *newly created* approval notifies (the early return above
    # is the resumed-turn path, which must not raise a second toast).
    await notify_approval(db, approval, "approval_pending")
    return approval


async def notify_approval(db: AsyncSession, approval: ToolApproval, event_type: str) -> None:
    """Fire-and-forget user notification (never raises — SPEC §21.7)."""
    try:
        chat = await db.get(ChatSession, approval.session_id)
        if chat is None:
            return
        if event_type == "approval_pending":
            event = {
                "type": "approval_pending",
                "approval_id": approval.id,
                "session_id": approval.session_id,
                "tool_name": approval.tool_name,
                "args_preview": clip(json.dumps(approval.tool_args or {}, ensure_ascii=False, default=str)),
            }
        else:
            event = {"type": "approval_resolved", "approval_id": approval.id, "status": approval.status}
        await notify(chat.user_id, event)
    except Exception:  # pragma: no cover - notifying must never break HITL
        pass


async def mark_resolved(
    db: AsyncSession, approval: ToolApproval, *, status: str, user_id: str | None
) -> ToolApproval:
    approval.status = status
    approval.resolved_at = datetime.now(timezone.utc)
    approval.resolved_by = user_id
    db.add(approval)
    await db.commit()
    await db.refresh(approval)
    await notify_approval(db, approval, "approval_resolved")
    return approval


async def pending_for_session(db: AsyncSession, session_id: str) -> list[ToolApproval]:
    return list(
        (
            await db.exec(
                select(ToolApproval).where(
                    ToolApproval.session_id == session_id, ToolApproval.status == "pending"
                )
            )
        ).all()
    )


# ── "always allow this tool for the rest of this chat" (Wave 4a) ────────
async def has_grant(db: AsyncSession, session_id: str, tool_name: str) -> bool:
    row = (
        await db.exec(
            select(SessionToolGrant).where(
                SessionToolGrant.session_id == session_id,
                SessionToolGrant.tool_name == tool_name,
            )
        )
    ).first()
    return row is not None


async def add_grant(
    db: AsyncSession, *, session_id: str, tool_name: str, user_id: str | None
) -> None:
    if await has_grant(db, session_id, tool_name):
        return
    db.add(SessionToolGrant(session_id=session_id, tool_name=tool_name, created_by=user_id))
    await db.commit()


async def list_grants(db: AsyncSession, session_id: str) -> list[SessionToolGrant]:
    return list(
        (
            await db.exec(
                select(SessionToolGrant)
                .where(SessionToolGrant.session_id == session_id)
                .order_by(SessionToolGrant.created_at)
            )
        ).all()
    )


async def revoke_grant(db: AsyncSession, session_id: str, tool_name: str) -> bool:
    row = (
        await db.exec(
            select(SessionToolGrant).where(
                SessionToolGrant.session_id == session_id,
                SessionToolGrant.tool_name == tool_name,
            )
        )
    ).first()
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True


async def has_tool_result(db: AsyncSession, session_id: str, tool_call_id: str) -> bool:
    row = (
        await db.exec(
            select(Message).where(
                Message.session_id == session_id,
                Message.role == "tool",
                Message.tool_call_id == tool_call_id,
            )
        )
    ).first()
    return row is not None
