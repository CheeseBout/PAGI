from __future__ import annotations

import httpx

from ...core.security import SSRFError, safe_async_client, ssrf_guard
from ..base import ToolContext, ToolSpec, register

_ALLOWED = {"GET", "POST", "PUT", "DELETE"}
_MAX_BODY = 20000


async def _handler(ctx: ToolContext, args: dict) -> dict:
    method = (args.get("method") or "GET").upper()
    if method not in _ALLOWED:
        return {"error": f"method must be one of {sorted(_ALLOWED)}"}
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    try:
        ssrf_guard(url)
    except SSRFError as exc:
        return {"error": f"blocked by SSRF guard: {exc}"}

    headers = args.get("headers") or {}
    body = args.get("body")
    try:
        async with safe_async_client(timeout=30.0) as client:
            resp = await client.request(method, url, headers=headers, content=body)
    except SSRFError as exc:
        return {"error": f"blocked by SSRF guard: {exc}"}
    except httpx.HTTPError as exc:
        return {"error": f"request failed: {exc}"}

    text = resp.text or ""
    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": text[:_MAX_BODY],
        "truncated": len(text) > _MAX_BODY,
    }


SPEC = register(
    ToolSpec(
        name="http_request",
        description="Perform an arbitrary HTTP request (GET/POST/PUT/DELETE) with "
        "custom headers and body. Has side effects — requires approval by default.",
        parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": sorted(_ALLOWED)},
                "url": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {"type": "string"},
            },
            "required": ["method", "url"],
        },
        handler=_handler,
        requires_approval=True,
    )
)
