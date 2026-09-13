"""Coverage for the Wave 1-5 backend upgrades."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from app.core import attachments, context, hitl
from app.core import agent_runtime as ar
from app.db.models import ChatSession, Message, ToolApproval, Trace
from app.db.session import SessionLocal


async def _new_conversation(auth_client) -> str:
    agents = (await auth_client.get("/api/agents")).json()
    return (
        await auth_client.post("/api/conversations", json={"agent_id": agents[0]["id"]})
    ).json()["id"]


# ── 4a: "always allow this tool for the chat" ──────────────────────────
@pytest.mark.asyncio
async def test_approve_with_remember_creates_grant(auth_client):
    sid = await _new_conversation(auth_client)
    async with SessionLocal() as db:
        msg = Message(session_id=sid, role="assistant", content="", tool_calls=[
            {"id": "tc1", "name": "write_file", "args": {"path": "a.txt", "content": "x"}}
        ])
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        appr = ToolApproval(
            session_id=sid, message_id=msg.id, tool_call_id="tc1",
            tool_name="write_file", tool_args={"path": "a.txt", "content": "x"},
        )
        db.add(appr)
        await db.commit()
        await db.refresh(appr)
        aid = appr.id

    r = await auth_client.post(f"/api/approvals/{aid}/approve?remember=session")
    assert r.status_code == 200

    r = await auth_client.get(f"/api/conversations/{sid}/grants")
    assert r.status_code == 200
    assert [g["tool_name"] for g in r.json()] == ["write_file"]

    async with SessionLocal() as db:
        assert await hitl.has_grant(db, sid, "write_file") is True

    r = await auth_client.delete(f"/api/conversations/{sid}/grants/write_file")
    assert r.status_code == 204
    r = await auth_client.get(f"/api/conversations/{sid}/grants")
    assert r.json() == []


@pytest.mark.asyncio
async def test_grants_dropped_with_conversation(auth_client):
    sid = await _new_conversation(auth_client)
    async with SessionLocal() as db:
        await hitl.add_grant(db, session_id=sid, tool_name="execute_code", user_id=None)
    r = await auth_client.delete(f"/api/conversations/{sid}")
    assert r.status_code == 204
    async with SessionLocal() as db:
        assert await hitl.has_grant(db, sid, "execute_code") is False


@pytest.mark.asyncio
async def test_hard_delete_conversation_with_full_fk_graph(auth_client):
    """DELETE must succeed (204, not 500) even when the conversation has
    messages + tool_approvals + traces + kb_query_logs referencing it."""
    from sqlmodel import select

    from app.db.models import KbQueryLog

    sid = await _new_conversation(auth_client)
    async with SessionLocal() as db:
        m = Message(session_id=sid, role="assistant", content="hi",
                    tool_calls=[{"id": "t1", "name": "x", "args": {}}])
        db.add(m)
        await db.commit()
        await db.refresh(m)
        db.add(ToolApproval(session_id=sid, message_id=m.id, tool_call_id="t1",
                            tool_name="write_file", tool_args={}))
        db.add(Trace(session_id=sid, message_id=m.id, provider="openai", model="x",
                     latency_ms=1))
        db.add(KbQueryLog(session_id=sid, message_id=m.id, collection_ids=[],
                          query_raw="q", queries_used=[], config_snapshot={},
                          stages=[], picked_chunk_ids=[]))
        await db.commit()

    r = await auth_client.delete(f"/api/conversations/{sid}")
    assert r.status_code == 204, r.text

    async with SessionLocal() as db:
        for model in (Message, ToolApproval, Trace, KbQueryLog, ChatSession):
            col = model.id if model is ChatSession else model.session_id
            rows = (await db.exec(select(model).where(col == sid))).all()
            assert rows == [], f"{model.__name__} rows left after delete"

    # deleting again -> 404 (already gone), never 500
    r = await auth_client.delete(f"/api/conversations/{sid}")
    assert r.status_code == 404


# ── 4e: usage aggregation + budget ────────────────────────────────────
@pytest.mark.asyncio
async def test_usage_aggregation_and_budget(auth_client):
    sid = await _new_conversation(auth_client)
    async with SessionLocal() as db:
        for i in range(3):
            db.add(Trace(
                session_id=sid, provider="openai", model="gpt-4.1-mini",
                latency_ms=100, tokens_in=1000, tokens_out=200,
                cached_tokens=400 if i else 0, cost_usd=0.01,
                cache_hit=bool(i), error=None if i < 2 else "boom",
            ))
        await db.commit()

    r = await auth_client.get("/api/admin/usage?group_by=model")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["calls"] == 3
    assert body["totals"]["cost_usd"] == pytest.approx(0.03)
    assert body["totals"]["cached_tokens"] == 800
    assert body["totals"]["errors"] == 1
    assert body["buckets"][0]["bucket"] == "openai/gpt-4.1-mini"

    r = await auth_client.get("/api/admin/usage/budget")
    assert r.status_code == 200
    b = r.json()
    assert b["spent_usd"] == pytest.approx(0.03)
    assert b["budget_usd"] == 0.0
    assert b["pct"] is None


# ── 1a: interrupted-turn resume sweep ────────────────────────────────
@pytest.mark.asyncio
async def test_resume_sweep_targets_stale_running_rows(auth_client, monkeypatch):
    sid = await _new_conversation(auth_client)
    async with SessionLocal() as db:
        s = await db.get(ChatSession, sid)
        s.turn_status = "running"
        s.turn_started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.add(s)
        await db.commit()

    called: list[str] = []

    async def _fake_run_turn(session_id, **kw):
        called.append(session_id)

    monkeypatch.setattr(ar, "run_turn", _fake_run_turn)
    n = await ar.resume_interrupted_turns()
    assert n == 1
    # let the scheduled gather run
    import asyncio
    await asyncio.sleep(0.05)
    assert called == [sid]


@pytest.mark.asyncio
async def test_resume_sweep_ignores_fresh_and_idle(auth_client, monkeypatch):
    sid = await _new_conversation(auth_client)
    async with SessionLocal() as db:
        s = await db.get(ChatSession, sid)
        s.turn_status = "running"
        s.turn_started_at = datetime.now(timezone.utc)  # just started
        db.add(s)
        await db.commit()
    monkeypatch.setattr(ar, "run_turn", lambda *a, **k: None)
    assert await ar.resume_interrupted_turns() == 0


# ── 3c: context trimming ─────────────────────────────────────────────
def test_cut_index_keeps_last_user_and_recent():
    msgs = [
        Message(session_id="s", role="user", content="q1 " * 500),
        Message(session_id="s", role="assistant", content="a1 " * 500),
        Message(session_id="s", role="user", content="q2 short"),
    ]
    cut = context._cut_index(msgs, budget=50)
    assert cut <= 2  # never drops the final user turn
    assert msgs[cut:][-1].content == "q2 short"


@pytest.mark.asyncio
async def test_prepare_fast_path_returns_all(auth_client):
    sid = await _new_conversation(auth_client)
    async with SessionLocal() as db:
        s = await db.get(ChatSession, sid)
        agent_stub = type("A", (), {"model": "gpt-4.1-mini", "max_tokens": 1024, "system_prompt": "hi"})
        msgs = [Message(session_id=sid, role="user", content="hello")]
        fitted, summary = await context.prepare(db, s, agent_stub, msgs)
    assert fitted == msgs
    assert summary is None


# ── 3b: image downscale + data-url cache ─────────────────────────────
def test_shrink_image_downscales_large_png():
    pytest.importorskip("PIL")
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4000, 3000), (10, 20, 30)).save(buf, format="PNG")
    original = buf.getvalue()
    shrunk, ct = attachments._shrink_image(original, "image/png")
    assert len(shrunk) < len(original)
    assert ct in ("image/jpeg", "image/png")
    with Image.open(io.BytesIO(shrunk)) as im:
        assert max(im.size) <= attachments._IMAGE_MAX_EDGE


def test_data_url_is_cached(tmp_path, monkeypatch):
    import app.core.attachments as a

    monkeypatch.setattr(a, "_root", lambda: tmp_path)
    sess = "sess1"
    (tmp_path / sess).mkdir()
    (tmp_path / sess / "x.txt").write_bytes(b"hello")
    meta = {"stored_name": "x.txt", "content_type": "text/plain"}
    a._data_url_cached.cache_clear()
    first = a.data_url(sess, meta)
    (tmp_path / sess / "x.txt").write_bytes(b"CHANGED")
    second = a.data_url(sess, meta)
    assert first == second  # served from cache, not re-read
    assert first.startswith("data:text/plain;base64,")
