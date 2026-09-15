"""Phase 18 — multi-day project development loop (PLAN §18)."""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from app.core.orchestration import project_dev
from app.db.models import Agent, ProjectIteration, ProjectRun, User
from app.db.session import SessionLocal
from app.providers import DoneEvent, TextDelta, ToolCallComplete


# ── fixtures / helpers ───────────────────────────────────────────────────
_planner_calls = {"n": 0}


class ProjectProvider:
    """Dispatches purely on the ``model`` kwarg stream_chat receives — that's
    the per-agent model string, unlike the ``get_provider(name)`` argument
    which is just the shared provider name (e.g. "openai")."""

    def __init__(self, *_a, **_k):
        pass

    async def stream_chat(self, *, messages, tools=None, model=None, **_kw):
        last = messages[-1] if messages else {}
        if model == "planner-model":
            _planner_calls["n"] += 1
            yield TextDelta("Add a hello.txt file this iteration.")
            yield DoneEvent(finish_reason="stop")
            return
        if model == "developer-model":
            if last.get("role") != "tool":
                yield ToolCallComplete(
                    tool_call_id="d1", name="write_file",
                    args={"path": "hello.txt", "content": "hi"},
                )
                yield DoneEvent(finish_reason="tool_calls")
                return
            yield TextDelta("Added hello.txt with a greeting.")
            yield DoneEvent(finish_reason="stop")
            return
        if model == "qa-model-pass":
            yield TextDelta("Looks correct.\nVERDICT: PASS")
            yield DoneEvent(finish_reason="stop")
            return
        if model == "qa-model-fail":
            yield TextDelta("Something's wrong.\nVERDICT: FAIL -- tests broke")
            yield DoneEvent(finish_reason="stop")
            return
        if model == "qa-model-ambiguous":
            yield TextDelta("I looked at it and it seems okay I guess.")
            yield DoneEvent(finish_reason="stop")
            return
        raise AssertionError(f"unexpected model in ProjectProvider: {model!r}")  # pragma: no cover


@pytest.fixture
def project_env(monkeypatch):
    monkeypatch.setattr("app.core.agent_runtime.get_provider", lambda n: ProjectProvider())
    monkeypatch.setattr("app.core.agent_runtime.provider_supports_vision", lambda *a, **k: True)
    _planner_calls["n"] = 0

    async def _fake_write_file(ctx, args):
        return {"bytes_written": len(args.get("content", ""))}

    async def _fake_execute_code(ctx, args):
        return {"stdout": "ok\n", "stderr": "", "exit_code": 0, "timed_out": False, "duration_ms": 1}

    from app.tools import TOOL_REGISTRY

    orig_write = TOOL_REGISTRY["write_file"].handler
    orig_exec = TOOL_REGISTRY["execute_code"].handler
    TOOL_REGISTRY["write_file"].handler = _fake_write_file
    TOOL_REGISTRY["execute_code"].handler = _fake_execute_code

    calls: list[tuple[str, dict]] = []

    async def _fake_sandbox_call(path, payload, *, timeout=130.0):
        calls.append((path, dict(payload)))
        if path == "/execute":
            return {"stdout": "deadbeef1234\n", "stderr": "", "exit_code": 0, "timed_out": False}
        if path == "/files/write":
            return {"bytes_written": len(payload.get("content", ""))}
        if path == "/workspace/clone":
            return {"cloned": True}
        return {}

    monkeypatch.setattr("app.tools.sandbox_client.call", _fake_sandbox_call)

    yield calls
    TOOL_REGISTRY["write_file"].handler = orig_write
    TOOL_REGISTRY["execute_code"].handler = orig_exec


