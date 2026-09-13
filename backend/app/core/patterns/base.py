"""Strategy seam for design patterns (Phase 14a, SPEC §16.1).

``run_turn`` builds a ``RunContext`` and calls ``get_strategy(cfg["pattern"]).drive(rc)``.
Adding an 8th pattern must not touch ``agent_runtime.py`` — it registers a
``Strategy`` here, the same way ``providers/`` lets you add a 5th provider without
touching the core.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...db.models import Agent, ChatSession
from ...tools import ToolContext, ToolSpec


@dataclass
class RunContext:
    """Everything a Strategy needs for one turn. Rebuilt from the DB each turn —
    there is no in-memory turn state to lose (SPEC §7.2 / §16.1)."""

    db: Any
    session_id: str
    chat: ChatSession
    agent: Agent
    specs: list[ToolSpec]
    specs_by_name: dict[str, ToolSpec]
    policy: dict[str, str]
    ctx: ToolContext
    wait_for_approval: bool
    mode: str  # interactive | unattended
    rag_mode: str
    cfg: dict[str, Any]  # resolved OrchestrationConfig
    emit: Callable[[dict], Awaitable[None]]
    unattended_allowed_tools: list[str] = field(default_factory=list)
    #: id of the most recently opened agent_runs row — patterns tag their WS
    #: events with it so the UI can jump to that node (SPEC §3.2 / §16.4)
    last_run_id: str | None = None

    async def emit_node(self, event: dict) -> None:
        """emit() with the current run_id folded in."""
        await self.emit({**event, "run_id": self.last_run_id})


class Strategy(Protocol):
    name: str

    async def drive(self, rc: RunContext) -> None: ...


class StrategyUnavailable(Exception):
    """Pattern is valid in the schema but not registered yet (e.g. supervisor
    before Phase 14c). Never silently fall back to react — SPEC §16.2."""


_REGISTRY: dict[str, Strategy] = {}


def register(strategy: Strategy) -> Strategy:
    _REGISTRY[strategy.name] = strategy
    return strategy


def get_strategy(pattern: str | None) -> Strategy:
    name = pattern or "react"
    strat = _REGISTRY.get(name)
    if strat is None:
        raise StrategyUnavailable(
            f"orchestration pattern '{name}' is configured but not available in this build"
        )
    return strat
