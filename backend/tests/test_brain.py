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


def test_every_answer_in_a_competency_is_judged():
    """This used to assert the opposite, and the change is the point.

    Judgement was raised once per section, inside a turn band, and never again
    once a verdict had landed. So a section was assessed on its opening couple
    of minutes and that assessment stood for the rest of it. Every answer is now
    judged, because the verdict is rendered into the live prompt and a stale one
    is worse than a late one.
    """
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=4))
    finish_opening(brain)
    brain.pending_judgment_request()  # drain anything from the opening

    for turn in range(3):
        drive(brain, 1)
        request = brain.pending_judgment_request()
        assert request is not None, f"no judgement raised after answer {turn + 1}"
        assert request.section_id == "alpha"


def test_only_one_judgement_is_outstanding_at_a_time():
    """A slow judge must not leave several assessments of the same section
    racing, where the last to land wins regardless of which was freshest."""
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=6))
    finish_opening(brain)
    brain.pending_judgment_request()

    drive(brain, 1)
    drive(brain, 1)  # a second answer while the first request is still queued

    assert brain.pending_judgment_request() is not None
    assert brain.pending_judgment_request() is None, "a second request was queued"


def test_the_opening_and_closing_are_never_judged():
    """Judgement is about competency depth. Neither of those is scored."""
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=2, ceiling_turns=4))
    drive(brain, 2)

    assert brain.current_section.kind.value == "opening"
    assert brain.pending_judgment_request() is None


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


def test_the_verdict_block_is_bounded_however_wordy_the_judge_is():
    """The prompt ceiling has to survive model-written prose.

    The judge writes `depth_rationale` and its claims freely, and both are now
    rendered into the live prompt. Unclipped, the size of the system instruction
    would be whatever the judge felt like emitting, which makes the bound above
    a number nobody is holding. Measured against the 26 real claims in the local
    database, the longest was 237 characters.

    Carried claims are asserted here too. They were never bounded, which is why
    the ceiling has been nominal: this test would have passed on a prompt that
    ran to well over 2,000 tokens in production.
    """
    from bot.brain.brain import Judgment
    from shared.contracts import CoverageLevel, KeyClaim

    def prompt_with(rationale_chars, claim_chars):
        brain = InterviewBrain(load_blueprint())
        while brain.current_section.kind.value == "opening":
            opening = brain.current_section
            brain.apply_judgment(
                Judgment(
                    section_id=opening.id,
                    coverage=CoverageLevel.PARTIAL,
                    rationale="ok",
                    claims=[
                        KeyClaim(text="z" * claim_chars, section_id=opening.id, turn_index=n)
                        for n in range(12)
                    ],
                )
            )
            brain.observe(bot_text="q?", candidate_text="We shipped it and retention improved.")
        section = brain.current_section
        brain.apply_judgment(
            Judgment(
                section_id=section.id,
                coverage=CoverageLevel.INSUFFICIENT,
                rationale="x" * rationale_chars,
            )
        )
        return brain.plan_turn().token_estimate()

    modest = prompt_with(120, 100)
    absurd = prompt_with(4000, 4000)

    # The point is not the absolute number, it is that the second one does not
    # run away. A judge having a verbose day must not change the prompt's size.
    assert absurd - modest < 40, (
        f"judge prose reaches the prompt unclipped: {modest} -> {absurd} tokens"
    )


#: What the live system instruction may cost, in tokens.
#:
#: **Raised from 1800 on a measurement rather than a feeling.** That number was
#: a proxy for keeping a mini-tier model viable in real time, and the proxy was
#: never checked. Streaming gpt-4.1-mini at four input sizes, eight interleaved
#: rounds each, time to first token:
#:
#:     ~1400 tokens   778ms median
#:     ~1800 tokens   757ms
#:     ~2300 tokens   820ms
#:     ~2800 tokens   742ms
#:
#: 1800 to 2800 is **-15ms on the median**, against a spread within a single
#: size of up to 358ms. Prompt length in this range does not measurably move
#: time to first token, so holding 1800 was buying nothing and costing content.
#:
#: The bound is kept, at a level that covers the true worst case with room,
#: because the thing worth guarding is that the prompt does not grow without
#: anybody noticing. Flatness, asserted separately below, is the property the
#: sectioned design actually rests on.
#:
#: **Raised again, from 2400, and this time it is the edge of the evidence
#: rather than a level with room.** The true worst case is `ml_senior` at 2781
#: tokens and `sre_staff` at 2470; both had been over 2400 for days while this
#: test was green, because it only ever measured the default fixture. The four
#: input sizes above top out at ~2800, so 2781 sits inside what was measured and
#: the two arms of that table nearest it — 2300 at 820ms and 2800 at 742ms —
#: bracket it with no penalty. Holding 2400 would have meant cutting material
#: that no measurement says costs anything.
#:
#: **The nineteen tokens between 2781 and this number are not headroom.** They
#: are the end of the table. Anything that pushes the prompt past 2800 leaves
#: the range where prompt length has ever been checked against time to first
#: token, so the next addition needs a fresh measurement rather than a larger
#: constant. Raising this again without extending that table is the failure this
#: comment exists to prevent.
MAX_SECTION_PROMPT_TOKENS = 2800


def _every_fixture() -> list[str]:
    """Every blueprint on disk, discovered rather than listed.

    The point of the parametrisation is that a fixture added next month is
    measured without anybody remembering to add it here. A hardcoded list would
    reproduce the bug this test was fixed for, one layer up.
    """
    from bot.blueprint_source import FIXTURES_DIR

    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture", _every_fixture())
def test_section_prompt_stays_small_and_bounded(fixture):
    """The premise behind running a mini-tier model live: the prompt is small
    and does not grow with the interview.

    **This test was passing on a prompt it was not rendering.** It drove a brain
    with no judge, so the verdict block, the carried claims and the contradiction
    block were all empty, and it measured a prompt no live interview ever sees.
    The real worst case was over 2,200 tokens while this asserted under 1,800 and
    went green. Every optional block is now populated.

    **And then it passed on a fixture that was not breaching.** Fixed once for
    the blocks it rendered, it still called `load_blueprint()` with no argument,
    so it only ever measured the default. `ml_senior` was 116 tokens over the
    bound for days with this green, because `pm_senior` has shorter target
    depths, no dealbreaker the interviewer can ask about, and a shorter candidate
    summary. Same assertion, an input that could not fail it — the third of that
    shape found this week.

    Parametrised over the fixture directory now, so the question "which
    blueprint is worst" is answered by measurement rather than by whichever one
    happens to be the default.
    """
    from bot.brain.brain import Judgment
    from shared.contracts import Contradiction, CoverageLevel, KeyClaim

    brain = InterviewBrain(load_blueprint(blueprint_id=fixture))

    # Carried claims, at the length the judge clips them to.
    opening = brain.current_section
    brain.apply_judgment(
        Judgment(
            section_id=opening.id,
            coverage=CoverageLevel.PARTIAL,
            rationale="ok",
            claims=[
                KeyClaim(text="z" * 200, section_id=opening.id, turn_index=n)
                for n in range(12)
            ],
        )
    )
    finish_opening(brain)

    # A contradiction, which takes the one mandatory slot, and an unexplained
    # term, which does not while a contradiction is present. Both are supplied
    # so the larger of the two is what gets measured.
    section = brain.current_section
    brain.apply_judgment(
        Judgment(
            section_id=section.id,
            coverage=CoverageLevel.INSUFFICIENT,
            rationale="x" * 400,
            contradictions=[
                Contradiction(
                    earlier_claim="a" * 300,
                    earlier_section_id="opening",
                    later_statement="b" * 300,
                    section_id=section.id,
                    turn_index=1,
                )
            ],
        )
    )

    sizes = []
    for _ in range(8):
        sizes.append(brain.plan_turn().token_estimate())
        brain.observe(
            bot_text="And what happened next?",
            candidate_text="We shipped it in the third quarter and retention improved.",
        )

    assert max(sizes) < MAX_SECTION_PROMPT_TOKENS, (
        f"worst-case system instruction for {fixture} is {max(sizes)} tokens"
    )
    # Flatness is the property that matters, not the absolute number: turn
    # twenty must cost no more than turn one.
    assert sizes[-1] <= sizes[0] + 150


