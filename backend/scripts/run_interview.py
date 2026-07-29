"""Book an interview and serve the candidate join page.

    uv run python scripts/run_interview.py --candidate cand_1a2c5787064a

Books the interview, prints the meeting credentials, then serves the API so you
can join at http://localhost:8000/join exactly as a real candidate would:
credentials, consent notice, then the call.

Ctrl-C when the interview is over and it prints the stored record.

An earlier version printed `room_url?t=token` and told you to open it. That does
not work — Daily has no query-parameter form for meeting tokens. The token must
be handed to `join({url, token})` in the browser SDK, which is what the join
page does.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db import create_all  # noqa: E402
from app.main import app  # noqa: E402
from bot.config import MissingConfig, Settings  # noqa: E402


async def book(candidate_id: str) -> dict | None:
    await create_all()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api"
    ) as client:
        response = await client.post(f"/candidates/{candidate_id}/interviews")
        if response.status_code != 200:
            print(f"Could not book the interview: {response.status_code} {response.text}")
            return None
        return response.json()


async def show_result(interview_id: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api"
    ) as client:
        view = (await client.get(f"/interviews/{interview_id}")).json()

    print("\n" + "=" * 72)
    print(f"  INTERVIEW {view['status'].upper()}")
    print("=" * 72)
    if view.get("failure_reason"):
        print("  failure:", view["failure_reason"])

    transcript = view.get("transcript") or {}
    turns = transcript.get("turns", [])
    print(f"  {len(turns)} turns over {transcript.get('duration_seconds', 0):.0f}s\n")
    for turn in turns:
        who = "INTERVIEWER" if turn["speaker"] == "interviewer" else "candidate  "
        print(f"  [{turn['at_seconds']:>6.1f}s] {who}: {turn['text']}")

    outcomes = [o for o in (view.get("section_outcomes") or []) if o.get("turns_spent")]
    if outcomes:
        print()
        for outcome in outcomes:
            print(
                f"  {outcome['section_id']:30} {outcome['coverage']:12} "
                f"turns={outcome['turns_spent']} declined={outcome.get('declined_turns', 0)}"
            )
    print(f"\n  Full record: GET /interviews/{interview_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one interview end to end.")
    parser.add_argument("--candidate", required=True, help="Candidate id (cand_...).")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    try:
        Settings.load()
    except MissingConfig as exc:
        print(exc)
        return 1

    booking = asyncio.run(book(args.candidate))
    if booking is None:
        return 1

    print("\n" + "=" * 72)
    print("  INTERVIEW BOOKED — these are what the employer sends the candidate")
    print("=" * 72)
    print(f"  meeting ID : {booking['meeting_id']}")
    print(f"  password   : {booking['password']}")
    print("=" * 72)
    print(f"  JOIN HERE  : http://localhost:{args.port}/join")
    print("=" * 72)
    print("  Enter the credentials above, tick the consent box, and join.")
    print("  The bot starts when you join, not before.")
    print("  Ctrl-C here when you are done to see the stored record.")
    print("=" * 72 + "\n")

    try:
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass

    asyncio.run(show_result(booking["interview_id"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
