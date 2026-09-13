"""HTTP client for the sandbox service (SPEC §6). Backend never runs agent
code in-process — it always calls out to `sandbox/` over the internal network."""

from __future__ import annotations

import asyncio

import httpx

from ..config import get_settings


class SandboxError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


# Cap how many sandbox calls the backend has in flight at once so a burst of
# tool calls can't fan out into an unbounded pile of container launches.
_gate = asyncio.Semaphore(get_settings().sandbox_max_concurrent_calls)


async def call(path: str, payload: dict, *, timeout: float = 130.0) -> dict:
    settings = get_settings()
    url = f"{settings.sandbox_url.rstrip('/')}{path}"
    headers = {"X-Internal-Token": settings.sandbox_internal_token}
    try:
        async with _gate:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:  # pragma: no cover - network dependent
        raise SandboxError(f"sandbox unreachable: {exc}") from exc

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = {"error": {"message": resp.text[:500]}}
        raise SandboxError(
            f"sandbox {resp.status_code}: {detail}", status_code=resp.status_code, payload=detail
        )
    return resp.json()


async def health() -> dict:
    settings = get_settings()
    url = f"{settings.sandbox_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers={"X-Internal-Token": settings.sandbox_internal_token})
            resp.raise_for_status()
            return resp.json()
    except Exception:  # pragma: no cover
        return {"status": "unreachable"}