def test_the_unexplained_term_block_is_bounded_too():
    """The term and the sentence it appeared in are both model prose."""
    from bot.brain.brain import Judgment
    from shared.contracts import UnexplainedTerm

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    section = brain.current_section
    brain.apply_judgment(
        Judgment(
            section_id=section.id,
            unexplained_terms=[
                UnexplainedTerm(
                    term="session management concurrency",
                    said="q" * 4000,
                    section_id=section.id,
                    turn_index=1,
                )
            ],
        )
    )

    assert brain.plan_turn().token_estimate() < MAX_SECTION_PROMPT_TOKENS


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


def contradiction_brain() -> InterviewBrain:
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=9))
    finish_opening(brain)
    drive(brain, 1)
    brain.apply_judgment(Judgment(section_id="alpha", contradictions=[contradiction("alpha")]))
    return brain


def test_a_contradiction_is_raised_once_in_a_live_interview():
    """The live driver never called `mark_contradiction_probed`, so the brain
    marks it itself once the interviewer has spoken after being shown it.
    `int_8e3db77a60a9` asked the same one seven times, into the closing."""
    brain = contradiction_brain()
    assert "fit together" in brain.plan_turn().system_instruction

    brain.observe(bot_text="Earlier you said you owned it. How do those fit together?")
    brain.observe(candidate_text="The CFO set the target, I chose how to get there.")

    assert brain.unprobed_contradictions() == []
    assert brain.contradictions[0].probed is True
    assert "fit together" not in brain.plan_turn().system_instruction


def test_a_contradiction_not_shown_is_not_spent():
    """A repair replaces the whole body. The contradiction was never in front
    of the model, so it must still be there afterwards."""
    brain = contradiction_brain()
    brain.observe(bot_text="Tell me about the pricing change.")
    brain.observe(candidate_text="Sorry, could you say that again?")
    assert "fit together" not in brain.plan_turn().system_instruction

    brain.observe(bot_text="Tell me about the pricing change.")

    assert len(brain.unprobed_contradictions()) == 1


def test_a_reworded_contradiction_is_not_raised_again():
    """The judge re-reads the window each turn and words the same inconsistency
    differently: live, "I know." came back under four later statements."""
    brain = contradiction_brain()
    brain.plan_turn()
    brain.observe(bot_text="How do those two fit together?")

    reworded = contradiction("alpha").model_copy(
        update={"later_statement": "The CFO forced the pricing change."}
    )
    brain.apply_judgment(Judgment(section_id="alpha", contradictions=[reworded]))

    assert brain.unprobed_contradictions() == []
    assert all(c.probed for c in brain.contradictions)


def test_no_contradiction_is_raised_in_the_closing():
    """The closing's last turn must not ask anything, and the contradiction
    block demands a question. Live, the goodbye was replaced by one."""
    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=1))
    finish_opening(brain)
    while brain.current_section.kind != SectionKind.CLOSING:
        drive(brain, 1)
    brain.apply_judgment(
        Judgment(section_id=brain.current_section.id, contradictions=[contradiction("beta")])
    )

    assert "fit together" not in brain.plan_turn().system_instruction
    brain.observe(bot_text="Thank you for your time. Goodbye.")
    # Not raised, so not marked: the record must not claim it was put to them.
    assert brain.contradictions[0].probed is False


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

    # Read the budget the section was actually given: an early opening hands its
    # unused time forward, so alpha's allocation is not the 60s the fixture named.
    budget = brain.current_section.budget_seconds
    drive(brain, 2, seconds=budget * 0.7)  # 1.4 budgets of time over two turns
    assert brain.current_section.id == "beta"


def test_budget_exhausted_below_floor_records_a_shortfall():
    """An honest gap beats a competency scored on one sentence."""
    brain = InterviewBrain(
        tiny_blueprint(budgets=(1.0, 8.0)), config=BrainConfig(floor_turns=5, ceiling_turns=9)
    )
    finish_opening(brain, seconds=5)
    budget = brain.current_section.budget_seconds
    drive(brain, 2, seconds=budget * 0.7)

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


# -- unexplained terms -------------------------------------------------------


def _term_judgment(brain, *terms):
    from bot.brain.brain import Judgment
    from shared.contracts import UnexplainedTerm

    section = brain.current_section
    return Judgment(
        section_id=section.id,
        unexplained_terms=[
            UnexplainedTerm(term=t, said=f"we used {t}", section_id=section.id, turn_index=1)
            for t in terms
        ],
    )


