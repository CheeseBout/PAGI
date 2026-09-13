from __future__ import annotations

from ..base import ToolContext, ToolSpec, register
from ..sandbox_client import SandboxError, call


async def _handler(ctx: ToolContext, args: dict) -> dict:
    language = args.get("language")
    code = args.get("code")
    if language not in ("python", "node", "bash"):
        return {"error": "language must be python | node | bash"}
    if not isinstance(code, str) or not code:
        return {"error": "code is required"}
    timeout = int(args.get("timeout_seconds") or 30)
    timeout = max(1, min(timeout, 120))
    try:
        return await call(
            "/execute",
            {
                "session_id": ctx.session_id,
                "language": language,
                "code": code,
                "timeout_seconds": timeout,
            },
            timeout=timeout + 20,
        )
    except SandboxError as exc:
        return {"error": str(exc)}


SPEC = register(
    ToolSpec(
        name="execute_code",
        description="Run a snippet of code (python/node/bash) in an isolated sandbox "
        "container with Internet access. Use for computation, data processing, or checks.",
        parameters={
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "node", "bash"]},
                "code": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
            },
            "required": ["language", "code"],
        },
        handler=_handler,
        requires_approval=True,
    )
)
