"""Corrective RAG — grade the retrieved context before generation (SPEC §14.4
step 7, §14.2 group 4).

    grade()  -> (verdict, score)  verdict ∈ sufficient | insufficient
    on insufficient, per ``crag_fallback``:
        none        -> just record the verdict
        broaden     -> caller re-runs retrieval with a widened query
        web_search  -> fetch web results, returned as extra pseudo-context
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("pagi.rag.crag")


async def grade(
    query: str, contexts: list[str], cfg: dict, *, model: str | None, provider: str | None
) -> tuple[str, float]:
    threshold = float(cfg.get("crag_threshold") or 0.6)
    if not contexts:
        return "insufficient", 0.0
    if not model:
        # no judge available — be permissive, don't block generation
        return "sufficient", 1.0

    from ..providers import DoneEvent, TextDelta, UsageEvent, get_provider

    try:
        prov = get_provider(provider or "openai")
    except Exception:
        prov = get_provider("openai")

    joined = "\n\n".join(f"[{i}] {c[:1200]}" for i, c in enumerate(contexts))
    parts: list[str] = []
    try:
        async for ev in prov.stream_chat(
            messages=[
                {
                    "role": "system",
                    "content": "You judge whether a set of context passages is enough to "
                    "answer a question. Reply with only a number 0.0-1.0 (1.0 = fully "
                    "answerable from the context).",
                },
                {"role": "user", "content": f"QUESTION: {query}\n\nCONTEXT:\n{joined}"},
            ],
            tools=[], model=model, temperature=0.0, max_tokens=8,
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
            elif isinstance(ev, (DoneEvent, UsageEvent)):
                continue
    except Exception as exc:  # pragma: no cover - external
        log.warning("crag_grade_failed", error=str(exc))
        return "sufficient", 1.0

    raw = "".join(parts).strip()
    try:
        score = float(raw.split()[0].rstrip("."))
    except (ValueError, IndexError):
        score = 1.0
    score = max(0.0, min(1.0, score))
    return ("sufficient" if score >= threshold else "insufficient"), round(score, 3)


async def web_fallback(query: str, *, max_results: int = 4) -> list[str]:
    """Web results as extra pseudo-context (SPEC §14.4). Empty if TAVILY_API_KEY
    is unset or the call fails."""
    from ..tools.builtin.web_search import search

    res = await search(query, max_results=max_results)
    if res.get("error"):
        log.info("crag_web_fallback_unavailable", detail=res["error"])
        return []
    out: list[str] = []
    if res.get("answer"):
        out.append(f"[web] {res['answer']}")
    for r in res.get("results") or []:
        if r.get("content"):
            out.append(f"[web] {r.get('title', '')}\n{r['content']}")
    return out
