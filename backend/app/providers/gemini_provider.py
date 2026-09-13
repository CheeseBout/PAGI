from __future__ import annotations

from .base import LLMProvider


class GeminiProvider(LLMProvider):
    """Google Gemini — litellm requires the ``gemini/`` prefix (SPEC §4.3)."""

    prefix = "gemini/"
