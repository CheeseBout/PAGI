from __future__ import annotations

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI — litellm accepts the bare model id (e.g. ``gpt-4.1``)."""

    prefix = ""
    stream_usage = True
