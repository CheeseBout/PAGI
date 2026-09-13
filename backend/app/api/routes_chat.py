"""WebSocket chat transport (SPEC §3)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.agent_runtime import (
    NotFound,
    clear_turn_status,
    edit_message_and_regenerate,
    regenerate_last,
    resolve_approval,
    run_turn,
)
from ..core import attachments as att
from ..core import memory_store
from ..core.memory import derive_title
from ..core.security import chat_rate_limiter, decode_token
from ..core.ws_manager import manager
from ..db.models import ChatSession, Message
from ..db.session import SessionLocal

router = APIRouter(tags=["chat"])
log = structlog.get_logger("pagi.ws")


async def _authenticate(ws: WebSocket) -> str | None:
    token = ws.cookies.get("pagi_session")
    return decode_token(token) if token else None


@router.websocket("/ws/chat/{session_id}")
async def ws_chat(ws: WebSocket, session_id: str) -> None:
    user_id = await _authenticate(ws)
    if not user_id:
        await ws.close(code=4401)
        return

    async with SessionLocal() as db:
        chat = await db.get(ChatSession, session_id)
        if chat is None or chat.user_id != user_id:
            await ws.close(code=4404)
            return

    await manager.connect(session_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            mtype = data.get("type")

            if mtype == "user_message":
                content = (data.get("content") or "").strip()
                raw_ids = data.get("attachment_ids") or []
                attachment_ids = [str(x) for x in raw_ids][:10] if isinstance(raw_ids, list) else []
                resolved_atts = att.collect(session_id, attachment_ids)
                if not content and not resolved_atts:
                    continue
                if manager.is_running(session_id):
                    await ws.send_json(
                        {"type": "error", "code": "turn_in_progress", "message": "Wait for the current reply (or Stop) before sending another message"}
                    )
                    continue
                if not chat_rate_limiter.allow(user_id):
                    await ws.send_json(
                        {"type": "error", "code": "rate_limited", "message": "Too many messages — slow down a bit"}
                    )
                    continue
                async with SessionLocal() as db:
                    umsg = Message(
                        session_id=session_id, role="user", content=content,
                        attachments=resolved_atts or None,
                    )
                    db.add(umsg)
                    s = await db.get(ChatSession, session_id)
                    if s is not None and not s.title:
                        s.title = derive_title(
                            content or (resolved_atts[0]["filename"] if resolved_atts else "")
                        )
                    if s is not None:
                        s.updated_at = datetime.now(timezone.utc)
                        db.add(s)
                    await db.commit()
                    umsg_id = umsg.id
                if content and memory_store.enabled():
                    asyncio.create_task(
                        memory_store.index_message(session_id, user_id, umsg_id, "user", content)
                    )
                task = asyncio.create_task(run_turn(session_id, wait_for_approval=True))
                manager.set_task(session_id, task)

            elif mtype == "abort":
                manager.abort(session_id)
                await clear_turn_status(session_id)

            elif mtype == "regenerate":
                if manager.is_running(session_id):
                    continue
                if not chat_rate_limiter.allow(user_id):
                    await ws.send_json(
                        {"type": "error", "code": "rate_limited", "message": "Too many messages — slow down a bit"}
                    )
                    continue
                task = asyncio.create_task(regenerate_last(session_id))
                manager.set_task(session_id, task)

            elif mtype == "edit_message":
                message_id = data.get("message_id")
                new_content = (data.get("content") or "").strip()
                if not message_id or not new_content:
                    continue
                if manager.is_running(session_id):
                    continue
                if not chat_rate_limiter.allow(user_id):
                    await ws.send_json(
                        {"type": "error", "code": "rate_limited", "message": "Too many messages — slow down a bit"}
                    )
                    continue
                async def _edit_task(mid=message_id, content_=new_content) -> None:
                    try:
                        await edit_message_and_regenerate(session_id, mid, content_)
                    except NotFound:
                        await ws.send_json(
                            {"type": "error", "code": "not_found", "message": "Message not found"}
                        )

                task = asyncio.create_task(_edit_task())
                manager.set_task(session_id, task)

            elif mtype == "approval_decision":
                approval_id = data.get("approval_id")
                decision = data.get("decision", "deny")
                remember = data.get("remember")
                if not approval_id:
                    continue
                async with SessionLocal() as db:
                    try:
                        await resolve_approval(db, approval_id, decision, user_id, remember=remember)
                    except (KeyError, ValueError) as exc:
                        await ws.send_json(
                            {"type": "error", "code": "approval_error", "message": str(exc)}
                        )
            else:
                await ws.send_json(
                    {"type": "error", "code": "bad_message", "message": f"unknown type: {mtype}"}
                )
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - defensive
        log.exception("ws_loop_error", session_id=session_id)
    finally:
        manager.disconnect(session_id, ws)
