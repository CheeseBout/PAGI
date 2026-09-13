"""ReAct — the default pattern (Phase 14a, SPEC §16.1).

A verbatim move of the old ``agent_runtime._drive()`` body. Acceptance test for
14a: the whole existing suite passes with no test changes.
"""

from __future__ import annotations

import structlog

from ..memory import load_messages, unresolved_tool_calls
from .base import RunContext, register

log = structlog.get_logger("pagi.patterns.react")


class ReActStrategy:
    name = "react"

    async def drive(self, rc: RunContext) -> None:
        # deferred import: agent_runtime imports this package
        from .. import agent_runtime as rt

        for _ in range(rt.MAX_ITERATIONS):
            messages = await load_messages(rc.db, rc.session_id)
            if not messages:
                return

            assistant_msg, pending = unresolved_tool_calls(messages)
            if pending:
                await rt._resolve_tools(
                    rc.db, rc.session_id, assistant_msg, pending, rc.specs_by_name,
                    rc.policy, rc.ctx, rc.wait_for_approval,
                )
                continue

            last = messages[-1]
            if last.role in ("user", "tool"):
                made_calls = await rt._call_llm(
                    rc.db, rc.session_id, rc.chat, rc.agent, rc.specs, messages,
                    rag_mode=rc.rag_mode,
                )
                if not made_calls:
                    return
                continue
            return  # last message is a completed assistant turn
        log.warning("max_iterations_reached", session_id=rc.session_id)


register(ReActStrategy())
