"""Desktop overlay — global notification channel (Phase 20b, SPEC §21.7)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocketDisconnect
from sqlmodel import select

from app.api import routes_notifications
from app.core import hitl
from app.core.agent_runtime import resolve_approval
from app.core.notify import NotificationHub, clip, hub, notify
from app.core.security import issue_token
from app.db.models import Agent, ChatSession, CronJob, Message, User
from app.db.session import SessionLocal
from app.scheduler import cron_jobs


class FakeWS:
    """Just enough of a WebSocket for the hub and the endpoint."""

    def __init__(self, cookies: dict | None = None, *, fail_send: bool = False):
        self.cookies = cookies or {}
        self.sent: list[dict] = []
        self.accepted = False
        self.closed_code: int | None = None
        self._fail_send = fail_send
        self._inbox: asyncio.Queue = asyncio.Queue()

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000):
        self.closed_code = code

    async def send_json(self, data):
        if self._fail_send:
            raise RuntimeError("client vanished")
        self.sent.append(data)

    async def receive_text(self):
        item = await self._inbox.get()
        if item is WebSocketDisconnect:
            raise WebSocketDisconnect()
        return item

    def disconnect_client(self):
        self._inbox.put_nowait(WebSocketDisconnect)

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


async def _user_id() -> str:
    async with SessionLocal() as db:
        return (await db.exec(select(User))).first().id


async def _listen(user_id: str) -> FakeWS:
    ws = FakeWS()
    await hub.connect(user_id, ws)
    return ws


@pytest.fixture(autouse=True)
def _clean_hub():
    hub._conns.clear()
    yield
    hub._conns.clear()


# ── hub ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_hub_delivers_only_to_the_owner():
    h = NotificationHub()
    mine, theirs = FakeWS(), FakeWS()
    await h.connect("u1", mine)
    await h.connect("u2", theirs)
    await h.emit("u1", {"type": "x"})
    assert mine.sent == [{"type": "x"}]
    assert theirs.sent == []


@pytest.mark.asyncio
async def test_hub_drops_a_dead_client_and_keeps_going():
    h = NotificationHub()
    dead, alive = FakeWS(fail_send=True), FakeWS()
    await h.connect("u", dead)
    await h.connect("u", alive)
    await h.emit("u", {"type": "x"})
    assert alive.sent == [{"type": "x"}]
    assert dead not in h._conns["u"]


@pytest.mark.asyncio
async def test_notify_never_raises(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("hub exploded")

    monkeypatch.setattr(hub, "emit", boom)
    await notify("u", {"type": "x"})  # must not raise
    await notify(None, {"type": "x"})  # no user -> no-op


def test_clip_cuts_and_collapses_whitespace():
    assert clip("a  b\n c") == "a b c"
    out = clip("x" * 500)
    assert len(out) == 200 and out.endswith("…")


# ── endpoint ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ws_rejects_missing_or_bad_cookie_with_4401(auth_client):
    for cookies in ({}, {"pagi_session": "not-a-token"}):
        ws = FakeWS(cookies)
        await routes_notifications.ws_notifications(ws)
        assert ws.closed_code == 4401
        assert ws.accepted is False


@pytest.mark.asyncio
async def test_ws_sends_hello_with_pending_count_then_streams_events(auth_client):
    uid = await _user_id()
    ws = FakeWS({"pagi_session": issue_token(uid)})
    task = asyncio.create_task(routes_notifications.ws_notifications(ws))
    for _ in range(50):
        if ws.sent:
            break
        await asyncio.sleep(0.01)
    assert ws.sent[0] == {"type": "hello", "pending_approvals": 0}

    await notify(uid, {"type": "cron_run_finished", "job_id": "j"})
    assert ws.types() == ["hello", "cron_run_finished"]

    ws.disconnect_client()
    await asyncio.wait_for(task, timeout=2)
    assert not hub.has_listeners(uid)  # cleaned up on disconnect


@pytest.mark.asyncio
async def test_ws_pings_periodically(auth_client, monkeypatch):
    monkeypatch.setattr(routes_notifications, "PING_INTERVAL_S", 0.05)
    uid = await _user_id()
    ws = FakeWS({"pagi_session": issue_token(uid)})
    task = asyncio.create_task(routes_notifications.ws_notifications(ws))
    await asyncio.sleep(0.25)
    ws.disconnect_client()
    await asyncio.wait_for(task, timeout=2)
    assert ws.types().count("ping") >= 2


# ── approvals ────────────────────────────────────────────────────────────
async def _session_with_message(uid: str) -> tuple[str, str]:
    async with SessionLocal() as db:
        agent = (await db.exec(select(Agent))).first()
        chat = ChatSession(user_id=uid, agent_id=agent.id)
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        msg = Message(session_id=chat.id, role="assistant", content="")
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return chat.id, msg.id


@pytest.mark.asyncio
async def test_approval_pending_then_resolved_notifications(auth_client):
    uid = await _user_id()
    ws = await _listen(uid)
    sid, mid = await _session_with_message(uid)

    async with SessionLocal() as db:
        approval = await hitl.get_or_create_approval(
            db, session_id=sid, message_id=mid, tool_call_id="tc1",
            tool_name="execute_code", tool_args={"code": "print(1)" + "x" * 500},
        )
        # resumed turn hits the same tool call again -> must NOT notify twice
        await hitl.get_or_create_approval(
            db, session_id=sid, message_id=mid, tool_call_id="tc1",
            tool_name="execute_code", tool_args={},
        )
    assert ws.types() == ["approval_pending"]
    ev = ws.sent[0]
    assert ev["approval_id"] == approval.id and ev["session_id"] == sid
    assert ev["tool_name"] == "execute_code"
    assert len(ev["args_preview"]) <= 200  # a preview, never the full args

    async with SessionLocal() as db:
        await resolve_approval(db, approval.id, "approve", uid)
    assert ws.types() == ["approval_pending", "approval_resolved"]
    assert ws.sent[1]["status"] == "approved"


@pytest.mark.asyncio
async def test_approval_resolved_via_rest_also_notifies(auth_client, monkeypatch):
    # REST /approve on an approval nobody is awaiting spawns a background turn;
    # stub it out — only the notification is under test here.
    monkeypatch.setattr("app.core.agent_runtime.run_turn", _noop_turn, raising=False)
    uid = await _user_id()
    ws = await _listen(uid)
    sid, mid = await _session_with_message(uid)
    async with SessionLocal() as db:
        approval = await hitl.get_or_create_approval(
            db, session_id=sid, message_id=mid, tool_call_id="tc2",
            tool_name="write_file", tool_args={"path": "a"},
        )
    r = await auth_client.post(f"/api/approvals/{approval.id}/deny")
    assert r.status_code == 200
    await asyncio.sleep(0.05)
    assert "approval_resolved" in ws.types()
    assert ws.sent[-1]["status"] == "denied"


async def _noop_turn(*_a, **_k):
    return None


@pytest.mark.asyncio
async def test_other_users_do_not_receive_approval_events(auth_client):
    uid = await _user_id()
    stranger = FakeWS()
    await hub.connect("someone-else", stranger)
    sid, mid = await _session_with_message(uid)
    async with SessionLocal() as db:
        await hitl.get_or_create_approval(
            db, session_id=sid, message_id=mid, tool_call_id="tc3",
            tool_name="execute_code", tool_args={},
        )
    assert stranger.sent == []


@pytest.mark.asyncio
async def test_a_broken_hub_does_not_break_approval_creation(auth_client, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("hub exploded")

    monkeypatch.setattr(hub, "emit", boom)
    uid = await _user_id()
    sid, mid = await _session_with_message(uid)
    async with SessionLocal() as db:
        approval = await hitl.get_or_create_approval(
            db, session_id=sid, message_id=mid, tool_call_id="tc4",
            tool_name="execute_code", tool_args={},
        )
        assert approval.status == "pending"  # the row was still created
        await hitl.mark_resolved(db, approval, status="denied", user_id=uid)
        assert approval.status == "denied"


@pytest.mark.asyncio
async def test_expired_approvals_notify_resolved(auth_client):
    from datetime import datetime, timedelta, timezone

    uid = await _user_id()
    sid, mid = await _session_with_message(uid)
    async with SessionLocal() as db:
        approval = await hitl.get_or_create_approval(
            db, session_id=sid, message_id=mid, tool_call_id="tc5",
            tool_name="execute_code", tool_args={},
        )
        approval.created_at = datetime.now(timezone.utc) - timedelta(days=365)
        db.add(approval)
        await db.commit()
    ws = await _listen(uid)
    assert await cron_jobs.sweep_expired_approvals() == 1
    assert ws.types() == ["approval_resolved"] and ws.sent[0]["status"] == "expired"


# ── cron ─────────────────────────────────────────────────────────────────
async def _cron_session(uid: str, *, assistant_text: str | None) -> str:
    # a bare session (no pre-seeded messages): the only assistant row is the one
    # under test, so "latest assistant message" is unambiguous even when two rows
    # would share a created_at tick
    async with SessionLocal() as db:
        agent = (await db.exec(select(Agent))).first()
        chat = ChatSession(user_id=uid, agent_id=agent.id)
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        sid = chat.id
        if assistant_text is not None:
            db.add(Message(session_id=sid, role="assistant", content=assistant_text))
            await db.commit()
    return sid


@pytest.mark.asyncio
async def test_cron_run_notifies_ok_with_clipped_summary(auth_client, monkeypatch):
    uid = await _user_id()
    ws = await _listen(uid)
    sid = await _cron_session(uid, assistant_text="done. " + "y" * 500)
    monkeypatch.setattr(cron_jobs, "run_turn", _noop_turn)

    await cron_jobs._run_cron_turn("job1", "nightly", sid, uid, [])

    assert ws.types() == ["cron_run_finished"]
    ev = ws.sent[0]
    assert (ev["job_id"], ev["job_name"], ev["session_id"], ev["status"]) == ("job1", "nightly", sid, "ok")
    assert len(ev["summary"]) <= 200


@pytest.mark.asyncio
async def test_cron_run_notifies_error_when_turn_raises_or_is_empty(auth_client, monkeypatch):
    uid = await _user_id()
    ws = await _listen(uid)

    async def boom(*_a, **_k):
        raise RuntimeError("provider down")

    sid = await _cron_session(uid, assistant_text=None)
    monkeypatch.setattr(cron_jobs, "run_turn", boom)
    await cron_jobs._run_cron_turn("job1", "nightly", sid, uid, [])

    monkeypatch.setattr(cron_jobs, "run_turn", _noop_turn)  # ran, but no reply stored
    await cron_jobs._run_cron_turn("job1", "nightly", sid, uid, [])

    assert [m["status"] for m in ws.sent] == ["error", "error"]


@pytest.mark.asyncio
async def test_cron_run_now_route_notifies(auth_client, monkeypatch):
    uid = await _user_id()
    ws = await _listen(uid)
    agent_id = (await auth_client.get("/api/agents")).json()[0]["id"]

    async def fake_turn(session_id, **_k):
        async with SessionLocal() as db:
            db.add(Message(session_id=session_id, role="assistant", content="all good"))
            await db.commit()

    monkeypatch.setattr(cron_jobs, "run_turn", fake_turn)
    job = (
        await auth_client.post(
            "/api/cron-jobs",
            json={"agent_id": agent_id, "name": "j", "schedule": "0 3 * * *", "prompt": "hi"},
        )
    ).json()
    r = await auth_client.post(f"/api/cron-jobs/{job['id']}/run-now")
    assert r.status_code == 202
    for _ in range(100):
        if ws.sent:
            break
        await asyncio.sleep(0.02)
    assert ws.types() == ["cron_run_finished"]
    assert ws.sent[0]["status"] == "ok" and ws.sent[0]["summary"] == "all good"


@pytest.mark.asyncio
async def test_unattended_mode_never_leaves_ask_tools_to_approve():
    """SPEC §21.7: cron cannot raise approval_pending — unattended drops every
    `ask` tool that isn't explicitly allowed, so no approval row can exist."""
    from app.tools.base import resolve_agent_tools

    specs, policy = resolve_agent_tools(["execute_code"], {}, unattended=True, unattended_allowed=[])
    assert [s.name for s in specs] == []
    assert "execute_code" not in policy


def test_cron_job_model_unchanged_for_kind():
    # the project branch of run_cron_job must still be reachable
    assert CronJob(agent_id="a", name="n", schedule="* * * * *", prompt="p").kind == "prompt"
