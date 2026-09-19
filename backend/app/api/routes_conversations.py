"""Conversation (session) CRUD + non-streaming chat fallback (SPEC §2.3, §2.4)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlmodel import delete as sqldelete
from sqlmodel import select
from sqlmodel import update as sqlupdate
from sqlmodel.ext.asyncio.session import AsyncSession

from ..core import attachments as att
from ..core import hitl
from ..core.agent_runtime import PendingApproval, run_turn
from ..core.memory import derive_title
from ..core.security import chat_rate_limiter
from ..db.models import (
    Agent,
    AgentRun,
    ChatSession,
    KbQueryLog,
    MemoryChunk,
    Message,
    SessionToolGrant,
    ToolApproval,
    Trace,
    User,
)
from ..schemas import ConversationCreate, ConversationPatch, MessageCreate
from .deps import APIError, get_current_user, get_db
from .serializers import agent_run_out, approval_out, grant_out, message_out, session_out

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


async def _owned(db: AsyncSession, session_id: str, user: User) -> ChatSession:
    s = await db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise APIError(404, "not_found", "Conversation not found")
    return s


def _parse_cursor_ts(raw: str) -> datetime:
    """Accept the 'Z'-suffixed ISO timestamps this API emits; compare as naive
    UTC since that's how SQLite gives datetimes back (see serializers._z)."""
    value = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@router.get("")
