"""Async engine / session factory + schema bootstrap.

Schema is managed by Alembic (``backend/alembic``). On startup we run
``alembic upgrade head`` in-process against a sync connection borrowed from the
async engine. A database that predates Alembic (tables but no
``alembic_version``) is brought up to the current column/table set and then
stamped, so existing dev installs upgrade without a manual step.

Tests set ``PAGI_SKIP_ALEMBIC=1`` and get a plain ``create_all`` instead.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import get_settings

_settings = get_settings()
_is_sqlite = _settings.async_database_url.startswith("sqlite")
_BACKEND_DIR = Path(__file__).resolve().parents[2]

# For sqlite file DSNs, make sure the parent directory exists.
if _settings.async_database_url.startswith("sqlite+aiosqlite:///"):
    _db_path = _settings.async_database_url.replace("sqlite+aiosqlite:///", "", 1)
    if _db_path and _db_path != ":memory:":
        Path(_db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

_connect_args = {"timeout": 30} if _is_sqlite else {}
engine = create_async_engine(
    _settings.async_database_url, echo=False, future=True, connect_args=_connect_args
)

if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── columns added to already-existing tables since the last release ──────
# Only used when upgrading a pre-Alembic database in place; fresh databases get
# these from create_all / the baseline migration.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "messages": [("attachments", "JSON")],
    "sessions": [
        ("turn_status", "VARCHAR(16) DEFAULT 'idle'"),
        ("turn_started_at", "TIMESTAMP"),
        ("summary", "TEXT"),
        ("summary_upto_message_id", "VARCHAR(64)"),
        ("parent_session_id", "VARCHAR"),
        ("root_session_id", "VARCHAR"),
        ("depth", "INTEGER DEFAULT 0"),
        ("kind", "VARCHAR(16) DEFAULT 'chat'"),
        ("spawned_by_tool_call_id", "VARCHAR(64)"),
        ("delegated_task", "TEXT"),
    ],
    "traces": [
        ("cached_tokens", "INTEGER"),
        ("cache_write_tokens", "INTEGER"),
        ("kind", "VARCHAR(16) DEFAULT 'chat'"),
    ],
    "agents": [
        ("kb_collection_ids", "JSON"),
        ("rag_config", "JSON"),
        ("is_delegatable", "BOOLEAN DEFAULT 0"),
        ("delegate_description", "TEXT DEFAULT ''"),
        ("orchestration", "JSON"),
    ],
}


def _ensure_added_columns(sync_conn) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    for table, cols in _ADDED_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, coltype in cols:
            if name not in existing:
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))


def _alembic_config(sync_conn):
    from alembic.config import Config

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.attributes["connection"] = sync_conn
    return cfg


def _bootstrap_schema(sync_conn) -> None:
    from alembic import command
    from sqlalchemy import inspect

    inspector = inspect(sync_conn)
    had_tables = inspector.has_table("users")
    managed = inspector.has_table("alembic_version")
    cfg = _alembic_config(sync_conn)

    if had_tables and not managed:
        # Pre-Alembic database: add the new tables/columns, then adopt it.
        SQLModel.metadata.create_all(sync_conn)
        _ensure_added_columns(sync_conn)
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")


async def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    if os.environ.get("PAGI_SKIP_ALEMBIC"):
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        return

    async with engine.begin() as conn:
        await conn.run_sync(_bootstrap_schema)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def sqlite_in_use() -> bool:
    return _settings.async_database_url.startswith("sqlite")
