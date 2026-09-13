"""Phase 13 — sub-agent / delegation (SPEC §15).

The tests bind to the six break points from SPEC §15.6 plus the ceilings — all
of them are silent-failure bugs if they regress.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core import delegation
from app.core.ws_manager import manager
from app.db.models import Agent, ChatSession, Message
from app.db.session import SessionLocal
from app.providers import DoneEvent, TextDelta, ToolCallComplete


class ScriptedProvider:
    """Dispatches on model string + whether the last turn is a tool result."""

    def __init__(self, model: str):
        self.model = model

    async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
        last = messages[-1] if messages else {}
        is_tool_turn = last.get("role") == "tool"
        if model == "boss-model":
            if not is_tool_turn:
                yield ToolCallComplete(
                    tool_call_id="tc-del-1",
                    name="delegate_task",
                    args={"agent": "Worker", "task": "compute the answer"},
                )
                yield DoneEvent(finish_reason="tool_calls")
            else:
                yield TextDelta("boss synthesised: 42")
                yield DoneEvent(finish_reason="stop")
        elif model == "worker-model":
            yield TextDelta("worker says 42")
            yield DoneEvent(finish_reason="stop")
        elif model == "worker-approve-model":
            if not is_tool_turn:
                yield ToolCallComplete(
                    tool_call_id="tc-exec-1",
                    name="execute_code",
                    args={"language": "python", "code": "print(42)"},
                )
                yield DoneEvent(finish_reason="tool_calls")
            else:
                yield TextDelta("worker ran code: 42")
                yield DoneEvent(finish_reason="stop")
        else:
            yield TextDelta("generic")
            yield DoneEvent(finish_reason="stop")


@pytest.fixture
def scripted(monkeypatch):
    monkeypatch.setattr(
        "app.core.agent_runtime.get_provider", lambda name: ScriptedProvider(name)
    )
    monkeypatch.setattr(
        "app.core.agent_runtime.provider_supports_vision", lambda *a, **k: True
    )


async def _mk_agents(worker_model="worker-model", worker_tools=None):
    async with SessionLocal() as db:
        boss = Agent(
            name="Boss", provider="openai", model="boss-model",
            tools_allowed=["delegate_task", "read_file"],
        )
        worker = Agent(
            name="Worker", provider="openai", model=worker_model,
            tools_allowed=worker_tools if worker_tools is not None else ["read_file"],
            is_delegatable=True, delegate_description="does the compute",
        )
        db.add(boss)
        db.add(worker)
        await db.commit()
        await db.refresh(boss)
        await db.refresh(worker)
        return boss.id, worker.id


async def _mk_root(user_id: str, agent_id: str) -> str:
    async with SessionLocal() as db:
        s = ChatSession(user_id=user_id, agent_id=agent_id)
        db.add(s)
        await db.commit()
        await db.refresh(s)
        db.add(Message(session_id=s.id, role="user", content="what is the answer?"))
        await db.commit()
        return s.id


async def _user_id() -> str:
    from app.db.models import User

    async with SessionLocal() as db:
        u = (await db.exec(__import__("sqlmodel").select(User))).first()
        return u.id


# ── happy path: boss delegates, worker answers, boss synthesises ──────────
@pytest.mark.asyncio
async def test_delegation_happy_path(auth_client, scripted):
    from app.core.agent_runtime import run_turn

    uid = await _user_id()
    boss_id, _ = await _mk_agents()
    root = await _mk_root(uid, boss_id)

    await run_turn(root, wait_for_approval=True)

    async with SessionLocal() as db:
        from sqlmodel import select

        msgs = (
            await db.exec(select(Message).where(Message.session_id == root).order_by(Message.created_at))
        ).all()
        # boss produced a final synthesised answer
        assert any("synthesised" in (m.content or "") for m in msgs)
        # a sub-agent session was created under this root
        kids = (
            await db.exec(select(ChatSession).where(ChatSession.root_session_id == root))
        ).all()
        assert len(kids) == 1
        assert kids[0].kind == "subagent"
        assert kids[0].parent_session_id == root
        assert kids[0].depth == 1
        assert kids[0].spawned_by_tool_call_id == "tc-del-1"


# ── (fix 4) resume idempotency: same tool_call_id -> same child ──────────
@pytest.mark.asyncio
async def test_delegate_is_idempotent_on_tool_call_id(auth_client, scripted):
    uid = await _user_id()
    boss_id, _ = await _mk_agents()
    root = await _mk_root(uid, boss_id)

    r1 = await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-X",
        worker_name="Worker", task="do it",
    )
    r2 = await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-X",
        worker_name="Worker", task="do it",
    )
    assert r1["child_session_id"] == r2["child_session_id"]

    async with SessionLocal() as db:
        from sqlmodel import select

        kids = (
            await db.exec(select(ChatSession).where(ChatSession.root_session_id == root))
        ).all()
        assert len(kids) == 1  # NOT two


# ── (fix 5) sub-agent sessions never show in the sidebar ────────────────
@pytest.mark.asyncio
async def test_subagent_sessions_hidden_from_conversation_list(auth_client, scripted):
    uid = await _user_id()
    boss_id, _ = await _mk_agents()
    root = await _mk_root(uid, boss_id)
    await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-Y", worker_name="Worker", task="x",
    )

    listing = (await auth_client.get("/api/conversations")).json()
    ids = {c["id"] for c in listing}
    assert root in ids
    async with SessionLocal() as db:
        from sqlmodel import select

        child = (
            await db.exec(select(ChatSession).where(ChatSession.kind == "subagent"))
        ).first()
    assert child.id not in ids


# ── /conversations/{id}/tree ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_conversation_tree_endpoint(auth_client, scripted):
    uid = await _user_id()
    boss_id, _ = await _mk_agents()
    root = await _mk_root(uid, boss_id)
    await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-Z", worker_name="Worker", task="x",
    )
    tree = (await auth_client.get(f"/api/conversations/{root}/tree")).json()
    assert tree["root"] == root
    assert len(tree["nodes"]) == 1
    assert tree["nodes"][0]["agent_name"] == "Worker"


# ── (fix 6) usage rolls sub-agent traces up + splits origin ────────────
@pytest.mark.asyncio
async def test_usage_splits_subagent_origin(auth_client, scripted):
    from app.db.models import Trace

    uid = await _user_id()
    boss_id, _ = await _mk_agents()
    root = await _mk_root(uid, boss_id)
    r = await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-U", worker_name="Worker", task="x",
    )
    child_id = r["child_session_id"]
    async with SessionLocal() as db:
        db.add(Trace(session_id=root, provider="openai", model="m", latency_ms=1, cost_usd=0.10))
        db.add(Trace(session_id=child_id, provider="openai", model="m", latency_ms=1, cost_usd=0.02))
        await db.commit()

    rep = (await auth_client.get("/api/admin/usage?group_by=session")).json()
    origin = {o["origin"]: o for o in rep["by_origin"]}
    assert origin["subagent"]["cost_usd"] == pytest.approx(0.02)
    assert origin["chat"]["cost_usd"] >= 0.10
    # group_by=session rolls the child's trace up to the root bucket
    root_bucket = next(b for b in rep["buckets"] if b["bucket"] == root)
    assert root_bucket["cost_usd"] == pytest.approx(0.12, abs=1e-6)


# ── (ceiling) depth exceeded -> error object, not a raise ───────────────
@pytest.mark.asyncio
async def test_depth_exceeded_returns_error_object(auth_client, scripted, monkeypatch):
    from app.config import get_settings

    real = get_settings()
    monkeypatch.setattr(
        "app.core.delegation.get_settings",
        lambda: real.model_copy(update={"max_subagent_depth": 1}),
    )

    uid = await _user_id()
    boss_id, worker_id = await _mk_agents()
    root = await _mk_root(uid, boss_id)
    # a depth-1 child
    r = await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-a", worker_name="Worker", task="x",
    )
    child_id = r["child_session_id"]
    # delegating again from the depth-1 child would be depth 2 > max 1
    out = await delegation.run_delegated(
        parent_session_id=child_id, tool_call_id="tc-b", worker_name="Worker", task="y",
    )
    assert out["error"] == "depth_exceeded"


# ── (ceiling) self-delegation rejected ─────────────────────────────────
@pytest.mark.asyncio
async def test_self_delegation_rejected(auth_client, scripted):
    uid = await _user_id()
    async with SessionLocal() as db:
        a = Agent(
            name="Solo", provider="openai", model="worker-model",
            tools_allowed=["delegate_task"], is_delegatable=True,
            delegate_description="itself",
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        aid = a.id
    root = await _mk_root(uid, aid)
    out = await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-s", worker_name="Solo", task="loop",
    )
    assert out["error"] == "self_delegation"


# ── not-delegatable agent rejected ────────────────────────────────────
@pytest.mark.asyncio
async def test_non_delegatable_rejected(auth_client, scripted):
    uid = await _user_id()
    async with SessionLocal() as db:
        boss = Agent(name="Boss", provider="openai", model="boss-model",
                     tools_allowed=["delegate_task"])
        plain = Agent(name="Plain", provider="openai", model="worker-model")
        db.add(boss)
        db.add(plain)
        await db.commit()
        await db.refresh(boss)
        bid = boss.id
    root = await _mk_root(uid, bid)
    out = await delegation.run_delegated(
        parent_session_id=root, tool_call_id="tc-n", worker_name="Plain", task="x",
    )
    assert out["error"] == "agent_not_delegatable"


# ── (fix 3) abort at the root cancels child turns ─────────────────────
@pytest.mark.asyncio
async def test_abort_cascades_to_children():
    async def _never():
        await asyncio.sleep(100)

    root_task = asyncio.ensure_future(_never())
    child_task = asyncio.ensure_future(_never())
    manager.set_task("root-s", root_task)
    manager.set_task("child-s", child_task)
    manager.register_child("root-s", "child-s", agent_name="W", depth=1)

    assert manager.abort("root-s") is True
    await asyncio.sleep(0.01)
    assert root_task.cancelled()
    assert child_task.cancelled()
    manager.forget_child("child-s")


# ── (fix 1) sub-agent events bubble to the root, wrapped ──────────────
@pytest.mark.asyncio
async def test_events_bubble_to_root(monkeypatch):
    from app.core import agent_runtime

    seen: list = []

    async def _capture(session_id, event):
        seen.append((session_id, event))

    monkeypatch.setattr(agent_runtime.manager, "broadcast", _capture)
    manager.register_child("root-b", "child-b", agent_name="Coder", depth=1)
    try:
        await agent_runtime._emit("child-b", {"type": "token", "content": "hi"})
    finally:
        manager.forget_child("child-b")

    assert seen == [
        (
            "root-b",
            {
                "type": "subagent",
                "sub_session_id": "child-b",
                "agent_name": "Coder",
                "depth": 1,
                "event": {"type": "token", "content": "hi"},
            },
        )
    ]


# ── (fix 2) approve a sub-agent's approval from the owner ─────────────
@pytest.mark.asyncio
async def test_subagent_approval_is_owned_by_the_user(auth_client, scripted):
    from app.core.agent_runtime import run_turn

    uid = await _user_id()
    boss_id, worker_id = await _mk_agents(
        worker_model="worker-approve-model",
        worker_tools=["execute_code", "read_file"],
    )
    # give the boss execute_code too so the intersection keeps it for the worker
    async with SessionLocal() as db:
        boss = await db.get(Agent, boss_id)
        boss.tools_allowed = ["delegate_task", "execute_code", "read_file"]
        db.add(boss)
        await db.commit()

    root = await _mk_root(uid, boss_id)

    # run the boss turn in the background — it will block on the worker's approval
    task = asyncio.ensure_future(run_turn(root, wait_for_approval=True))
    # give it a moment to reach the pending approval
    for _ in range(50):
        await asyncio.sleep(0.05)
        pend = (await auth_client.get("/api/approvals?status=pending")).json()
        if pend:
            break
    assert pend, "sub-agent never raised an approval"
    ap = pend[0]
    assert ap.get("agent_name") == "Worker"
    assert ap.get("sub_session_id")

    r = await auth_client.post(f"/api/approvals/{ap['id']}/approve")
    assert r.status_code == 200, r.text

    await asyncio.wait_for(task, timeout=10)
    async with SessionLocal() as db:
        from sqlmodel import select

        msgs = (
            await db.exec(select(Message).where(Message.session_id == root))
        ).all()
        assert any("synthesised" in (m.content or "") for m in msgs)
