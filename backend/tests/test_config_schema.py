"""Phase 15a — Config Schema API + metadata sidecar (SPEC §17)."""

from __future__ import annotations

import pytest

from app.core.orchestration.config import OrchestrationConfig
from app.core.orchestration.config import resolve_config as resolve_orch
from app.core.orchestration.meta import check_field_metadata as check_orch_meta
from app.rag.config_meta import check_field_metadata as check_rag_meta


def test_every_rag_field_has_metadata():
    problems = check_rag_meta()
    assert problems == [], problems


def test_every_orchestration_field_has_metadata():
    problems = check_orch_meta()
    assert problems == [], problems


def test_orchestration_cascade_env_agent_request():
    base = resolve_orch()
    assert base["pattern"] == "react"
    assert base["max_attempts"] == 3
    out = resolve_orch(
        agent={"pattern": "reflexion", "max_attempts": 2},
        request={"max_attempts": 5},
    )
    assert out["pattern"] == "reflexion"
    assert out["max_attempts"] == 5  # request wins over agent
    assert out["success_score"] == 0.8  # untouched -> env


def test_orchestration_rejects_unknown_pattern():
    with pytest.raises(Exception):
        OrchestrationConfig.model_validate({"pattern": "totally_made_up"})


def test_orchestration_rejects_unknown_key():
    with pytest.raises(Exception):
        OrchestrationConfig.model_validate({"nope": 1})


@pytest.mark.asyncio
async def test_config_schema_endpoints(auth_client):
    for which in ("rag", "orchestration"):
        r = await auth_client.get(f"/api/meta/config-schema/{which}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "json_schema" in body
        assert body["fields"]
        assert body["groups"]
        assert body["defaults"]

    r = await auth_client.get("/api/meta/config-schema/nonsense")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patterns_endpoint(auth_client):
    r = await auth_client.get("/api/meta/patterns")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert {"react", "reflexion", "supervisor", "debate"} <= ids
    sup = next(p for p in r.json() if p["id"] == "supervisor")
    assert sup["requires_delegation"] is True


@pytest.mark.asyncio
async def test_delegatable_agents_endpoint(auth_client):
    r = await auth_client.get("/api/meta/delegatable-agents")
    assert r.status_code == 200
    assert r.json() == []

    r = await auth_client.post(
        "/api/agents",
        json={
            "name": "Coder",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "is_delegatable": True,
            "delegate_description": "writes code",
        },
    )
    assert r.status_code == 201, r.text
    r = await auth_client.get("/api/meta/delegatable-agents")
    assert [a["name"] for a in r.json()] == ["Coder"]


@pytest.mark.asyncio
async def test_agent_rejects_bad_orchestration(auth_client):
    r = await auth_client.post(
        "/api/agents",
        json={
            "name": "Bad",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "orchestration": {"pattern": "nonsense"},
        },
    )
    assert r.status_code == 422

    r = await auth_client.post(
        "/api/agents",
        json={
            "name": "Sup",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "orchestration": {"pattern": "supervisor"},
        },
    )
    assert r.status_code == 422
