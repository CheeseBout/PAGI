"""2D avatar static file serving (SPEC §20.4, MVP tier).

No session auth — anyone with a guessable ``agent_id`` (UUID) can fetch avatar
assets, an accepted risk for a single-tenant deployment behind an internal
reverse proxy (same posture the main README already documents for the Docker
socket). What *is* non-negotiable at every tier: containment that resolves
symlinks before checking (not a bare ``".."`` string match — Starlette's
``StaticFiles`` alone doesn't do this), and ``X-Content-Type-Options: nosniff``
on every response so a served asset can never be MIME-sniffed into something
executable.
"""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..core.avatar import InvalidAvatarPath, resolve
from ..db.models import Agent
from .deps import APIError, get_db

router = APIRouter(prefix="/avatars", tags=["avatars"])


@router.get("/{agent_id}/{path:path}")
async def get_avatar_file(agent_id: str, path: str, db: AsyncSession = Depends(get_db)):
    exists = (await db.exec(select(Agent.id).where(Agent.id == agent_id))).first()
    if exists is None:
        raise APIError(404, "not_found", "Agent not found")

    try:
        target = resolve(agent_id, path)
    except InvalidAvatarPath as exc:
        raise APIError(400, "invalid_path", str(exc))

    if not target.is_file():
        raise APIError(404, "not_found", "Avatar asset not found")

    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        target,
        media_type=content_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )
