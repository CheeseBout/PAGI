"""Global notification stream (SPEC §21.7): ``WS /ws/notifications``.

Server -> client only. Auth is the same session cookie as the chat socket
(``4401`` when missing/invalid). Sends ``hello`` on connect, then whatever the
hub emits for this user, plus a ``ping`` every ``PING_INTERVAL_S``.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func
from sqlmodel import select

from ..core.notify import hub
from ..core.security import decode_token
from ..db.models import ChatSession, ToolApproval
from ..db.session import SessionLocal

router = APIRouter(tags=["notifications"])

PING_INTERVAL_S = 30


async def pending_approval_count(user_id: str) -> int:
    async with SessionLocal() as db:
        n = (
            await db.exec(
                select(func.count())
                .select_from(ToolApproval)
                .join(ChatSession, ChatSession.id == ToolApproval.session_id)
                .where(ChatSession.user_id == user_id, ToolApproval.status == "pending")
            )
        ).one()
    return int(n or 0)


async def _ping_loop(ws: WebSocket) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_S)
        await ws.send_json({"type": "ping"})


@router.websocket("/ws/notifications")
async def ws_notifications(ws: WebSocket) -> None:
    token = ws.cookies.get("pagi_session")
    user_id = decode_token(token) if token else None
    if not user_id:
        await ws.close(code=4401)
        return

    await hub.connect(user_id, ws)
    pinger: asyncio.Task | None = None
    try:
        await ws.send_json({"type": "hello", "pending_approvals": await pending_approval_count(user_id)})
        pinger = asyncio.create_task(_ping_loop(ws))
        # one-way channel: just keep reading so a client close is noticed
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # send on a half-closed socket, etc.
        pass
    finally:
        if pinger is not None:
            pinger.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pinger
        hub.disconnect(user_id, ws)