async def list_conversations(
    archived: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    before: str | None = Query(default=None, description="ISO updated_at cursor — page older"),
    origin: str | None = Query(default=None, description="web | overlay (SPEC §21.5)"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(ChatSession).where(
        ChatSession.user_id == user.id,
        ChatSession.archived == archived,
        ChatSession.kind == "chat",  # sub-agent sessions never show in the sidebar
    )
    if origin is not None:
        if origin not in ("web", "overlay"):
            raise APIError(400, "bad_request", "`origin` must be 'web' or 'overlay'")
        stmt = stmt.where(ChatSession.origin == origin)
    if before:
        try:
            stmt = stmt.where(ChatSession.updated_at < _parse_cursor_ts(before))
        except ValueError:
            raise APIError(400, "bad_request", "`before` must be an ISO-8601 timestamp")
    rows = (await db.exec(stmt.order_by(ChatSession.updated_at.desc()).limit(limit))).all()
    return [
        {
            "id": s.id,
            "title": s.title,
            "agent_id": s.agent_id,
            "origin": s.origin,
            "updated_at": session_out(s)["updated_at"],
        }
        for s in rows
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    agent = await db.get(Agent, body.agent_id)
    if agent is None:
        raise APIError(422, "invalid_agent", "Agent does not exist")
    s = ChatSession(user_id=user.id, agent_id=body.agent_id)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return session_out(s)


@router.get("/{session_id}")
async def get_conversation(
    session_id: str,
    limit: int = Query(default=50, le=200),
    before: str | None = Query(default=None, description="message id cursor — page older messages"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = await _owned(db, session_id, user)

    if before:
        anchor = await db.get(Message, before)
        if anchor is None or anchor.session_id != session_id:
            raise APIError(400, "bad_request", "`before` is not a message in this conversation")
        older = (
            await db.exec(
                select(Message)
                .where(
                    Message.session_id == session_id,
                    (Message.created_at < anchor.created_at)
                    | ((Message.created_at == anchor.created_at) & (Message.id < anchor.id)),
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            )
        ).all()
        rows = list(reversed(older))
    else:
        rows = (
            await db.exec(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            )
        ).all()
        rows.reverse()

    from ..rag.service import rag_for_messages

    assistant_ids = [m.id for m in rows if m.role == "assistant"]
    rag_map = await rag_for_messages(assistant_ids)

    trace_map: dict[str, Trace] = {}
    if assistant_ids:
        traces = (
            await db.exec(
                select(Trace).where(Trace.message_id.in_(assistant_ids), Trace.kind == "chat")
            )
        ).all()
        trace_map = {t.message_id: t for t in traces if t.message_id}

    return {
        "session": session_out(s),
        "messages": [
            message_out(
                m,
                rag=rag_map.get(m.id),
                cost_usd=trace_map[m.id].cost_usd if m.id in trace_map else None,
                latency_ms=trace_map[m.id].latency_ms if m.id in trace_map else None,
            )
            for m in rows
        ],
    }


@router.get("/{session_id}/tree")
async def conversation_tree(
    session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Sub-agent tree for this conversation (Phase 13, SPEC §2.3)."""
    s = await _owned(db, session_id, user)
    from ..core.delegation import tree_nodes

    root_id = s.root_session_id or s.id
    return {"root": root_id, "nodes": await tree_nodes(db, root_id)}


@router.get("/{session_id}/runs")
async def conversation_runs(
    session_id: str,
    message_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pattern node timeline (Phase 14, SPEC §2.3) — gathered by root so it
    includes sub-agent nodes."""
    s = await _owned(db, session_id, user)
    root_id = s.root_session_id or s.id
    stmt = select(AgentRun).where(
        AgentRun.root_session_id == root_id,
        AgentRun.node != "turn",  # the A/B marker isn't part of the node timeline
    )
    if message_id:
        stmt = stmt.where(AgentRun.message_id == message_id)
    rows = (await db.exec(stmt.order_by(AgentRun.step_no, AgentRun.created_at))).all()
    return [agent_run_out(r) for r in rows]


@router.patch("/{session_id}")
async def patch_conversation(
    session_id: str,
    body: ConversationPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = await _owned(db, session_id, user)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(s, key, value)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return session_out(s)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    """Hard delete: the conversation and every row that references it.

    Uses bulk ``DELETE ... WHERE`` statements (not per-row ``session.delete()``)
    so it stays O(1) round-trips per table and doesn't hit the async-ORM
    cascade-loop issue. FK-safe order: children first, session last; within the
    children, tables that also FK ``messages`` (traces, tool_approvals) go before
    ``messages``.
    """
    s = await _owned(db, session_id, user)

    # Phase 13: a conversation is a tree — delete every sub-agent session under it
    # too, scoped by root_session_id.
    root_id = s.root_session_id or s.id
    child_ids = list(
        (
            await db.exec(
                select(ChatSession.id).where(ChatSession.root_session_id == root_id)
            )
        ).all()
    )
    all_ids = [root_id, *child_ids]

    for model in (
        Trace, ToolApproval, KbQueryLog, SessionToolGrant, MemoryChunk, AgentRun, Message,
    ):
        await db.exec(sqldelete(model).where(model.session_id.in_(all_ids)))
    # drop the self-referential FKs before deleting the session rows so SQLite's
    # per-row FK check can't trip on delete order within the tree
    await db.exec(
        sqlupdate(ChatSession)
        .where(ChatSession.id.in_(all_ids))
        .values(parent_session_id=None, root_session_id=None)
    )
    await db.exec(sqldelete(ChatSession).where(ChatSession.id.in_(all_ids)))
    await db.commit()

    # best-effort: drop backend attachment store + sandbox workspace for each session
    for sid in all_ids:
        att.delete_for_session(sid)
        try:
            from ..tools import sandbox_client

            await sandbox_client.call("/files/delete", {"session_id": sid, "path": "."})
        except Exception:  # pragma: no cover - sandbox optional / unreachable
            pass


@router.get("/{session_id}/grants")
async def list_session_grants(
    session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    await _owned(db, session_id, user)
    return [grant_out(g) for g in await hitl.list_grants(db, session_id)]


@router.delete("/{session_id}/grants/{tool_name}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session_grant(
    session_id: str,
    tool_name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned(db, session_id, user)
    if not await hitl.revoke_grant(db, session_id, tool_name):
        raise APIError(404, "not_found", "No such grant")


@router.post("/{session_id}/messages")
async def post_message(
    session_id: str,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = await _owned(db, session_id, user)
    if not chat_rate_limiter.allow(user.id):
        raise APIError(429, "rate_limited", "Too many messages — slow down a bit")

    content = body.content or ""
    resolved = att.collect(session_id, body.attachment_ids)
    if not content.strip() and not resolved:
        raise APIError(400, "empty_message", "Message needs text or an attachment")

    db.add(
        Message(
            session_id=session_id, role="user", content=content,
            attachments=resolved or None,
        )
    )
    if not s.title:
        s.title = derive_title(content or (resolved[0]["filename"] if resolved else ""))
    s.updated_at = datetime.now(timezone.utc)
    db.add(s)
    await db.commit()

    try:
        await run_turn(session_id, wait_for_approval=False)
    except PendingApproval as pa:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"pending_approval": approval_out(pa.approval)},
        )

    last_assistant = (
        await db.exec(
            select(Message)
            .where(Message.session_id == session_id, Message.role == "assistant")
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
    ).first()
    return {"message": message_out(last_assistant) if last_assistant else None}
