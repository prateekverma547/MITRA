"""Run the guardrail from CLAUDE.md: gpt-4.1-mini vs gpt-4.1, side by side.

    uv run python scripts/compare_models.py
    uv run python scripts/compare_models.py --scenarios thin_answer contradiction

Drives identical scripted candidates through the brain on both models and writes
both transcripts to `backend/sessions/model_comparison/` for reading.

The decision this informs is explicitly a judgement, not a metric: if mini
complies lazily — accepts hollow answers, drifts off role, softens the redirect —
the live default goes back to gpt-4.1. Probing quality outranks speed and cost.
So this script reports signals and prints transcripts; it does not declare a
winner.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.blueprint_source import load_blueprint  # noqa: E402
from bot.brain.brain import InterviewBrain  # noqa: E402
from bot.brain.drivers import OpenAIInterviewer, OpenAIJudge  # noqa: E402
from bot.brain.harness import (  # noqa: E402
    contradicting_candidate,
    off_topic_candidate,
    run_interview,
    thin_answer_candidate,
    unheard_candidate,
    write_run,
)
from bot.brain.state import BrainConfig  # noqa: E402
from bot.config import MissingConfig, Settings  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "sessions" / "model_comparison"

SCENARIOS = {
    "thin_answer": (
        thin_answer_candidate,
        "Every answer is vague. Does the interviewer push for specifics, or accept it?",
    ),
    "contradiction": (
        contradicting_candidate,
        "Claims sole ownership of a decision, then says it was forced on him. "
        "Cross-section — only carried claims can surface it.",
    ),
    "off_topic": (
        off_topic_candidate,
        "Repeatedly steers away from the role. Does the redirect hold?",
    ),
    "repair": (
        unheard_candidate,
        "Keeps asking for the question again. Does the interviewer return to "
        "the SAME question, or quietly abandon it and ask something else?",
    ),
}

MODELS = ["gpt-4.1-mini", "gpt-4.1"]


def signals(run, brain) -> dict:
    """Cheap, honest indicators. Not a score — the transcripts are the evidence."""
    interviewer_turns = [t["text"] for t in run.transcript if t["speaker"] == "interviewer"]
    joined = " ".join(interviewer_turns).lower()
    role = brain.blueprint.role_title.lower()

    return {
        "interviewer_turns": len(interviewer_turns),
        "avg_words": round(
            sum(len(t.split()) for t in interviewer_turns) / max(1, len(interviewer_turns)), 1
        ),
        "questions_asked": sum(t.count("?") for t in interviewer_turns),
        "sections_reached": len({t["section_id"] for t in run.transcript}),
        "names_role": role in joined,
        "contradictions_found": len(brain.contradictions),
        "claims_carried": len(brain.carried_claims),
        "coverage": {
            o["section_id"]: o["coverage"]
            for o in run.outcomes
            if o["kind"] == "competency"
        },
        "shortfalls": [o["section_id"] for o in run.outcomes if o["coverage_shortfall"]],
        # Added when the repair and register blocks landed: the guardrail is
        # about instruction adherence under a heavier prompt, and these are the
        # instructions that made it heavier.
        "repairs_requested": brain.repairs_requested,
        "returned_to_the_question": _returned_to_the_question(run),
        "field_words_used": _field_words_used(joined, brain.blueprint),
    }


def _returned_to_the_question(run) -> str:
    """After a repair, did the next question ask for the same thing?

    Reported as a fraction rather than a verdict. Judging "same thing" properly
    needs a human reading the transcript, which is what the files are for.
    """
    from bot.brain.repair import RepairKind, classify

    turns = run.transcript
    asked_again = 0
    opportunities = 0
    for i, turn in enumerate(turns):
        if turn["speaker"] == "candidate" and classify(turn["text"]) is RepairKind.REPEAT:
            before = next(
                (t["text"] for t in reversed(turns[:i]) if t["speaker"] == "interviewer"), ""
            )
            after = next(
                (t["text"] for t in turns[i + 1:] if t["speaker"] == "interviewer"), ""
            )
            if not before or not after:
                continue
            opportunities += 1
            shared = set(_content_words(before)) & set(_content_words(after))
            if len(shared) >= 3:
                asked_again += 1
    return f"{asked_again}/{opportunities}" if opportunities else "n/a"


def _content_words(text: str) -> list[str]:
    import re

    stop = {
        "the", "a", "an", "and", "or", "you", "your", "that", "this", "was",
        "were", "did", "do", "can", "could", "would", "about", "with", "for",
        "what", "how", "why", "tell", "me", "of", "to", "in", "on", "it", "is",
        "sorry", "i", "my", "we", "they", "at", "as", "so", "but", "if", "not",
    }
    words = re.sub(r"[^a-z ]+", " ", text.lower()).split()
    return [w for w in words if w not in stop and len(w) > 2]


def _field_words_used(joined: str, blueprint) -> str:
    """How much of the borrowed vocabulary the interviewer actually reached for."""
    register = getattr(blueprint, "domain_language", None)
    if register is None or not register.vocabulary:
        return "n/a"
    used = sum(1 for term in register.vocabulary if term.lower() in joined)
    return f"{used}/{len(register.vocabulary)}"


async def run_one(scenario: str, model: str, settings: Settings) -> tuple:
    blueprint = load_blueprint()
    brain = InterviewBrain(blueprint, config=BrainConfig(floor_turns=2, ceiling_turns=3))
    candidate = SCENARIOS[scenario][0]()

    run = await run_interview(
        brain,
        interviewer=OpenAIInterviewer(api_key=settings.openai_api_key, model=model),
        candidate=candidate,
        # Judge held at the reasoning tier for both runs, so the only variable
        # is the live conversation model.
        judge=OpenAIJudge(api_key=settings.openai_api_key, model=settings.blueprint_model),
        seconds_per_turn=40,
        max_turns=24,
    )
    write_run(run, OUTPUT_DIR / f"{scenario}__{model}.json", label=f"{scenario} / {model}")
    return run, brain


async def main() -> int:
    parser = argparse.ArgumentParser(description="Compare live-conversation models.")
    parser.add_argument("--scenarios", nargs="*", default=list(SCENARIOS), choices=list(SCENARIOS))
    parser.add_argument("--models", nargs="*", default=MODELS)
    parser.add_argument("--print-transcripts", action="store_true")
    args = parser.parse_args()

    try:
        settings = Settings.load()
    except MissingConfig as exc:
        print(exc)
        return 1

    for scenario in args.scenarios:
        print("\n" + "=" * 78)
        print(f"SCENARIO: {scenario} — {SCENARIOS[scenario][1]}")
        print("=" * 78)

        for model in args.models:
            run, brain = await run_one(scenario, model, settings)
            stats = signals(run, brain)
            print(f"\n  {model}")
            for key, value in stats.items():
                print(f"    {key:22} {value}")

            if args.print_transcripts:
                print(run.render())

    print(f"\nTranscripts written to {OUTPUT_DIR}")
    print("Read them. The signals above are indicators, not a verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
