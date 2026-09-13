"""structlog setup + LLM trace persistence (PLAN §9, SPEC §1.8)."""

from __future__ import annotations

import logging

import structlog

from ..config import get_settings

# Offline fallback only ($/1M tokens). The primary source is litellm's bundled
# price map (``litellm.model_cost``), which ships with the package and refreshes
# on upgrade — see ``estimate_cost``. This table just keeps cost estimates
# working on installs where that map failed to load or lacks the model.
_FALLBACK_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gemini-2.5-flash-lite": (0.1, 0.4),
    "gemini-2.5-pro": (1.25, 10.0),
}


def configure_logging() -> None:
    level = getattr(logging, get_settings().log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def _input_rate_per_token(model: str, provider: str | None) -> float | None:
    """USD per single input token, for prompt-cache adjustments."""
    try:
        import litellm

        kwargs: dict = {"model": model, "prompt_tokens": 1_000_000, "completion_tokens": 0}
        if provider:
            kwargs["custom_llm_provider"] = provider
        pin, _ = litellm.cost_per_token(**kwargs)
        if pin:
            return float(pin) / 1_000_000
    except Exception:
        pass
    fb = _FALLBACK_PRICES.get(model.split("/")[-1])
    return fb[0] / 1_000_000 if fb else None


def estimate_cost(
    model: str,
    tokens_in: int | None,
    tokens_out: int | None,
    *,
    provider: str | None = None,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Estimate the USD cost of one LLM call.

    Primary source is ``litellm.cost_per_token`` (backed by ``litellm.model_cost``,
    a per-token price map bundled with litellm and refreshed on upgrade). Falls
    back to ``_FALLBACK_PRICES`` for models litellm doesn't know, then ``None``.

    ``cached_tokens`` / ``cache_write_tokens`` apply a rough prompt-cache
    correction: for OpenAI/Gemini the cached read is ~50% off and is already
    inside ``tokens_in``; for Anthropic the cache read (~-90%) and cache write
    (~+25%) are additional to ``tokens_in``.
    """
    tin, tout = tokens_in or 0, tokens_out or 0
    if not tin and not tout:
        return 0.0

    total: float | None = None
    try:
        import litellm

        kwargs: dict = {"model": model, "prompt_tokens": tin, "completion_tokens": tout}
        if provider:
            kwargs["custom_llm_provider"] = provider
        prompt_cost, completion_cost = litellm.cost_per_token(**kwargs)
        maybe = float(prompt_cost) + float(completion_cost)
        if maybe > 0:
            total = maybe
    except Exception:  # unknown model, litellm import/version issue, etc.
        pass

    if total is None:
        price = _FALLBACK_PRICES.get(model.split("/")[-1])
        if not price:
            return None
        pin, pout = price
        total = (pin * tin + pout * tout) / 1_000_000

    if cached_tokens or cache_write_tokens:
        per_in = _input_rate_per_token(model, provider)
        if per_in:
            if provider == "anthropic":
                total += per_in * (0.25 * cache_write_tokens - 0.90 * cached_tokens)
            else:
                total -= per_in * 0.50 * cached_tokens
            total = max(0.0, total)

    return round(total, 6)


def price_lookup(model: str, provider: str | None = None) -> dict:
    """Resolved per-1M-token price for a model, for the model picker / admin UI."""
    try:
        import litellm

        kwargs: dict = {"model": model, "prompt_tokens": 1_000_000, "completion_tokens": 0}
        if provider:
            kwargs["custom_llm_provider"] = provider
        pin, _ = litellm.cost_per_token(**kwargs)
        kwargs["prompt_tokens"], kwargs["completion_tokens"] = 0, 1_000_000
        _, pout = litellm.cost_per_token(**kwargs)
        if pin or pout:
            return {
                "model": model,
                "input_per_1m": round(float(pin), 4),
                "output_per_1m": round(float(pout), 4),
                "source": "litellm",
            }
    except Exception:
        pass

    fb = _FALLBACK_PRICES.get(model.split("/")[-1])
    if fb:
        return {"model": model, "input_per_1m": fb[0], "output_per_1m": fb[1], "source": "fallback"}
    return {"model": model, "known": False}


log = structlog.get_logger("pagi")
