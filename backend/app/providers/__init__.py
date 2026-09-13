"""LLM provider adapters (SPEC §4). All four share one interface and route
through litellm; agent_runtime only ever sees normalised StreamEvent objects."""

from __future__ import annotations

from ..config import get_settings
from .anthropic_provider import AnthropicProvider
from .base import (
    DoneEvent,
    LLMProvider,
    StreamEvent,
    TextDelta,
    ToolCallComplete,
    UsageEvent,
)
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider
from .openrouter_provider import OpenRouterProvider

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openrouter": OpenRouterProvider,
}


def get_provider(name: str) -> LLMProvider:
    try:
        cls = _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown provider: {name!r}") from exc
    settings = get_settings()
    key = getattr(settings, f"{name}_api_key")
    return cls(api_key=key)


def provider_supports_vision(provider: str, model: str) -> bool:
    """Best-effort: does this provider/model accept image inputs? Unknown -> True
    (let the provider reject it) so a stale litellm map never blocks a capable
    model."""
    try:
        import litellm

        return bool(litellm.supports_vision(model=model, custom_llm_provider=provider))
    except Exception:
        return True


__all__ = [
    "get_provider",
    "provider_supports_vision",
    "LLMProvider",
    "StreamEvent",
    "TextDelta",
    "ToolCallComplete",
    "UsageEvent",
    "DoneEvent",
]
