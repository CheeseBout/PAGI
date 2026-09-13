import pytest

from app.core.agent_runtime import NotFound, truncate_after
from app.db.models import Message
from app.db.session import SessionLocal


@pytest.mark.asyncio
async def test_truncate_after_drops_everything_past_the_anchor(auth_client):
    agents = (await auth_client.get("/api/agents")).json()
    agent_id = agents[0]["id"]
    sid = (await auth_client.post("/api/conversations", json={"agent_id": agent_id})).json()["id"]

    ids = []
    async with SessionLocal() as db:
        for role, content in [("user", "hi"), ("assistant", "hello"), ("user", "more")]:
            m = Message(session_id=sid, role=role, content=content)
            db.add(m)
            await db.commit()
            await db.refresh(m)
            ids.append(m.id)

    async with SessionLocal() as db:
        await truncate_after(db, sid, ids[0])  # keep only the first "hi"

    r = await auth_client.get(f"/api/conversations/{sid}")
    contents = [m["content"] for m in r.json()["messages"]]
    assert contents == ["hi"]


@pytest.mark.asyncio
async def test_truncate_after_noop_when_anchor_is_last(auth_client):
    agents = (await auth_client.get("/api/agents")).json()
    agent_id = agents[0]["id"]
    sid = (await auth_client.post("/api/conversations", json={"agent_id": agent_id})).json()["id"]

    async with SessionLocal() as db:
        m = Message(session_id=sid, role="user", content="only one")
        db.add(m)
        await db.commit()
        await db.refresh(m)
        mid = m.id

    async with SessionLocal() as db:
        await truncate_after(db, sid, mid)

    r = await auth_client.get(f"/api/conversations/{sid}")
    assert [m["content"] for m in r.json()["messages"]] == ["only one"]


@pytest.mark.asyncio
async def test_truncate_after_unknown_message_raises(auth_client):
    agents = (await auth_client.get("/api/agents")).json()
    agent_id = agents[0]["id"]
    sid = (await auth_client.post("/api/conversations", json={"agent_id": agent_id})).json()["id"]

    async with SessionLocal() as db:
        db.add(Message(session_id=sid, role="user", content="hi"))
        await db.commit()

    async with SessionLocal() as db:
        with pytest.raises(NotFound):
            await truncate_after(db, sid, "does-not-exist")
