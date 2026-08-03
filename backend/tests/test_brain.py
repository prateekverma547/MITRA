"""Deterministic brain-logic suite — fake LLM only, CI-gating.

Per CLAUDE.md this suite must stay fast and trustworthy: transitions, budget
arithmetic, carryover assembly and SectionOutcome construction. Anything whose
answer depends on what a model chooses to say belongs in the tolerant behaviour
suite instead, or this one becomes flaky and stops being believed.

The brain takes its clock by injection, so a 40-minute interview runs instantly.
"""

import pytest

from bot.blueprint_source import load_blueprint
from bot.brain.brain import InterviewBrain, Judgment
from bot.brain.harness import (
    FakeInterviewer,
    FakeJudge,
    ScriptedCandidate,
    run_interview,
)
from bot.brain.state import BrainConfig
from shared.contracts import (
    Competency,
    CompetencyPlan,
    Contradiction,
    CoverageLevel,
    EvaluationSpec,
    InterviewBlueprint,
    KeyClaim,
    SectionKind,
)


def tiny_blueprint(
    *,
    duration: int = 20,
    grace: int = 0,
    weights: tuple[float, float] = (0.7, 0.3),
    budgets: tuple[float, float] = (8.0, 8.0),
) -> InterviewBlueprint:
    """Two competencies, so budget arithmetic is checkable by hand."""
    return InterviewBlueprint(
        blueprint_id="tiny",
        evaluation_spec=EvaluationSpec(
            role_title="Test Role",
            seniority="Senior",
            experience_expectation="10 years",
            duration_minutes=duration,
            overrun_grace_minutes=grace,
            competencies=[
                Competency(id="alpha", name="Alpha", description="a", weight=weights[0]),
                Competency(id="beta", name="Beta", description="b", weight=weights[1]),
            ],
        ),
        opening_minutes=2.0,
        closing_minutes=2.0,
        competency_plans=[
            CompetencyPlan(
                competency_id="alpha",
                name="Alpha",
                target_depth="deep",
                seed_questions=["a1"],
                time_budget_minutes=budgets[0],
            ),
            CompetencyPlan(
                competency_id="beta",
                name="Beta",
                target_depth="deep",
                seed_questions=["b1"],
                time_budget_minutes=budgets[1],
            ),
        ],
        suggested_opening="Say hello.",
    )


def finish_opening(brain: InterviewBrain, *, seconds: float = 10.0) -> None:
    """Drive past the opening warm-up, however many turns it takes.

    The opening is a warm-up of two or three light exchanges, not a fixed
    single turn — tests must not hardcode its length.
    """
    while brain.current_section.kind == SectionKind.OPENING:
        brain.tick(brain.elapsed_seconds + seconds)
        brain.observe(
            bot_text="Hello, tell me a bit about yourself.",
            candidate_text="I have about twelve years in product management.",
        )


def drive(brain: InterviewBrain, turns: int, *, seconds: float = 10.0) -> None:
    """Feed the brain candidate turns without any LLM in the loop."""
    for i in range(turns):
        brain.tick(brain.elapsed_seconds + seconds)
        brain.observe(bot_text=f"q{i}", candidate_text=f"a{i}")


# -- section ordering --------------------------------------------------------


def test_sections_are_opening_then_competencies_then_closing():
    brain = InterviewBrain(load_blueprint())

    kinds = [s.kind for s in brain.sections]
    assert kinds[0] == SectionKind.OPENING
    assert kinds[-1] == SectionKind.CLOSING
    assert all(k == SectionKind.COMPETENCY for k in kinds[1:-1])
    assert [s.id for s in brain.sections][1:-1] == [
        p.competency_id for p in load_blueprint().competency_plans
    ]


def test_opening_and_closing_get_real_budgets():
    """They were implicit before, which is how the PM fixture allocated every
    minute to competencies and left no room to say hello."""
    blueprint = load_blueprint()
    brain = InterviewBrain(blueprint)

    assert brain.sections[0].budget_seconds == blueprint.opening_minutes * 60
    assert brain.sections[-1].budget_seconds == blueprint.closing_minutes * 60


# -- floors and ceilings -----------------------------------------------------


