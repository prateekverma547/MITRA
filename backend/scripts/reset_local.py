"""Clear a developer's database back to the demo accounts, on a local machine only.

    cd backend && uv run python scripts/reset_local.py --yes

Removes every job request, candidate, interview, HR round, decision and audit
event, and keeps the organisation, its business units and the people who sign
in. A SQLite database is copied to `<file>.bak-<time>` first, and the session
files (transcript logs and recordings) are moved into `sessions/.bak-<time>/`,
so nothing is lost by accident. Then upload the files in `samples/` and walk the journey (see
samples/README.md).

Refuses to run on a deployed instance.
"""

import argparse
import asyncio
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete  # noqa: E402

from app import db  # noqa: E402
from bot.config import SESSIONS_DIR  # noqa: E402
from org.seed import DEMO_PASSWORD, seed_demo_org  # noqa: E402

#: Kept: who signs in, and the units they belong to.
KEEP = {"organisations", "business_units", "users", "alembic_version"}


def _backup() -> str | None:
    url = db.database_url()
    if not url.startswith("sqlite"):
        return None
    path = Path(url.split("///", 1)[1])
    if not path.is_file():
        return None
    copy = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, copy)
    return str(copy)


def _move_session_files(stamp: str) -> int:
    """Move every interview's local files aside; they belong to rows being removed."""
    if not SESSIONS_DIR.is_dir():
        return 0
    moved = 0
    target = SESSIONS_DIR / f".bak-{stamp}"
    for item in SESSIONS_DIR.iterdir():
        if item.name.startswith("."):
            continue
        target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item), str(target / item.name))
        moved += 1
    return moved


async def main(confirmed: bool) -> int:
    if db.is_deployed():
        print("Refusing: this is a deployed instance. Its records belong to real people.")
        return 2
    if not confirmed:
        print("This removes every job, candidate and interview from your local database. Run again with --yes.")
        return 1
    await db.prepare_schema()
    backup = _backup()
    db.load_models()
    async with db.get_sessionmaker()() as session:
        for table in reversed(db.Base.metadata.sorted_tables):
            if table.name not in KEEP:
                await session.execute(delete(table))
        await seed_demo_org(session, password=DEMO_PASSWORD, examples=False)
        await session.commit()
    await db.reset_engine()
    moved = _move_session_files(f"{datetime.now():%Y%m%d-%H%M%S}")
    print("Cleared. The organisation, its business units and the four demo accounts are kept.")
    if moved:
        print(f"Moved {moved} session file(s) (transcript logs, recordings) into {SESSIONS_DIR}/.bak-<time>/")
    if backup:
        print(f"Backup of the previous database: {backup}")
    print("Next: upload the files in samples/ (see samples/README.md).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="Confirm: remove everything but the accounts.")
    sys.exit(asyncio.run(main(parser.parse_args().yes)))
