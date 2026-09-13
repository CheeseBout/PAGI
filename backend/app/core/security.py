"""Security primitives: password hashing, session JWT, SSRF guard, login rate limit.

See PLAN.md §8 and SPEC.md §8 / §5.2.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from ..config import get_settings

_settings = get_settings()
_ph = PasswordHasher()

SESSION_COOKIE = "pagi_session"
SESSION_TTL = timedelta(days=7)
SESSION_RENEW_WHEN_LEFT = timedelta(days=1)


# ── passwords ────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ── session token ───────────────────────────────────────────────────────
def issue_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": int(now.timestamp()), "exp": int((now + SESSION_TTL).timestamp())}
    return jwt.encode(payload, _settings.session_secret, algorithm="HS256")


def decode_token(token: str) -> str | None:
    """Return the user id, or None if the token is invalid/expired."""
    try:
        payload = jwt.decode(token, _settings.session_secret, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def token_needs_refresh(token: str) -> bool:
    try:
        payload = jwt.decode(token, _settings.session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    return exp - datetime.now(timezone.utc) < SESSION_RENEW_WHEN_LEFT


# ── SSRF guard ──────────────────────────────────────────────────────────
class SSRFError(ValueError):
    pass


def ssrf_guard(url: str) -> None:
    """Raise SSRFError if *url* resolves to a private / loopback / link-local /
    metadata address. Used by fetch_url and http_request (SPEC §5.2)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError("only http/https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no host")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:  # pragma: no cover - network dependent
        raise SSRFError(f"cannot resolve host: {exc}") from exc

    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SSRFError(f"blocked address: {addr}")
        if addr == "169.254.169.254":
            raise SSRFError("blocked: cloud metadata endpoint")


async def _validate_redirect(request: httpx.Request) -> None:
    # httpx.AsyncClient requires its "request" event hooks to be coroutines.
    ssrf_guard(str(request.url))


def safe_async_client(timeout: float, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """`httpx.AsyncClient` that re-runs `ssrf_guard` on every request it sends,
    including redirect hops — a plain pre-flight `ssrf_guard(url)` check only
    validates the first URL, so a 302 to a private/metadata address would
    otherwise sail through `follow_redirects=True` unchecked.

    `transport` is exposed only so tests can inject `httpx.MockTransport`."""
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        event_hooks={"request": [_validate_redirect]},
        transport=transport,
    )


# ── rate limiting (in-memory, sliding window per key) ───────────────────
class RateLimiter:
    """Max `limit` events per `window` seconds per key (IP, user id, ...).

    Two usage styles:
      - login flow: `is_blocked` / `record_failure` / `reset` (only failures count)
      - chat flow:  `allow(key)` (every call counts, no separate reset)
    """

    def __init__(self, limit: int = 5, window: int = 15 * 60) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> None:
        self._hits[key] = [t for t in self._hits.get(key, []) if now - t < self.window]

    def is_blocked(self, key: str) -> bool:
        now = time.time()
        self._prune(key, now)
        return len(self._hits.get(key, [])) >= self.limit

    def retry_after(self, key: str) -> int:
        now = time.time()
        self._prune(key, now)
        hits = self._hits.get(key, [])
        if not hits:
            return 0
        return max(1, int(self.window - (now - hits[0])))

    def record_failure(self, key: str) -> None:
        self._hits.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def allow(self, key: str) -> bool:
        """Record one hit now; return False if that pushes the key over the limit."""
        now = time.time()
        self._prune(key, now)
        hits = self._hits.setdefault(key, [])
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


login_rate_limiter = RateLimiter(limit=5, window=15 * 60)
# 30 chat turns / minute / user — generous for interactive use, blocks runaway loops/abuse.
chat_rate_limiter = RateLimiter(limit=30, window=60)


# ── basic prompt-injection heuristics ────────────────────────────────────
# Not a defense on its own — the real mitigation is the role="tool" boundary
# (SPEC §4.2) plus a system-prompt reminder (core/memory.py) and HITL gating
# every side-effecting tool. This just flags likely attempts for the audit
# log (PLAN §10 risk table) so an operator can review what an agent fetched.
_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard the above",
    "disregard previous",
    "you are now",
    "new instructions:",
    "system prompt:",
    "reveal your instructions",
    "reveal your system prompt",
    "do anything now",
    "act as if",
    "override your instructions",
]


_ZERO_WIDTH = ("​", "‌", "‍", "﻿", "⁠")


def scan_for_injection(text: str) -> list[str]:
    """Return the heuristic markers found in *text* (case-insensitive), if any.

    Strips zero-width characters and applies NFKC normalization first so a
    phrase broken up with invisible characters or fullwidth/compatibility
    unicode variants still matches — both are trivial, common ways to dodge a
    plain substring scan.
    """
    if not text:
        return []
    cleaned = unicodedata.normalize("NFKC", text)
    for ch in _ZERO_WIDTH:
        cleaned = cleaned.replace(ch, "")
    lowered = cleaned.lower()
    return [p for p in _INJECTION_PATTERNS if p in lowered]
