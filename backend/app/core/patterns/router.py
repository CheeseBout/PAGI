"""Router — classify with a cheap model, then take a branch (Phase 14c, SPEC §16.2).

Two nodes, always: ``route`` then ``execute``. A branch can point at another
agent (delegate), swap the model tier, or just fall through to ReAct. Routing is
classification — use a cheap ``router_model``; this also optimises cost.
"""

from __future__ import annotations

import structlog

from ._common import NodeLog, complete_json, run_react_once
from .base import RunContext, register

log = structlog.get_logger("pagi.patterns.router")

_ROUTE_SYS = (
    "Classify the user's request into exactly one of the named routes. "
    'Return JSON {"route": "<name>", "confidence": 0.0-1.0, "reason": "..."}.'
)


class RouterStrategy:
    name = "router"

    async def drive(self, rc: RunContext) -> None:
        cfg = rc.cfg
        routes = cfg.get("routes") or []
        conf_min = float(cfg.get("route_confidence_min") or 0.5)
        fallback = cfg.get("fallback_route")
        emit_mid = cfg.get("emit_intermediate", True)
        task = await _first_user_text(rc)

        if not routes:
            await run_react_once(rc)
            return

        names = [r.get("name") for r in routes if r.get("name")]
        catalogue = "\n".join(
            f"- {r.get('name')}: {r.get('description', '')}" for r in routes
        )
        async with NodeLog(rc, node="route", step_no=0) as nl:
            obj = await complete_json(
                rc,
                system=_ROUTE_SYS,
                user=f"ROUTES:\n{catalogue}\n\nREQUEST:\n{task}",
                model=cfg.get("router_model"),
                kind="route",
            )
            picked = obj.get("route") if isinstance(obj, dict) else None
            conf = 0.0
            try:
                conf = float(obj.get("confidence")) if isinstance(obj, dict) else 0.0
            except (TypeError, ValueError):
                conf = 0.0
            if picked not in names or conf < conf_min:
                picked = fallback if fallback in names else None
            nl.payload = {"route": picked, "confidence": conf}
            nl.output_summary = f"route={picked} conf={conf}"

        if emit_mid:
            await rc.emit_node(
                {
                    "type": "route_decision",
                    "route": picked,
                    "confidence": conf,
                    "reason": (obj or {}).get("reason", "") if isinstance(obj, dict) else "",
                }
            )

        chosen = next((r for r in routes if r.get("name") == picked), None)
        async with NodeLog(rc, node="execute", step_no=1):
            if chosen and chosen.get("target_agent_id"):
                await _delegate_branch(rc, chosen["target_agent_id"], task)
            else:
                if chosen and chosen.get("target_model"):
                    rc.agent.model = chosen["target_model"]  # swap tier for this turn
                await run_react_once(rc)


async def _delegate_branch(rc: RunContext, agent_id: str, task: str) -> None:
    from ...db.models import Agent, Message
    from ..delegation import run_delegated

    worker = await rc.db.get(Agent, agent_id)
    if worker is None or not getattr(worker, "is_delegatable", False):
        await run_react_once(rc)
        return
    res = await run_delegated(
        parent_session_id=rc.session_id,
        tool_call_id=f"route:{rc.session_id}:{agent_id}",
        worker_name=worker.name,
        task=task,
        wait_for_approval=rc.wait_for_approval,
        mode=rc.mode,
        unattended_allowed_tools=rc.unattended_allowed_tools,
    )
    rc.db.add(
        Message(
            session_id=rc.session_id, role="assistant",
            content=res.get("answer") or res.get("message") or "(no answer)",
        )
    )
    await rc.db.commit()


async def _first_user_text(rc: RunContext) -> str:
    from sqlmodel import select

    from ...db.models import Message

    row = (
        await rc.db.exec(
            select(Message)
            .where(Message.session_id == rc.session_id, Message.role == "user")
            .order_by(Message.created_at)
        )
    ).first()
    return (row.content or "") if row else ""


register(RouterStrategy())
