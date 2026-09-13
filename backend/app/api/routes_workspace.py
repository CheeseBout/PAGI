"""User-facing view of a session's sandbox workspace (Wave 4c).

Proxies the sandbox file service so the UI can browse / read / delete workspace
files directly (the same operations agents get as tools, but performed by the
signed-in owner). Writes still go through agents + HITL.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import ChatSession, User
from ..tools import sandbox_client
from .deps import APIError, get_current_user, get_db

router = APIRouter(prefix="/api/conversations", tags=["workspace"])

_MAX_FILE_BYTES = 200_000


async def _owned(db: AsyncSession, session_id: str, user: User) -> ChatSession:
    s = await db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise APIError(404, "not_found", "Conversation not found")
    return s


def _sandbox_error(exc: sandbox_client.SandboxError) -> APIError:
    code = getattr(exc, "status_code", None)
    if code == 400:
        return APIError(400, "invalid_path", "Bad path")
    return APIError(502, "sandbox_error", "Sandbox unavailable")


@router.get("/{session_id}/workspace")
async def list_workspace(
    session_id: str,
    path: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned(db, session_id, user)
    try:
        return await sandbox_client.call(
            "/files/list", {"session_id": session_id, "path": path}, timeout=15.0
        )
    except sandbox_client.SandboxError as exc:
        raise _sandbox_error(exc)


@router.get("/{session_id}/workspace/file")
async def read_workspace_file(
    session_id: str,
    path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned(db, session_id, user)
    try:
        res = await sandbox_client.call(
            "/files/read", {"session_id": session_id, "path": path}, timeout=15.0
        )
    except sandbox_client.SandboxError as exc:
        raise _sandbox_error(exc)
    content = res.get("content") or ""
    truncated = len(content) > _MAX_FILE_BYTES
    return {
        "path": path,
        "content": content[:_MAX_FILE_BYTES],
        "size_bytes": res.get("size_bytes"),
        "truncated": truncated,
    }


@router.delete("/{session_id}/workspace", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_file(
    session_id: str,
    path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned(db, session_id, user)
    try:
        await sandbox_client.call(
            "/files/delete", {"session_id": session_id, "path": path}, timeout=15.0
        )
    except sandbox_client.SandboxError as exc:
        raise _sandbox_error(exc)
