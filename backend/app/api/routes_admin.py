"""Admin / observability routes (SPEC §2.8)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_settings
from ..db.models import ChatSession, Trace, User
from ..observability.tracing import price_lookup
from ..tools.sandbox_client import health as sandbox_health
from .deps import APIError, get_current_user, get_db
from .serializers import trace_out

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/traces")
async def list_traces(
    session_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Trace).order_by(Trace.created_at.desc()).limit(limit)
    if session_id:
        stmt = stmt.where(Trace.session_id == session_id)
    if provider:
        stmt = stmt.where(Trace.provider == provider)
    if kind:
        stmt = stmt.where(Trace.kind == kind)
    return [trace_out(t) for t in (await db.exec(stmt)).all()]


def _bucket_key(t: Trace, group_by: str, sess: dict[str, dict] | None = None) -> str:
    if group_by == "model":
        return f"{t.provider}/{t.model}" if t.provider else t.model
    if group_by == "session":
        # roll a sub-agent's traces up to the tree root (SPEC §15.6)
        info = (sess or {}).get(t.session_id or "")
        return (info or {}).get("root") or t.session_id or "(none)"
    if group_by == "kind":
        return t.kind or "chat"
    created = t.created_at
    return created.strftime("%Y-%m-%d") if created else "(unknown)"


@router.get("/usage")
async def usage(
    group_by: str = Query(default="day", pattern="^(day|model|session|kind)$"),
    since: str | None = Query(default=None, description="ISO date/datetime lower bound"),
    session_id: str | None = Query(default=None),
    kind: str | None = Query(default=None, description="filter to one trace kind"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Trace)
    if session_id:
        stmt = stmt.where(Trace.session_id == session_id)
    if kind:
        stmt = stmt.where(Trace.kind == kind)
    if since:
        try:
            lb = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if lb.tzinfo:
                lb = lb.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            raise APIError(400, "bad_request", "`since` must be ISO-8601")
        stmt = stmt.where(Trace.created_at >= lb)
    rows = (await db.exec(stmt)).all()

    # session_id -> {kind, root} so we can roll sub-agent traces up + split origin
    sess_ids = {t.session_id for t in rows if t.session_id}
    sess: dict[str, dict] = {}
    if sess_ids:
        for s in (
            await db.exec(select(ChatSession).where(ChatSession.id.in_(sess_ids)))
        ).all():
            sess[s.id] = {
                "kind": getattr(s, "kind", "chat"),
                "root": getattr(s, "root_session_id", None) or s.id,
            }

    buckets: dict[str, dict] = {}
    for t in rows:
        b = buckets.setdefault(
            _bucket_key(t, group_by, sess),
            {"bucket": None, "calls": 0, "tokens_in": 0, "tokens_out": 0,
             "cached_tokens": 0, "cost_usd": 0.0, "errors": 0, "cache_hits": 0},
        )
        b["bucket"] = _bucket_key(t, group_by, sess)
        b["calls"] += 1
        b["tokens_in"] += t.tokens_in or 0
        b["tokens_out"] += t.tokens_out or 0
        b["cached_tokens"] += t.cached_tokens or 0
        b["cost_usd"] = round(b["cost_usd"] + (t.cost_usd or 0.0), 6)
        b["errors"] += 1 if t.error else 0
        b["cache_hits"] += 1 if t.cache_hit else 0
    out = sorted(buckets.values(), key=lambda b: b["bucket"], reverse=(group_by != "day"))
    totals = {
        "calls": sum(b["calls"] for b in out),
        "tokens_in": sum(b["tokens_in"] for b in out),
        "tokens_out": sum(b["tokens_out"] for b in out),
        "cached_tokens": sum(b["cached_tokens"] for b in out),
        "cost_usd": round(sum(b["cost_usd"] for b in out), 6),
        "errors": sum(b["errors"] for b in out),
    }
    # always break cost/calls down by kind so the Usage tab can show RAG spend
    by_kind: dict[str, dict] = {}
    for t in rows:
        k = by_kind.setdefault(t.kind or "chat", {"kind": t.kind or "chat", "calls": 0, "cost_usd": 0.0})
        k["calls"] += 1
        k["cost_usd"] = round(k["cost_usd"] + (t.cost_usd or 0.0), 6)
    # split main-chat vs sub-agent spend (Phase 13c, SPEC §15.6)
    by_origin: dict[str, dict] = {
        "chat": {"origin": "chat", "calls": 0, "cost_usd": 0.0},
        "subagent": {"origin": "subagent", "calls": 0, "cost_usd": 0.0},
    }
    for t in rows:
        origin = "subagent" if (sess.get(t.session_id or "") or {}).get("kind") == "subagent" else "chat"
        by_origin[origin]["calls"] += 1
        by_origin[origin]["cost_usd"] = round(
            by_origin[origin]["cost_usd"] + (t.cost_usd or 0.0), 6
        )
    return {
        "group_by": group_by,
        "buckets": out,
        "totals": totals,
        "by_kind": sorted(by_kind.values(), key=lambda k: k["cost_usd"], reverse=True),
        "by_origin": [by_origin["chat"], by_origin["subagent"]],
    }


@router.get("/usage/budget")
async def usage_budget(
    db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)
):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    rows = (await db.exec(select(Trace).where(Trace.created_at >= month_start))).all()
    spent = round(sum(t.cost_usd or 0.0 for t in rows), 6)
    budget = get_settings().monthly_budget_usd
    return {
        "month": now.strftime("%Y-%m"),
        "spent_usd": spent,
        "budget_usd": budget,
        "pct": round(spent / budget * 100, 1) if budget > 0 else None,
        "over": budget > 0 and spent >= budget,
    }


@router.get("/model-pricing")
async def model_pricing(
    model: str = Query(..., min_length=1),
    provider: str | None = Query(default=None),
    _: User = Depends(get_current_user),
):
    return price_lookup(model, provider)


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    db_ok = "ok"
    try:
        await db.exec(select(Trace).limit(1))
    except Exception:  # pragma: no cover
        db_ok = "error"
    sb = await sandbox_health()
    return {
        "status": "ok",
        "db": db_ok,
        "sandbox": "ok" if sb.get("status") == "ok" else "unreachable",
    }
