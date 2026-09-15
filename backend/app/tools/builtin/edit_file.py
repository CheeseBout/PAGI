from __future__ import annotations

from ..base import ToolContext, ToolSpec, register
from ..sandbox_client import SandboxError, call


async def _handler(ctx: ToolContext, args: dict) -> dict:
    path = args.get("path")
    find = args.get("find")
    replace = args.get("replace")
    if not path or find is None or replace is None:
        return {"error": "path, find and replace are all required"}
    occurrence = args.get("occurrence") or "first"
    if occurrence not in ("first", "all"):
        return {"error": "occurrence must be first | all"}
    try:
        return await call(
            "/files/edit",
            {
                "session_id": ctx.workspace_id,
                "path": path,
                "find": find,
                "replace": replace,
                "occurrence": occurrence,
            },
        )
    except SandboxError as exc:
        return {"error": str(exc)}


SPEC = register(
    ToolSpec(
        name="edit_file",
        description="Find-and-replace a substring inside a workspace file "
        "(avoids rewriting the whole file). Errors if `find` is not present.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "find": {"type": "string"},
                "replace": {"type": "string"},
                "occurrence": {"type": "string", "enum": ["first", "all"]},
            },
            "required": ["path", "find", "replace"],
        },
        handler=_handler,
        requires_approval=True,
    )
)
