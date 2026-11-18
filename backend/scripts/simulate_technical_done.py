"""Mark a booked technical round as done, on a developer's machine only.

    cd backend && uv run python scripts/simulate_technical_done.py "Candidate Name"
    cd backend && uv run python scripts/simulate_technical_done.py cand_1234abcd

Until Track I's interview lands, nothing moves a candidate from "Technical
booked" to "Technical done", so the second half of the journey (HR sends them
to the BU head, who decides) cannot be walked through with your own candidate.
This makes that one system move, and nothing else: no report is attached, and
the results page says plainly that there is no technical report.

The move is recorded with the reason "Simulated locally", so it can never pass
for a real interview. Refuses to run on a deployed instance.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import db  # noqa: E402
from app.db import Candidate  # noqa: E402
from hiring.stages import move  # noqa: E402
from shared.stages import CandidateStage  # noqa: E402

REASON = "Simulated locally: no technical interview was held. Track I's interview replaces this."


async def main(who: str) -> int:
    if db.is_deployed():
        print("Refusing: this is a deployed instance. Technical rounds finish when the interview does.")
        return 2
    async with db.get_sessionmaker()() as session:
        candidate = await session.get(Candidate, who)
        if candidate is None:
            matches = (
                await session.execute(
                    select(Candidate).where(
                        Candidate.name.ilike(f"%{who}%"),
                        Candidate.stage == CandidateStage.TECHNICAL_BOOKED.value,
                    )
                )
            ).scalars().all()
            if len(matches) != 1:
                print(f"{len(matches)} candidates named like {who!r} have a technical round booked. Use the candidate id.")
                for c in matches:
                    print(f"  {c.id}  {c.name}")
                return 1
            candidate = matches[0]
        if candidate.stage != CandidateStage.TECHNICAL_BOOKED.value:
            print(f"{candidate.name} is at {candidate.stage!r}. Only a booked technical round can be marked done.")
            return 1
        await move(session, candidate, CandidateStage.TECHNICAL_DONE, by=None, reason=REASON)
        await session.commit()
    await db.reset_engine()
    print(f"{candidate.name}: technical round marked done. HR can now send them to the BU head.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("candidate", help="Candidate id, or part of their name")
    sys.exit(asyncio.run(main(parser.parse_args().candidate)))
