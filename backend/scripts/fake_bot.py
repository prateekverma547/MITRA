"""A stand-in for a bot that emits a plausible event stream, for live-view work.

No call, no Daily, no OpenAI: it writes InterviewEvents through the real
emitter into the real store, so a live view (or an SSE client) on the running
web process sees them through the real relay.

    cd backend && uv run python scripts/fake_bot.py --create --turns 20 --interval 0.5
    cd backend && uv run python scripts/fake_bot.py --interview-id int_0123456789ab

Point DATABASE_URL at the same database as the web process. `--create` makes a
demo interview row (local only; refused on a deployed instance). Prints the
emitter's stats at the end: emitted, written, dropped.
"""

import argparse
import asyncio
import json
import random
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live.dev import ensure_demo_interview  # noqa: E402
from live.emit import EventEmitter, make_event  # noqa: E402
from shared.interview_contracts import (  # noqa: E402
    AreaChanged,
    CostUpdate,
    EventSource,
    IntegrityStatus,
    InterviewEnded,
    InterviewStarted,
    Participant,
    ReplyWait,
    Skip,
    TimeNoteSent,
    ToolCalled,
    TranscriptTurnEvent,
)

QUESTIONS = [
    "Tell me about a product decision you made with incomplete data.",
    "What did you measure to know it worked?",
    "Who disagreed, and how did you resolve it?",
    "What would you do differently now?",
]
ANSWERS = [
    "We shipped a smaller version first and watched the drop-off.",
    "Conversion at the second step, and support tickets per thousand users.",
    "The sales lead did; we agreed on a two-week trial with a clear stop rule.",
    "I would have involved support earlier.",
]


async def run(interview_id: str, turns: int, interval: float, seed: int) -> dict:
    rng = random.Random(seed)
    emitter = EventEmitter(name=f"fake_bot {interview_id}")
    await emitter.start()
    started = time.monotonic()

    def clock() -> float:
        return time.monotonic() - started

    def emit(payload, source=EventSource.BOT):
        emitter.emit(make_event(interview_id, payload, source=source, at_seconds=clock()))

    emit(Participant(change="joined", role="candidate", participant_id="p_cand"), EventSource.SERVER)
    emit(InterviewStarted(engine="realtime", booked_minutes=30))
    emit(AreaChanged(area_id="opening", area_name="Opening", budget_minutes=2), EventSource.COORDINATOR)
    index = 0
    for i in range(turns):
        await asyncio.sleep(interval)
        if i == 1:
            emit(AreaChanged(area_id="strategy", area_name="Product strategy", budget_minutes=8),
                 EventSource.COORDINATOR)
        if i > 0:
            detect, model = round(rng.uniform(0.3, 1.0), 3), round(rng.uniform(0.5, 1.2), 3)
            emit(ReplyWait(turn_index=index, end_of_speech_detect_seconds=detect,
                           model_to_first_sound_seconds=model, total_seconds=round(detect + model, 3)))
        emit(TranscriptTurnEvent(turn_index=index, speaker="interviewer",
                                 text=QUESTIONS[i % len(QUESTIONS)]))
        index += 1
        await asyncio.sleep(interval)
        emit(TranscriptTurnEvent(turn_index=index, speaker="candidate", text=ANSWERS[i % len(ANSWERS)]))
        index += 1
        emit(IntegrityStatus(distance_cm=round(rng.uniform(55, 75), 1), distance_in_band=True,
                             faces_in_view=1, page_visible=True), EventSource.BROWSER)
        if i % 5 == 4:
            emit(CostUpdate(component="realtime", model="gpt-realtime-2.1",
                            input_tokens=rng.randint(3000, 4000), output_tokens=rng.randint(200, 400),
                            usd=round(rng.uniform(0.01, 0.02), 4)))
        if i == turns * 2 // 3:
            emit(TimeNoteSent(text="About a third of the time is left."), EventSource.COORDINATOR)
        if i == turns // 2:
            # Mitra moved on from a question (the coordinator does not know its id yet).
            emit(Skip(area_id="strategy", question_id=None), EventSource.COORDINATOR)
    # Mitra ends it with end_interview and code accepts (round 14, A-95; C-A20).
    emit(ToolCalled(tool="end_interview", outcome="accepted_completed"), EventSource.COORDINATOR)
    emit(InterviewEnded(ended_because="interview_complete", duration_seconds=round(clock(), 2)))
    return await emitter.close(timeout=5)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interview-id")
    parser.add_argument("--create", action="store_true", help="make a demo interview row (local only)")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.5, help="seconds between events")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    interview_id = args.interview_id or f"int_{uuid.uuid4().hex[:12]}"
    if args.create:
        await ensure_demo_interview(interview_id)
    elif not args.interview_id:
        parser.error("give --interview-id, or --create for a demo interview")
    print(f"emitting for {interview_id}", flush=True)
    stats = await run(interview_id, args.turns, args.interval, args.seed)
    print(json.dumps({"interview_id": interview_id, **stats}))
    from app.db import reset_engine

    await reset_engine()
    return 0 if stats["dropped"] == 0 and stats["lost_on_close"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
