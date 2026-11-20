"""Schema tests for a freshly created database.

The earlier version of this file tested a runtime step that added missing columns to
live tables. That step is gone: the schema now changes only through Alembic revisions,
and the tests that matter for that live in test_migrations.py.
"""

import pytest
from sqlalchemy import inspect

from app import db


@pytest.fixture
async def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/schema.db")
    await db.reset_engine()
    yield
    await db.reset_engine()


async def columns_of(table: str) -> set[str]:
    async with db.get_engine().begin() as connection:
        return await connection.run_sync(
            lambda sync: {c["name"] for c in inspect(sync).get_columns(table)}
        )


async def test_a_fresh_database_has_the_refinement_column_the_panel_depends_on(database):
    await db.create_all()
    assert "blueprint_refinements" in await columns_of("candidates")


async def test_a_fresh_database_has_the_recording_columns(database):
    await db.create_all()
    assert {"recording_status", "recording_expires_at", "recording_path"} <= await columns_of(
        "interviews"
    )
