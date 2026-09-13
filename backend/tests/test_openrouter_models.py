"""GET /api/meta/openrouter-models — free-tier model picker (agent editor)."""

from __future__ import annotations

import pytest

from app.api import routes_meta


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, url, headers=None):
        self.calls.append({"url": url, "headers": headers})
        return self._resp


_PAYLOAD = {
    "data": [
        {"id": "meta-llama/llama-3.1-8b-instruct:free", "name": "Llama 3.1 8B (free)",
         "context_length": 131072, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "some/zero-priced-model", "name": "Zero Priced",
         "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-4o", "name": "GPT-4o",
         "context_length": 128000, "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
    ]
}


@pytest.fixture(autouse=True)
def _reset_cache():
    routes_meta._or_models_cache["ts"] = 0.0
    routes_meta._or_models_cache["models"] = []
    yield
    routes_meta._or_models_cache["ts"] = 0.0
    routes_meta._or_models_cache["models"] = []


def _patch(monkeypatch, payload=_PAYLOAD):
    client = _FakeClient(_FakeResp(payload))
    monkeypatch.setattr(routes_meta, "safe_async_client", lambda *_a, **_k: client)
    return client


async def test_requires_auth(client):
    resp = await client.get("/api/meta/openrouter-models")
    assert resp.status_code == 401


async def test_filters_to_free_models_only(auth_client, monkeypatch):
    _patch(monkeypatch)
    resp = await auth_client.get("/api/meta/openrouter-models")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()}
    assert ids == {"meta-llama/llama-3.1-8b-instruct:free", "some/zero-priced-model"}
    assert "openai/gpt-4o" not in ids


async def test_sends_api_key_when_configured(auth_client, monkeypatch):
    fake_client = _patch(monkeypatch)
    monkeypatch.setattr(
        routes_meta, "get_settings", lambda: type("S", (), {"openrouter_api_key": "or-test-key"})()
    )
    await auth_client.get("/api/meta/openrouter-models")
    assert fake_client.calls[0]["headers"]["Authorization"] == "Bearer or-test-key"


async def test_response_is_cached_across_calls(auth_client, monkeypatch):
    fake_client = _patch(monkeypatch)
    await auth_client.get("/api/meta/openrouter-models")
    await auth_client.get("/api/meta/openrouter-models")
    assert len(fake_client.calls) == 1


async def test_upstream_error_returns_502(auth_client, monkeypatch):
    class _BoomClient(_FakeClient):
        async def get(self, url, headers=None):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(routes_meta, "safe_async_client", lambda *_a, **_k: _BoomClient(None))
    resp = await auth_client.get("/api/meta/openrouter-models")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "openrouter_unreachable"
