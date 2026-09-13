"""Common provider interface + normalised streaming events (SPEC §4.1)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class TextDelta:
    content: str


@dataclass
class ToolCallComplete:
    tool_call_id: str
    name: str
    args: dict


@dataclass
class UsageEvent:
    tokens_in: int
    tokens_out: int
    cache_hit: bool | None = None
    #: prompt tokens served from the provider's prompt cache (read)
    cached_tokens: int = 0
    #: prompt tokens written into the cache this call (Anthropic only)
    cache_write_tokens: int = 0


@dataclass
class DoneEvent:
    finish_reason: str  # "stop" | "tool_calls" | "max_tokens" | "error"


StreamEvent = TextDelta | ToolCallComplete | UsageEvent | DoneEvent


def _attr(obj, key: str):
    """Read *key* from an object or a dict — litellm usage shapes vary by version."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


class LLMProvider:
    """Base adapter. Subclasses only set `prefix` — the litellm model-string
    namespace for that vendor (SPEC §4.3)."""

    prefix: str = ""
    #: send OpenAI-style ``stream_options={"include_usage": True}`` — safe for
    #: openai/openrouter; other vendors reject it through litellm.
    stream_usage: bool = False

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    # -- helpers ---------------------------------------------------------
    def _model_string(self, model: str) -> str:
        if self.prefix and not model.startswith(self.prefix):
            return f"{self.prefix}{model}"
        return model

    # -- main entrypoint ----------------------------------------------------
    async def stream_chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        import litellm  # imported lazily — heavy dependency

        kwargs: dict = {
            "model": self._model_string(model),
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
            kwargs["tool_choice"] = "auto"

        acc: dict[int, dict] = {}
        usage_in = usage_out = 0
        cached_read = cache_write = 0
        finish_reason = "stop"

        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            usage = getattr(chunk, "usage", None)
            if usage:
                usage_in = _attr(usage, "prompt_tokens") or usage_in
                usage_out = _attr(usage, "completion_tokens") or usage_out
                # OpenAI / Gemini / OpenRouter: cached prompt tokens live in
                # prompt_tokens_details.cached_tokens and are part of prompt_tokens.
                details = _attr(usage, "prompt_tokens_details")
                cached_read = _attr(details, "cached_tokens") or cached_read
                # Anthropic (through litellm): separate top-level counters.
                cached_read = _attr(usage, "cache_read_input_tokens") or cached_read
                cache_write = _attr(usage, "cache_creation_input_tokens") or cache_write

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            delta = getattr(choice, "delta", None)

            if delta is not None and getattr(delta, "content", None):
                yield TextDelta(delta.content)

            for tc in (getattr(delta, "tool_calls", None) or []) if delta is not None else []:
                idx = getattr(tc, "index", 0) or 0
                slot = acc.setdefault(idx, {"id": None, "name": "", "args": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["args"] += fn.arguments

            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

        for slot in acc.values():
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            yield ToolCallComplete(
                tool_call_id=slot["id"] or slot["name"] or "call_0",
                name=slot["name"],
                args=args if isinstance(args, dict) else {},
            )

        if usage_in or usage_out or cached_read or cache_write:
            yield UsageEvent(
                tokens_in=usage_in,
                tokens_out=usage_out,
                cache_hit=(cached_read > 0) or None,
                cached_tokens=int(cached_read or 0),
                cache_write_tokens=int(cache_write or 0),
            )
        yield DoneEvent(finish_reason="tool_calls" if acc else finish_reason)
