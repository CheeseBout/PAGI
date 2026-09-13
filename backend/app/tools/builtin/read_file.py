from __future__ import annotations

from ..base import ToolContext, ToolSpec, register
from ..sandbox_client import SandboxError, call


async def _handler(ctx: ToolContext, args: dict) -> dict:
    path = args.get("path")
    if not path:
        return {"error": "path is required"}
    try:
        return await call("/files/read", {"session_id": ctx.session_id, "path": path})
    except SandboxError as exc:
        return {"error": str(exc)}


SPEC = register(
    ToolSpec(
        name="read_file",
        description="Read a text file from the session workspace (path relative to workspace root).",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=_handler,
        requires_approval=False,
    )
)
