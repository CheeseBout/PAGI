from __future__ import annotations

import httpx

from ...core.security import SSRFError, safe_async_client, ssrf_guard
from ..base import ToolContext, ToolSpec, register

_MAX_EXCERPT = 8000


async def _handler(ctx: ToolContext, args: dict) -> dict:
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    try:
        ssrf_guard(url)
    except SSRFError as exc:
        return {"error": f"blocked by SSRF guard: {exc}"}

    try:
        async with safe_async_client(timeout=20.0) as client:
            resp = await client.get(url, headers={"User-Agent": "PAGI/0.1 (+fetch_url)"})
    except SSRFError as exc:
        return {"error": f"blocked by SSRF guard: {exc}"}
    except httpx.HTTPError as exc:
        return {"error": f"request failed: {exc}"}

    body = resp.text or ""
    excerpt = body[:_MAX_EXCERPT]
    return {
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "body_excerpt": excerpt,
        "truncated": len(body) > _MAX_EXCERPT,
    }


SPEC = register(
    ToolSpec(
        name="fetch_url",
        description="HTTP GET a single URL and return an excerpt of the body. "
        "Read-only; private/loopback/metadata addresses are blocked.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        handler=_handler,
        requires_approval=False,
    )
)
