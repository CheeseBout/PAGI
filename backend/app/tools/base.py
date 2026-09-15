"""Tool registration + per-agent tool resolution (SPEC §5, §9, §10)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from sqlmodel.ext.asyncio.session import AsyncSession

ToolHandler = Callable[["ToolContext", dict], Awaitable[dict]]


@dataclass
class ToolContext:
    """Passed to every tool handler for the duration of one tool call.

    The fields below the first two are set by ``agent_runtime`` so tools that
    spawn work of their own (``delegate_task``, Phase 13) inherit the turn's
    approval / unattended mode and know where they sit in the session tree.
    """

    session_id: str
    db: "AsyncSession"
    wait_for_approval: bool = True
    mode: str = "interactive"  # interactive | unattended
    unattended_allowed_tools: list[str] = field(default_factory=list)
    agent_id: str | None = None
    tools_allowed: list[str] = field(default_factory=list)
    depth: int = 0
    # sandbox directory key for THIS turn (Phase 18, PLAN §18) — equal to
    # session_id unless the session has a `workspace_id` override, in which
    # case several sessions (e.g. Planner/Developer/QA) share one persistent
    # sandbox workspace. Only the sandbox-backed tool handlers use this; other
    # uses of ctx.session_id (rag_search logging, delegate_task bookkeeping)
    # must keep using the real session_id.
    workspace_id: str = ""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: ToolHandler
    requires_approval: bool = True  # default-deny (PLAN §4)
    tags: list[str] = field(default_factory=list)

    def llm_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


TOOL_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    TOOL_REGISTRY[spec.name] = spec
    return spec


def effective_policy(spec: ToolSpec, tool_policy: dict) -> str:
    """`auto` or `ask` — agent.tool_policy overrides the tool default."""
    override = (tool_policy or {}).get(spec.name)
    if override in ("auto", "ask"):
        return override
    return "ask" if spec.requires_approval else "auto"


def resolve_agent_tools(
    tools_allowed: list[str],
    tool_policy: dict,
    *,
    extra: list[ToolSpec] | None = None,
    unattended: bool = False,
    unattended_allowed: list[str] | None = None,
) -> tuple[list[ToolSpec], dict[str, str]]:
    """Return (specs, {name: policy}) for the tools an agent may use this turn.

    In unattended (cron) mode, tools whose effective policy is ``ask`` are
    dropped unless explicitly listed in ``unattended_allowed`` — in which case
    they are forced to ``auto`` for this run only (SPEC §10)."""
    unattended_allowed = set(unattended_allowed or [])
    by_name: dict[str, ToolSpec] = {}
    for spec in extra or []:
        by_name[spec.name] = spec
    for name in tools_allowed or []:
        if name in TOOL_REGISTRY:
            by_name[name] = TOOL_REGISTRY[name]

    specs: list[ToolSpec] = []
    policy: dict[str, str] = {}
    for name, spec in by_name.items():
        pol = effective_policy(spec, tool_policy)
        if unattended and pol == "ask":
            if name in unattended_allowed:
                pol = "auto"
            else:
                continue
        specs.append(spec)
        policy[name] = pol
    return specs, policy
