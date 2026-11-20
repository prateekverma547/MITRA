"""Backup and restore: the parts that run without PostgreSQL.

The Postgres path (pg_dump, pg_restore) is exercised against a real PostgreSQL 16
server by the drill in docs/OPERATIONS.md, because a test that stubs pg_dump would
prove nothing about the dump.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str):
    """Import a script by path, so the tests need no package layout for scripts/."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backup = load("db_backup")
restore = load("db_restore")


def make_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE interviews (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO interviews VALUES ('int_1')")


def test_the_url_is_normalised_the_way_the_app_reads_it():
    assert backup.normalised_url("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    assert backup.normalised_url("postgres://u:p@h/db") == "postgresql://u:p@h/db"
    assert backup.normalised_url("sqlite+aiosqlite:///x.db") == "sqlite:///x.db"


def test_the_password_stays_out_of_the_command_line(monkeypatch):
    """A password in argv shows up in the process list and in shell history."""
    args, env = backup.pg_connection_args("postgresql://user:s3cret@db.example:6543/mitra")
    assert "s3cret" not in " ".join(args)
    assert env["PGPASSWORD"] == "s3cret"
    assert args[args.index("-p") + 1] == "6543"


def test_a_sqlite_backup_is_a_consistent_checked_copy(tmp_path):
    source = tmp_path / "live.db"
    make_sqlite(source)
    target = tmp_path / "copy.dump"

    backup.backup_sqlite(f"sqlite:///{source}", target)

    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT id FROM interviews").fetchall() == [("int_1",)]


def test_a_sqlite_backup_of_a_missing_file_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="does not exist"):
        backup.backup_sqlite(f"sqlite:///{tmp_path}/nope.db", tmp_path / "x.dump")


def test_pruning_keeps_the_newest_and_removes_their_checksums(tmp_path):
    names = [f"mitra-2026110{d}T000000Z.dump" for d in range(1, 6)]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
        (tmp_path / (name + ".sha256")).write_text("abc")

    removed = backup.prune(tmp_path, keep=2)

    remaining = sorted(p.name for p in tmp_path.glob("mitra-*.dump"))
    assert remaining == sorted(names[-2:])
    assert len(removed) == 3
    assert not (tmp_path / (names[0] + ".sha256")).exists()


def test_a_tampered_backup_is_refused_before_anything_is_restored(tmp_path):
    dump = tmp_path / "mitra-x.dump"
    dump.write_bytes(b"real contents")
    (tmp_path / "mitra-x.dump.sha256").write_text(f"{'0' * 64}  mitra-x.dump\n")

    with pytest.raises(RuntimeError, match="checksum"):
        restore.verify_checksum(dump)


def test_a_backup_with_its_checksum_verifies(tmp_path):
    dump = tmp_path / "mitra-y.dump"
    dump.write_bytes(b"real contents")
    digest = backup.sha256_of(dump)
    (tmp_path / "mitra-y.dump.sha256").write_text(f"{digest}  mitra-y.dump\n")

    restore.verify_checksum(dump)  # no error


def test_the_restore_target_name_is_read_from_the_url():
    assert restore.target_name("postgresql://u:p@h:5432/mitra_restored") == "mitra_restored"
    assert restore.target_name("sqlite:////tmp/x/live.db") == "live.db"
