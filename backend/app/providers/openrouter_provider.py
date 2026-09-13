from __future__ import annotations

from .base import LLMProvider


class OpenRouterProvider(LLMProvider):
    """OpenRouter — model strings look like ``openrouter/<vendor>/<model>``."""

    prefix = "openrouter/"
    stream_usage = True
