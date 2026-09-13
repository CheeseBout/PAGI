"""Thin LLM + embedding wrapper used by the eval metrics / synth.

Kept small and injectable so tests pass a fake instead of hitting a provider.
``complete`` returns raw text; ``complete_json`` parses the first JSON object.
"""

from __future__ import annotations

import json
import re

import structlog

from ...config import get_settings
from ..embeddings import embed_texts

log = structlog.get_logger("pagi.rag.eval")

_JSON_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


class Judge:
    def __init__(
        self, *, model: str | None = None, provider: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        s = get_settings()
        self.model = model or s.summary_model or "gpt-4o-mini"
        self.provider = provider or "openai"
        self.embed_model = embed_model or s.rag_embedding_model

    async def complete(self, system: str, user: str, *, max_tokens: int = 800) -> str:
        from ...providers import DoneEvent, TextDelta, UsageEvent, get_provider

        try:
            prov = get_provider(self.provider)
        except Exception:
            prov = get_provider("openai")
        parts: list[str] = []
        async for ev in prov.stream_chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[],
            model=self.model,
            temperature=0.0,
            max_tokens=max_tokens,
        ):
            if isinstance(ev, TextDelta):
                parts.append(ev.content)
            elif isinstance(ev, (DoneEvent, UsageEvent)):
                continue
        return "".join(parts)

    async def complete_json(self, system: str, user: str, *, max_tokens: int = 800):
        raw = await self.complete(system, user, max_tokens=max_tokens)
        m = _JSON_RE.search(raw)
        if not m:
            raise ValueError(f"judge returned no JSON: {raw[:200]!r}")
        return json.loads(m.group(0))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await embed_texts(self.embed_model, texts, kind="eval")
