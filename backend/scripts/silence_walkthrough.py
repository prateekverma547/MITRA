"""Walk the silence ladder through its scenarios and print what it would do.

The text harness cannot exercise this: there is no real dead air in a scripted
conversation, so the timers never fire. This drives the ladder directly instead,
so the behaviour is readable rather than only asserted.

    PYTHONPATH=. uv run python scripts/silence_walkthrough.py

Free. No model calls, no audio. Whether the timings *feel* right is still a
judgement to be made by ear in a live session.
"""
import asyncio
from pipecat.frames.frames import EndWorkerFrame, LLMMessagesAppendFrame, TTSSpeakFrame
from bot.presence import RoomPresence
from bot.silence import SilenceEscalation, SilenceLadder


class Worker:
    def __init__(self): self.frames = []
    async def queue_frames(self, frames): self.frames.extend(frames)


def describe(frame):
    if isinstance(frame, TTSSpeakFrame):
        return f'says: "{frame.text}"'
    if isinstance(frame, LLMMessagesAppendFrame):
        return "asks the model to rephrase and check their audio"
    if isinstance(frame, EndWorkerFrame):
        return f"ENDS THE SESSION (reason: {frame.reason})"
    return None


async def scenario(title, *, present, leaves_after=None, patience=1.0, rungs=4):
    print("=" * 74)
    print(title)
    print("=" * 74)
    ladder = SilenceLadder()
    presence = RoomPresence()
    if present:
        presence.joined("cand-1")
    escalation = SilenceEscalation(
        ladder=ladder, session_id="walkthrough",
        presence=presence, patience=lambda: patience,
    )
    worker = Worker()
    escalation.attach(worker)

    thresholds = ladder.thresholds(patience)
    print(f"rings at: {[round(t) for t in thresholds]} seconds of dead air\n")

    seen = 0
    for rung in range(rungs):
        if leaves_after is not None and rung == leaves_after:
            presence.left("cand-1", "connection lost")
            print("   [the candidate drops out of the call]")

        await escalation.handle_idle(aggregator=None)
        at = round(thresholds[min(rung, len(thresholds) - 1)])

        new = worker.frames[seen:]
        seen = len(worker.frames)
        spoken = [describe(f) for f in new if describe(f)]
        for line in spoken:
            print(f"   {at:>4}s  {line}")
        if not spoken:
            print(f"   {at:>4}s  waits, nobody is in the room")

        # In the pipeline the ladder cancels its own timer before closing, so
        # nothing fires after this. Stop here rather than poking a session that
        # has already ended.
        if any(isinstance(f, EndWorkerFrame) for f in new):
            break
    print()


async def main():
    await scenario("A candidate who goes quiet and comes back to nothing", present=True)
    await scenario("A candidate whose call drops", present=True, leaves_after=1)
    await scenario(
        "After a greeting, where silence means something is wrong",
        present=True, patience=SilenceLadder.easy_question_factor, rungs=1,
    )
    await scenario(
        "After 'tell me about a decision you regret', where they are thinking",
        present=True, patience=SilenceLadder.deep_question_factor, rungs=1,
    )

asyncio.run(main())
