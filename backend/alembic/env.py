"""Alembic environment.

Targets SQLModel metadata. Runs either standalone (``alembic upgrade head`` from
a shell, using ``database_url`` from settings) or in-process during app startup,
in which case ``app.db.session`` passes a live sync connection via
``config.attributes['connection']``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from app.config import get_settings
from app.db import models  # noqa: F401  (register tables on SQLModel.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Migrations run against a *sync* DSN (strip the async driver suffix).
_dsn = get_settings().database_url
config.set_main_option("sqlalchemy.url", _dsn)

# batch mode = SQLite can ALTER via table-copy; harmless on Postgres.
_BATCH = _dsn.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_BATCH,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_BATCH,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    injected = config.attributes.get("connection")
    if injected is not None:
        _run(injected)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
