"""``OrchestrationConfig`` + the 3-tier cascade resolver (SPEC §16.2).

Same shape as ``rag/config.py``: every field Optional, ``None`` = "inherit from
the tier above", ``extra="forbid"`` so a typo is a 422 not a silent no-op.

    ORCH_DEFAULT_CONFIG_JSON + the baseline below   (tier 1 — env)
      -> agents.orchestration                       (tier 2 — per agent)
      -> per-request override                       (tier 3 — try / eval)

Three tiers, not four: a pattern is a property of the agent, there is no
"collection"-equivalent tier.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...config import get_settings

Pattern = Literal[
    "react",
    "reflexion",
    "plan_execute",
    "router",
    "supervisor",
    "debate",
    "evaluator_optimizer",
]

# patterns that spawn sub-agents (need SUBAGENT_ENABLED + delegatable workers)
DELEGATION_PATTERNS = {"supervisor", "debate", "router"}


class OrchestrationConfig(BaseModel):
    """All fields Optional — see module docstring. Defaults live in ``_BASELINE``."""

    model_config = {"extra": "forbid"}

    # ── shared ────────────────────────────────────────────────────────
    pattern: Pattern | None = None
    max_llm_calls: int | None = Field(default=None, ge=1, le=100)
    node_timeout_seconds: int | None = Field(default=None, ge=1)
    emit_intermediate: bool | None = None
    # in-traffic A/B (SPEC §16.6): with probability ab_split, run ``ab_pattern``
    # instead of ``pattern`` this turn. The chosen arm shows up as agent_runs.pattern.
    ab_pattern: Pattern | None = None
    ab_split: float | None = Field(default=None, ge=0, le=1)

    # ── reflexion ────────────────────────────────────────────────────
    max_attempts: int | None = Field(default=None, ge=1, le=5)
    evaluator_model: str | None = None
    success_score: float | None = Field(default=None, ge=0, le=1)
    reflection_max_chars: int | None = Field(default=None, ge=50)

    # ── plan_execute ────────────────────────────────────────────────
    planner_model: str | None = None
    max_steps: int | None = Field(default=None, ge=1, le=12)
    verify_each_step: bool | None = None
    replan_on_failure: bool | None = None
    max_replans: int | None = Field(default=None, ge=0, le=3)

    # ── router ─────────────────────────────────────────────────────
    router_model: str | None = None
    routes: list[dict[str, Any]] | None = None
    fallback_route: str | None = None
    route_confidence_min: float | None = Field(default=None, ge=0, le=1)

    # ── supervisor ────────────────────────────────────────────────
    supervisor_model: str | None = None
    worker_agent_ids: list[str] | None = None
    max_workers: int | None = Field(default=None, ge=1, le=5)
    parallel: bool | None = None
    synthesis_model: str | None = None
    worker_timeout_seconds: int | None = Field(default=None, ge=1)

    # ── debate ───────────────────────────────────────────────────
    debater_agent_ids: list[str] | None = None
    rounds: int | None = Field(default=None, ge=1, le=3)
    judge_model: str | None = None
    require_distinct_models: bool | None = None

    # ── evaluator_optimizer ─────────────────────────────────────
    max_rounds: int | None = Field(default=None, ge=1, le=5)
    stop_score: float | None = Field(default=None, ge=0, le=1)
    optimizer_model: str | None = None


# System-wide defaults (SPEC §16.2). Overridable per-field by env
# ORCH_DEFAULT_CONFIG_JSON, then agent, then request.
_BASELINE: dict[str, Any] = {
    "pattern": "react",
    "max_llm_calls": None,
    "node_timeout_seconds": None,
    "emit_intermediate": True,
    "ab_pattern": None,
    "ab_split": 0.0,
    "max_attempts": 3,
    "evaluator_model": None,
    "success_score": 0.8,
    "reflection_max_chars": 500,
    "planner_model": None,
    "max_steps": 6,
    "verify_each_step": True,
    "replan_on_failure": True,
    "max_replans": 1,
    "router_model": None,
    "routes": [],
    "fallback_route": None,
    "route_confidence_min": 0.5,
    "supervisor_model": None,
    "worker_agent_ids": [],
    "max_workers": 3,
    "parallel": True,
    "synthesis_model": None,
    "worker_timeout_seconds": None,
    "debater_agent_ids": [],
    "rounds": 1,
    "judge_model": None,
    "require_distinct_models": True,
    "max_rounds": 3,
    "stop_score": 0.85,
    "optimizer_model": None,
}


def _clean(layer: Any) -> dict[str, Any]:
    """Coerce a layer (dict / OrchestrationConfig / JSON str / None) to a dict of
    only its set, non-None fields — after validation (so a typo raises here)."""
    if layer is None:
        return {}
    if isinstance(layer, OrchestrationConfig):
        return layer.model_dump(exclude_none=True)
    if isinstance(layer, str):
        try:
            layer = json.loads(layer or "{}")
        except ValueError:
            return {}
    if not isinstance(layer, dict):
        return {}
    parsed = OrchestrationConfig.model_validate(
        {k: v for k, v in layer.items() if v is not None}
    )
    return parsed.model_dump(exclude_none=True)


def _env_layer() -> dict[str, Any]:
    s = get_settings()
    merged = dict(_BASELINE)
    merged.update(_clean(getattr(s, "orch_default_config_json", "{}")))
    return merged


def resolve_config(*, agent: Any = None, request: Any = None) -> dict[str, Any]:
    """Return a fully-populated config dict after applying the cascade."""
    out = _env_layer()
    for layer in (agent, request):
        out.update(_clean(layer))
    return out
