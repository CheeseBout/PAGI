"""Desktop overlay: the continuous chat session (SPEC §21.5).

``POST /api/overlay/session`` is idempotent get-or-create: one live session per
(user, agent) with ``origin='overlay'``. Everything else about that session —
messages, attachments, approvals, WebSocket — goes through the ordinary
conversation endpoints; it is just a ``ChatSession`` the web UI can also see.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import Agent, ChatSession, User
from ..schemas import OverlaySessionRequest
from .deps import APIError, get_current_user, get_db
from .serializers import session_out

router = APIRouter(prefix="/api/overlay", tags=["overlay"])

# Two simultaneous first calls (overlay start + a second instance) must not
# both create a session. The backend is a single process, so a lock is enough.
_create_lock = asyncio.Lock()


@router.post("/session")
async def get_or_create_overlay_session(
    body: OverlaySessionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    agent_id = body.agent_id if body else None
    if agent_id:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise APIError(404, "not_found", "Agent not found")
    else:
        agent = (await db.exec(select(Agent).where(Agent.is_default == True))).first()  # noqa: E712
        if agent is None:
            agent = (await db.exec(select(Agent).order_by(Agent.created_at))).first()
        if agent is None:
            raise APIError(422, "no_agent", "No agent exists yet — create one first")

    async with _create_lock:
        existing = (
            await db.exec(
                select(ChatSession)
                .where(
                    ChatSession.user_id == user.id,
                    ChatSession.agent_id == agent.id,
                    ChatSession.origin == "overlay",
                    ChatSession.archived == False,  # noqa: E712
                    ChatSession.kind == "chat",
                )
                .order_by(ChatSession.updated_at.desc())
            )
        ).first()
        if existing is not None:
            return {"session": session_out(existing), "created": False}

        s = ChatSession(user_id=user.id, agent_id=agent.id, origin="overlay")
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return {"session": session_out(s), "created": True}
