"""Schema tests — the part of the database that bites in deployment, not locally.

`create_all` creates missing tables and silently ignores tables that already
exist. So adding a field to a model works perfectly on a developer's fresh
SQLite file and does nothing at all to the Railway Postgres that was created
before the field existed. The failure surfaces as a query error in production
against a schema that looked fine in every test.

These tests pin the additive-migration step that closes that gap.
"""

import pytest
from sqlalchemy import Column, Integer, String, inspect, text

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


async def test_a_new_nullable_column_is_added_to_an_existing_table(database):
    await db.create_all()
    assert "late_addition" not in await columns_of("candidates")

    # Simulate a developer adding a field to the model after the table exists.
    table = db.Candidate.__table__
    table.append_column(Column("late_addition", String, nullable=True))
    try:
        await db.create_all()
        assert "late_addition" in await columns_of("candidates")
    finally:
        table._columns.remove(table.c.late_addition)


async def test_a_new_not_null_column_refuses_rather_than_guessing(database):
    """Existing rows have no value for it, and inventing one corrupts records."""
    await db.create_all()

    table = db.Candidate.__table__
    table.append_column(Column("required_addition", String, nullable=False))
    try:
        with pytest.raises(RuntimeError, match="needs a real migration"):
            await db.create_all()
    finally:
        table._columns.remove(table.c.required_addition)


async def test_a_not_null_column_with_a_server_default_is_allowed(database):
    """The default answers "what do existing rows get?", so nothing is guessed."""
    await db.create_all()

    table = db.Candidate.__table__
    table.append_column(
        Column("counted", Integer, nullable=False, server_default="1")
    )
    try:
        await db.create_all()
        assert "counted" in await columns_of("candidates")
    finally:
        table._columns.remove(table.c.counted)


async def test_existing_rows_survive_the_migration(database):
    """The whole point: adding a field must not cost the data already stored."""
    await db.create_all()
    async with db.get_engine().begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO jobs (id, source_filename, jd_text, spec_status) "
                "VALUES ('job_keep', 'jd.txt', 'text', 'ready')"
            )
        )

    table = db.Job.__table__
    table.append_column(Column("late_addition", String, nullable=True))
    try:
        await db.create_all()
        async with db.get_engine().begin() as connection:
            rows = (await connection.execute(text("SELECT id FROM jobs"))).all()
        assert [r[0] for r in rows] == ["job_keep"]
    finally:
        table._columns.remove(table.c.late_addition)


async def test_the_refinement_column_the_panel_depends_on_exists(database):
    """Named explicitly because it is the column that motivated the migration."""
    await db.create_all()
    assert "blueprint_refinements" in await columns_of("candidates")
