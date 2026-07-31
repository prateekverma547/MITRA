"""Run an interview to a natural finish and check the call would actually end.

Two live reports of the call staying open after the goodbye. This drives a real
conversation to its close and then asserts the whole chain: the brain reports
finished, and the observer queues the frame that hangs up.

    PYTHONPATH=. uv run python scripts/ending_run.py
"""
import asyncio
from pipecat.frames.frames import BotStoppedSpeakingFrame, EndWorkerFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from bot.blueprint_source import load_blueprint
from bot.brain.brain import InterviewBrain
from bot.brain.drivers import OpenAIInterviewer
from bot.brain.harness import ScriptedCandidate, run_interview
from bot.brain.state import BrainConfig
from bot.config import Settings
from bot.ending import SessionEnder


class Worker:
    def __init__(self): self.frames = []
    async def queue_frames(self, frames): self.frames.extend(frames)


async def main():
    settings = Settings.load()
    brain = InterviewBrain(load_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=1))
    run = await run_interview(
        brain,
        interviewer=OpenAIInterviewer(api_key=settings.openai_api_key, model=settings.llm_model),
        candidate=ScriptedCandidate(replies=[
            "Hi, good to meet you.",
            "I led a payments migration that took about nine months.",
            "We cut failed transactions by a third by retrying on a second provider.",
            "The trade-off was delaying the reporting work by a quarter.",
            "I ran an A/B test and the result surprised me, retention did not move.",
            "We shipped it anyway because the support load dropped.",
            "I mentored two juniors through that project.",
            "No, nothing from me, thanks.",
        ], max_turns=20),
        seconds_per_turn=45, max_turns=24,
    )

    for t in run.transcript[-6:]:
        print(f"{'MITRA ' if t['speaker'] == 'interviewer' else 'CAND  '} {t['text']}")

    print()
    print(f"ended_because: {run.ended_because}")
    print(f"is_finished:   {brain.is_finished}")

    worker = Worker()
    ender = SessionEnder(brain=brain, session_id="check")
    ender.attach(worker)
    await ender.on_push_frame(FramePushed(
        source=None, destination=None, frame=BotStoppedSpeakingFrame(),
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    ))
    hung_up = [f for f in worker.frames if isinstance(f, EndWorkerFrame)]
    print(f"call ends:     {bool(hung_up)}"
          + (f" (reason: {hung_up[0].reason})" if hung_up else "  <- STILL BROKEN"))

asyncio.run(main())
