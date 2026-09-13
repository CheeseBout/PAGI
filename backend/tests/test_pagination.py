from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Message
from app.db.session import SessionLocal


async def _new_conversation(auth_client) -> tuple[str, str]:
    agents = (await auth_client.get("/api/agents")).json()
    agent_id = agents[0]["id"]
    sid = (await auth_client.post("/api/conversations", json={"agent_id": agent_id})).json()["id"]
    return sid, agent_id


@pytest.mark.asyncio
async def test_message_cursor_pagination(auth_client):
    sid, _ = await _new_conversation(auth_client)

    async with SessionLocal() as db:
        base = datetime.now(timezone.utc)
        for i in range(5):
            db.add(
                Message(
                    session_id=sid, role="user" if i % 2 == 0 else "assistant",
                    content=f"msg{i}", created_at=base + timedelta(seconds=i),
                )
            )
        await db.commit()

    r = await auth_client.get(f"/api/conversations/{sid}?limit=2")
    assert r.status_code == 200
    page1 = r.json()["messages"]
    assert [m["content"] for m in page1] == ["msg3", "msg4"]

    before_id = page1[0]["id"]
    r2 = await auth_client.get(f"/api/conversations/{sid}?limit=2&before={before_id}")
    assert r2.status_code == 200
    page2 = r2.json()["messages"]
    assert [m["content"] for m in page2] == ["msg1", "msg2"]


@pytest.mark.asyncio
async def test_message_pagination_bad_cursor(auth_client):
    sid, _ = await _new_conversation(auth_client)
    r = await auth_client.get(f"/api/conversations/{sid}?before=does-not-exist")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_conversation_list_cursor_pagination(auth_client):
    _, agent_id = await _new_conversation(auth_client)
    for _ in range(2):
        await auth_client.post("/api/conversations", json={"agent_id": agent_id})

    r = await auth_client.get("/api/conversations?limit=2")
    assert r.status_code == 200
    page1 = r.json()
    assert len(page1) == 2

    before = page1[-1]["updated_at"]
    r2 = await auth_client.get(f"/api/conversations?limit=2&before={before}")
    assert r2.status_code == 200
    page2 = r2.json()
    page1_ids = {c["id"] for c in page1}
    assert all(c["id"] not in page1_ids for c in page2)
