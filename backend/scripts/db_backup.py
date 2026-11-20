"""Back up the MITRA database to a file that has been checked, then prune old backups.

    python scripts/db_backup.py                 # keeps the 14 newest
    python scripts/db_backup.py --keep 30 --dir /var/backups/mitra

Reads DATABASE_URL and normalises it the way app/db.py does, so the backup is of the
database the app actually uses.

**Postgres (deployed):** `pg_dump` in custom format. The dump is listed with
`pg_restore --list` before it counts as a backup, and a SHA-256 sidecar is written next
to it. The `pg_dump` on PATH must be the same major version as the server, or newer.
Override the binary with PG_DUMP and PG_RESTORE.

**SQLite (local development only):** a consistent copy through the SQLite backup API,
checked with `PRAGMA integrity_check`.

**Never prints the URL or the password.** The backups contain transcripts, CVs and
job descriptions, so store them where only the people who may read those can read them.

Standard library only, on purpose: the machine that runs a backup does not need the
application's dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "backups"
DEFAULT_KEEP = 14


def normalised_url(raw: str) -> str:
    """The same rewrite app/db.py applies, so both read one URL the same way."""
    url = raw
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    url = url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return url


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pg_connection_args(url: str) -> tuple[list[str], dict[str, str]]:
    """Connection flags and environment for pg_dump, with the password kept out of argv.

    A password on the command line shows up in the process list and in shell history.
    It goes in PGPASSWORD instead.
    """
    parts = urlsplit(url)
    args = [
        "-h", parts.hostname or "localhost",
        "-p", str(parts.port or 5432),
        "-U", unquote(parts.username or ""),
        "-d", (parts.path or "/").lstrip("/"),
    ]
    env = dict(os.environ)
    if parts.password:
        env["PGPASSWORD"] = unquote(parts.password)
    return args, env


def backup_postgres(url: str, target: Path) -> None:
    pg_dump = os.environ.get("PG_DUMP", "pg_dump")
    pg_restore = os.environ.get("PG_RESTORE", "pg_restore")
    args, env = pg_connection_args(url)

    with target.open("wb") as out:
        dumped = subprocess.run(
            [pg_dump, "--format=custom", "--no-owner", "--no-privileges", *args],
            stdout=out,
            stderr=subprocess.PIPE,
            env=env,
        )
    if dumped.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {dumped.stderr.decode(errors='replace').strip()}")

    # A dump that cannot be listed cannot be restored. Check it now, while the source
    # still exists, rather than on the day it is needed.
    with target.open("rb") as dump:
        listed = subprocess.run(
            [pg_restore, "--list"], stdin=dump, capture_output=True, env=env
        )
    if listed.returncode != 0:
        raise RuntimeError(f"pg_restore could not read the dump: {listed.stderr.decode(errors='replace').strip()}")
    if b"TABLE DATA" not in listed.stdout:
        raise RuntimeError("the dump lists no table data, so it would restore an empty database")


def sqlite_file(url: str) -> Path:
    """The file a sqlite:// URL points at.

    SQLAlchemy's rule: everything after `sqlite:///` is the path. So `sqlite:///./x.db`
    is the relative file ./x.db, and `sqlite:////tmp/x.db` is the absolute /tmp/x.db.
    Parsing it as a URL path instead reads `/./x.db` as the root of the drive on Windows.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("not a sqlite:/// URL")
    return Path(url[len(prefix):])


def backup_sqlite(url: str, target: Path) -> None:
    source_path = sqlite_file(url)
    if not source_path.exists():
        raise RuntimeError("the SQLite database file does not exist")

    source = sqlite3.connect(source_path)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        source.close()
        dest.close()

    check = sqlite3.connect(target)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"the SQLite copy failed its integrity check: {result}")


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups. Called only after a new one is verified."""
    dumps = sorted(directory.glob("mitra-*.dump"), reverse=True)
    removed: list[Path] = []
    for old in dumps[keep:]:
        old.unlink()
        sidecar = old.with_name(old.name + ".sha256")
        if sidecar.exists():
            sidecar.unlink()
        removed.append(old)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the MITRA database.")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="where backups are written")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="how many to keep")
    args = parser.parse_args()

    raw = os.environ.get("DATABASE_URL")
    if not raw:
        print("DATABASE_URL is not set. Refusing to guess which database to back up.", file=sys.stderr)
        return 2
    if args.keep < 1:
        print("--keep must be at least 1, or the backup would be pruned as soon as it is made.", file=sys.stderr)
        return 2

    url = normalised_url(raw)
    args.dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    final = args.dir / f"mitra-{stamp}.dump"
    partial = final.with_suffix(".partial")

    try:
        if url.startswith("postgresql://"):
            backup_postgres(url, partial)
            kind = "postgres"
        elif url.startswith("sqlite://"):
            backup_sqlite(url, partial)
            kind = "sqlite"
        else:
            print("Unrecognised DATABASE_URL scheme; nothing was backed up.", file=sys.stderr)
            return 2
    except Exception as exc:  # noqa: BLE001
        partial.unlink(missing_ok=True)
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        return 1

    digest = sha256_of(partial)
    partial.replace(final)
    final.with_name(final.name + ".sha256").write_text(f"{digest}  {final.name}\n", encoding="ascii")
    print(f"backed up ({kind}) to {final} ({final.stat().st_size} bytes, sha256 {digest[:16]}...)")

    removed = prune(args.dir, args.keep)
    if removed:
        print(f"pruned {len(removed)} older backup(s); keeping the newest {args.keep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
