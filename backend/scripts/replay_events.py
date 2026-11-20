"""Play the InterviewEvent fixture through the real emit -> store -> relay path.

Keeps the fixture's own timing: each event is emitted when its `at_seconds`
says, measured from the first event. The fixture spans 30 minutes, so
`--speed 60` plays it in 30 seconds; `--speed 1` is real time.

    cd backend && uv run python scripts/replay_events.py --create --speed 60
    cd backend && uv run python scripts/replay_events.py --interview-id int_0123456789ab --speed 1

Every event is rewritten for the target interview: `interview_id` replaced,
`event_id` prefixed with a run id (so a second replay is new events, not
duplicates), `wall_time` set to the moment it is emitted. Each is re-validated
against InterviewEvent before it is emitted. Point DATABASE_URL at the web
process's database and open /api/live/interviews/<id>/events to watch it.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live.dev import ensure_demo_interview  # noqa: E402
from live.emit import EventEmitter  # noqa: E402
from shared.interview_contracts import InterviewEvent  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "shared" / "fixtures" / "interview_events.json"


def rewrite(raw: dict, interview_id: str, run_id: str) -> InterviewEvent:
    return InterviewEvent.model_validate({
        **raw,
        "interview_id": interview_id,
        "event_id": f"{interview_id}:replay-{run_id}:{raw['event_id']}",
        "wall_time": datetime.now(UTC),
    })


async def replay(interview_id: str, *, speed: float, fixture: Path, run_id: str) -> dict:
    events = sorted(json.loads(fixture.read_text()), key=lambda e: e["at_seconds"])
    emitter = EventEmitter(name=f"replay {interview_id}")
    await emitter.start()
    first = events[0]["at_seconds"]
    started = time.monotonic()
    for raw in events:
        due = (raw["at_seconds"] - first) / speed
        delay = due - (time.monotonic() - started)
        if delay > 0:
            await asyncio.sleep(delay)
        emitter.emit(rewrite(raw, interview_id, run_id))
    stats = await emitter.close(timeout=5)
    return {"events_in_fixture": len(events), "played_seconds": round(time.monotonic() - started, 2), **stats}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interview-id")
    parser.add_argument("--create", action="store_true", help="make a demo interview row (local only)")
    parser.add_argument("--speed", type=float, default=1.0, help="1 = the fixture's real timing")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed must be positive")
    interview_id = args.interview_id or f"int_{uuid.uuid4().hex[:12]}"
    if args.create:
        await ensure_demo_interview(interview_id)
    elif not args.interview_id:
        parser.error("give --interview-id, or --create for a demo interview")
    run_id = uuid.uuid4().hex[:8]
    print(f"replaying {args.fixture.name} into {interview_id} at {args.speed}x (run {run_id})", flush=True)
    stats = await replay(interview_id, speed=args.speed, fixture=args.fixture, run_id=run_id)
    print(json.dumps({"interview_id": interview_id, "run_id": run_id, **stats}))
    from app.db import reset_engine

    await reset_engine()
    ok = stats["written"] == stats["events_in_fixture"] and not stats["dropped"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