def test_a_term_stays_open_until_the_judge_stops_reporting_it():
    """Being asked once is not enough, and that is the whole point.

    A contradiction closes when it has been raised. A term closes when it has
    been explained, which is a different event: the failure this exists for is
    an interviewer that asked, got a restatement, and treated having asked as
    having found out.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    brain.apply_judgment(_term_judgment(brain, "session management"))
    assert [t.term for t in brain.unexplained_terms()] == ["session management"]

    # Asked about, and deflected. The judge still reports it, so it stays open.
    brain.mark_term_raised(brain.unexplained_terms()[0])
    brain.apply_judgment(_term_judgment(brain, "session management"))
    assert [t.term for t in brain.unexplained_terms()] == ["session management"]

    # Now actually explained: the judge stops reporting it, so it closes.
    brain.apply_judgment(_term_judgment(brain))
    assert brain.unexplained_terms() == []


def test_the_raise_count_survives_the_list_being_replaced():
    """The escalated wording depends on it. The list is replaced wholesale each
    judgement, so a naive implementation forgets it was ever asked."""
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    brain.apply_judgment(_term_judgment(brain, "session management"))
    brain.mark_term_raised(brain.unexplained_terms()[0])
    brain.apply_judgment(_term_judgment(brain, "session management"))

    assert brain.unexplained_terms()[0].times_raised == 1


def test_the_term_asked_about_already_is_offered_first():
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    brain.apply_judgment(_term_judgment(brain, "hash table", "session management"))
    brain.mark_term_raised(
        next(t for t in brain.unexplained_terms() if t.term == "session management")
    )
    brain.apply_judgment(_term_judgment(brain, "hash table", "session management"))

    assert brain.unexplained_terms()[0].term == "session management"


def test_a_contradiction_takes_the_mandatory_slot_from_a_term():
    """Two blocks each saying "your next question is already decided" is not
    twice as strong. It is two instructions pointing at different things, and
    the model resolves that by choosing, which is the discretion they exist to
    remove."""
    from bot.brain.prompting import _decided_question_early, _decided_question_late
    from shared.contracts import Contradiction, UnexplainedTerm

    contradiction = Contradiction(
        earlier_claim="I owned it", earlier_section_id="a",
        later_statement="it was forced on me", section_id="b", turn_index=1,
    )
    term = UnexplainedTerm(term="session management", said="we used it", section_id="b", turn_index=1)

    # The slot is split across the section body now: a contradiction renders
    # above it, an unexplained term below. Only one of them may fire.
    early = _decided_question_early([contradiction])
    late = _decided_question_late([contradiction], [term])
    assert "inconsistency" in early
    assert late == "", "a term took the slot a contradiction had already taken"

    assert _decided_question_early([]) == ""
    assert "session management" in _decided_question_late([], [term])


def test_the_term_block_names_the_term_and_supplies_the_question():
    """Shape, not emphasis. The block it replaced described a state and was
    measured at no effect."""
    from bot.brain.prompting import _render_unexplained_term
    from shared.contracts import UnexplainedTerm

    first = _render_unexplained_term(
        UnexplainedTerm(term="session management", said="we used session management",
                        section_id="b", turn_index=1)
    )
    assert "session management" in first
    assert "Walk me through how that actually works" in first
    # The four menu options are forbidden by name, not merely deprioritised.
    assert "trade-off" in first and "metric" in first

    again = _render_unexplained_term(
        UnexplainedTerm(term="session management", said="we used it",
                        section_id="b", turn_index=1, times_raised=1)
    )
    assert "twice now" in again and "What does it actually do" in again


def test_a_term_gets_two_asks_and_then_is_let_go():
    """Uncapped, gpt-4.1 asked the identical sentence four times while the
    candidate gave the identical non-answer. For somebody who genuinely cannot
    explain something that is worse than the failure it replaced."""
    from bot.brain.brain import MAX_TERM_ATTEMPTS

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    for _ in range(MAX_TERM_ATTEMPTS):
        brain.apply_judgment(_term_judgment(brain, "session management"))
        offered = brain.unexplained_terms()
        assert offered, "the term stopped being offered before its allowance"
        brain.mark_term_raised(offered[0])

    # Still unexplained, and now closed to further asking.
    brain.apply_judgment(_term_judgment(brain, "session management"))
    assert brain.unexplained_terms() == []


def test_letting_go_is_said_out_loud_once():
    """A candidate pushed twice and then met with a silent change of subject is
    left working out whether they failed something."""
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    for _ in range(2):
        brain.apply_judgment(_term_judgment(brain, "session management"))
        brain.mark_term_raised(brain.unexplained_terms()[0])

    prompt = brain.plan_turn().system_instruction
    assert "LET THIS ONE GO" in prompt
    assert "session management" in prompt

    # It survives being rendered again, because a rendered prompt is not a thing
    # anybody heard. In a live interview one was queued, rendered, discarded and
    # never spoken.
    assert "LET THIS ONE GO" in brain.plan_turn().system_instruction

    # It clears once the interviewer has actually spoken. Once, not twice.
    brain.observe(bot_text="That's alright, let's leave that one there for now.",
                  candidate_text="Okay.")
    assert "LET THIS ONE GO" not in brain.plan_turn().system_instruction


def test_the_record_crosses_sections_and_the_allowance_does_not():
    """**Both directions, because either alone passes a half-fixed brain.**

    The allowance and the record were one field and they answer different
    questions. Scoping the field per section was right for the allowance and
    silently cost the record: a term asked once here and once two topics later
    spends neither topic's allowance, so it reached no report at all. Measured
    at five terms recorded falling to two.

    Asserted here together. A brain that records across sections but also lets
    the allowance run across them fails the second half; one that bounds the
    allowance but records nothing fails the first.
    """
    from bot.brain.brain import MAX_TERM_ATTEMPTS

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    first = brain.current_section.id

    # ONE ask here. Not enough to spend the allowance, so nothing is let go of.
    brain.apply_judgment(_term_judgment(brain, "session management"))
    brain.mark_term_raised(brain.unexplained_terms()[0])
    brain.apply_judgment(_term_judgment(brain, "session management"))
    assert brain.term_to_let_go() is None, "one ask spent a two-ask allowance"

    # ...and it is on the record anyway, which is the half that was lost.
    outcome = next(o for o in brain.outcomes() if o.section_id == first)
    assert [t.term for t in outcome.unexplained_terms] == ["session management"]

    while brain.current_section.id == first and not brain.is_finished:
        brain.observe(
            bot_text="And what happened next?",
            candidate_text="We shipped it in the third quarter and retention improved.",
        )
    second = brain.current_section.id
    assert second != first, "never reached a second competency"

    # The allowance did NOT come with it: a full fresh allowance is available.
    for _ in range(MAX_TERM_ATTEMPTS):
        brain.apply_judgment(_term_judgment(brain, "session management"))
        offered = brain.unexplained_terms()
        assert offered, "the allowance carried across a section boundary"
        brain.mark_term_raised(offered[0])

    # Both sections carry the finding, which is the truth: asked in two topics,
    # explained in neither.
    brain.apply_judgment(_term_judgment(brain, "session management"))
    recorded = {
        o.section_id
        for o in brain.outcomes()
        if any(t.term == "session management" for t in o.unexplained_terms)
    }
    assert recorded == {first, second}, f"recorded in {recorded}, expected both"


def test_a_term_answered_when_asked_is_struck_from_the_record():
    """An answer given two topics late is still an answer.

    The report says what a person could and could not do, so leaving "asked
    about session management and could not explain it" against somebody who did
    explain it is a written claim about them that is false by the end.

    **The window is the verdict that read the answer**, and that is the whole of
    what makes this sound. See the paired test below for the case it must not
    strike.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    section_id = brain.current_section.id

    brain.apply_judgment(_term_judgment(brain, "session management"))
    brain.mark_term_raised(brain.unexplained_terms()[0])
    # The next verdict is the one that read their answer, and it drops the term.
    brain.apply_judgment(_term_judgment(brain))

    outcome = next(o for o in brain.outcomes() if o.section_id == section_id)
    assert outcome.unexplained_terms == [], "a real answer left the finding standing"


def test_a_term_that_merely_stopped_coming_up_stays_on_the_record():
    """**The paired case, and the one a first version got wrong.**

    That version struck anything this section had asked about as soon as it left
    the judge's list. But the judge answers "what is unexplained in what I can
    see now", and a term leaves that list when the conversation moves on at
    least as often as when somebody explains it. Measured, it struck two of the
    three terms a run recorded.

    Here the verdict that read the answer still reports it — they deflected — and
    only a later verdict goes quiet. That is the subject changing, not an
    answer, and the finding has to survive it.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    section_id = brain.current_section.id

    brain.apply_judgment(_term_judgment(brain, "session management"))
    brain.mark_term_raised(brain.unexplained_terms()[0])
    brain.apply_judgment(_term_judgment(brain, "session management"))  # deflected
    brain.apply_judgment(_term_judgment(brain))                        # moved on

    outcome = next(o for o in brain.outcomes() if o.section_id == section_id)
    assert [t.term for t in outcome.unexplained_terms] == ["session management"]


def test_a_term_nobody_asked_about_is_never_recorded():
    """The judge reporting a term is not a finding about the candidate.

    Only having been asked and not answered is. Without this, every term the
    judge ever mentioned would land in the report as though the candidate had
    failed to explain something nobody put to them.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    section_id = brain.current_section.id

    for _ in range(3):
        brain.apply_judgment(_term_judgment(brain, "session management"))
        brain.plan_turn()
        brain.observe(
            bot_text="What did you decide not to build that year?",
            candidate_text="The session management manages those people per sessions.",
        )

    outcome = next(o for o in brain.outcomes() if o.section_id == section_id)
    assert outcome.unexplained_terms == [], "recorded a term nobody asked about"


def test_a_terms_allowance_is_per_section_and_starts_again_in_the_next():
    """**Nothing covered this, which is how it survived two weeks in writing.**

    The raise count and the closed set were brain-level, and that is one bug
    with two faces. A term that spent its asks in the first competency could
    never be asked about again, however right the question; and a term first
    raised in the first competency stayed live to the last, which is what filled
    later topics with earlier topics' material. Measured: in one run the sixth
    topic asked six questions and every one was about the first topic's.

    Both directions are asserted here, because fixing one and not the other
    would pass a test written for either alone.
    """
    from bot.brain.brain import MAX_TERM_ATTEMPTS

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    first = brain.current_section.id

    for _ in range(MAX_TERM_ATTEMPTS):
        brain.apply_judgment(_term_judgment(brain, "session management"))
        brain.mark_term_raised(brain.unexplained_terms()[0])

    # Spent here, and it stays spent here.
    brain.apply_judgment(_term_judgment(brain, "session management"))
    assert brain.unexplained_terms() == [], "the cap stopped binding inside a section"

    while brain.current_section.id == first and not brain.is_finished:
        brain.observe(
            bot_text="And what happened next?",
            candidate_text="We shipped it in the third quarter and retention improved.",
        )
    assert brain.current_section.id != first, "never reached a second competency"

    # A different section, so a clean slate: the same word can be asked about
    # again, and it is asked with the first-ask wording rather than the
    # escalation, because in this section nobody has asked yet.
    brain.apply_judgment(_term_judgment(brain, "session management"))
    offered = brain.unexplained_terms()
    assert offered, "a term capped in an earlier section was still closed here"
    assert offered[0].times_raised == 0
    assert brain.current_section.closed_terms == set()