def test_section_cannot_end_before_its_floor():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=3, ceiling_turns=9))
    finish_opening(brain)
    assert brain.current_section.id == "alpha"

    drive(brain, 2)  # two candidate turns — still under the floor of 3
    assert brain.current_section.id == "alpha"


def test_ceiling_advances_even_without_a_verdict():
    """A section that will not converge is eating another competency's time."""
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=4))
    finish_opening(brain)
    assert brain.current_section.id == "alpha"

    drive(brain, 4)
    assert brain.current_section.id == "beta"


def test_sufficient_verdict_advances_early():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=8))
    finish_opening(brain)
    drive(brain, 2)
    assert brain.current_section.id == "alpha"

    brain.apply_judgment(Judgment(section_id="alpha", coverage=CoverageLevel.SUFFICIENT))
    drive(brain, 1)

    assert brain.current_section.id == "beta"


def test_partial_verdict_does_not_advance():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=8))
    finish_opening(brain)
    drive(brain, 2)

    brain.apply_judgment(Judgment(section_id="alpha", coverage=CoverageLevel.PARTIAL))
    drive(brain, 1)

    assert brain.current_section.id == "alpha"


# -- the judge must never block ---------------------------------------------


def test_brain_never_waits_on_the_judge():
    """A silent judge must not stall the interview — heuristics rule."""
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=3))
    finish_opening(brain)
    drive(brain, 3)  # no verdict ever applied

    assert brain.current_section.id == "beta"


def test_judgment_request_is_only_raised_inside_the_decision_band():
    """Below the floor there is nothing to judge; at the ceiling heuristics decide."""
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=4))
    finish_opening(brain)
    brain.pending_judgment_request()  # drain anything from the opening

    drive(brain, 1)  # 1 turn — below floor
    assert brain.pending_judgment_request() is None

    drive(brain, 1)  # 2 turns — inside the band
    request = brain.pending_judgment_request()
    assert request is not None and request.section_id == "alpha"


def test_late_judgment_for_a_finished_section_is_still_recorded():
    """The verdict arrives after we moved on; it must still reach the outcome."""
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=2))
    finish_opening(brain)
    drive(brain, 2)
    assert brain.current_section.id == "beta"

    brain.apply_judgment(
        Judgment(section_id="alpha", coverage=CoverageLevel.SUFFICIENT, rationale="late")
    )

    alpha = next(o for o in brain.outcomes() if o.section_id == "alpha")
    assert alpha.coverage == CoverageLevel.SUFFICIENT
    assert alpha.depth_rationale == "late"


# -- section-scoped context --------------------------------------------------


def test_context_does_not_grow_with_the_interview():
    """The whole point of the design: a new section does not replay history.

    A single bridging exchange is carried over — see
    `test_new_section_carries_one_bridging_exchange` for why — but everything
    older is dropped.
    """
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=4))
    finish_opening(brain)
    drive(brain, 4)  # four exchanges in alpha
    assert brain.current_section.id == "beta"

    messages = brain.plan_turn().messages
    contents = " ".join(m["content"] for m in messages)

    assert len(messages) == 2  # exactly one bridging exchange
    assert "a0" not in contents  # the opening is long gone
    assert "a1" not in contents  # and so are alpha's earlier turns


def test_new_section_carries_one_bridging_exchange():
    """Without this the model invents what it cannot see.

    Observed live: at a section boundary the window was empty, so the model
    opened with "you mentioned earlier that you shaped a key product strategy"
    to a candidate whose only words had been "I don't want to."
    """
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=2))
    finish_opening(brain)
    drive(brain, 2)
    assert brain.current_section.id == "beta"

    messages = brain.plan_turn().messages

    assert [m["role"] for m in messages] == ["assistant", "user"]
    # It is the genuine last exchange, not a summary and not an invention.
    assert messages[-1]["content"] == "a1"


def test_verbatim_window_is_bounded():
    brain = InterviewBrain(
        tiny_blueprint(),
        config=BrainConfig(floor_turns=1, ceiling_turns=50, verbatim_turns=4),
    )
    finish_opening(brain)
    drive(brain, 10)

    plan = brain.plan_turn()
    assert len(plan.messages) == 4