async def _role_agent(name: str, model: str, tools: list[str]) -> str:
    async with SessionLocal() as db:
        a = Agent(
            name=name, provider="openai", model=model,
            tools_allowed=tools, is_delegatable=True,
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return a.id


async def _make_run(
    *, qa_model: str = "qa-model-pass", max_iterations: int = 10, budget_usd: float = 0.0,
    qa_fail_pause_threshold: int = 3,
) -> str:
    developer_tools = ["read_file", "list_files", "search_files", "write_file", "execute_code",
                        "edit_file", "delete_file"]
    planner_id = await _role_agent("Planner", "planner-model", ["read_file", "list_files", "search_files"])
    developer_id = await _role_agent("Developer", "developer-model", developer_tools)
    qa_id = await _role_agent("QA", qa_model, ["execute_code", "read_file", "list_files", "search_files"])

    async with SessionLocal() as db:
        uid = (await db.exec(select(User))).first().id
        run = await project_dev.create_project_run(
            db, name="demo", planner_agent_id=planner_id, developer_agent_id=developer_id,
            qa_agent_id=qa_id, user_id=uid, max_iterations=max_iterations,
            budget_usd=budget_usd, schedule=None,
        )
        run.qa_fail_pause_threshold = qa_fail_pause_threshold
        db.add(run)
        await db.commit()
        return run.id


# ── happy path ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_iteration_happy_path(auth_client, project_env):
    run_id = await _make_run()
    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        its = (
            await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))
        ).all()
    assert len(its) == 1
    it = its[0]
    assert it.status == "done"
    assert it.qa_verdict == "pass"
    assert it.planner_session_id and it.developer_session_id and it.qa_session_id
    assert it.workspace_commit_sha == "deadbeef1234"
    assert it.cost_usd is not None
    assert run.iterations_done == 1
    assert run.status == "active"

    # git init (create) + progress write + commit + workspace clone all happened
    paths = [c[0] for c in project_env]
    assert paths.count("/execute") == 2  # git init + git commit
    assert "/files/write" in paths
    assert "/workspace/clone" in paths


# ── QA verdict handling ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qa_fail_marks_iteration_and_streak(auth_client, project_env):
    run_id = await _make_run(qa_model="qa-model-fail")
    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        it = (
            await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))
        ).first()
    assert it.status == "qa_failed"
    assert it.qa_verdict == "fail"
    assert it.qa_reason == "tests broke"
    assert run.qa_fail_streak == 1


@pytest.mark.asyncio
async def test_three_qa_fails_pause_the_run(auth_client, project_env):
    run_id = await _make_run(qa_model="qa-model-fail", max_iterations=10)
    for _ in range(3):
        await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
    assert run.qa_fail_streak == 3
    assert run.status == "paused"
    assert run.iterations_done == 3

    # paused -> a further call is a no-op (no new iteration, no crash)
    await project_dev.run_iteration(run_id)
    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        count = len(
            (await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))).all()
        )
    assert count == 3
    assert run.iterations_done == 3


@pytest.mark.asyncio
async def test_ambiguous_qa_answer_treated_as_fail(auth_client, project_env):
    run_id = await _make_run(qa_model="qa-model-ambiguous")
    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        it = (
            await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))
        ).first()
    assert it.status == "qa_failed"
    assert it.qa_verdict == "fail"
    assert it.qa_reason == "no verdict line found"


# ── caps ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_max_iterations_reached_marks_done(auth_client, project_env):
    run_id = await _make_run(max_iterations=1)
    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
    assert run.status == "done"
    assert run.iterations_done == 1

    await project_dev.run_iteration(run_id)  # no-op past done
    async with SessionLocal() as db:
        count = len(
            (await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))).all()
        )
    assert count == 1


@pytest.mark.asyncio
async def test_budget_exceeded_pauses_before_any_work(auth_client, project_env):
    run_id = await _make_run(budget_usd=0.01)
    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        run.spent_usd = 0.02
        db.add(run)
        await db.commit()

    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        count = len(
            (await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))).all()
        )
    assert run.status == "paused"
    assert count == 0
    assert _planner_calls["n"] == 0  # never even started delegating


# ── resume / idempotency ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_crashed_iteration_resumes_without_redoing_finished_phases(auth_client, monkeypatch, project_env):
    from app.core.delegation import run_delegated as real_run_delegated

    state = {"failed_once": False}

    async def _flaky_run_delegated(**kwargs):
        if "developer" in kwargs["tool_call_id"] and not state["failed_once"]:
            state["failed_once"] = True
            return {"error": "simulated_crash", "message": "boom"}
        return await real_run_delegated(**kwargs)

    monkeypatch.setattr("app.core.orchestration.project_dev.run_delegated", _flaky_run_delegated)

    run_id = await _make_run()
    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        it = (
            await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))
        ).first()
    assert it.status == "error"
    assert "simulated_crash" in it.error
    assert run.iterations_done == 0  # not counted — retryable
    assert _planner_calls["n"] == 1

    # retry: planner short-circuits (idempotency latch), developer actually runs
    await project_dev.run_iteration(run_id)

    async with SessionLocal() as db:
        run = await db.get(ProjectRun, run_id)
        its = (
            await db.exec(select(ProjectIteration).where(ProjectIteration.project_run_id == run_id))
        ).all()
    assert len(its) == 1  # same iteration row, not a duplicate
    assert its[0].status == "done"
    assert run.iterations_done == 1
    assert _planner_calls["n"] == 1  # planner was NOT re-invoked