def test_a_capped_term_does_not_come_back_while_the_section_still_has_room():
    """The per-term cap sits underneath the section allowance, so one term
    cannot take both attempts when two genuinely different terms are open."""
    from bot.brain.brain import MAX_TERM_PROBES_PER_SECTION

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    brain.current_section.term_probes_spent = 0

    brain.apply_judgment(_term_judgment(brain, "session management", "hash table"))
    first = brain.unexplained_terms()[0]
    brain.mark_term_raised(first)
    brain.mark_term_raised(first)  # its own allowance, spent

    # Still reported by the judge, because it is still genuinely unexplained,
    # and never offered again.
    brain.current_section.term_probes_spent = MAX_TERM_PROBES_PER_SECTION - 1
    brain.apply_judgment(_term_judgment(brain, "session management", "hash table"))
    assert [t.term for t in brain.unexplained_terms()] == ["hash table"]


def test_a_term_asked_twice_and_never_explained_reaches_the_report():
    """It is evidence. "Never asked" and "asked twice and could not say" are
    different facts about a candidate, and the transcript makes it easy to miss."""
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    section_id = brain.current_section.id

    for _ in range(2):
        brain.apply_judgment(_term_judgment(brain, "session management"))
        brain.mark_term_raised(brain.unexplained_terms()[0])

    # The judge still reports it after the second ask. That is what makes it a
    # finding, and it is the point at which it is recorded: see
    # `_let_go_of_what_was_asked_and_not_answered`. Recording it when the
    # question was asked would write "asked and could not say" about somebody
    # whose answer nobody had read yet.
    brain.apply_judgment(_term_judgment(brain, "session management"))

    outcome = next(o for o in brain.outcomes() if o.section_id == section_id)
    assert [t.term for t in outcome.unexplained_terms] == ["session management"]


def test_a_term_explained_before_the_cap_never_reaches_the_report():
    """Asked once and answered is not a finding."""
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    section_id = brain.current_section.id

    brain.apply_judgment(_term_judgment(brain, "session management"))
    brain.mark_term_raised(brain.unexplained_terms()[0])
    brain.apply_judgment(_term_judgment(brain))  # the judge stops reporting it

    outcome = next(o for o in brain.outcomes() if o.section_id == section_id)
    assert outcome.unexplained_terms == []


# -- the spoken acknowledgement ----------------------------------------------


def test_the_filler_can_only_ever_repeat_their_own_words():
    """The safety property, and the reason it is a template rather than a call.

    The praise ban works. A filler generated by asking a model for something
    friendly to say is a route straight around it: this morning a model
    volunteered "that's a clear technical approach" about an answer that
    explained nothing.
    """
    from bot.brain.prompting import reflect_back

    said = reflect_back("session management concurrency")
    assert said == "Okay. So you used session management concurrency."

    for banned in ("clear", "solid", "interesting", "great", "good", "impressive"):
        assert banned not in said.lower()


def test_the_filler_says_nothing_rather_than_reaching():
    """Silence beats an invention. An interviewer that says "interesting" to
    fill a gap is the failure this is written against."""
    from bot.brain.prompting import MAX_REFLECTION_CHARS, reflect_back

    assert reflect_back("") is None
    assert reflect_back("   ") is None
    assert reflect_back(None) is None
    assert reflect_back("x" * (MAX_REFLECTION_CHARS + 1)) is None


# -- the escalation counts asks, not offers ----------------------------------


def _offer(brain, term="session management"):
    """Render a prompt carrying the term, which is an offer and nothing more."""
    brain.apply_judgment(_term_judgment(brain, term))
    return brain.plan_turn().system_instruction


def test_a_probe_about_a_different_open_term_is_still_counted():
    """The count used to stop at the term the prompt named.

    The judge returns a list of open terms and only one is offered. A question
    naming any of the others is a probe that happened: measured across ten runs,
    89 probe-shaped questions against 67 counted, and ten of sixty sections
    holding more probes than the allowance permits. The allowance was bounding
    one term's appearances rather than term probing.
    """
    from shared.contracts import UnexplainedTerm

    brain = InterviewBrain(tiny_blueprint(), config=BrainConfig(floor_turns=1, ceiling_turns=8))
    finish_opening(brain)
    section = brain.current_section
    brain.apply_judgment(
        Judgment(
            section_id=section.id,
            coverage=CoverageLevel.PARTIAL,
            unexplained_terms=[
                UnexplainedTerm(term="session management", said="we used session management",
                                section_id=section.id, turn_index=1),
                UnexplainedTerm(term="idempotency keys", said="and idempotency keys",
                                section_id=section.id, turn_index=1),
            ],
        )
    )
    brain.plan_turn()  # offers the first term

    brain.observe(
        bot_text="You said idempotency keys. Walk me through how that actually works?",
        candidate_text="They stop a repeat going through twice.",
    )

    assert section.term_raises.get("idempotency keys") == 1
    assert section.term_probes_spent == 1


def test_a_deictic_question_is_never_charged_to_a_term_that_was_not_offered():
    """Naming carries across terms; pointing does not.

    "How does that actually work" refers to whatever was just offered. Charging
    it to some other open term would be a guess, in a mechanism whose whole
    design is to fail towards not counting.
    """
    from bot.brain.terms import asked_about

    assert asked_about("session management", "How does that actually work?")
    assert not asked_about(
        "session management", "How does that actually work?", allow_deictic=False
    )
    assert asked_about(
        "session management", "What does session management do?", allow_deictic=False
    )


def test_a_term_offered_but_not_asked_about_is_not_counted():
    """The bug. The model is handed a term and may ask something else."""
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    _offer(brain)
    brain.observe(
        bot_text="What was the hardest prioritisation call you made that year?",
        candidate_text="We shipped it in the third quarter.",
    )

    assert brain.unexplained_terms()[0].times_raised == 0


def test_the_first_ask_is_the_plain_wording():
    """A term nobody has asked about yet gets asked about plainly."""
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    prompt = _offer(brain)

    assert "Walk me through how that actually works" in prompt
    assert "still do not know what it actually does" not in prompt


def test_the_escalation_needs_a_real_ask_behind_it():
    """The sentence claims a history, so the history has to exist.

    It fires on the second ask, when the candidate genuinely has been asked once
    and did not answer. Before the fix it fired on the second *offer*, which
    could be the first time anything was put to them.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    # Offered twice, asked nothing. The claim would be false, so it is not made.
    for _ in range(2):
        _offer(brain)
        brain.observe(
            bot_text="What did you decide not to build that year?",
            candidate_text="The session management manages those people per sessions.",
        )
    assert "twice now" not in _offer(brain)

    # Now a real ask, unanswered. The claim is true, so it is made.
    brain.observe(
        bot_text="You said session management. Walk me through how that works?",
        candidate_text="The session management manages those people per sessions.",
    )
    assert "twice now" in _offer(brain)


def test_a_term_asked_about_twice_and_still_unexplained_is_let_go():
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    for _ in range(2):
        _offer(brain)
        brain.observe(
            bot_text="You said session management. Walk me through how that works?",
            candidate_text="The session management manages those people per sessions.",
        )

    brain.apply_judgment(_term_judgment(brain, "session management"))
    assert "LET THIS ONE GO" in brain.plan_turn().system_instruction


def test_a_term_offered_three_times_and_asked_about_once_is_still_open():
    """**This is the bug, and it must fail loudly if anyone reverts the fix.**

    Three offers and one real question. Counting offers spent the allowance,
    let the term go, and told the candidate they had been asked twice.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    _offer(brain)
    brain.observe(
        bot_text="You said session management. Walk me through how that works?",
        candidate_text="The session management manages those people per sessions.",
    )
    for _ in range(2):
        _offer(brain)
        brain.observe(
            bot_text="What did you decide not to build that year?",
            candidate_text="The session management manages those people per sessions.",
        )

    open_terms = brain.unexplained_terms()
    assert open_terms, "the term was let go after a single real ask"
    assert open_terms[0].times_raised == 1
    assert "LET THIS ONE GO" not in brain.plan_turn().system_instruction