def test_section_prompt_stays_small_and_bounded():
    """The premise behind running a mini-tier model live: the prompt is small
    and does not grow with the interview.

    Compare with Milestone 1's single whole-interview prompt at ~1,800 tokens
    plus an ever-growing chat log.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    sizes = []
    for _ in range(8):
        sizes.append(brain.plan_turn().token_estimate())
        brain.observe(
            bot_text="And what happened next?",
            candidate_text="We shipped it in the third quarter and retention improved.",
        )

    # Milestone 1 sent ~1,800 tokens of prompt *plus* the whole conversation so
    # far, growing to roughly 10,000 by the end of a 40-minute interview.
    #
    # The bound was 1,600 and moved to 1,800 when the repair, domain-language
    # and one-question blocks landed. Moved deliberately and not far: the
    # benchmark is Milestone 1's static prompt, and the point is that this one
    # matches it while resetting every section instead of growing all interview.
    # If this needs raising again, the honest question is what to cut.
    assert max(sizes) < 1800
    # Flatness is the property that matters, not the absolute number: turn
    # twenty must cost no more than turn one.
    assert sizes[-1] <= sizes[0] + 150


# -- claims carryover --------------------------------------------------------


def test_claims_carry_into_later_sections():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=2))
    finish_opening(brain)

    brain.apply_judgment(
        Judgment(
            section_id="alpha",
            claims=[KeyClaim(text="Owned the pricing decision.", section_id="alpha", turn_index=1)],
        )
    )
    drive(brain, 2)
    assert brain.current_section.id == "beta"

    assert [c.text for c in brain.carried_claims] == ["Owned the pricing decision."]
    # Carried as instruction, not as replayed conversation.
    assert "Owned the pricing decision." in brain.plan_turn().system_instruction


def test_carried_claims_are_bounded():
    brain = InterviewBrain(
        tiny_blueprint(),
        config=BrainConfig(floor_turns=1, ceiling_turns=2, max_carried_claims=3),
    )
    finish_opening(brain)
    brain.apply_judgment(
        Judgment(
            section_id="alpha",
            claims=[
                KeyClaim(text=f"claim {i}", section_id="alpha", turn_index=i) for i in range(10)
            ],
        )
    )
    drive(brain, 2)

    assert len(brain.carried_claims) == 3


def test_duplicate_claims_are_not_carried_twice():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=2))
    finish_opening(brain)
    claim = KeyClaim(text="same claim", section_id="alpha", turn_index=1)
    brain.apply_judgment(Judgment(section_id="alpha", claims=[claim, claim]))
    drive(brain, 2)

    assert len(brain.carried_claims) == 1


# -- contradictions ----------------------------------------------------------


def contradiction(section_id: str = "beta") -> Contradiction:
    return Contradiction(
        earlier_claim="I owned the pricing decision.",
        earlier_section_id="alpha",
        later_statement="The pricing change was forced on us by the CFO.",
        section_id=section_id,
        turn_index=7,
    )


def test_contradiction_is_recorded_and_surfaced_once():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=9))
    finish_opening(brain)
    drive(brain, 1)

    brain.apply_judgment(Judgment(section_id="alpha", contradictions=[contradiction("alpha")]))
    assert len(brain.unprobed_contradictions()) == 1
    prompt = " ".join(brain.plan_turn().system_instruction.lower().split())
    assert "fit together" in prompt
    # Both statements must be quoted back, or the model has to invent the recap.
    assert "i owned the pricing decision" in prompt
    assert "forced on us by the cfo" in prompt

    brain.mark_contradiction_probed(brain.unprobed_contradictions()[0])

    assert brain.unprobed_contradictions() == []
    assert brain.contradictions[0].probed is True
    # Never raised a second time.
    assert "forced on us by the CFO" not in brain.plan_turn().system_instruction


def test_contradiction_prompt_is_curious_not_prosecutorial():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=9))
    finish_opening(brain)
    drive(brain, 1)
    brain.apply_judgment(Judgment(section_id="alpha", contradictions=[contradiction("alpha")]))

    # Normalised: the prompt is hard-wrapped, so literals span line breaks.
    prompt = " ".join(brain.plan_turn().system_instruction.lower().split())
    assert "curious" in prompt and "prosecutorial" in prompt
    assert "do not accuse" in prompt
    assert "verdict" in prompt
    # The bot gathers evidence for a human; it must not name the behaviour.
    assert 'do not use the word "contradiction"' in prompt


def test_duplicate_contradictions_are_recorded_once():
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=9))
    finish_opening(brain)
    drive(brain, 1)
    brain.apply_judgment(
        Judgment(section_id="alpha", contradictions=[contradiction("alpha"), contradiction("alpha")])
    )

    assert len(brain.contradictions) == 1


# -- time budget and the squeeze --------------------------------------------


def test_section_ends_when_its_budget_is_exhausted():
    brain = InterviewBrain(
        tiny_blueprint(budgets=(1.0, 8.0)), config=BrainConfig(floor_turns=2, ceiling_turns=9)
    )
    finish_opening(brain, seconds=10)
    assert brain.current_section.id == "alpha"

    drive(brain, 2, seconds=40)  # 80s against a 60s budget
    assert brain.current_section.id == "beta"


def test_budget_exhausted_below_floor_records_a_shortfall():
    """An honest gap beats a competency scored on one sentence."""
    brain = InterviewBrain(
        tiny_blueprint(budgets=(1.0, 8.0)), config=BrainConfig(floor_turns=5, ceiling_turns=9)
    )
    finish_opening(brain, seconds=5)
    drive(brain, 2, seconds=40)

    alpha = next(o for o in brain.outcomes() if o.section_id == "alpha")
    assert alpha.coverage_shortfall is True
    assert alpha.coverage == CoverageLevel.INSUFFICIENT
    assert "insufficient signal" in alpha.shortfall_reason.lower()


def test_overrun_squeezes_remaining_sections_by_weight():
    """Alpha over-runs; beta and gamma give up time in proportion to weight."""
    blueprint = InterviewBlueprint(
        blueprint_id="squeeze",
        evaluation_spec=EvaluationSpec(
            role_title="R",
            seniority="S",
            experience_expectation="e",
            duration_minutes=30,
            overrun_grace_minutes=0,
            competencies=[
                Competency(id="alpha", name="A", description="a", weight=0.2),
                Competency(id="beta", name="B", description="b", weight=0.6),
                Competency(id="gamma", name="G", description="g", weight=0.2),
            ],
        ),
        opening_minutes=1.0,
        closing_minutes=1.0,
        competency_plans=[
            CompetencyPlan(
                competency_id=c, name=c, target_depth="d", seed_questions=["q"],
                time_budget_minutes=9.0,
            )
            for c in ("alpha", "beta", "gamma")
        ],
        suggested_opening="hi",
    )
    brain = InterviewBrain(blueprint, config=BrainConfig(floor_turns=1, ceiling_turns=2))

    finish_opening(brain, seconds=60)
    drive(brain, 2, seconds=360)  # alpha runs 12 min against a 9 min budget
    assert brain.current_section.id == "beta"

    beta = next(s for s in brain.sections if s.id == "beta")
    gamma = next(s for s in brain.sections if s.id == "gamma")

    # Beta carries three times gamma's weight, so it keeps three times the time.
    assert beta.budget_seconds > gamma.budget_seconds
    assert beta.budget_seconds / gamma.budget_seconds == pytest.approx(3.0, rel=0.05)
    # And the squeeze respects the wall.
    assert beta.budget_seconds + gamma.budget_seconds <= (
        brain.hard_stop_seconds - brain.elapsed_seconds - 60
    ) + 1


def test_closing_time_is_protected_from_the_squeeze():
    """Running late is not a reason to hang up mid-sentence."""
    brain = InterviewBrain(
        tiny_blueprint(duration=20, budgets=(8.0, 8.0)),
        config=BrainConfig(floor_turns=1, ceiling_turns=2),
    )
    finish_opening(brain, seconds=60)
    drive(brain, 2, seconds=300)

    closing = brain.sections[-1]
    assert closing.budget_seconds == 2.0 * 60


def test_hard_stop_skips_to_closing_and_records_the_gap():
    """Competencies never reached must be reported as gaps, not silently dropped.

    Without the skip, the brain would step through the remaining sections one
    turn each and keep interviewing well past the wall.
    """
    blueprint = InterviewBlueprint(
        blueprint_id="hardstop",
        evaluation_spec=EvaluationSpec(
            role_title="R",
            seniority="S",
            experience_expectation="e",
            duration_minutes=20,
            overrun_grace_minutes=0,
            competencies=[
                Competency(id=c, name=c, description="d", weight=1 / 3)
                for c in ("alpha", "beta", "gamma")
            ],
        ),
        opening_minutes=1.0,
        closing_minutes=1.0,
        competency_plans=[
            CompetencyPlan(
                competency_id=c, name=c, target_depth="d", seed_questions=["q"],
                time_budget_minutes=6.0,
            )
            for c in ("alpha", "beta", "gamma")
        ],
        suggested_opening="hi",
    )
    brain = InterviewBrain(blueprint, config=BrainConfig(floor_turns=1, ceiling_turns=9))

    finish_opening(brain, seconds=60)
    drive(brain, 2, seconds=700)  # alpha over-runs, then we blow past the wall

    assert brain.current_section.kind == SectionKind.CLOSING

    gamma = next(o for o in brain.outcomes() if o.section_id == "gamma")
    assert gamma.coverage == CoverageLevel.NOT_STARTED
    assert gamma.turns_spent == 0
    assert gamma.coverage_shortfall is True
    assert "time limit" in gamma.shortfall_reason.lower()


def test_grace_extends_the_wall():
    with_grace = InterviewBrain(tiny_blueprint(duration=20, grace=5))
    without = InterviewBrain(tiny_blueprint(duration=20, grace=0))

    assert with_grace.hard_stop_seconds == 25 * 60
    assert without.hard_stop_seconds == 20 * 60


def test_duration_comes_from_the_spec_not_a_constant():
    """The SRE fixture is 60 minutes; nothing may assume 40."""
    sre = InterviewBrain(load_blueprint(blueprint_id="sre_staff"))
    pm = InterviewBrain(load_blueprint(blueprint_id="pm_senior"))

    assert sre.hard_stop_seconds == 60 * 60  # 60 min, zero grace
    assert pm.hard_stop_seconds == 45 * 60  # 40 min + 5 grace


# -- outcomes ----------------------------------------------------------------


async def test_full_interview_produces_one_outcome_per_section():
    blueprint = load_blueprint()
    brain = InterviewBrain(blueprint)
    judge = FakeJudge(
        verdicts={p.competency_id: CoverageLevel.SUFFICIENT for p in blueprint.competency_plans}
    )

    run = await run_interview(
        brain,
        interviewer=FakeInterviewer(),
        candidate=ScriptedCandidate(
            replies=["I owned the payments platform end to end for four years."]
        ),
        judge=judge,
        seconds_per_turn=30,
    )

    assert run.ended_because == "brain_finished"
    assert [o["section_id"] for o in run.outcomes] == [s.id for s in brain.sections]
    assert all(o["turns_spent"] > 0 for o in run.outcomes)
    assert all(o["coverage"] == "sufficient" for o in run.outcomes)
    assert not any(o["coverage_shortfall"] for o in run.outcomes)


async def test_candidate_leaving_ends_the_interview():
    brain = InterviewBrain(load_blueprint())

    run = await run_interview(
        brain,
        interviewer=FakeInterviewer(),
        candidate=ScriptedCandidate(replies=["hi"], max_turns=2),
        judge=FakeJudge(silent=True),
    )

    assert run.ended_because == "candidate_left"


async def test_outcomes_serialise_for_persistence():
    brain = InterviewBrain(tiny_blueprint())
    run = await run_interview(
        brain,
        interviewer=FakeInterviewer(),
        candidate=ScriptedCandidate(replies=["a"]),
        judge=FakeJudge(claims={"alpha": ["did the thing"]}),
        seconds_per_turn=30,
    )

    alpha = next(o for o in run.outcomes if o["section_id"] == "alpha")
    assert alpha["schema_version"] == 1
    assert alpha["kind"] == "competency"
    assert alpha["competency_id"] == "alpha"
    # Claims keep their transcript anchor — a claim without one is a rumour.
    assert alpha["key_claims"][0]["turn_index"] >= 0


# -- deployment footprint ----------------------------------------------------


def test_turn_detection_does_not_pull_in_torch():
    """Guards a ~570MB dependency removal.

    The `local-smart-turn` extra exists for the v2 CoreML/PyTorch analysers and
    drags in torch, torchaudio, transformers and coremltools. We use
    LocalSmartTurnAnalyzerV3, whose model is a bundled ONNX file. Re-adding the
    extra would balloon the deployed image with code that never executes, and
    nothing else would fail to warn us.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys;"
            "from bot.turn_taking import build_turn_strategies, build_vad_analyzer;"
            "build_vad_analyzer(); build_turn_strategies();"
            "print('torch' in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("False"), (
        "torch was imported while building turn detection — the "
        "local-smart-turn extra is probably back in pyproject.toml"
    )


