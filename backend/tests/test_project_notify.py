"""Desktop overlay — project loop notifications (Phase 20b, SPEC §21.7)."""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.core.notify import hub
from app.core.orchestration import project_dev
from app.db.models import User
from app.db.session import SessionLocal

# reuse the Phase 18 scaffolding: fake provider, sandbox stubs, run factory
from tests.test_project_dev import _make_run, project_env  # noqa: F401
from tests.test_notify import FakeWS


async def _listen() -> FakeWS:
    async with SessionLocal() as db:
        uid = (await db.exec(select(User))).first().id
    ws = FakeWS()
    await hub.connect(uid, ws)
    return ws


@pytest.fixture(autouse=True)
def _clean_hub():
    hub._conns.clear()
    yield
    hub._conns.clear()


@pytest.mark.asyncio
async def test_finished_iteration_notifies_with_verdict(auth_client, project_env):  # noqa: F811
    run_id = await _make_run()
    ws = await _listen()
    await project_dev.run_iteration(run_id)

    assert ws.types() == ["project_iteration_finished"]
    ev = ws.sent[0]
    assert ev["project_run_id"] == run_id and ev["project_name"] == "demo"
    assert (ev["iteration_no"], ev["qa_verdict"], ev["status"]) == (1, "pass", "done")


@pytest.mark.asyncio
async def test_qa_fail_streak_pause_notifies_paused_with_reason(auth_client, project_env):  # noqa: F811
    run_id = await _make_run(qa_model="qa-model-fail")
    ws = await _listen()
    for _ in range(3):
        await project_dev.run_iteration(run_id)

    assert ws.types() == [
        "project_iteration_finished", "project_iteration_finished",
        "project_iteration_finished", "project_paused",
    ]
    assert ws.sent[0]["status"] == "qa_failed" and ws.sent[0]["qa_verdict"] == "fail"
    assert ws.sent[-1]["reason"] == "qa_fail_streak"


@pytest.mark.asyncio
async def test_budget_pause_notifies_paused(auth_client, project_env):  # noqa: F811
    from app.db.models import ProjectRun

    run_id = await _make_run(budget_usd=0.01)
    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        run.spent_usd = 0.02
        db.add(run)
        await db.commit()
    ws = await _listen()

    await project_dev.run_iteration(run_id)

    assert ws.types() == ["project_paused"]
    assert ws.sent[0]["reason"] == "budget"


@pytest.mark.asyncio
async def test_errored_iteration_notifies_error(auth_client, project_env, monkeypatch):  # noqa: F811
    run_id = await _make_run()
    ws = await _listen()

    async def failing(**_kw):
        return {"error": "boom", "message": "planner failed"}

    monkeypatch.setattr(project_dev, "run_delegated", failing)
    await project_dev.run_iteration(run_id)

    assert ws.types() == ["project_iteration_finished"]
    assert ws.sent[0]["status"] == "error" and ws.sent[0]["qa_verdict"] is None


@pytest.mark.asyncio
async def test_a_broken_hub_does_not_break_the_iteration(auth_client, project_env, monkeypatch):  # noqa: F811
    from app.db.models import ProjectIteration

    async def boom(*_a, **_k):
        raise RuntimeError("hub exploded")

    monkeypatch.setattr(hub, "emit", boom)
    run_id = await _make_run()
    await _listen()
    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        its = (await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))).all()
    assert [i.status for i in its] == ["done"]
