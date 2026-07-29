"""Measure how often the interviewer actually raises a surfaced contradiction.

    uv run python scripts/contradiction_rate.py --trials 10

Purpose is prompt tuning, not model selection (that is settled: gpt-4.1-mini).
The callback instruction is a soft behaviour — the model is handed a
contradiction and told to ask about it, and it may simply not. A low raise rate
means the imperative is too weak, which is the same class of bug as the original
"raise it only if it fits naturally" hedge that produced a 0% rate.

Two distinct rates are reported and must not be conflated:

- **detection rate** — how often the judge spotted the contradiction at all.
- **raise rate** — given the model was handed one, how often it asked about it.

A run stops once the stakeholders section is done, since that is where the
scripted contradiction lands. Cheap enough to repeat.
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.blueprint_source import load_blueprint  # noqa: E402
from bot.brain.brain import InterviewBrain  # noqa: E402
from bot.brain.drivers import OpenAIInterviewer, OpenAIJudge  # noqa: E402
from bot.brain.harness import contradicting_candidate, run_interview  # noqa: E402
from bot.brain.state import BrainConfig  # noqa: E402
from bot.config import MissingConfig, Settings  # noqa: E402

TARGET_SECTION = "stakeholders"


async def trial(model: str, settings: Settings) -> dict:
    brain = InterviewBrain(
        load_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=3)
    )
    run = await run_interview(
        brain,
        interviewer=OpenAIInterviewer(api_key=settings.openai_api_key, model=model),
        candidate=contradicting_candidate(),
        judge=OpenAIJudge(api_key=settings.openai_api_key, model=settings.blueprint_model),
        seconds_per_turn=40,
        max_turns=14,
        stop_after_section=TARGET_SECTION,
    )
    raised = [o for o in run.callback_opportunities if o.raised]
    return {
        "detected": len(brain.contradictions) > 0,
        "opportunities": len(run.callback_opportunities),
        "raised": len(raised),
        "examples": [o.bot_text for o in run.callback_opportunities],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Measure contradiction callback rates.")
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--models", nargs="*", default=["gpt-4.1-mini", "gpt-4.1"])
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()

    try:
        settings = Settings.load()
    except MissingConfig as exc:
        print(exc)
        return 1

    for model in args.models:
        results = await asyncio.gather(
            *(trial(model, settings) for _ in range(args.trials)),
            return_exceptions=True,
        )
        ok = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, BaseException)]

        # A measurement script that prints 0/0 when every call failed is worse
        # than one that crashes: the zeros read like a finding.
        if not ok:
            print(f"\n{model}: ALL {len(results)} TRIALS FAILED — no measurement taken.")
            print(f"  {type(failures[0]).__name__}: {failures[0]}")
            return 1
        if failures:
            print(f"\n  WARNING: {len(failures)}/{len(results)} trials failed; "
                  f"rates below are over the {len(ok)} that succeeded.")
            print(f"  first error: {type(failures[0]).__name__}: {failures[0]}")
        errors = len(failures)

        detected = sum(1 for r in ok if r["detected"])
        opportunities = sum(r["opportunities"] for r in ok)
        raised = sum(r["raised"] for r in ok)

        print(f"\n{'=' * 68}\n{model}  ({len(ok)} trials"
              + (f", {errors} errored" if errors else "") + ")")
        print("=" * 68)
        print(f"  detection rate (judge spotted it):  {detected}/{len(ok)}")
        print(f"  callback opportunities handed over: {opportunities}")
        print(
            f"  raise rate (model asked about it):  {raised}/{opportunities}"
            + (f"  = {100 * raised / opportunities:.0f}%" if opportunities else "")
        )

        if args.show_misses:
            misses = [
                text
                for r in ok
                for text, was_raised in zip(
                    r["examples"], [True] * r["raised"] + [False] * (r["opportunities"] - r["raised"])
                )
                if not was_raised
            ]
            for text in Counter(misses).most_common(5):
                print(f"    MISS: {text[0][:150]}")

    print("\nLow raise rate => the imperative is too soft. Tighten it, re-measure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
