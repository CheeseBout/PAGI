"""Tool layer. Importing this package registers every builtin tool."""

from __future__ import annotations

from . import builtin  # noqa: F401  (side effect: registers builtin tools)
from .base import (
    TOOL_REGISTRY,
    ToolContext,
    ToolSpec,
    effective_policy,
    register,
    resolve_agent_tools,
)

__all__ = [
    "TOOL_REGISTRY",
    "ToolContext",
    "ToolSpec",
    "effective_policy",
    "register",
    "resolve_agent_tools",
]