def test_a_question_can_be_about_the_term_without_naming_it():
    """"How does that actually work" straight after they used the term is an
    ask, and not counting it would leave the term open for ever."""
    from bot.brain.terms import asked_about

    assert asked_about("session management", "How does that actually work?")
    assert asked_about("session management", "Walk me through how that works?")
    assert asked_about("hash table", "You said you used a hash table. What does it do?")
    assert asked_about("hash tables", "Tell me about the hash table?")


def test_the_check_errs_towards_not_counting():
    """Undercounting costs a turn. Overcounting tells a candidate something
    untrue, which is the bug being fixed."""
    from bot.brain.terms import asked_about

    # A statement is never an ask, however much of their term it repeats. This
    # is the spoken acknowledgement, which used to burn an attempt.
    assert not asked_about("session management", "Okay. So you used session management.")
    assert not asked_about("session management", "What was the trade-off you made?")
    assert not asked_about("session management", "What did you decide not to build?")
    assert not asked_about("session management", "How did you measure the improvement?")
    # A term made only of weak words identifies nothing, so it is never matched
    # on its words alone.
    assert not asked_about("the platform", "What was the bet behind the platform?")
    assert not asked_about("", "How does that work?")


# -- the section allowance ---------------------------------------------------


def _real_ask(brain, term):
    """One genuine question about a term, counted the way a live turn is."""
    brain.observe(
        bot_text=f"You said {term}. Walk me through how that actually works?",
        candidate_text="It manages the sessions across the platform.",
    )


def test_the_allowance_is_spent_by_variants_of_one_term():
    """The measured failure, from a real interview.

    Replaying it through the judge returned "management concurrency", then that
    plus "session management", then those plus "concurrency control": three
    strings for one idea, all alive at once, none ever dropped. Per-term caps
    gave each its own allowance, so one word bought six sanctioned questions.
    """
    from bot.brain.brain import MAX_TERM_PROBES_PER_SECTION

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    variants = ["management concurrency", "session management", "concurrency control"]

    for variant in variants[:MAX_TERM_PROBES_PER_SECTION]:
        brain.apply_judgment(_term_judgment(brain, *variants))
        assert brain.unexplained_terms(), "stopped offering before the allowance was spent"
        brain.plan_turn()
        _real_ask(brain, variant)

    # A third variant is still unexplained and still reported. It is not asked.
    brain.apply_judgment(_term_judgment(brain, *variants))
    assert brain.unexplained_terms() == [], (
        "a third variant of the same idea got a fresh allowance"
    )


def test_once_spent_no_term_block_renders_in_that_section():
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    for variant in ("management concurrency", "session management"):
        brain.apply_judgment(_term_judgment(brain, variant))
        brain.plan_turn()
        _real_ask(brain, variant)

    brain.apply_judgment(_term_judgment(brain, "concurrency control"))
    prompt = brain.plan_turn().system_instruction
    assert "THIS TURN'S QUESTION IS ALREADY DECIDED" not in prompt


def test_the_let_go_is_spoken_exactly_once_when_the_allowance_is_spent():
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    for variant in ("management concurrency", "session management"):
        brain.apply_judgment(_term_judgment(brain, variant))
        brain.plan_turn()
        _real_ask(brain, variant)

    assert "LET THIS ONE GO" in brain.plan_turn().system_instruction
    brain.observe(bot_text="That's alright, let's leave that one there for now.",
                  candidate_text="Sure.")
    assert "LET THIS ONE GO" not in brain.plan_turn().system_instruction


def test_the_spoken_acknowledgement_does_not_spend_the_allowance():
    """It repeats their own term back and asks nothing. Counting it burned an
    attempt with no question having been put to the candidate."""
    from bot.brain.prompting import reflect_back

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    brain.apply_judgment(_term_judgment(brain, "session management"))
    brain.plan_turn()

    brain.observe(bot_text=reflect_back("session management"), candidate_text="Yes.")

    assert brain.current_section.term_probes_spent == 0
    assert brain.unexplained_terms()[0].times_raised == 0


def test_a_new_section_starts_with_a_fresh_allowance():
    """The bound is on one section spending itself on vocabulary, not on the
    interview ever asking again."""
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    first = brain.current_section.id

    for variant in ("management concurrency", "session management"):
        brain.apply_judgment(_term_judgment(brain, variant))
        brain.plan_turn()
        _real_ask(brain, variant)
    assert brain.unexplained_terms() == []

    while brain.current_section.id == first and not brain.is_finished:
        brain.observe(bot_text="And what happened next?", candidate_text="We shipped it.")

    assert brain.current_section.term_probes_spent == 0
    brain.apply_judgment(_term_judgment(brain, "idempotency keys"))
    assert [t.term for t in brain.unexplained_terms()] == ["idempotency keys"]


def test_no_worked_example_can_be_mistaken_for_a_candidate_phrase():
    """The interviewer said this to a candidate who never said it. It was the
    example inside the praise ban, read as their words and spoken back at them.

    A fabricated quote in a hiring record is what `feedback/score.py` verifies
    against the transcript, and this arrives through the front door: the
    interviewer says it out loud and it lands in the transcript those quotes are
    checked against.
    """
    from bot.brain.prompting import VOICE_RULES, render_section_prompt
    from bot.brain.state import Section
    from shared.contracts import SectionKind

    assert "So you used session management and concurrency control" not in VOICE_RULES
    assert "session management" not in VOICE_RULES
    assert "concurrency" not in VOICE_RULES

    blueprint = load_blueprint()
    prompt = render_section_prompt(
        blueprint=blueprint,
        section=Section(
            id="c", kind=SectionKind.COMPETENCY, name="Technical depth",
            budget_seconds=600, target_depth="deep", seed_questions=["Tell me about X."],
        ),
        carried_claims=[], unprobed_contradictions=[], remaining_seconds=300,
        is_last_competency=False,
    )
    # Nothing anywhere in a rendered prompt is a quotable domain phrase that the
    # model could hand back to a candidate as their own words.
    assert "session management" not in prompt


# -- the spoken acknowledgement is off ---------------------------------------


def _director(monkeypatch):
    from bot.brain_director import BrainDirector

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    director = BrainDirector(brain=brain, llm=object())
    brain.apply_judgment(_term_judgment(brain, "session management"))
    return brain, director


def test_no_acknowledgement_is_spoken_when_the_flag_is_off(monkeypatch):
    """Off by default. On a real interview it spoke 0.2 seconds after the
    candidate began a sentence; their answer arrived as eight fragments and the
    report scored them 2.0 out of 5 for communication, quoting five of them."""
    monkeypatch.delenv("FILLER_ENABLED", raising=False)
    brain, director = _director(monkeypatch)

    assert director._filler_for(brain.unexplained_terms()[0]) is None


def test_the_acknowledgement_still_works_when_switched_on(monkeypatch):
    """The code stays. It may be worth rebuilding once it can fire on what was
    actually just said and never over somebody still talking."""
    monkeypatch.setenv("FILLER_ENABLED", "1")
    brain, director = _director(monkeypatch)

    said = director._filler_for(brain.unexplained_terms()[0])
    assert said == "Okay. So you used session management."


