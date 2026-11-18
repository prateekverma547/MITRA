"""Migrations: the schema the app runs on is the schema the models describe.

The failure these guard against is the one `create_all` made easy: a model
that works on a developer's fresh SQLite and does nothing to the Postgres that
already holds real interviews.
"""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app import db


@pytest.fixture
async def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/migrations.db")
    await db.reset_engine()
    yield
    await db.reset_engine()


def _config(connection):
    from pathlib import Path

    from alembic.config import Config

    config = Config(str(Path(db.__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


async def _run(fn):
    async with db.get_engine().begin() as connection:
        return await connection.run_sync(fn)


async def test_a_fresh_database_is_built_by_the_migrations_and_matches_the_models(database):
    await db.prepare_schema()

    def check(connection):
        tables = set(inspect(connection).get_table_names())
        diff = compare_metadata(MigrationContext.configure(connection), db.Base.metadata)
        return tables, diff

    tables, diff = await _run(check)
    assert {"organisations", "business_units", "users", "decisions", "audit_events"} <= tables
    assert {"jobs", "candidates", "interviews", "clarification_turns"} <= tables
    assert diff == [], f"models and migrations disagree; add a migration: {diff}"


async def test_a_database_from_before_migrations_is_stamped_then_upgraded(database):
    """stg and production were built by create_all. They must be adopted at the
    baseline, not rebuilt, and their rows must survive with sensible values."""

    def build_legacy(connection):
        command.upgrade(_config(connection), "0001")
        connection.execute(text("DROP TABLE alembic_version"))
        connection.execute(
            text(
                "INSERT INTO jobs (id, jd_text, spec_status, spec_version) "
                "VALUES ('job_old', 'An old JD', 'ready', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candidates (id, job_id, cv_text, blueprint_status, spec_version) "
                "VALUES ('cand_old', 'job_old', 'An old CV', 'ready', 1)"
            )
        )

    await _run(build_legacy)
    await db.prepare_schema()

    def read(connection):
        job = connection.execute(text("SELECT request_status, org_id FROM jobs")).one()
        cand = connection.execute(text("SELECT stage FROM candidates")).one()
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return job, cand, version

    job, cand, version = await _run(read)
    assert tuple(job) == ("open", None)
    assert cand[0] == "new"
    assert version == "0002"


async def test_running_it_twice_changes_nothing(database):
    await db.prepare_schema()
    await db.prepare_schema()


async def test_the_organisation_migration_can_be_reversed(database):
    def round_trip(connection):
        config = _config(connection)
        command.upgrade(config, "head")
        command.downgrade(config, "0001")
        after_down = set(inspect(connection).get_table_names())
        command.upgrade(config, "head")
        return after_down

    after_down = await _run(round_trip)
    assert "users" not in after_down
    assert "jobs" in after_down
