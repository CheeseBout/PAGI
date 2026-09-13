from __future__ import annotations

from ..base import ToolContext, ToolSpec, register
from ..sandbox_client import SandboxError, call


async def _handler(ctx: ToolContext, args: dict) -> dict:
    payload = {"session_id": ctx.session_id}
    if args.get("path"):
        payload["path"] = args["path"]
    try:
        return await call("/files/list", payload)
    except SandboxError as exc:
        return {"error": str(exc)}


SPEC = register(
    ToolSpec(
        name="list_files",
        description="List entries in a workspace directory (default: workspace root).",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": [],
        },
        handler=_handler,
        requires_approval=False,
    )
)
