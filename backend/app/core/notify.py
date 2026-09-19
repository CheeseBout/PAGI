"""Per-user notification hub (SPEC §21.7).

The chat WebSocket (``ws_manager``) is per *session*; cron runs, project
iterations and approvals raised while nobody has that session open have no way
to reach a client. This hub is the missing user-level channel behind
``WS /ws/notifications``.

Delivery is deliberately weak: at most once, not persisted, no replay. The DB
stays the source of truth (``GET /api/approvals?status=pending``, cron and
project history) — a client that reconnects re-syncs from there. Emitting is
fire-and-forget and must **never** raise into the caller: a dead client or a
hub bug may not fail a cron run, a project iteration or an approval.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("pagi.notify")

# SPEC §21.7 — args_preview / summary are cut so a notification never carries
# a whole tool payload or model answer.
PREVIEW_MAX_CHARS = 200


def clip(text: Any, limit: int = PREVIEW_MAX_CHARS) -> str:
    s = text if isinstance(text, str) else str(text)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


class NotificationHub:
    def __init__(self) -> None:
        self._conns: dict[str, set[WebSocket]] = {}

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._conns.setdefault(user_id, set()).add(ws)

    def disconnect(self, user_id: str, ws: WebSocket) -> None:
        conns = self._conns.get(user_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self._conns.pop(user_id, None)

    def has_listeners(self, user_id: str) -> bool:
        return bool(self._conns.get(user_id))

    async def emit(self, user_id: str, event: dict[str, Any]) -> None:
        for ws in list(self._conns.get(user_id, set())):
            try:
                await ws.send_json(event)
            except Exception:  # client vanished mid-send
                self.disconnect(user_id, ws)


hub = NotificationHub()


async def notify(user_id: str | None, event: dict[str, Any]) -> None:
    """Fire-and-forget: swallow everything (SPEC §21.7, invariant 3)."""
    if not user_id:
        return
    try:
        await hub.emit(user_id, event)
    except Exception:  # pragma: no cover - defensive, emit() already guards sends
        log.warning("notify_failed", exc_info=True)
