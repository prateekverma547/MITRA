"""Generate a blueprint for a Business Analyst and listen to how it talks.

The unit tests prove invented jargon is discarded. They cannot tell you whether
the interview sounds like someone who has done the job, which is the entire
point of the feature.

    PYTHONPATH=. uv run python scripts/register_run.py

Costs a blueprint generation plus a short conversation.
"""
import asyncio, json
from blueprint.generate import BlueprintGenerator
from bot.brain.brain import InterviewBrain
from bot.brain.drivers import OpenAIInterviewer
from bot.brain.harness import ScriptedCandidate, run_interview
from bot.brain.state import BrainConfig
from bot.config import Settings
from shared.contracts import Competency, EvaluationSpec

JD = """Business Analyst, Retail Banking Technology.
You will run requirement discovery sessions with product and operations, author
BRDs and functional specification documents, break work into user stories, and
drive stakeholder sign-off. You will support UAT and manage the change request
process with vendors. SQL for data analysis is expected.
"""

CV = """Jayati Pahuja, Business Analyst, 5 years.
Saxo Bank: led requirement gathering for digital self-service, authored BRDs,
FRDs and user stories, reduced requirement gaps by 27 percent, ran UAT cycles
with operations. Deloitte: data analysis using SQL and reporting tools,
stakeholder management across workstreams. MBA Business Analytics.
"""

SPEC = EvaluationSpec(
    role_title="Business Analyst, Retail Banking",
    seniority="Mid",
    experience_expectation="5 years",
    duration_minutes=20,
    competencies=[
        Competency(id="requirements", name="Requirements and documentation",
                   description="Elicits and documents requirements that survive delivery.", weight=0.6),
        Competency(id="stakeholders", name="Stakeholder management",
                   description="Aligns product, operations and vendors.", weight=0.4),
    ],
)


async def main():
    settings = Settings.load()
    generator = BlueprintGenerator(api_key=settings.openai_api_key, model=settings.blueprint_model)
    blueprint = await generator.generate(
        blueprint_id="register-demo", spec=SPEC, cv_text=CV, jd_text=JD
    )

    print("=" * 74)
    print("WHAT IT BORROWED FROM THE DOCUMENTS")
    print("=" * 74)
    reg = blueprint.domain_language
    print(json.dumps(reg.model_dump() if reg else None, indent=2))
    print()
    print("SEED QUESTIONS")
    for plan in blueprint.competency_plans:
        for q in plan.seed_questions[:2]:
            print(f"  - {q}")

    brain = InterviewBrain(blueprint, config=BrainConfig(floor_turns=2, ceiling_turns=3))
    run = await run_interview(
        brain,
        interviewer=OpenAIInterviewer(api_key=settings.openai_api_key, model=settings.llm_model),
        candidate=ScriptedCandidate(replies=[
            "Hi, yes, good to meet you.",
            "I have been a BA for about five years, mostly in banking.",
            "We ran discovery workshops with operations and I wrote the BRD from those.",
            "The hardest part was operations and product wanting different things.",
            "I took it to a joint session and we agreed a phased scope.",
            "We used UAT to catch the gaps before release.",
        ], max_turns=6),
        seconds_per_turn=45, max_turns=10,
    )
    print()
    print("=" * 74)
    print("HOW IT TALKS")
    print("=" * 74)
    for t in run.transcript:
        if t["speaker"] == "interviewer":
            print(f"MITRA  {t['text']}")
        else:
            print(f"CAND   {t['text']}")

asyncio.run(main())
