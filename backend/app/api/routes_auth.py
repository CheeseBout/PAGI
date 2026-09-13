"""Auth routes (SPEC §2.1, §8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_settings
from ..core.security import (
    SESSION_COOKIE,
    SESSION_TTL,
    issue_token,
    login_rate_limiter,
    verify_password,
)
from ..db.models import User
from ..schemas import LoginRequest
from .deps import APIError, get_current_user, get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    ip = request.client.host if request.client else "unknown"
    if login_rate_limiter.is_blocked(ip):
        retry = login_rate_limiter.retry_after(ip)
        raise APIError(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", f"Try again in {retry}s")

    user = (await db.exec(select(User).where(User.username == body.username))).first()
    if user is None or not verify_password(body.password, user.password_hash):
        login_rate_limiter.record_failure(ip)
        raise APIError(status.HTTP_401_UNAUTHORIZED, "invalid_credentials", "Wrong username or password")

    login_rate_limiter.reset(ip)
    _set_session_cookie(response, issue_token(user.id))
    return {"user": {"id": user.id, "username": user.username}}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username}
