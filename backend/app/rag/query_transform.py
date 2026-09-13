"""Pre-retrieval query transformation (SPEC §14.2 group 4, §14.4 step 1).

    none         -> [original]
    hyde         -> [original, hypothetical answer passage]  (embed the passage)
    multi_query  -> [original, sub-query ×N]
    step_back    -> [original, one more abstract question]
    decompose    -> [original, atomic sub-question ×2-4]
    auto         -> router.transform_for(query) picks one of the above (LLM-free)

Returns ``[{kind, text}]`` — always including the original. The pipeline embeds /
BM25-searches every entry and unions the hits.
"""

from __future__ import annotations

import structlog

from .router import transform_for as _route

log = structlog.get_logger("pagi.rag.transform")

_PROMPTS = {
    "hyde": (
        "Write a short, factual passage (2-4 sentences) that would directly answer "
        "the question, as if copied from a reference document. Do not hedge.",
    ),
    "step_back": (
        "Write ONE more general/abstract question whose answer would help answer "
        "the original. Output only the question.",
    ),
}


async def _llm(model: str | None, provider: str | None, system: str, user: str) -> str:
    from ..providers import DoneEvent, TextDelta, UsageEvent, get_provider

    if not model:
        return ""
    try:
        prov = get_provider(provider or "openai")
    except Exception:
        prov = get_provider("openai")
    parts: list[str] = []
    try:
        async for ev in prov.stream_chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[], model=model, temperature=0.0, max_tokens=300,
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
            elif isinstance(ev, (DoneEvent, UsageEvent)):
                continue
    except Exception as exc:  # pragma: no cover - external
        log.warning("transform_llm_failed", error=str(exc))
        return ""
    return "".join(parts).strip()


def _lines(text: str, cap: int) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip().lstrip("-*0123456789. )").strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= cap:
            break
    return out


async def transform(
    query: str,
    cfg: dict,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> tuple[list[dict], str]:
    """Return (queries, mode_used). ``queries[0]`` is always the original."""
    mode = cfg.get("query_transform") or "none"
    if mode == "auto":
        mode = _route(query)
    base = [{"kind": "original", "text": query}]
    if mode == "none" or not query.strip():
        return base, "none"

    model = cfg.get("transform_model") or model

    if mode == "hyde":
        doc = await _llm(model, provider, _PROMPTS["hyde"][0], query)
        return (base + [{"kind": "hyde", "text": doc}]) if doc else base, "hyde"

    if mode == "step_back":
        q = await _llm(model, provider, _PROMPTS["step_back"][0], query)
        return (base + [{"kind": "step_back", "text": q}]) if q else base, "step_back"

    if mode == "multi_query":
        n = int(cfg.get("multi_query_n") or 3)
        raw = await _llm(
            model, provider,
            f"Rewrite the question as {n} diverse standalone search queries, one per "
            "line, no numbering.",
            query,
        )
        subs = _lines(raw, n)
        return (base + [{"kind": "multi_query", "text": s} for s in subs]) if subs else base, "multi_query"

    if mode == "decompose":
        raw = await _llm(
            model, provider,
            "Break the question into 2-4 atomic sub-questions that together answer "
            "it, one per line, no numbering.",
            query,
        )
        subs = _lines(raw, 4)
        return (base + [{"kind": "decompose", "text": s} for s in subs]) if subs else base, "decompose"

    return base, "none"