# -- fragmented speech ------------------------------------------------------
#
# Live session `int_0a7ca5d0aca5`, a Business Analyst interview booked for 40
# minutes, reached its closing after 291 seconds with every section marked
# `insufficient`. Nothing crashed: the brain genuinely believed it had finished.
#
# Deepgram delivers a halting speaker in fragments, and `turns_spent` was
# incremented once per utterance. One answer arrived as eight of them, so a
# six-turn section was spent on a single reply. Across the real transcript, 12
# interviewer turns drew 52 candidate utterances, and all eight sections were
# burned through.
#
# The text-mode harness could never catch this: a ScriptedCandidate replies once
# per question, so an answer is never split.

ONE_ANSWER_IN_FRAGMENTS = [
    "I",
    "under I tried to understand the client 1st.",
    "So that",
    "I get to know what their businesses are.",
    "I try to make a conversation with a client",
    "on a regular basis.",
    "When they got comfortable with me,",
    "they told me all the pain areas.",
]


def test_one_answer_arriving_in_fragments_spends_one_turn():
    brain = InterviewBrain(load_blueprint())
    brain.observe(bot_text="What steps did you take to understand the client?")
    section = brain.current_section

    for fragment in ONE_ANSWER_IN_FRAGMENTS:
        brain.observe(candidate_text=fragment)

    assert brain.current_section is section, (
        "eight fragments of one answer walked the interview into another section"
    )
    assert section.turns_spent == 1


