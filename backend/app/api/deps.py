"""Shared FastAPI dependencies + the standard error envelope (SPEC §12.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Cookie, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from ..core.security import SESSION_COOKIE, decode_token
from ..db.models import User
from ..db.session import SessionLocal


def err(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


class APIError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=err(code, message))


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    pagi_session: str | None = Cookie(default=None),
) -> User:
    if not pagi_session:
        raise APIError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Not authenticated")
    user_id = decode_token(pagi_session)
    if not user_id:
        raise APIError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Session expired or invalid")
    user = await db.get(User, user_id)
    if user is None:
        raise APIError(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Unknown user")
    return user


assert SESSION_COOKIE == "pagi_session"  # keep the Cookie() param name in sync
