"""Test config — must set env before any `app.*` import (settings are cached)."""

from __future__ import annotations

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="pagi-test-")
_TMP_DB = os.path.join(_TMP_DIR, "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB}")
os.environ.setdefault("SESSION_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
# Tests bootstrap the schema with create_all, not the Alembic runner.
os.environ.setdefault("PAGI_SKIP_ALEMBIC", "1")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin")
os.environ.setdefault("SANDBOX_URL", "http://localhost:9")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("UPLOAD_DIR", os.path.join(_TMP_DIR, "uploads"))
os.environ.setdefault("AVATAR_DIR", os.path.join(_TMP_DIR, "avatars"))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db.seed import seed  # noqa: E402
from app.db.session import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402

# Tables in FK-safe delete order (children before parents) — used to reset
# state between tests so they don't see each other's rows on the shared
# sqlite file (module-scoped DATABASE_URL, see above).
_TABLES_FK_ORDER = [
    "traces", "tool_approvals", "session_tool_grants", "memory_chunks",
    "agent_runs", "agent_config_versions", "weakness_reports",
    "agent_eval_cases", "agent_eval_runs",
    "kb_eval_cases", "kb_eval_runs", "kb_query_logs", "kb_chunks",
    "kb_documents", "kb_collections",
    "cron_jobs", "project_iterations", "project_runs",
    "mcp_servers", "messages", "sessions", "agents", "users",
]


async def _reset_db() -> None:
    await init_db()
    async with engine.begin() as conn:
        for table in _TABLES_FK_ORDER:
            await conn.exec_driver_sql(f"DELETE FROM {table}")


@pytest_asyncio.fixture
async def client():
    await _reset_db()
    async with SessionLocal() as db:
        await seed(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_client(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
