"""Shared 2D avatar model library (SPEC §20.5) — one upload, usable by every
agent's picker without copying the model into each agent's own directory
(`backend/app/api/routes_agents.py::upload_avatar_model` is the per-agent,
private-to-one-agent variant). Requires session auth like the rest of the
agent-config surface; the static asset itself is still served unauthenticated
via `routes_avatar_files.py` (an agent's own route resolves into the shared
library as a fallback — see `core/avatar.py::resolve`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from starlette.datastructures import UploadFile

from ..core.avatar import AvatarUploadError, list_shared_models, save_uploaded_model_shared
from .deps import APIError, get_current_user

router = APIRouter(prefix="/api/avatar-library", tags=["avatars"])


@router.get("")
async def list_avatar_library(_=Depends(get_current_user)):
    return {"models": list_shared_models()}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_to_avatar_library(request: Request, _=Depends(get_current_user)):
    form = await request.form()
    entries: list[tuple[str, bytes]] = []
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            entries.append((key, await value.read()))

    try:
        model_path = save_uploaded_model_shared(entries)
    except AvatarUploadError as exc:
        raise APIError(400, "invalid_upload", str(exc))

    return {"model_path": model_path, "models": list_shared_models()}
