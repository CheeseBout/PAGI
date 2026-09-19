"""Multi-day project development loop (Phase 18, PLAN §18).

``run_iteration`` is a plain function, not a chat ``Strategy`` — there is no
user streaming at the other end (PLAN §18a). It drives Planner -> Developer ->
QA by calling ``core/delegation.py::run_delegated`` directly, three times,
reusing Phase 13's session-tree/HITL/crash-safety machinery exactly instead of
writing a second delegation loop (PLAN principle 10).

Each iteration adds one git commit to a persistent sandbox workspace shared by
all three roles via ``ChatSession.workspace_id`` (see ``agent_runtime.py`` and
the sandbox-touching builtin tools). The git commit itself, and writing the
Planner's answer into ``PROGRESS.md``, call ``sandbox_client`` directly —
that's orchestration plumbing, not an LLM tool call.
"""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone

import structlog
from sqlmodel import select

from ...db.models import Agent, ChatSession, ProjectIteration, ProjectRun
from ...db.session import SessionLocal
from ...tools import sandbox_client
from ..delegation import run_delegated
from ..notify import notify

log = structlog.get_logger("pagi.project_dev")

_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)\s*(?:[-—]+\s*(.*))?", re.IGNORECASE)

_PLANNER_TOOLS = ["read_file", "list_files", "search_files"]
_DEVELOPER_TOOLS = ["execute_code", "write_file", "edit_file", "delete_file"]
_QA_TOOLS = ["execute_code", "read_file", "list_files", "search_files"]


class ProjectValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _planner_task(iteration_no: int) -> str:
    return (
        f"This is iteration {iteration_no} of an ongoing software project. Read "
        "PLAN.md and PROGRESS.md in your workspace if they exist to understand the "
        "project goal and what has been done so far (if neither exists, this is the "
        "very first iteration -- start from scratch). Decide on ONE focused, "
        "achievable chunk of work for this iteration only, not the whole project. "
        "Your final answer must be a concise, concrete plan for that chunk of work "
        "-- it will be saved as the new PROGRESS.md for the Developer to follow."
    )


def _developer_task(iteration_no: int) -> str:
    return (
        f"This is iteration {iteration_no}. Read PROGRESS.md in your workspace for "
        "what to build this iteration. Implement it: write/edit the necessary files "
        "and use execute_code to verify your work runs before finishing. Your final "
        "answer must summarize what you changed and why in a few sentences -- it "
        "will become the git commit message for this iteration."
    )


def _qa_task(iteration_no: int, commit_sha: str | None) -> str:
    where = f"commit {commit_sha}" if commit_sha else "the current workspace state"
    return (
        f"This is iteration {iteration_no} of an ongoing software project. A change "
        f"was just committed to this isolated checkout ({where}). Inspect the files "
        "and, if there is a way to run tests or otherwise sanity-check the code, use "
        "execute_code to do so. Decide whether this iteration's change is "
        "acceptable. End your final answer with exactly one line, verbatim: "
        "'VERDICT: PASS' if it's acceptable, or 'VERDICT: FAIL -- <short reason>' if "
        "it is not."
    )


def _parse_qa_verdict(answer: str | None) -> tuple[str, str | None]:
    if not answer:
        return "fail", "no verdict line found"
    match = None
    for line in reversed(answer.strip().splitlines()):
        match = _VERDICT_RE.search(line)
        if match:
            break
    if not match:
        return "fail", "no verdict line found"
    verdict = match.group(1).lower()
    reason = (match.group(2) or "").strip() or None
    return verdict, reason


async def git_init_workspace(workspace_id: str) -> None:
    code = (
        "git init -q; "
        "git config user.email 'agent@pagi.local'; "
        "git config user.name 'PAGI Project Agent'"
    )
    await sandbox_client.call(
        "/execute",
        {"session_id": workspace_id, "language": "bash", "code": code, "timeout_seconds": 20},
        timeout=30,
    )


