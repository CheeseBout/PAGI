from __future__ import annotations

from ..base import ToolContext, ToolSpec, register
from ..sandbox_client import SandboxError, call


async def _handler(ctx: ToolContext, args: dict) -> dict:
    pattern = args.get("pattern")
    if not pattern:
        return {"error": "pattern is required"}
    payload = {
        "session_id": ctx.session_id,
        "pattern": pattern,
        "regex": bool(args.get("regex", False)),
        "offset": int(args.get("offset") or 0),
        "limit": int(args.get("limit") or 200),
    }
    if args.get("path"):
        payload["path"] = args["path"]
    try:
        return await call("/files/search", payload)
    except SandboxError as exc:
        return {"error": str(exc)}


SPEC = register(
    ToolSpec(
        name="search_files",
        description="Grep the workspace for a pattern (plain string by default, "
        "or regex). Returns up to `limit` matches (default/max 200/500) starting "
        "at `offset`; `has_more` in the result indicates more matches remain — "
        "call again with a higher `offset` to page through them. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "regex": {"type": "boolean"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["pattern"],
        },
        handler=_handler,
        requires_approval=False,
    )
)
