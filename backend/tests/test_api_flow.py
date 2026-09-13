import pytest

from app.db.models import Message, Trace
from app.db.session import SessionLocal


@pytest.mark.asyncio
async def test_health_no_auth(client):
    r = await client.get("/api/admin/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_requires_auth(client):
    r = await client.get("/api/agents")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_login_bad_credentials(client):
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_then_list_seeded_agents(auth_client):
    r = await auth_client.get("/api/agents")
    assert r.status_code == 200
    agents = r.json()
    providers = {a["provider"] for a in agents}
    assert providers == {"openai", "anthropic", "gemini", "openrouter"}


@pytest.mark.asyncio
async def test_create_conversation_and_fetch(auth_client):
    agents = (await auth_client.get("/api/agents")).json()
    default = next(a for a in agents if a["is_default"])
    r = await auth_client.post("/api/conversations", json={"agent_id": default["id"]})
    assert r.status_code == 201
    sid = r.json()["id"]

    r = await auth_client.get(f"/api/conversations/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["id"] == sid
    assert body["messages"] == []

    r = await auth_client.get("/api/conversations")
    assert any(c["id"] == sid for c in r.json())


@pytest.mark.asyncio
async def test_pending_approvals_empty(auth_client):
    r = await auth_client.get("/api/approvals?status=pending")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_conversation_messages_carry_cost_and_latency(auth_client):
    """GET /conversations/{id} enriches each assistant message with the
    cost_usd/latency_ms of the `chat`-kind Trace row that produced it (Phase 4
    observability UI) — tokens_in/out already lived on Message itself."""
    agents = (await auth_client.get("/api/agents")).json()
    sid = (
        await auth_client.post("/api/conversations", json={"agent_id": agents[0]["id"]})
    ).json()["id"]

    async with SessionLocal() as db:
        msg = Message(session_id=sid, role="assistant", content="hi", tokens_in=12, tokens_out=34)
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        db.add(Trace(
            session_id=sid, message_id=msg.id, provider="openai", model="gpt-4.1-mini",
            latency_ms=456, cost_usd=0.0123, kind="chat",
        ))
        # a non-chat trace on the same message must not be picked up instead
        db.add(Trace(
            session_id=sid, message_id=msg.id, provider="openai", model="gpt-4.1-mini",
            latency_ms=1, cost_usd=99.0, kind="summary",
        ))
        await db.commit()
        mid = msg.id

    r = await auth_client.get(f"/api/conversations/{sid}")
    assert r.status_code == 200
    out = next(m for m in r.json()["messages"] if m["id"] == mid)
    assert out["tokens_in"] == 12
    assert out["tokens_out"] == 34
    assert out["cost_usd"] == pytest.approx(0.0123)
    assert out["latency_ms"] == 456


@pytest.mark.asyncio
async def test_conversation_message_without_trace_omits_cost_fields(auth_client):
    agents = (await auth_client.get("/api/agents")).json()
    sid = (
        await auth_client.post("/api/conversations", json={"agent_id": agents[0]["id"]})
    ).json()["id"]
    async with SessionLocal() as db:
        db.add(Message(session_id=sid, role="assistant", content="hi"))
        await db.commit()

    r = await auth_client.get(f"/api/conversations/{sid}")
    out = r.json()["messages"][0]
    assert "cost_usd" not in out
    assert "latency_ms" not in out
