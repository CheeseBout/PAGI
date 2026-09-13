from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Message, ToolApproval
from app.db.session import SessionLocal
from app.scheduler.cron_jobs import sweep_expired_approvals


@pytest.mark.asyncio
async def test_sweep_expires_old_pending_approvals(auth_client):
    agents = (await auth_client.get("/api/agents")).json()
    agent_id = agents[0]["id"]
    sid = (await auth_client.post("/api/conversations", json={"agent_id": agent_id})).json()["id"]

    async with SessionLocal() as db:
        msg = Message(
            session_id=sid, role="assistant", content=None,
            tool_calls=[{"id": "tc1", "name": "execute_code", "args": {}}],
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)

        stale = datetime.now(timezone.utc) - timedelta(hours=100)  # default timeout is 24h
        approval = ToolApproval(
            session_id=sid, message_id=msg.id, tool_call_id="tc1", tool_name="execute_code",
            tool_args={}, status="pending", created_at=stale,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)
        approval_id = approval.id

    swept = await sweep_expired_approvals()
    assert swept == 1

    r = await auth_client.get("/api/approvals?status=expired")
    ids = [a["id"] for a in r.json()]
    assert approval_id in ids

    # a second sweep finds nothing left to expire
    assert await sweep_expired_approvals() == 0


@pytest.mark.asyncio
async def test_sweep_leaves_recent_pending_approvals_alone(auth_client):
    agents = (await auth_client.get("/api/agents")).json()
    agent_id = agents[0]["id"]
    sid = (await auth_client.post("/api/conversations", json={"agent_id": agent_id})).json()["id"]

    async with SessionLocal() as db:
        msg = Message(
            session_id=sid, role="assistant", content=None,
            tool_calls=[{"id": "tc1", "name": "write_file", "args": {}}],
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        approval = ToolApproval(
            session_id=sid, message_id=msg.id, tool_call_id="tc1", tool_name="write_file",
            tool_args={}, status="pending",
        )
        db.add(approval)
        await db.commit()

    assert await sweep_expired_approvals() == 0
    r = await auth_client.get("/api/approvals?status=pending")
    assert len(r.json()) == 1
