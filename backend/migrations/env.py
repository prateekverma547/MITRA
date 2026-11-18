"""Alembic environment.

Two ways in:

- **From the app** (`app.db.prepare_schema`), which already holds an async
  connection inside the running event loop. It passes that connection in
  `config.attributes["connection"]` and we migrate on it directly. Calling
  `asyncio.run` from here would fail inside the app's loop.
- **From the command line** (`uv run alembic upgrade head`, `alembic revision
  --autogenerate`). We open our own async engine for that.

SQLite cannot alter a table in place, so on SQLite every change runs in batch
mode (copy, alter, swap). Postgres does not need it and does not get it.
"""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import Base, database_url, load_models

load_models()
target_metadata = Base.metadata
config = context.config


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online_async() -> None:
    engine = create_async_engine(database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_configure)
        await connection.commit()
    await engine.dispose()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
    else:
        asyncio.run(_run_online_async())


if context.is_offline_mode():
    run_offline()
else:
    run_online()