def test_nothing_is_pushed_ahead_of_the_question_by_default(monkeypatch):
    """The director must not emit a spoken frame before the model has answered.

    Asserted on the source rather than by running a pipeline: the frame is only
    pushed inside `if filler:`, and `_filler_for` returns None when the flag is
    off, which the two tests above cover.
    """
    import inspect

    from bot import brain_director

    source = inspect.getsource(brain_director.BrainDirector.process_frame)
    assert "if filler:" in source
    assert "TTSSpeakFrame(filler)" in source


def test_the_judge_is_told_to_look_for_the_explanation_before_listing():
    """It never dropped a term across seven judgements on a real interview, and
    listed idempotency keys as unexplained on the judgement where the candidate
    explained them. The removal rule was a bullet under "Do NOT report"; it is
    now the first step of an ordered test."""
    from bot.brain.drivers import JUDGE_SYSTEM

    assert "STEP 1" in JUDGE_SYSTEM and "STEP 2" in JUDGE_SYSTEM
    assert "REPLACED each time" in JUDGE_SYSTEM
    # The discriminator that stops it dropping terms too eagerly.
    assert "restatement is not an explanation" in JUDGE_SYSTEM.lower()


# -- the probe asks what the section is for ----------------------------------


def _section(target_depth):
    from bot.brain.state import Section
    from shared.contracts import SectionKind

    return Section(id="s", kind=SectionKind.COMPETENCY, name="X",
                   budget_seconds=240, target_depth=target_depth)


def _term(times_raised=0):
    from shared.contracts import UnexplainedTerm

    return UnexplainedTerm(term="auto scaling", said="we use auto scaling",
                           section_id="s", turn_index=1, times_raised=times_raised)


def test_the_audience_not_the_act_decides_the_question():
    """"Explain" appears in both competencies on the real blueprint:

        technical      "able to explain in detail the architecture and flows"
        communication  "able to explain technical concepts to non-technical
                        stakeholders"

    Keying on the act would fire on both. Only one names who is listening.
    """
    from bot.brain.prompting import _is_about_talking_to_people

    technical = _section("Able to explain in detail the architecture and flows of "
                         "large-scale payments systems, including trade-offs.")
    communication = _section("Able to clearly explain technical concepts and reasoning "
                             "to non-technical stakeholders, and demonstrate how "
                             "decisions were communicated and aligned across teams.")

    assert _is_about_talking_to_people(technical) is False
    assert _is_about_talking_to_people(communication) is True


def test_it_fails_towards_the_technical_question():
    """When it cannot tell, it must do what it did before."""
    from bot.brain.prompting import _is_about_talking_to_people, _render_unexplained_term

    for depth in (None, "", "Can name a specific hard call and what was sacrificed."):
        assert _is_about_talking_to_people(_section(depth)) is False
    # And with no section at all, which is how every existing caller behaved.
    assert "Walk me through how that actually works" in _render_unexplained_term(_term())


def test_a_communication_section_asks_about_the_communication():
    """Five of six turns in one such section asked the mechanics of terms inside
    an analogy, and the employer's stated dealbreaker went unassessed."""
    from bot.brain.prompting import _render_unexplained_term

    comms = _section("Able to explain technical concepts to non-technical stakeholders.")

    first = _render_unexplained_term(_term(), comms)
    assert "How did you explain that to them?" in first
    assert "Walk me through how that actually works" not in first
    # And the alternative removed is the technical question, not the menu.
    assert "Do not ask instead how it works" in first

    again = _render_unexplained_term(_term(times_raised=1), comms)
    assert "How did you put that to them?" in again
    assert "What does it actually do" not in again


def test_the_technical_section_is_unchanged():
    from bot.brain.prompting import _render_unexplained_term

    tech = _section("Able to explain in detail the architecture and flows.")

    assert "Walk me through how that actually works" in _render_unexplained_term(_term(), tech)
    assert "What does it actually do" in _render_unexplained_term(_term(times_raised=1), tech)


def test_the_let_go_says_the_right_thing_was_missing():
    from bot.brain.prompting import _render_let_go

    tech = _section("Able to explain in detail the architecture and flows.")
    comms = _section("Able to explain technical concepts to non-technical stakeholders.")

    assert "say what it does" in _render_let_go(_term(), tech)
    assert "say how they explained it" in _render_let_go(_term(), comms)


def test_term_selection_is_untouched_by_any_of_this():
    """The section shapes the question asked, never which term is chosen. Nothing
    compares a term to a topic, which is the shape that has misfired here."""
    import inspect

    from bot.brain import brain as brain_module

    source = inspect.getsource(brain_module.InterviewBrain.unexplained_terms)
    assert "target_depth" not in source
    assert "_AUDIENCE" not in source


# -- the ceiling follows the allocation --------------------------------------


def _competency(budget_seconds, seeds=4):
    from bot.brain.state import Section
    from shared.contracts import SectionKind

    return Section(id="c", kind=SectionKind.COMPETENCY, name="C",
                   budget_seconds=budget_seconds,
                   seed_questions=[f"q{i}?" for i in range(seeds)])


def _ceiling(budget_seconds, seeds=4):
    brain = InterviewBrain(load_blueprint())
    return brain._turn_bounds(_competency(budget_seconds, seeds))[1]


def test_a_bigger_section_gets_more_turns():
    """It was a flat six for every competency. An eleven minute section and a
    four minute one got the same six questions, and across fifteen interviews
    the ceiling ended fourteen of twenty sections while the budget ended none."""
    assert _ceiling(720) > _ceiling(240)


def test_the_ceiling_is_derived_from_the_budget():
    """One turn per 22 seconds of allocation, which is the measured median
    length of a competency turn under the current code."""
    from bot.brain.brain import SECONDS_PER_COMPETENCY_TURN

    assert SECONDS_PER_COMPETENCY_TURN == 22
    # 264s buys twelve turns, and nothing rounds it up.
    assert _ceiling(264, seeds=8) == 12


def test_no_section_gets_fewer_turns_than_it_used_to():
    """The old flat value is the floor, so this change can only add questions."""
    from bot.brain.brain import MIN_COMPETENCY_TURNS

    for budget in (0, 30, 60, 120, 131):
        assert _ceiling(budget) >= MIN_COMPETENCY_TURNS


def test_a_huge_allocation_is_capped_by_the_material_that_was_planned():
    """The cap is not about the candidate's patience. It bounds a wrong
    allocation: blueprint generation asks for "3 or 4 questions" per competency,
    so a section handed thirty turns of budget has nothing planned to fill them
    and would improvise for most of it."""
    from bot.brain.brain import MAX_TERM_PROBES_PER_SECTION, _DEPTH_RAMP_STAGES

    seeds = 4
    planned = _DEPTH_RAMP_STAGES * seeds + MAX_TERM_PROBES_PER_SECTION
    assert _ceiling(60 * 60, seeds=seeds) == planned
    # More planned questions earns more turns, so the cap follows the blueprint.
    assert _ceiling(60 * 60, seeds=8) > _ceiling(60 * 60, seeds=4)


def test_an_explicit_ceiling_still_pins_it():
    """The deterministic suite drives sections to their end in two turns rather
    than fourteen, and must keep being able to."""
    from bot.brain.state import BrainConfig

    brain = InterviewBrain(load_blueprint(), config=BrainConfig(ceiling_turns=2))
    assert brain._turn_bounds(_competency(720))[1] == 2


def test_the_opening_and_closing_bounds_are_untouched():
    """Those were measured from a real candidate saying they had been banged
    straight into the deep end."""
    brain = InterviewBrain(load_blueprint())
    opening = next(s for s in brain.sections if s.kind.value == "opening")
    closing = next(s for s in brain.sections if s.kind.value == "closing")

    assert brain._turn_bounds(opening) == (2, 3)
    assert brain._turn_bounds(closing) == (1, 2)


