from __future__ import annotations

import httpx

from ...config import get_settings
from ..base import ToolContext, ToolSpec, register

_TAVILY_URL = "https://api.tavily.com/search"
_MAX_TOTAL_CONTENT = 8000  # budget across all result snippets, protects context


async def search(
    query: str, *, max_results: int = 5, depth: str = "basic", topic: str = "general"
) -> dict:
    """Raw Tavily search — shared by the tool handler and the CRAG fallback
    (SPEC §14.4 step 7)."""
    query = (query or "").strip()
    if not query:
        return {"error": "query is required"}

    api_key = get_settings().tavily_api_key
    if not api_key:
        return {"error": "web_search is not configured (set TAVILY_API_KEY)"}

    max_results = max(1, min(int(max_results or 5), 10))
    depth = depth if depth in ("basic", "advanced") else "basic"
    topic = topic if topic in ("general", "news") else "general"

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": depth,
        "topic": topic,
        "include_answer": True,
        "include_raw_content": False,
        "include_images": False,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(_TAVILY_URL, json=payload)
    except httpx.HTTPError as exc:
        return {"error": f"web_search request failed: {exc}"}

    if resp.status_code == 401:
        return {"error": "web_search rejected: invalid TAVILY_API_KEY"}
    if resp.status_code == 429:
        return {"error": "web_search rate-limited or out of quota"}
    if resp.status_code >= 400:
        return {"error": f"web_search error {resp.status_code}: {resp.text[:300]}"}

    try:
        data = resp.json()
    except ValueError:
        return {"error": "web_search returned a non-JSON response"}

    results: list[dict] = []
    budget = _MAX_TOTAL_CONTENT
    truncated = False
    for item in data.get("results") or []:
        full = item.get("content") or ""
        snippet = full[: max(budget, 0)]
        if len(snippet) < len(full):
            truncated = True
        budget -= len(snippet)
        results.append(
            {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "content": snippet,
                "score": item.get("score"),
            }
        )
        if budget <= 0:
            truncated = True
            break

    return {
        "query": query,
        "answer": data.get("answer") or "",
        "results": results,
        # flattened text so the prompt-injection audit (which only scans
        # top-level string fields) covers the result bodies too.
        "results_text": "\n\n".join(
            f"{r['title']}\n{r['url']}\n{r['content']}" for r in results
        ),
        "truncated": truncated,
    }


async def _handler(ctx: ToolContext, args: dict) -> dict:
    try:
        max_results = int(args.get("max_results") or 5)
    except (TypeError, ValueError):
        max_results = 5
    return await search(
        args.get("query") or "",
        max_results=max_results,
        depth=args.get("search_depth") or "basic",
        topic=args.get("topic") or "general",
    )


SPEC = register(
    ToolSpec(
        name="web_search",
        description=(
            "Search the web via Tavily and return the top results (title, URL, "
            "content snippet) plus a short synthesized answer. Read-only. Use it "
            "for current events, recent releases, or facts outside your training data."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many results to return (default 5).",
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": "`advanced` is slower but more thorough (default `basic`).",
                },
                "topic": {
                    "type": "string",
                    "enum": ["general", "news"],
                    "description": "Use `news` for recent-events queries (default `general`).",
                },
            },
            "required": ["query"],
        },
        handler=_handler,
        requires_approval=False,
    )
)
