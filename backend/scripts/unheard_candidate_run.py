"""Run a real conversation with a candidate whose words are not arriving.

Not a unit test. The point is to read what actually comes out, because the unit
tests agreed with my assumptions and the assumptions were wrong twice: once on
what counts as broken audio, and once on whether `degraded` even survives the
trip to the browser.

    uv run python scripts/unheard_candidate_run.py

Costs a handful of live model calls. Run it after touching the repair handling,
the health heuristics, or the scorer's treatment of a poor recording.
"""
import asyncio, json
from bot.brain.brain import InterviewBrain
from bot.brain.drivers import OpenAIInterviewer
from bot.brain.harness import run_interview, unheard_candidate
from bot.brain.state import BrainConfig
from bot.blueprint_source import load_blueprint
from bot.config import Settings
from bot.persistence import build_transcript
from feedback.health import assess
from feedback.score import FeedbackScorer


async def main():
    settings = Settings.load()
    blueprint = load_blueprint()
    brain = InterviewBrain(blueprint, config=BrainConfig(floor_turns=2, ceiling_turns=3))
    run = await run_interview(
        brain,
        interviewer=OpenAIInterviewer(api_key=settings.openai_api_key, model=settings.llm_model),
        candidate=unheard_candidate(),
        seconds_per_turn=40,
        max_turns=14,
    )

    print("=" * 74)
    print("THE CONVERSATION")
    print("=" * 74)
    for t in run.transcript:
        who = "MITRA " if t["speaker"] == "interviewer" else "CAND  "
        print(f"{who} {t['text']}")

    transcript = build_transcript(
        interview_id="scripted-unheard", turns=run.transcript, duration_seconds=600
    )
    health = assess(transcript, {}, repair_requests=brain.repairs_requested)
    print()
    print()
    print("repairs the brain counted:", brain.repairs_requested)
    print("=" * 74)
    print("WHAT THE CHANNEL LOOKED LIKE")
    print("=" * 74)
    print(json.dumps(health.model_dump(mode="json"), indent=2))

    scorer = FeedbackScorer(api_key=settings.openai_api_key, model=settings.feedback_model)
    report = await scorer.score(
        interview_id="scripted-unheard",
        blueprint_id=blueprint.blueprint_id,
        spec=blueprint.evaluation_spec,
        transcript=transcript,
        section_outcomes=[o.model_dump(mode="json") for o in brain.outcomes()],
        health=health,
    )
    print()
    print("=" * 74)
    print("WHAT THE REPORT SAYS")
    print("=" * 74)
    print("recommendation:", report.recommendation)
    print("banner:", report.conversation_health.as_sentence)
    print()
    for s in report.competency_scores[:3]:
        print(f"  {s.name}: {s.score if s.score is not None else 'no score'}")
        print(f"    {s.rationale[:190]}")

asyncio.run(main())