def test_term_probing_can_be_switched_off_without_touching_anything_else(monkeypatch):
    """A baseline for the product without the feature, which never existed.

    Term probing was added because the interviewer never followed anything up:
    mechanism questions ran at 2 in 39 real opportunities before it. Repetition
    and cross-section drift arrived with it, five attempts to separate the two
    have failed, and every one was argued without a measurement of the interview
    without it, because it has been present in every run ever taken.

    **Off means off, and everything else carries on.** The judge still runs and
    still names what it thinks is unexplained; only the offer to the interviewer
    stops, and with it everything downstream of the offer.
    """
    from shared.contracts import UnexplainedTerm

    def fresh():
        brain = InterviewBrain(load_blueprint())
        finish_opening(brain)
        section_id = brain.current_section.id
        brain.apply_judgment(
            Judgment(
                section_id=section_id,
                coverage=CoverageLevel.PARTIAL,
                unexplained_terms=[
                    UnexplainedTerm(term="session management", said="we used it",
                                    section_id=section_id, turn_index=1)
                ],
            )
        )
        return brain

    monkeypatch.delenv("TERM_PROBING_DISABLED", raising=False)
    on = fresh()
    assert [t.term for t in on.unexplained_terms()] == ["session management"]
    assert "MANDATORY: THIS TURN" in on.plan_turn().system_instruction

    monkeypatch.setenv("TERM_PROBING_DISABLED", "1")
    off = fresh()
    assert off.unexplained_terms() == [], "a term was still offered"
    assert "MANDATORY: THIS TURN" not in off.plan_turn().system_instruction
    assert off.term_to_let_go() is None

    # What carries on: the judge ran, and what it said is still on the section.
    assert [t.term for t in off.current_section.unexplained_terms] == ["session management"]


def test_a_sections_first_ask_never_claims_it_asked_before():
    """**The falsehood `terms.py` exists to prevent, arriving from the prompt.**

    A section's first ask said "you have said X twice now" 29 times across six
    runs, while the brain logged "raised 1x" on the same turn. It was not the
    model imitating: the phrase was in neither the bridge nor the window on any
    of the 29. `_open_terms` still held the previous section's objects, whose
    `times_raised` was that section's count, and the block reads that field.

    Both directions, because a fix that silenced the escalation everywhere would
    pass a test written only for the first half.
    """
    from shared.contracts import UnexplainedTerm

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)
    first = brain.current_section.id

    def judged(section_id):
        return Judgment(
            section_id=section_id,
            coverage=CoverageLevel.PARTIAL,
            unexplained_terms=[
                UnexplainedTerm(term="session management", said="we used it",
                                section_id=section_id, turn_index=1)
            ],
        )

    brain.apply_judgment(judged(first))
    brain.mark_term_raised(brain.unexplained_terms()[0])

    # A real second ask in the same section. The claim is true, so it is made.
    assert "twice now" in brain.plan_turn().system_instruction

    while brain.current_section.id == first and not brain.is_finished:
        brain.observe(
            bot_text="And what happened next?",
            candidate_text="We shipped it in the third quarter and retention improved.",
        )
    assert brain.current_section.id != first, "never reached a second competency"

    # A new section, and nobody has asked here. No judgement has arrived for it
    # either.
    #
    # **This used to assert that the term block still rendered here**, with the
    # first-ask wording, about the previous section's term. That was the carry
    # itself written down as desired behaviour: `_open_terms` held the last
    # section's list, so the interviewer opened a new topic by asking about the
    # old one's word. Measured before it was cleared, all 13 first-turn probes in
    # a ten-run arm named a term the candidate had not said in that section.
    #
    # The falsehood this test was written for is unchanged and asserted first. It
    # now cannot occur at all here, because there is no term to escalate about.
    prompt = brain.plan_turn().system_instruction
    assert "twice now" not in prompt, "claimed a history this section does not have"
    assert "MANDATORY: THIS TURN'S QUESTION IS ALREADY DECIDED" not in prompt, (
        "asked about a term carried from the previous section"
    )
    # The section's own opener has the turn instead.
    assert "START THIS TOPIC BROADLY" in prompt


def test_the_interview_has_a_total_term_budget_under_the_per_section_one():
    """**The per-section allowance cannot bound this, and that is measured.**

    A section has two probes and the judge always has something unexplained to
    nominate, so the allowance is always spent on whatever is left. Explaining
    one term in the fixture took its ten probes to zero and handed all ten to
    the next term: 31 probes before, 31 after.

    Both bounds are asserted. A budget that replaced the per-section allowance
    would let one section spend the whole interview, which is the thing the
    per-section count was added for and which re-enabled a communication topic
    asking how somebody explained a term a technical topic asked the workings
    of.
    """
    from bot.brain.brain import (
        MAX_TERM_PROBES_PER_INTERVIEW,
        MAX_TERM_PROBES_PER_SECTION,
    )
    from shared.contracts import UnexplainedTerm

    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    spent = 0
    sections_used = 0
    while spent < MAX_TERM_PROBES_PER_INTERVIEW and not brain.is_finished:
        section_id = brain.current_section.id
        here = 0
        while True:
            brain.apply_judgment(
                Judgment(
                    section_id=section_id,
                    coverage=CoverageLevel.PARTIAL,
                    unexplained_terms=[
                        UnexplainedTerm(term=f"term {spent}-{here}", said="we used it",
                                        section_id=section_id, turn_index=1)
                    ],
                )
            )
            offered = brain.unexplained_terms()
            if not offered:
                break
            brain.mark_term_raised(offered[0])
            spent += 1; here += 1
        # The per-section allowance still bounds one section.
        assert here <= MAX_TERM_PROBES_PER_SECTION, "one section spent more than its own allowance"
        sections_used += 1
        while brain.current_section.id == section_id and not brain.is_finished:
            brain.observe(bot_text="And what happened next?",
                          candidate_text="We shipped it in the third quarter.")

    assert spent == MAX_TERM_PROBES_PER_INTERVIEW
    assert sections_used > 1, "the budget was spent inside a single section"

    # Spent. Nothing more is offered, anywhere, however fresh the term.
    brain.apply_judgment(
        Judgment(
            section_id=brain.current_section.id,
            coverage=CoverageLevel.PARTIAL,
            unexplained_terms=[
                UnexplainedTerm(term="a brand new term", said="we used it",
                                section_id=brain.current_section.id, turn_index=1)
            ],
        )
    )
    assert brain.unexplained_terms() == [], "kept probing past the interview budget"
    assert "MANDATORY: THIS TURN" not in brain.plan_turn().system_instruction


# -- asking to skip a question -----------------------------------------------


def test_asking_to_skip_a_question_is_a_declined_turn():
    """From `int_7e31aaff4ab2` at 234s, and the machinery already existed.

    `looks_like_refusal` holds "skip it" but matches whole utterances only, so
    "right now, I don't know the answer of this question. Can we skip it?" was
    read here as an ordinary answer — while the interviewer model's own
    `end_interview` tool read it as somebody leaving. Routing it to the refusal
    path costs nothing new: the block already says acknowledge briefly, ask a
    different and easier question on the same topic, and never re-ask the
    declined one. That is what a skip should get.
    """
    brain = InterviewBrain(load_blueprint())
    brain.observe(bot_text="How did you size that model for the GPU budget?")
    section = brain.current_section

    brain.observe(
        candidate_text=(
            "right now, I don't know the answer of this question. Can we skip it?"
        )
    )

    assert section.declined_turns == 1
    assert brain.withdrew is False
    assert brain.current_section is section


def test_a_skip_does_not_end_the_interview():
    """Four consecutive declines still close it gracefully, which is the
    existing rule and the right one. One does not."""
    brain = InterviewBrain(load_blueprint())
    brain.observe(bot_text="How did you size that model for the GPU budget?")

    brain.observe(candidate_text="can we skip this one")

    assert brain.withdrew is False
    assert brain.is_finished is False
    assert "THEY HAVE ASKED TO STOP" not in brain.plan_turn().system_instruction


