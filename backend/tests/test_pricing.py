"""Dynamic cost estimation from litellm's price map (SPEC §1.8)."""

from __future__ import annotations

import litellm

from app.observability.tracing import estimate_cost, price_lookup


def test_zero_tokens_is_free():
    assert estimate_cost("whatever", 0, 0) == 0.0


def test_uses_litellm_cost_per_token(monkeypatch):
    calls: list[dict] = []

    def fake_cost_per_token(**kwargs):
        calls.append(kwargs)
        return (0.001, 0.002)

    monkeypatch.setattr(litellm, "cost_per_token", fake_cost_per_token)
    got = estimate_cost("claude-haiku-4-5", 100, 50, provider="anthropic")
    assert got == 0.003
    assert calls and calls[0]["custom_llm_provider"] == "anthropic"
    assert calls[0]["prompt_tokens"] == 100


def test_falls_back_when_litellm_unknown(monkeypatch):
    def boom(**_kwargs):
        raise ValueError("model not in map")

    monkeypatch.setattr(litellm, "cost_per_token", boom)
    # gpt-4o-mini is in the offline fallback table -> (0.15, 0.6) per 1M
    got = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
    assert got == round(0.15 + 0.6, 6)


def test_returns_none_for_truly_unknown(monkeypatch):
    monkeypatch.setattr(litellm, "cost_per_token", lambda **_k: (_ for _ in ()).throw(KeyError("x")))
    assert estimate_cost("no-such-model-anywhere", 10, 10) is None


def test_price_lookup_shape(monkeypatch):
    # cost_per_token returns the cost for the token counts it is given.
    def fake(**k):
        return (k["prompt_tokens"] * 3e-6, k["completion_tokens"] * 15e-6)

    monkeypatch.setattr(litellm, "cost_per_token", fake)
    out = price_lookup("claude-sonnet-4-5", "anthropic")
    assert out["source"] == "litellm"
    assert out["input_per_1m"] == 3.0
    assert out["output_per_1m"] == 15.0
