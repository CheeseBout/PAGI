"""Concurrency + disk-quota guards for the sandbox service (Wave 1d).

The FastAPI routes are sync (`def`) so they run in Starlette's threadpool; these
guards use threading primitives, not asyncio.

- a global bounded semaphore caps how many exec containers run at once,
- a per-session lock stops one runaway agent launching many execs in parallel,
- a best-effort workspace byte quota rejects writes past a ceiling.

All limits are opt-out: set the env var to 0 to disable.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from pathlib import Path

from config import config

_MAX_CONCURRENT = int(os.getenv("SANDBOX_MAX_CONCURRENT_EXEC", "4"))
_QUEUE_TIMEOUT = float(os.getenv("SANDBOX_EXEC_QUEUE_TIMEOUT", "0.5"))
_QUOTA_MB = int(os.getenv("SANDBOX_WORKSPACE_QUOTA_MB", "512"))

_slots = threading.BoundedSemaphore(_MAX_CONCURRENT) if _MAX_CONCURRENT > 0 else None
_session_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()
_active = 0
_active_lock = threading.Lock()


class SandboxBusy(RuntimeError):
    """Raised when an exec slot cannot be acquired promptly."""


class QuotaExceeded(RuntimeError):
    """Raised when a write would push the workspace past its byte quota."""


def _session_lock(session_id: str) -> threading.Lock:
    with _registry_lock:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_locks[session_id] = lock
        return lock


@contextlib.contextmanager
def exec_slot(session_id: str) -> Iterator[None]:
    """Hold one global exec slot + this session's exec lock, or raise SandboxBusy."""
    global _active
    slock = _session_lock(session_id)
    if not slock.acquire(blocking=False):
        raise SandboxBusy("this conversation already has code running")
    got_global = False
    try:
        if _slots is not None:
            if not _slots.acquire(timeout=_QUEUE_TIMEOUT):
                raise SandboxBusy("sandbox is at capacity, retry shortly")
            got_global = True
        with _active_lock:
            _active += 1
        try:
            yield
        finally:
            with _active_lock:
                _active -= 1
    finally:
        if got_global and _slots is not None:
            _slots.release()
        slock.release()


def workspace_size_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def check_quota(path: Path, incoming_bytes: int = 0) -> None:
    if _QUOTA_MB <= 0:
        return
    limit = _QUOTA_MB * 1024 * 1024
    if workspace_size_bytes(path) + max(0, incoming_bytes) > limit:
        raise QuotaExceeded(f"workspace exceeds the {_QUOTA_MB} MB quota")


def stats() -> dict:
    with _active_lock:
        active = _active
    return {
        "active": active,
        "max": _MAX_CONCURRENT,
        "quota_mb": _QUOTA_MB,
    }
