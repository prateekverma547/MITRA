"""Milestone 2 Definition of Done, as a runnable script.

    uv run python scripts/build_blueprint.py
    uv run python scripts/build_blueprint.py --cv tests/fixtures/documents/cv_thin_pm.txt

Takes a sample JD and CV, runs the real clarification chat (auto-answered by a
scripted employer so it needs no human), generates the blueprint, stores it in
the database, and prints it back out as JSON.

The auto-answers exist so this is repeatable. The clarification chat is meant to
be driven by a person; here a small script plays that part so the pipeline can
be exercised end to end without one.
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import (  # noqa: E402
    BlueprintStatus,
    Candidate,
    ClarificationTurn,
    Job,
    SpecStatus,
    create_all,
    get_sessionmaker,
)
from blueprint.clarify import (  # noqa: E402
    EMPLOYER_ROLE,
    INTERVIEWER_ROLE,
    ClarificationChat,
)
from blueprint.documents import extract_text  # noqa: E402
from blueprint.simulated_employer import (  # noqa: E402
    BRIEFS,
    DistractedEmployer,
    SimulatedEmployer,
)
from blueprint.generate import BlueprintGenerator  # noqa: E402
from bot.config import MissingConfig, Settings  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "documents"


async def run(
    jd_path: Path,
    cv_path: Path,
    *,
    max_questions: int,
    distracted: bool = False,
    brief: str = "northwind",
) -> int:
    try:
        settings = Settings.load()
    except MissingConfig as exc:
        logger.error(str(exc))
        return 1

    await create_all()
    sessionmaker = get_sessionmaker()

    jd_text = extract_text(filename=jd_path.name, data=jd_path.read_bytes())
    cv_text = extract_text(filename=cv_path.name, data=cv_path.read_bytes())
    logger.info(f"parsed JD ({len(jd_text)} chars) and CV ({len(cv_text)} chars)")

    # ---- clarification chat ------------------------------------------------
    chat = ClarificationChat(api_key=settings.openai_api_key, model=settings.blueprint_model)
    employer_cls = DistractedEmployer if distracted else SimulatedEmployer
    employer = employer_cls(
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        brief=BRIEFS[brief],
    )
    history: list[dict[str, str]] = []
    spec = None
    inferred: list[str] = []

    print("\n" + "=" * 74)
    print("  CLARIFICATION CHAT")
    print("=" * 74)

    for _ in range(max_questions):
        turn = await chat.next_turn(jd_text=jd_text, history=history)
        print(f"\n  AI       : {turn.reply}")
        history.append({"role": INTERVIEWER_ROLE, "content": turn.reply})

        inferred.extend(i for i in turn.inferred if i not in inferred)

        if turn.done and turn.spec is not None:
            spec = turn.spec
            break

        answer = await employer.answer(question=turn.reply, history=history[:-1])
        print(f"  EMPLOYER : {answer}")
        history.append({"role": EMPLOYER_ROLE, "content": answer})

    if spec is None:
        logger.error(f"Clarification did not converge within {max_questions} questions.")
        return 1

    print("\n" + "=" * 74)
    print("  EVALUATION SPEC")
    print("=" * 74)
    print(f"  role       : {spec.role_title} ({spec.seniority})")
    print(f"  duration   : {spec.duration_minutes} min (+{spec.overrun_grace_minutes} grace)")
    for competency in spec.competencies:
        print(f"    {competency.weight:>5.2f}  {competency.name}")
    print(f"  red flags  : {len(spec.red_flags)}")
    if inferred:
        # Loud on purpose: these are positions the employer never actually
        # stated. A summary skimmed and approved is not the same as a
        # preference expressed.
        print("\n  ASSUMED, NOT STATED BY THE EMPLOYER:")
        for item in inferred:
            print(f"    ! {item}")

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    async with sessionmaker() as session:
        job = Job(
            id=job_id,
            source_filename=jd_path.name,
            jd_text=jd_text,
            evaluation_spec=spec.model_dump(mode="json"),
            spec_status=SpecStatus.READY,
        )
        for i, turn_record in enumerate(history):
            job.clarification_turns.append(
                ClarificationTurn(
                    index=i, role=turn_record["role"], content=turn_record["content"]
                )
            )
        session.add(job)
        await session.commit()

    # ---- blueprint generation ---------------------------------------------
    candidate_id = f"cand_{uuid.uuid4().hex[:12]}"
    generator = BlueprintGenerator(
        api_key=settings.openai_api_key, model=settings.blueprint_model
    )
    blueprint = await generator.generate(
        blueprint_id=candidate_id, spec=spec, cv_text=cv_text
    )

    async with sessionmaker() as session:
        session.add(
            Candidate(
                id=candidate_id,
                job_id=job_id,
                name=blueprint.candidate_name,
                source_filename=cv_path.name,
                cv_text=cv_text,
                blueprint=blueprint.model_dump(mode="json"),
                blueprint_status=BlueprintStatus.READY,
            )
        )
        await session.commit()

    # ---- read it back out of the database ---------------------------------
    async with sessionmaker() as session:
        stored = await session.scalar(select(Candidate).where(Candidate.id == candidate_id))

    print("\n" + "=" * 74)
    print("  INTERVIEW BLUEPRINT (read back from the database)")
    print("=" * 74)
    print(f"  candidate  : {stored.name}")
    print(f"  summary    : {blueprint.candidate_summary}")
    print(f"\n  claims to verify ({len(blueprint.claims_to_verify)}):")
    for claim in blueprint.claims_to_verify:
        print(f"    - {claim.claim}")
    print("\n  sections:")
    print(f"    {blueprint.opening_minutes:>5.1f} min  opening")
    for plan in blueprint.competency_plans:
        print(f"    {plan.time_budget_minutes:>5.1f} min  {plan.name}")
        print(f"                 depth: {plan.target_depth[:88]}")
        print(f"                 q1:    {plan.seed_questions[0][:88]}")
    print(f"    {blueprint.closing_minutes:>5.1f} min  closing")

    total = (
        blueprint.opening_minutes
        + blueprint.closing_minutes
        + sum(p.time_budget_minutes for p in blueprint.competency_plans)
    )
    print(f"\n  total planned: {total:g} min against a {spec.duration_minutes} min interview")

    out_path = Path(__file__).resolve().parents[1] / "sessions" / f"{candidate_id}.blueprint.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(stored.blueprint, indent=2))
    print(f"\n  full JSON: {out_path}")
    print(f"  job_id={job_id}  candidate_id={candidate_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the JD -> spec -> blueprint pipeline.")
    parser.add_argument("--jd", type=Path, default=FIXTURES / "jd_senior_pm.txt")
    parser.add_argument("--cv", type=Path, default=FIXTURES / "cv_strong_pm.txt")
    parser.add_argument("--max-questions", type=int, default=8)
    parser.add_argument(
        "--brief",
        choices=sorted(BRIEFS),
        default="northwind",
        help="Which simulated employer to use. Must match the JD.",
    )
    parser.add_argument(
        "--distracted",
        action="store_true",
        help=(
            "Simulate an employer who answers a different question than the one "
            "asked, to check the chat re-asks instead of inventing a position."
        ),
    )
    args = parser.parse_args()

    return asyncio.run(
        run(
            args.jd,
            args.cv,
            max_questions=args.max_questions,
            distracted=args.distracted,
            brief=args.brief,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
