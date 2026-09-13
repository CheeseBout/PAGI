"""Chat file upload / retrieval (SPEC §2.4).

`target=attachment` (default) stores the file on the backend for inlining into
the next message; `target=workspace` additionally pushes it into the session's
sandbox workspace so tools (`read_file`, `execute_code`, …) can use it.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import Response
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_settings
from ..core import attachments as att
from ..db.models import ChatSession, User
from ..tools import sandbox_client
from .deps import APIError, get_current_user, get_db

router = APIRouter(prefix="/api/conversations", tags=["files"])


async def _owned(db: AsyncSession, session_id: str, user: User) -> ChatSession:
    s = await db.get(ChatSession, session_id)
    if s is None or s.user_id != user.id:
        raise APIError(404, "not_found", "Conversation not found")
    return s


@router.post("/{session_id}/files", status_code=status.HTTP_201_CREATED)
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    target: str = Form(default="attachment"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned(db, session_id, user)

    if not att.is_allowed(file.content_type or ""):
        raise APIError(400, "unsupported_type", f"File type not allowed: {file.content_type}")

    data = await file.read()
    if not data:
        raise APIError(400, "empty_file", "Uploaded file is empty")
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise APIError(400, "too_large", f"File exceeds the {get_settings().max_upload_mb} MB limit")

    meta = att.save(
        session_id,
        filename=file.filename or "upload",
        content_type=file.content_type or "",
        data=data,
    )

    if target == "workspace":
        try:
            await sandbox_client.call(
                "/files/upload",
                {
                    "session_id": session_id,
                    "path": meta["filename"],
                    "content_b64": base64.b64encode(data).decode("ascii"),
                },
            )
        except sandbox_client.SandboxError as exc:
            raise APIError(502, "sandbox_error", str(exc))
        meta["workspace_path"] = meta["filename"]

    return meta


@router.get("/{session_id}/files/{attachment_id}")
async def get_file(
    session_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned(db, session_id, user)
    meta = att.get_meta(session_id, attachment_id)
    if meta is None:
        raise APIError(404, "not_found", "Attachment not found")
    raw = att.load_bytes(session_id, meta)
    if raw is None:
        raise APIError(404, "not_found", "Attachment file is missing")
    return Response(
        content=raw,
        media_type=meta.get("content_type", "application/octet-stream"),
        headers={
            "Content-Disposition": f'inline; filename="{meta.get("filename", "file")}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/{session_id}/files/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    session_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned(db, session_id, user)
    att.delete_one(session_id, attachment_id)
