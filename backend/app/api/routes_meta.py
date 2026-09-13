"""Config-schema metadata for the schema-driven Settings forms (SPEC §2.10, §17).

Read-only, no per-user state, but still auth-gated — it exposes the list of
configured agents and models.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_settings
from ..core.orchestration.config import OrchestrationConfig
from ..core.orchestration.config import resolve_config as resolve_orch
from ..core.orchestration.meta import (
    ORCH_FIELD_META,
    ORCH_GROUPS,
    PATTERN_INFO,
)
from ..core.security import safe_async_client
from ..db.models import Agent, User
from ..rag.config import RagConfig
from ..rag.config import resolve_config as resolve_rag
from ..rag.config_meta import RAG_FIELD_META, RAG_GROUPS
from .deps import APIError, get_current_user, get_db

router = APIRouter(prefix="/api/meta", tags=["meta"])

# in-process cache — OpenRouter's catalogue changes rarely; avoid hitting it
# on every keystroke/render of the agent-editor provider dropdown.
_OR_CACHE_TTL = 600.0
_or_models_cache: dict = {"ts": 0.0, "models": []}


def _is_free(pricing: dict) -> bool:
    try:
        return float(pricing.get("prompt", 1)) == 0.0 and float(pricing.get("completion", 1)) == 0.0
    except (TypeError, ValueError):
        return False


@router.get("/openrouter-models")
async def openrouter_models(_: User = Depends(get_current_user)):
    """Free-tier OpenRouter models (id ends ``:free`` or $0 prompt+completion
    pricing), for the agent-editor model picker. Cached in-process for
    `_OR_CACHE_TTL` seconds."""
    now = time.monotonic()
    if _or_models_cache["models"] and now - _or_models_cache["ts"] < _OR_CACHE_TTL:
        return _or_models_cache["models"]

    settings = get_settings()
    headers = {}
    if settings.openrouter_api_key:
        headers["Authorization"] = f"Bearer {settings.openrouter_api_key}"

    try:
        async with safe_async_client(15.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # network / upstream error — surface, don't cache
        raise APIError(502, "openrouter_unreachable", f"could not reach OpenRouter: {exc}")

    free = [
        {
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "context_length": m.get("context_length"),
        }
        for m in payload.get("data", [])
        if m.get("id") and (str(m["id"]).endswith(":free") or _is_free(m.get("pricing") or {}))
    ]
    free.sort(key=lambda m: m["name"].lower())
    _or_models_cache["ts"] = now
    _or_models_cache["models"] = free
    return free


def _build(model_cls, field_meta: dict, groups: list[dict], defaults: dict) -> dict:
    return {
        "json_schema": model_cls.model_json_schema(),
        "fields": field_meta,
        "groups": sorted(groups, key=lambda g: g["order"]),
        "defaults": defaults,
    }


@router.get("/config-schema/{which}")
async def config_schema(which: str, _: User = Depends(get_current_user)):
    if which == "rag":
        return _build(RagConfig, RAG_FIELD_META, RAG_GROUPS, resolve_rag())
    if which == "orchestration":
        return _build(
            OrchestrationConfig, ORCH_FIELD_META, ORCH_GROUPS, resolve_orch()
        )
    raise APIError(404, "not_found", "unknown config schema: " + which)


@router.get("/patterns")
async def patterns(_: User = Depends(get_current_user)):
    out = []
    for pid, info in PATTERN_INFO.items():
        params = sorted(
            name
            for name, m in ORCH_FIELD_META.items()
            if m["group"] == pid or m["group"] == "shared"
        )
        out.append({"id": pid, "params": params, **info})
    return out


@router.get("/delegatable-agents")
async def delegatable_agents(
    db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    rows = (
        await db.exec(
            select(Agent)
            .where(Agent.is_delegatable == True)  # noqa: E712
            .order_by(Agent.name)
        )
    ).all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "delegate_description": a.delegate_description,
            "provider": a.provider,
            "model": a.model,
        }
        for a in rows
    ]
