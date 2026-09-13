"""WebSocket fan-out + per-session running-turn registry (SPEC §3, §15.4)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("pagi.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: dict[str, set[WebSocket]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # sub-agent tree: root_session_id -> set(child_session_id) (Phase 13)
        self._children: dict[str, set[str]] = {}
        # child_session_id -> {root, agent_name, depth} for event bubbling
        self._root_of: dict[str, dict[str, Any]] = {}

    # -- connections --------------------------------------------------------
    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._conns.setdefault(session_id, set()).add(ws)

    def disconnect(self, session_id: str, ws: WebSocket) -> None:
        conns = self._conns.get(session_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self._conns.pop(session_id, None)

    async def broadcast(self, session_id: str, event: dict[str, Any]) -> None:
        for ws in list(self._conns.get(session_id, set())):
            try:
                await ws.send_json(event)
            except Exception:  # pragma: no cover - client vanished mid-send
                self.disconnect(session_id, ws)

    def has_listeners(self, session_id: str) -> bool:
        return bool(self._conns.get(session_id))

    # -- sub-agent tree (Phase 13, SPEC §15.4) ----------------------------
    def register_child(
        self, root_session_id: str, child_session_id: str, *, agent_name: str = "", depth: int = 1
    ) -> None:
        self._children.setdefault(root_session_id, set()).add(child_session_id)
        self._root_of[child_session_id] = {
            "root": root_session_id,
            "agent_name": agent_name,
            "depth": depth,
        }

    def bubble_target(self, session_id: str) -> dict[str, Any] | None:
        """{root, agent_name, depth} if this session's events bubble to a root,
        else None (it is a root itself)."""
        return self._root_of.get(session_id)

    def forget_child(self, child_session_id: str) -> None:
        info = self._root_of.pop(child_session_id, None)
        if info is not None:
            kids = self._children.get(info["root"])
            if kids:
                kids.discard(child_session_id)
                if not kids:
                    self._children.pop(info["root"], None)

    # -- running turn -----------------------------------------------------
    def set_task(self, session_id: str, task: asyncio.Task) -> None:
        self._tasks[session_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(session_id, None))

    def abort(self, session_id: str) -> bool:
        """Cancel this session's turn AND every sub-agent turn under it."""
        cancelled = False
        task = self._tasks.get(session_id)
        if task and not task.done():
            task.cancel()
            cancelled = True
        for child_id in list(self._children.get(session_id, set())):
            child_task = self._tasks.get(child_id)
            if child_task and not child_task.done():
                child_task.cancel()
                cancelled = True
        return cancelled

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and not task.done())

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(Exception):
                await task


manager = ConnectionManager()
