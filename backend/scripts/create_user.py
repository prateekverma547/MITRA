"""Create a console account, on any database including a deployed one.

    cd backend && uv run python scripts/create_user.py \\
        --org "Acme" --email hr@acme.com --name "Asha Rao" --role hr
    cd backend && uv run python scripts/create_user.py \\
        --org "Acme" --email ai.head@acme.com --name "Ravi K" --role bu_head --bu AI

The password is asked for at the prompt, so it never lands in shell history.
The organisation and BU are created if they do not exist yet, which is how the
first admin of a new deployment gets in. After that, user management moves
into the console (phase 4).
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from org.users import UserError, create_user, get_or_create_bu, get_or_create_org  # noqa: E402
from shared.stages import Role  # noqa: E402


async def main(args: argparse.Namespace, password: str) -> int:
    await db.prepare_schema()
    async with db.get_sessionmaker()() as session:
        org = await get_or_create_org(session, args.org)
        bu_id = (await get_or_create_bu(session, org, args.bu)).id if args.bu else None
        try:
            user = await create_user(
                session, org_id=org.id, email=args.email, name=args.name,
                role=Role(args.role), password=password, bu_id=bu_id,
            )
        except UserError as exc:
            print(f"Not created: {exc}")
            return 1
        await session.commit()
    await db.reset_engine()
    print(f"Created {user.email} ({user.role}{', ' + args.bu if args.bu else ''}) in {args.org}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--org", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    parser.add_argument("--bu", help="Business unit name; BU heads only.")
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Again: "):
        print("The two passwords differ.")
        sys.exit(1)
    sys.exit(asyncio.run(main(args, password)))
