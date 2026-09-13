"""web_search tool (Tavily) — SPEC §5.2."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import ToolContext
from app.tools.builtin import web_search

CTX = ToolContext(session_id="s", db=None)  # handler ignores ctx


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, json=None):
        self.calls.append({"url": url, "json": json})
        return self._resp


def _patch(monkeypatch, resp, *, key="test-key"):
    monkeypatch.setattr(web_search, "get_settings", lambda: SimpleNamespace(tavily_api_key=key))
    client = _FakeClient(resp)
    monkeypatch.setattr(web_search.httpx, "AsyncClient", lambda **_kw: client)
    return client


@pytest.mark.asyncio
async def test_missing_key(monkeypatch):
    monkeypatch.setattr(web_search, "get_settings", lambda: SimpleNamespace(tavily_api_key=None))
    out = await web_search._handler(CTX, {"query": "anything"})
    assert "not configured" in out["error"]


@pytest.mark.asyncio
async def test_missing_query(monkeypatch):
    _patch(monkeypatch, _FakeResp())
    out = await web_search._handler(CTX, {"query": "   "})
    assert out["error"] == "query is required"


@pytest.mark.asyncio
async def test_happy_path(monkeypatch):
    payload = {
        "answer": "Paris is the capital of France.",
        "results": [
            {"title": "France", "url": "https://x/1", "content": "Paris ...", "score": 0.9},
            {"title": "Capital", "url": "https://x/2", "content": "Paris ...", "score": 0.8},
        ],
    }
    client = _patch(monkeypatch, _FakeResp(payload=payload))
    out = await web_search._handler(CTX, {"query": "capital of France", "max_results": 2})

    assert out["answer"].startswith("Paris")
    assert len(out["results"]) == 2
    assert out["results"][0]["url"] == "https://x/1"
    assert "France" in out["results_text"]
    assert out["truncated"] is False
    # api key travels in the body, clamped max_results forwarded
    assert client.calls[0]["json"]["api_key"] == "test-key"
    assert client.calls[0]["json"]["max_results"] == 2


@pytest.mark.asyncio
async def test_bad_key_status(monkeypatch):
    _patch(monkeypatch, _FakeResp(status_code=401, text="unauthorized"))
    out = await web_search._handler(CTX, {"query": "x"})
    assert "invalid TAVILY_API_KEY" in out["error"]


@pytest.mark.asyncio
async def test_content_budget_truncates(monkeypatch):
    big = "z" * 20000
    payload = {"results": [{"title": "t", "url": "u", "content": big}]}
    _patch(monkeypatch, _FakeResp(payload=payload))
    out = await web_search._handler(CTX, {"query": "x"})
    assert out["truncated"] is True
    assert len(out["results"][0]["content"]) <= web_search._MAX_TOTAL_CONTENT
