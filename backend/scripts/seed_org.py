"""Create the demo organisation for clicking through the console locally.

    cd backend && uv run python scripts/seed_org.py
    cd backend && uv run python scripts/seed_org.py --password something-longer

Brings the database to the latest migration, then creates (or finds) the demo
organisation from `org/seed.py`: five BUs, an org admin, an HR user, two BU
heads (AI and Finance), one job in each of those BUs, and a screened candidate
in each. Running it twice changes nothing.

Every account gets the same password, printed at the end. Refuses to run on a
deployed instance: these are well-known demo credentials.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from org.seed import PEOPLE, seed_demo_org  # noqa: E402

DEFAULT_PASSWORD = "mitra-demo-password"


async def main(password: str) -> int:
    if db.is_deployed():
        print("Refusing: this is a deployed instance, and demo accounts have a known password.")
        return 2
    await db.prepare_schema()
    async with db.get_sessionmaker()() as session:
        demo = await seed_demo_org(session, password=password)
        await session.commit()
    await db.reset_engine()

    print(f"Organisation {demo.org_id}, BUs: {', '.join(demo.bu_ids)}")
    print(f"Jobs: {', '.join(demo.job_ids)}")
    print("\nSign in at /console with any of these (password for all of them below):")
    for email, name, role, bu in PEOPLE:
        print(f"  {email:28} {role.value:10} {bu or ''}")
    print(f"\nPassword: {password}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.password)))
