"""Restore a MITRA backup into a database. DESTRUCTIVE: it replaces what is there.

    python scripts/db_restore.py --file backups/mitra-20261105T120000Z.dump \\
        --target-url postgresql://user:pass@host:5432/mitra_restored \\
        --confirm mitra_restored

Safety, in the order the script applies it:

1. **No default target.** `--target-url` is required. A restore that quietly used
   DATABASE_URL would overwrite the live database because someone ran a command in the
   wrong terminal.
2. **The target name must be typed back.** `--confirm` has to equal the target
   database's name. That is the one moment a person checks they have the right one.
3. **The checksum must match** when a `.sha256` sidecar is present. A dump that changed
   on disk is not restored.
4. **A SQLite target that exists is copied aside first**, so a bad restore can be undone.

After a Postgres restore, run `alembic current` (from backend/) to confirm the revision,
then `alembic upgrade head` if the backup is older than the code. Both are safe to run.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from db_backup import normalised_url, pg_connection_args, sqlite_file


def expected_checksum(dump: Path) -> str | None:
    sidecar = dump.with_name(dump.name + ".sha256")
    if not sidecar.exists():
        return None
    return sidecar.read_text(encoding="ascii").split()[0]


def verify_checksum(dump: Path) -> None:
    expected = expected_checksum(dump)
    if expected is None:
        print("no .sha256 sidecar next to this backup, so its contents are not checked", file=sys.stderr)
        return
    digest = hashlib.sha256()
    with dump.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError("the backup does not match its checksum; it has changed since it was made")


def target_name(url: str) -> str:
    if url.startswith("sqlite://"):
        return sqlite_file(url).name
    return (urlsplit(url).path or "/").lstrip("/")


def restore_postgres(dump: Path, url: str) -> None:
    pg_restore = os.environ.get("PG_RESTORE", "pg_restore")
    args, env = pg_connection_args(url)
    # --clean --if-exists drops what the dump contains before recreating it, so the
    # target ends up as the backup and nothing else.
    with dump.open("rb") as stream:
        result = subprocess.run(
            [pg_restore, "--clean", "--if-exists", "--no-owner", "--no-privileges", *args],
            stdin=stream,
            capture_output=True,
            env=env,
        )
    if result.returncode != 0:
        # pg_restore reports errors on stderr and still exits non-zero for warnings it
        # considers harmless, so show what it said rather than only the code.
        raise RuntimeError(f"pg_restore failed: {result.stderr.decode(errors='replace').strip()}")


def restore_sqlite(dump: Path, url: str) -> None:
    target = sqlite_file(url)
    if target.exists():
        aside = target.with_suffix(target.suffix + ".before-restore")
        shutil.copy2(target, aside)
        print(f"copied the existing file aside to {aside}")
    shutil.copy2(dump, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a MITRA backup. Replaces the target.")
    parser.add_argument("--file", type=Path, required=True, help="the backup to restore")
    parser.add_argument("--target-url", required=True, help="the database to restore into (never defaulted)")
    parser.add_argument("--confirm", required=True, help="the target database's name, typed back")
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"no such backup: {args.file}", file=sys.stderr)
        return 2

    url = normalised_url(args.target_url)
    name = target_name(url)
    if args.confirm != name:
        print(
            f"--confirm must equal the target database name ({name!r}). Nothing was restored.",
            file=sys.stderr,
        )
        return 2

    try:
        verify_checksum(args.file)
        if url.startswith("postgresql://"):
            restore_postgres(args.file, url)
        elif url.startswith("sqlite://"):
            restore_sqlite(args.file, url)
        else:
            print("Unrecognised target scheme; nothing was restored.", file=sys.stderr)
            return 2
    except Exception as exc:  # noqa: BLE001
        print(f"RESTORE FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"restored {args.file.name} into {name!r}. Next: alembic current, then alembic upgrade head if behind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
