"""Shared helpers for the non-ReAct patterns (Phase 14, SPEC §16).

  * ``simple_llm`` / ``complete_json`` — one-shot control-plane LLM calls
  * ``NodeLog`` — writes an ``agent_runs`` row per pattern node (SPEC §1.14)
  * ``budget`` — the shared ``max_llm_calls`` / ORCH_MAX_TOTAL_LLM_CALLS ceiling
  * ``run_react_once`` — run the base ReAct loop to produce one assistant answer
  * ``append_user`` — inject a steering message so the next ReAct pass re-runs
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import structlog

from ...config import get_settings
from ...db.models import AgentRun, Message, Trace
from ...observability.tracing import estimate_cost
from ...providers import DoneEvent, TextDelta, UsageEvent, get_provider
from .base import RunContext

log = structlog.get_logger("pagi.patterns")


class BudgetExceeded(Exception):
    """Hard ceiling on LLM calls for the whole turn (SPEC §16.3)."""


class _Budget:
    def __init__(self, rc: RunContext) -> None:
        s = get_settings()
        cfg_max = rc.cfg.get("max_llm_calls")
        self.limit = int(cfg_max) if cfg_max else s.orch_max_total_llm_calls
        self.used = 0

    def spend(self, n: int = 1) -> None:
        self.used += n
        if self.used > self.limit:
            raise BudgetExceeded(
                f"orchestration used more than {self.limit} LLM calls this turn"
            )


def budget(rc: RunContext) -> _Budget:
    return _Budget(rc)


async def simple_llm(
    rc: RunContext,
    *,
    system: str,
    user: str,
    model: str | None = None,
    kind: str = "chat",
    max_tokens: int = 512,
    temperature: float = 0.0,
) -> str:
    """One-shot text completion for a control node. Records a Trace with ``kind``
    so the Usage tab can attribute the cost (SPEC §16 / §1.8)."""
    mdl = model or rc.agent.model
    try:
        prov = get_provider(rc.agent.provider)
    except Exception:
        prov = get_provider("openai")

    parts: list[str] = []
    tin = tout = 0
    started = time.perf_counter()
    err: str | None = None
    try:
        async for ev in prov.stream_chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[], model=mdl, temperature=temperature, max_tokens=max_tokens,
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
            elif isinstance(ev, UsageEvent):
                tin, tout = ev.tokens_in, ev.tokens_out
            elif isinstance(ev, DoneEvent):
                pass
    except Exception as exc:  # pragma: no cover - external
        err = str(exc)
        log.warning("pattern_llm_failed", node_kind=kind, error=err)

    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        rc.db.add(
            Trace(
                session_id=rc.session_id, provider=rc.agent.provider, model=mdl,
                latency_ms=latency_ms, tokens_in=tin or None, tokens_out=tout or None,
                kind=kind, error=err,
                cost_usd=estimate_cost(mdl, tin, tout, provider=rc.agent.provider),
            )
        )
        await rc.db.commit()
    except Exception:  # pragma: no cover
        pass
    return "".join(parts).strip()


async def complete_json(rc: RunContext, *, system: str, user: str, model: str | None = None,
                        kind: str = "chat", max_tokens: int = 800) -> dict | list:
    """simple_llm + best-effort JSON parse of the first {...} / [...] found."""
    raw = await simple_llm(
        rc, system=system + "\nReply with JSON only.", user=user,
        model=model, kind=kind, max_tokens=max_tokens,
    )
    return _first_json(raw)


def _first_json(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("\n") + 1 :] if "\n" in raw else raw
    for opener, closer in (("{", "}"), ("[", "]")):
        i = raw.find(opener)
        j = raw.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(raw[i : j + 1])
            except ValueError:
                continue
    return {}


class NodeLog:
    """Async context manager: writes one ``agent_runs`` row (running -> done/failed).

    Used both for observability and to let resume skip already-done nodes
    (SPEC §16.4).
    """

    def __init__(self, rc: RunContext, *, node: str, step_no: int, attempt: int = 1,
                 message_id: str | None = None, input_summary: str | None = None):
        self.rc = rc
        self.node = node
        self.step_no = step_no
        self.attempt = attempt
        self.message_id = message_id
        self.input_summary = input_summary
        self.row: AgentRun | None = None
        self._t0 = 0.0
        self.payload: dict = {}
        self.output_summary: str | None = None

    async def __aenter__(self) -> "NodeLog":
        self._t0 = time.perf_counter()
        root = self.rc.chat.root_session_id or self.rc.session_id
        self.row = AgentRun(
            session_id=self.rc.session_id,
            root_session_id=root,
            message_id=self.message_id,
            pattern=self.rc.cfg.get("pattern", "react"),
            step_no=self.step_no,
            node=self.node,
            attempt=self.attempt,
            input_summary=(self.input_summary or "")[:2000] or None,
            status="running",
        )
        self.rc.db.add(self.row)
        try:
            await self.rc.db.commit()
            await self.rc.db.refresh(self.row)
        except Exception:  # pragma: no cover
            pass
        self.rc.last_run_id = self.row.id if self.row is not None else None
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self.row is None:
            return False
        self.row.latency_ms = int((time.perf_counter() - self._t0) * 1000)
        self.row.payload = self.payload
        self.row.output_summary = (self.output_summary or "")[:2000] or None
        if exc_type is not None:
            self.row.status = "failed"
            self.row.error = str(exc)[:2000]
        else:
            self.row.status = "done"
        try:
            self.rc.db.add(self.row)
            await self.rc.db.commit()
        except Exception:  # pragma: no cover
            pass
        return False  # never swallow


async def done_nodes(rc: RunContext) -> dict[tuple[str, int], dict]:
    """{(node, step_no): payload} for rows already 'done' — resume skips these."""
    from sqlmodel import select

    rows = (
        await rc.db.exec(
            select(AgentRun).where(
                AgentRun.session_id == rc.session_id, AgentRun.status == "done"
            )
        )
    ).all()
    return {(r.node, r.step_no): (r.payload or {}) for r in rows}


async def append_user(rc: RunContext, text: str) -> None:
    """Inject a steering message so the base ReAct loop re-runs on the next pass."""
    rc.db.add(Message(session_id=rc.session_id, role="user", content=text))
    rc.chat.updated_at = datetime.now(timezone.utc)
    rc.db.add(rc.chat)
    await rc.db.commit()


async def last_assistant_text(rc: RunContext) -> str:
    from sqlmodel import select

    row = (
        await rc.db.exec(
            select(Message)
            .where(Message.session_id == rc.session_id, Message.role == "assistant")
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
    ).first()
    return (row.content or "") if row else ""


async def run_react_once(rc: RunContext) -> str:
    """Run the base ReAct loop once; return the assistant answer it produced."""
    from .base import get_strategy

    await get_strategy("react").drive(rc)
    return await last_assistant_text(rc)
