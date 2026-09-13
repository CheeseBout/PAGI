"""``delegate_task`` — hand a sub-task to another agent (SPEC §5.5, §15.3).

Policy ``auto`` on purpose: every dangerous tool *inside* the sub-agent still
goes through its own HITL. Gating one more time here would only cause interrupt
fatigue without adding real safety.

Runs in the backend, never through the sandbox.
"""

from __future__ import annotations

import structlog

from ...config import get_settings
from ...db.models import Agent
from ..base import ToolContext, ToolSpec, register

log = structlog.get_logger("pagi.tools.delegate")


async def _handler(ctx: ToolContext, args: dict) -> dict:
    from ...core.delegation import DelegationError, run_delegated

    worker = (args.get("agent") or "").strip()
    task = (args.get("task") or "").strip()
    context = args.get("context")
    if not worker or not task:
        return {"error": "invalid_args", "message": "both `agent` and `task` are required"}

    tool_call_id = getattr(ctx, "_tool_call_id", None) or f"{ctx.session_id}:{worker}:{task[:32]}"

    try:
        return await run_delegated(
            parent_session_id=ctx.session_id,
            tool_call_id=tool_call_id,
            worker_name=worker,
            task=task,
            context=context if isinstance(context, str) else None,
            wait_for_approval=ctx.wait_for_approval,
            mode=ctx.mode,
            unattended_allowed_tools=ctx.unattended_allowed_tools,
        )
    except DelegationError as exc:
        log.info("delegate_rejected", code=exc.code, session_id=ctx.session_id)
        return {"error": exc.code, "message": exc.message}
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("delegate_failed", session_id=ctx.session_id)
        return {"error": "subagent_failed", "message": str(exc)}


async def enum_workers() -> list[str]:
    """Names of delegatable agents — used to fill the tool schema's enum."""
    from ...db.session import SessionLocal
    from sqlmodel import select

    async with SessionLocal() as db:
        rows = (
            await db.exec(select(Agent).where(Agent.is_delegatable == True))  # noqa: E712
        ).all()
    return [a.name for a in rows]


SPEC = register(
    ToolSpec(
        name="delegate_task",
        description=(
            "Hand a self-contained sub-task to a specialist agent and get back its "
            "final answer. The task text must stand on its own — the other agent "
            "cannot see this conversation. Use `context` to pass any facts it needs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the specialist agent to delegate to.",
                },
                "task": {
                    "type": "string",
                    "description": "What to do, including how you'll know it's done.",
                },
                "context": {
                    "type": "string",
                    "description": "Facts the task depends on. Required if it needs info from this chat.",
                },
            },
            "required": ["agent", "task"],
        },
        handler=_handler,
        requires_approval=False,
    )
)
