"""A candidate asking to leave, run against the real model.

    PYTHONPATH=. uv run python scripts/withdrawal_run.py

What to look for: the offer is made rather than assumed, the goodbye does not
fish for more conversation, and the interview reports itself finished so the
call can be ended without the candidate hanging up.
"""
import asyncio
from bot.blueprint_source import load_blueprint
from bot.brain.brain import InterviewBrain
from bot.brain.drivers import OpenAIInterviewer
from bot.brain.harness import run_interview, withdrawing_candidate
from bot.brain.state import BrainConfig
from bot.config import Settings


async def main():
    settings = Settings.load()
    brain = InterviewBrain(load_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=3))
    run = await run_interview(
        brain,
        interviewer=OpenAIInterviewer(api_key=settings.openai_api_key, model=settings.llm_model),
        candidate=withdrawing_candidate(),
        seconds_per_turn=40, max_turns=10,
    )
    for t in run.transcript:
        print(f"{'MITRA ' if t['speaker'] == 'interviewer' else 'CAND  '} {t['text']}")

    print()
    print(f"withdrew:    {brain.withdrew}")
    print(f"is_finished: {brain.is_finished}   <- the call ends on this")
    print(f"ended:       {run.ended_because}")
    print()
    for o in brain.outcomes():
        if o.coverage_shortfall:
            print(f"  {o.section_id}: {o.shortfall_reason}")

asyncio.run(main())