def test_fragments_do_not_race_the_interview_to_its_closing():
    """The shape of the live session: 12 questions, 52 fragmented utterances.

    That is roughly five minutes of a forty-minute interview, and it must not
    get anywhere near the closing. Live it reached the closing and hung up.
    """
    brain = InterviewBrain(load_blueprint())
    entered = []

    for _ in range(12):
        brain.observe(bot_text="Can you give me a specific example of that?")
        for fragment in ONE_ANSWER_IN_FRAGMENTS[:4]:  # 12 x 4 ~= the real 52
            brain.observe(candidate_text=fragment)
        if brain.current_section.id not in entered:
            entered.append(brain.current_section.id)

    assert not brain.is_finished
    assert brain.current_section.kind is not SectionKind.CLOSING
    # Twelve answers cannot legitimately cover eight sections.
    assert len(entered) <= 3, f"burned through {entered}"


def test_a_leading_no_is_not_a_refusal_when_the_answer_follows_in_the_next_fragment():
    """"No." then "that's not how it went…" is one answer, split by the vendor.

    Whole-utterance matching is what keeps "no" apart from "no, but…". Fragments
    defeat it by turning the "no" into the whole utterance, so the correction
    has to survive arriving late.
    """
    brain = InterviewBrain(load_blueprint())
    brain.observe(bot_text="Was your documentation ever challenged?")
    section = brain.current_section

    brain.observe(candidate_text="No.")
    brain.observe(
        candidate_text=(
            "The client did scrutinise it, and we walked through every "
            "acceptance criterion together before sign-off."
        )
    )

    assert section.turns_spent == 1
    assert section.declined_turns == 0
    assert section.substantive_turns == 1
