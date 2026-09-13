from __future__ import annotations

from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    """Anthropic (Claude) via litellm — model strings are prefixed ``anthropic/``."""

    prefix = "anthropic/"