async def _git_commit(workspace_id: str, message: str) -> str | None:
    msg = shlex.quote(message or "iteration update")
    code = (
        "git config user.email 'agent@pagi.local' >/dev/null 2>&1; "
        "git config user.name 'PAGI Project Agent' >/dev/null 2>&1; "
        "git add -A; "
        f"git diff --cached --quiet || git commit -q -m {msg}; "
        "git rev-parse HEAD 2>/dev/null"
    )
    result = await sandbox_client.call(
        "/execute",
        {"session_id": workspace_id, "language": "bash", "code": code, "timeout_seconds": 30},
        timeout=50,
    )
    out = (result.get("stdout") or "").strip()
    return out.splitlines()[-1].strip() if out else None


async def create_project_run(
    db,
    *,
    name: str,
    planner_agent_id: str,
    developer_agent_id: str,
    qa_agent_id: str,
    user_id: str,
    max_iterations: int = 10,
    budget_usd: float = 0.0,
    schedule: str | None = None,
) -> ProjectRun:
    agents = {}
    for role, aid in (
        ("planner", planner_agent_id), ("developer", developer_agent_id), ("qa", qa_agent_id),
    ):
        agent = await db.get(Agent, aid)
        if agent is None:
            raise ProjectValidationError("invalid_agent", f"{role} agent not found")
        if not agent.is_delegatable:
            raise ProjectValidationError(
                "agent_not_delegatable", f"{role} agent must have is_delegatable=true"
            )
        agents[role] = agent

    developer_tools = set(agents["developer"].tools_allowed or [])
    for role in ("planner", "qa"):
        role_tools = set(agents[role].tools_allowed or [])
        if not role_tools <= developer_tools:
            raise ProjectValidationError(
                "tools_not_subset_of_developer",
                f"{role} agent's tools_allowed must be a subset of the developer "
                "agent's tools_allowed (the project's root session uses the "
                "developer agent, and a sub-agent never gets a tool its parent "
                "lacks)",
            )

    # A dedicated, non-delegatable "driver" agent anchors the delegation tree
    # (parent_session_id/depth/tool-intersection bookkeeping in
    # run_delegated) — it can't be one of the three role agents themselves,
    # since that role's own delegate call would then be flagged as
    # self-delegation. Its tools_allowed mirrors the developer's (the
    # superset per the subset check above) so the intersection never clips
    # any role's tools.
    driver = Agent(
        name=f"[project driver] {name}",
        provider=agents["developer"].provider,
        model=agents["developer"].model,
        tools_allowed=list(developer_tools),
        is_delegatable=False,
    )
    db.add(driver)
    await db.commit()
    await db.refresh(driver)

    root = ChatSession(
        user_id=user_id, agent_id=driver.id, kind="chat",
        title=f"[project] {name}",
    )
    db.add(root)
    await db.commit()
    await db.refresh(root)

    run = ProjectRun(
        name=name,
        planner_agent_id=planner_agent_id,
        developer_agent_id=developer_agent_id,
        qa_agent_id=qa_agent_id,
        root_session_id=root.id,
        max_iterations=max_iterations,
        budget_usd=budget_usd,
        schedule=schedule,
    )
    run.workspace_path = run.id
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await git_init_workspace(run.workspace_path)
    return run


async def _notify_project(project_run_id: str, event_type: str, **fields) -> None:
    """User notification for a project event (SPEC §21.7). Fire-and-forget:
    looks up the owner via the run's root session and never raises."""
    try:
        async with SessionLocal() as db:
            run = await db.get(ProjectRun, project_run_id)
            if run is None:
                return
            root = await db.get(ChatSession, run.root_session_id)
            if root is None:
                return
            user_id, name = root.user_id, run.name
        await notify(
            user_id,
            {"type": event_type, "project_run_id": project_run_id, "project_name": name, **fields},
        )
    except Exception:  # pragma: no cover - notifying must never break the loop
        log.warning("project_notify_failed", project_run_id=project_run_id, exc_info=True)


