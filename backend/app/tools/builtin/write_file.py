from __future__ import annotations

from ..base import ToolContext, ToolSpec, register
from ..sandbox_client import SandboxError, call


async def _handler(ctx: ToolContext, args: dict) -> dict:
    path = args.get("path")
    content = args.get("content")
    if not path:
        return {"error": "path is required"}
    if content is None:
        return {"error": "content is required"}
    mode = args.get("mode") or "overwrite"
    if mode not in ("overwrite", "append"):
        return {"error": "mode must be overwrite | append"}
    try:
        return await call(
            "/files/write",
            {"session_id": ctx.session_id, "path": path, "content": content, "mode": mode},
        )
    except SandboxError as exc:
        return {"error": str(exc)}


SPEC = register(
    ToolSpec(
        name="write_file",
        description="Write (overwrite or append) a text file in the session workspace. Has side effects.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append"]},
            },
            "required": ["path", "content"],
        },
        handler=_handler,
        requires_approval=True,
    )
)