def test_answering_after_a_skip_still_counts_as_an_answer():
    """The fragment rule that keeps "No." followed by a real answer from being
    read as a refusal has to hold for a skip too."""
    brain = InterviewBrain(load_blueprint())
    brain.observe(bot_text="How did you size that model for the GPU budget?")
    section = brain.current_section

    brain.observe(candidate_text="can we skip this one")
    brain.observe(
        candidate_text=(
            "Actually, we sized it against the 80GB card, so the 24B in bf16 "
            "left about twelve gigabytes of headroom for the KV cache."
        )
    )

    assert section.declined_turns == 0
    assert section.substantive_turns == 1


# -- the block's own probe has to count --------------------------------------

#: Exactly what `_render_unexplained_term` supplies on the first rung, and what
#: the model says back. No question mark, because it is an imperative.
FIRST_RUNG = "You said session management. Walk me through how that actually works."


def test_the_blocks_own_first_ask_is_counted():
    """**The bug, and every test above wrote around it.**

    Each of them phrased the probe "Walk me through how that works?" with a
    question mark. Production never emits one there: the block supplies an
    imperative ending in a full stop, and `asked_about` required a question
    mark, so the brain could not see its own probe.

    Live in `int_7e31aaff4ab2` that sentence was asked at 511s and again word
    for word at 542s. Neither counted, the raise count stayed at zero, and the
    escalated wording could never render.
    """
    from bot.brain.terms import asked_about

    assert asked_about("session management", FIRST_RUNG)
    assert asked_about("session management", FIRST_RUNG, allow_deictic=False)


def test_a_statement_is_still_not_an_ask():
    """The case the question-mark test was added for, which still holds."""
    from bot.brain.terms import asked_about

    assert not asked_about("session management", "Okay. So you used session management.")
    assert not asked_about("session management", "Okay, session management. Right.")
    # Reporting that somebody else asked is not asking.
    assert not asked_about(
        "session management", "He asked me to explain session management to the client."
    )


def test_the_second_probe_of_a_term_asks_something_else():
    """The failure this fixes, end to end.

    First rung asks how it works. Second rung says it has been asked once and
    asks what it does. Before the count was fixed the second rung could not
    render, so the second probe was the first probe again.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    first = _offer(brain)
    assert "Walk me through how that actually works" in first
    assert "twice now" not in first

    brain.observe(
        bot_text=FIRST_RUNG,
        candidate_text="The session management manages those people per sessions.",
    )

    second = _offer(brain)
    assert "twice now" in second
    assert "What does it actually do" in second


def test_the_escalation_still_needs_a_real_ask_behind_it():
    """Unchanged, and the reason the count stays the authority.

    Rendering the second rung on the second *offer* instead would say "you have
    said X twice now" to somebody asked once, which is the falsehood this whole
    file exists to prevent. Fixing the counter fixes the rung; loosening the
    rung would not have fixed the counter.
    """
    brain = InterviewBrain(load_blueprint())
    finish_opening(brain)

    for _ in range(2):
        _offer(brain)
        brain.observe(
            bot_text="What did you decide not to build that year?",
            candidate_text="The session management manages those people per sessions.",
        )

    assert "twice now" not in _offer(brain)


# -- session clock -----------------------------------------------------------


def test_session_remaining_counts_down_from_the_scheduled_duration():
    """The interviewer is told how much of the scheduled interview is left, from
    the moment the interview starts, counted across every section."""
    brain = InterviewBrain(tiny_blueprint(duration=20, grace=5), now=0.0)

    assert brain.scheduled_seconds == 20 * 60
    assert brain.session_remaining_seconds == 20 * 60

    brain.tick(5 * 60)
    assert brain.session_remaining_seconds == 15 * 60


def test_session_remaining_floors_at_zero_and_ignores_grace():
    """Grace is the wall, not the target: the clock reads zero at the scheduled end
    even though the brain still has grace before it force-closes."""
    brain = InterviewBrain(tiny_blueprint(duration=20, grace=5), now=0.0)

    brain.tick(20 * 60 + 30)
    assert brain.session_remaining_seconds == 0.0
    assert brain.elapsed_seconds < brain.hard_stop_seconds


def test_every_turn_prompt_states_the_session_time_left():
    brain = InterviewBrain(tiny_blueprint(duration=40, grace=5), now=0.0)

    brain.tick(10 * 60)
    prompt = brain.plan_turn().system_instruction
    assert "40 min total, 30 left" in prompt

    brain.tick(31 * 60)
    prompt = brain.plan_turn().system_instruction
    assert "9 left" in prompt


def test_session_time_is_stated_once_the_scheduled_time_is_up():
    brain = InterviewBrain(tiny_blueprint(duration=20, grace=5), now=0.0)

    brain.tick(21 * 60)
    prompt = brain.plan_turn().system_instruction
    assert "20 min is up" in prompt
    assert "wind down" in prompt


# -- seed questions sized to the time -----------------------------------------


def test_seed_questions_needed_fills_the_turn_cap_for_the_budget():
    from bot.brain.brain import seed_questions_needed

    # 4 minutes is ~10 exchanges, 2 of them for term probes, so 3 seeds (the old count).
    assert seed_questions_needed(4 * 60) == 3
    # 8 minutes is ~21 exchanges, 19 of them planned: 7 seeds at 3 stages each.
    assert seed_questions_needed(8 * 60) == 7
    # A very long competency is capped rather than asked for dozens of questions.
    assert seed_questions_needed(60 * 60) == 8


def test_seed_count_guidance_scales_with_the_scheduled_duration():
    from blueprint.generate import seed_count_guidance

    short = tiny_blueprint(duration=20).evaluation_spec
    long = tiny_blueprint(duration=40).evaluation_spec

    # 20 minutes less 4 of opening and closing leaves 16; alpha's 0.7 weight is 11.2
    # minutes, which needs eight seeds. The 40-minute spec needs more again.
    assert "alpha: 8 seed_questions (about 11 minutes" in seed_count_guidance(short)
    assert "beta: 4 seed_questions (about 5 minutes" in seed_count_guidance(short)
    assert seed_count_guidance(long) != seed_count_guidance(short)


# -- time handed back by early sections ---------------------------------------


def test_time_left_by_an_early_section_is_granted_to_the_rest_of_the_interview():
    """The opening finishes at one minute, not two. The minute it saves goes to the
    competencies, so the scheduled length is still filled."""
    brain = InterviewBrain(tiny_blueprint(duration=20, grace=5), now=0.0)
    competencies = [s for s in brain.sections if s.kind == SectionKind.COMPETENCY]
    before = sum(s.budget_seconds for s in competencies)

    brain.tick(60.0)
    brain._advance("budget_exhausted")

    granted = sum(s.budget_seconds for s in competencies)
    assert granted > before
    closing = brain.sections[-1].budget_seconds
    assert granted + 60.0 + closing == pytest.approx(20 * 60)


def test_grant_is_split_by_weight():
    brain = InterviewBrain(tiny_blueprint(duration=20, grace=5, weights=(0.7, 0.3)), now=0.0)
    alpha, beta = [s for s in brain.sections if s.kind == SectionKind.COMPETENCY]
    alpha_before, beta_before = alpha.budget_seconds, beta.budget_seconds

    brain.tick(60.0)
    brain._advance("budget_exhausted")

    gain_alpha = alpha.budget_seconds - alpha_before
    gain_beta = beta.budget_seconds - beta_before
    assert gain_alpha / gain_beta == pytest.approx(0.7 / 0.3)


def test_no_grant_when_the_schedule_is_already_full():
    brain = InterviewBrain(tiny_blueprint(duration=20, grace=5), now=0.0)
    competencies = [s for s in brain.sections if s.kind == SectionKind.COMPETENCY]
    before = [s.budget_seconds for s in competencies]

    brain.tick(120.0)
    brain._advance("budget_exhausted")

    assert [s.budget_seconds for s in competencies] == pytest.approx(before)