async def _fail_iteration(iteration_id: str, code: str, message: str) -> None:
    async with SessionLocal() as db:
        it = await db.get(ProjectIteration, iteration_id)
        if it is None:
            return
        it.status = "error"
        it.error = f"{code}: {message}"[:2000]
        it.finished_at = datetime.now(timezone.utc)
        db.add(it)
        await db.commit()
        run_id, iteration_no = it.project_run_id, it.iteration_no
    log.warning("project_iteration_failed", iteration_id=iteration_id, code=code, message=message)
    await _notify_project(
        run_id, "project_iteration_finished", iteration_no=iteration_no, qa_verdict=None, status="error"
    )


async def run_iteration(project_run_id: str) -> None:
    async with SessionLocal() as db:
        run = await db.get(ProjectRun, project_run_id)
        if run is None or run.status != "active":
            return
        if run.budget_usd > 0 and run.spent_usd >= run.budget_usd:
            run.status = "paused"
            db.add(run)
            await db.commit()
            await _notify_project(project_run_id, "project_paused", reason="budget")
            return

        next_no = run.iterations_done + 1
        if next_no > run.max_iterations:
            run.status = "done"
            db.add(run)
            await db.commit()
            return

        iteration = (
            await db.exec(
                select(ProjectIteration).where(
                    ProjectIteration.project_run_id == run.id,
                    ProjectIteration.iteration_no == next_no,
                )
            )
        ).first()
        if iteration is not None and iteration.status in ("done", "qa_failed"):
            return  # already finished — duplicate trigger, no-op
        if iteration is None:
            iteration = ProjectIteration(project_run_id=run.id, iteration_no=next_no)
            db.add(iteration)
            await db.commit()
            await db.refresh(iteration)

        driver_session = await db.get(ChatSession, run.root_session_id)
        planner = await db.get(Agent, run.planner_agent_id)
        developer = await db.get(Agent, run.developer_agent_id)
        qa = await db.get(Agent, run.qa_agent_id)
        workspace_id = run.workspace_path
        iter_id = iteration.id

        # A fresh, independent anchor session per iteration (its own root —
        # NOT parented under run.root_session_id). Phase 13's
        # max_subagents_per_turn ceiling counts every descendant of a root
        # for that root's entire lifetime, not "per turn" as the name
        # suggests; reusing one root across many iterations (3 new children
        # each) would exhaust the default ceiling of 5 after not even two
        # iterations. A fresh anchor resets that count to zero every time.
        anchor = ChatSession(
            user_id=driver_session.user_id, agent_id=driver_session.agent_id,
            kind="chat", title=f"[project] iter {next_no} anchor",
        )
        db.add(anchor)
        await db.commit()
        await db.refresh(anchor)
        anchor_session_id = anchor.id

    if planner is None or developer is None or qa is None:
        await _fail_iteration(iter_id, "agent_missing", "a project role agent was deleted")
        return

    try:
        # ── Planner ──────────────────────────────────────────────────
        planner_result = await run_delegated(
            parent_session_id=anchor_session_id,
            tool_call_id=f"project:{run.id}:iter{next_no}:planner",
            worker_name=planner.name,
            task=_planner_task(next_no),
            wait_for_approval=False, mode="unattended",
            unattended_allowed_tools=_PLANNER_TOOLS,
            workspace_id=workspace_id,
        )
        if "error" in planner_result:
            await _fail_iteration(iter_id, planner_result["error"], planner_result.get("message", ""))
            return
        async with SessionLocal() as db:
            it = await db.get(ProjectIteration, iter_id)
            it.planner_session_id = planner_result["child_session_id"]
            db.add(it)
            await db.commit()

        await sandbox_client.call(
            "/files/write",
            {
                "session_id": workspace_id, "path": "PROGRESS.md",
                "content": planner_result.get("answer") or "", "mode": "overwrite",
            },
        )

        # ── Developer ────────────────────────────────────────────────
        developer_result = await run_delegated(
            parent_session_id=anchor_session_id,
            tool_call_id=f"project:{run.id}:iter{next_no}:developer",
            worker_name=developer.name,
            task=_developer_task(next_no),
            wait_for_approval=False, mode="unattended",
            unattended_allowed_tools=_DEVELOPER_TOOLS,
            workspace_id=workspace_id,
        )
        if "error" in developer_result:
            await _fail_iteration(iter_id, developer_result["error"], developer_result.get("message", ""))
            return
        async with SessionLocal() as db:
            it = await db.get(ProjectIteration, iter_id)
            it.developer_session_id = developer_result["child_session_id"]
            db.add(it)
            await db.commit()

        # ── commit ───────────────────────────────────────────────────
        commit_msg = (developer_result.get("answer") or f"iteration {next_no}")[:500]
        sha = await _git_commit(workspace_id, commit_msg)
        async with SessionLocal() as db:
            it = await db.get(ProjectIteration, iter_id)
            it.workspace_commit_sha = sha
            db.add(it)
            await db.commit()

        # ── QA (isolated checkout) ──────────────────────────────────
        qa_workspace_id = f"{workspace_id}-qa"
        await sandbox_client.call(
            "/workspace/clone",
            {"from_session_id": workspace_id, "to_session_id": qa_workspace_id},
        )
        qa_result = await run_delegated(
            parent_session_id=anchor_session_id,
            tool_call_id=f"project:{run.id}:iter{next_no}:qa",
            worker_name=qa.name,
            task=_qa_task(next_no, sha),
            context=None,  # deliberate — QA never sees the Developer's transcript
            wait_for_approval=False, mode="unattended",
            unattended_allowed_tools=_QA_TOOLS,
            workspace_id=qa_workspace_id,
        )
        if "error" in qa_result:
            await _fail_iteration(iter_id, qa_result["error"], qa_result.get("message", ""))
            return

        verdict, reason = _parse_qa_verdict(qa_result.get("answer"))
        cost = sum(
            (r.get("cost_usd") or 0.0) for r in (planner_result, developer_result, qa_result)
        )

        async with SessionLocal() as db:
            it = await db.get(ProjectIteration, iter_id)
            it.qa_session_id = qa_result["child_session_id"]
            it.qa_verdict = verdict
            it.qa_reason = reason
            it.cost_usd = cost
            it.status = "qa_failed" if verdict == "fail" else "done"
            it.finished_at = datetime.now(timezone.utc)
            db.add(it)
            iteration_status = it.status

            run = await db.get(ProjectRun, project_run_id)
            run.iterations_done = next_no
            run.spent_usd += cost
            paused_reason = None
            if verdict == "fail":
                run.qa_fail_streak += 1
                if run.qa_fail_streak >= run.qa_fail_pause_threshold:
                    run.status = "paused"
                    paused_reason = "qa_fail_streak"
            else:
                run.qa_fail_streak = 0
            if run.status == "active" and run.iterations_done >= run.max_iterations:
                run.status = "done"
            run.updated_at = datetime.now(timezone.utc)
            db.add(run)
            await db.commit()
        log.info(
            "project_iteration_done", project_run_id=project_run_id, iteration_no=next_no,
            verdict=verdict, commit_sha=sha,
        )
        await _notify_project(
            project_run_id, "project_iteration_finished",
            iteration_no=next_no, qa_verdict=verdict, status=iteration_status,
        )
        if paused_reason:
            await _notify_project(project_run_id, "project_paused", reason=paused_reason)
    except Exception as exc:  # pragma: no cover - keep the cron job alive
        await _fail_iteration(iter_id, "unexpected", str(exc))