# ── ToolContext.workspace_id plumbing (regression guard for the 7-file edit) ─
@pytest.mark.asyncio
async def test_write_file_tool_uses_workspace_id_not_session_id(monkeypatch):
    from app.tools.base import ToolContext
    from app.tools.builtin.write_file import _handler

    captured = {}

    async def _fake_call(path, payload, *, timeout=130.0):
        captured["path"] = path
        captured["payload"] = payload
        return {"bytes_written": 1}

    monkeypatch.setattr("app.tools.builtin.write_file.call", _fake_call)

    ctx = ToolContext(session_id="real-chat-session", db=None, workspace_id="shared-project-workspace")
    await _handler(ctx, {"path": "x.txt", "content": "y"})
    assert captured["payload"]["session_id"] == "shared-project-workspace"

    # default (non-project) sessions: workspace_id == session_id, zero behaviour change
    ctx2 = ToolContext(session_id="plain-session", db=None, workspace_id="plain-session")
    await _handler(ctx2, {"path": "x.txt", "content": "y"})
    assert captured["payload"]["session_id"] == "plain-session"


# ── REST integration ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_projects_rest_create_and_iterate(auth_client, project_env):
    planner_id = await _role_agent("P2", "planner-model", ["read_file", "list_files", "search_files"])
    developer_id = await _role_agent(
        "D2", "developer-model",
        ["read_file", "list_files", "search_files", "write_file", "execute_code", "edit_file", "delete_file"],
    )
    qa_id = await _role_agent("Q2", "qa-model-pass", ["execute_code", "read_file", "list_files", "search_files"])

    r = await auth_client.post(
        "/api/projects",
        json={
            "name": "rest-demo", "planner_agent_id": planner_id, "developer_agent_id": developer_id,
            "qa_agent_id": qa_id, "max_iterations": 2, "budget_usd": 0,
        },
    )
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]
    assert r.json()["status"] == "active"

    ri = await auth_client.post(f"/api/projects/{project_id}/iterate-now")
    assert ri.status_code == 202, ri.text

    got = None
    for _ in range(100):
        await asyncio.sleep(0.1)
        got = (await auth_client.get(f"/api/projects/{project_id}")).json()
        if got["iterations"] and got["iterations"][0]["status"] != "running":
            break
    assert got["iterations"], "no iteration was created"
    assert got["iterations"][0]["status"] == "done"
    assert got["run"]["iterations_done"] == 1

    # pause/resume
    p = await auth_client.post(f"/api/projects/{project_id}/pause")
    assert p.status_code == 200 and p.json()["status"] == "paused"
    again = await auth_client.post(f"/api/projects/{project_id}/pause")
    assert again.status_code == 409
    res = await auth_client.post(f"/api/projects/{project_id}/resume")
    assert res.status_code == 200 and res.json()["status"] == "active"

    d = await auth_client.delete(f"/api/projects/{project_id}")
    assert d.status_code == 204
    assert (await auth_client.get(f"/api/projects/{project_id}")).status_code == 404


@pytest.mark.asyncio
async def test_create_project_rejects_qa_tools_not_subset_of_developer(auth_client):
    planner_id = await _role_agent("P3", "planner-model", [])
    developer_id = await _role_agent("D3", "developer-model", ["write_file"])
    qa_id = await _role_agent("Q3", "qa-model-pass", ["execute_code"])  # not a subset of developer's

    r = await auth_client.post(
        "/api/projects",
        json={
            "name": "bad", "planner_agent_id": planner_id, "developer_agent_id": developer_id,
            "qa_agent_id": qa_id,
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "tools_not_subset_of_developer"


@pytest.mark.asyncio
async def test_create_project_rejects_non_delegatable_agent(auth_client):
    async with SessionLocal() as db:
        a = Agent(name="NotDelegatable", provider="openai", model="m", is_delegatable=False)
        db.add(a)
        await db.commit()
        await db.refresh(a)
        aid = a.id

    r = await auth_client.post(
        "/api/projects",
        json={"name": "bad2", "planner_agent_id": aid, "developer_agent_id": aid, "qa_agent_id": aid},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "agent_not_delegatable"
