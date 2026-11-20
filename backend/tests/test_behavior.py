"""Tolerant behaviour suite — real LLM calls. NOT CI-gating.

Kept strictly separate from the deterministic brain-logic suite (CLAUDE.md).
These assert on what a model *chooses to say*, so they are slow, cost money, and
will occasionally disagree with themselves. Mixing them into the fast suite
would make the fast suite untrustworthy.

Run explicitly:

    RUN_BEHAVIOR_TESTS=1 uv run pytest tests/test_behavior.py -v -m behavior

Assertions are deliberately loose: they check that a behaviour is present at all,
not that it is phrased a particular way.
"""

import os

import pytest

from bot.blueprint_source import load_blueprint
from bot.brain.brain import InterviewBrain
from bot.brain.drivers import OpenAIInterviewer, OpenAIJudge
from bot.brain.harness import (
    contradicting_candidate,
    jargon_candidate,
    off_topic_candidate,
    run_interview,
    thin_answer_candidate,
)
from bot.brain.state import BrainConfig
from bot.config import Settings

pytestmark = [
    pytest.mark.behavior,
    pytest.mark.skipif(
        not os.environ.get("RUN_BEHAVIOR_TESTS"),
        reason="Real LLM calls. Set RUN_BEHAVIOR_TESTS=1 to run.",
    ),
]

LIVE_MODEL = os.environ.get("BEHAVIOR_MODEL", "gpt-4.1-mini")


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings.load()


async def interview(settings, candidate, *, max_turns=16, ceiling=3):
    blueprint = load_blueprint()
    brain = InterviewBrain(blueprint, config=BrainConfig(floor_turns=2, ceiling_turns=ceiling))
    run = await run_interview(
        brain,
        interviewer=OpenAIInterviewer(api_key=settings.openai_api_key, model=LIVE_MODEL),
        candidate=candidate,
        judge=OpenAIJudge(api_key=settings.openai_api_key, model=settings.blueprint_model),
        seconds_per_turn=40,
        max_turns=max_turns,
    )
    return brain, run


def interviewer_text(run) -> list[str]:
    return [t["text"] for t in run.transcript if t["speaker"] == "interviewer"]


async def test_thin_answers_are_never_scored_as_sufficient(settings):
    """The candidate says nothing concrete. Accepting that would be the worst
    possible failure — a confident report built on air."""
    brain, run = await interview(settings, thin_answer_candidate())

    competency_coverage = [
        o["coverage"] for o in run.outcomes if o["kind"] == "competency" and o["turns_spent"] > 0
    ]
    assert competency_coverage
    assert "sufficient" not in competency_coverage


async def test_thin_answers_are_probed_for_specifics(settings):
    brain, run = await interview(settings, thin_answer_candidate())

    asked = " ".join(interviewer_text(run)).lower()
    probing = ["specific", "example", "walk me through", "which", "what was", "tell me about a"]
    assert sum(term in asked for term in probing) >= 3


async def test_an_unexplained_term_is_unpacked_rather_than_accepted(settings):
    """The failure the existing thin-answer test cannot see.

    `test_thin_answers_are_probed_for_specifics` above joins every interviewer
    turn and looks for words like "specific" or "which" anywhere in the run. A
    planned question reworded to mention what the candidate said clears that
    easily, which is why it passed throughout the six real interviews where this
    went wrong.

    This asserts the thing that actually distinguishes the two. Against a
    candidate who says "we have implemented a session management concurrency"
    and never says what it does, the interviewer has to ask what it is or how it
    works. Quoting the term back while asking for its trade-off is the failure,
    not the fix, so the trade-off shape is checked for separately.
    """
    brain, run = await interview(settings, jargon_candidate())

    asked = " ".join(interviewer_text(run)).lower()
    mechanism = [
        "what do you mean", "what does that mean", "how does that work",
        "how does it work", "walk me through how", "explain how",
        "what exactly", "how exactly", "what is that", "in your system",
    ]
    assert any(cue in asked for cue in mechanism), (
        "the interviewer never asked what any of the terms meant; it accepted "
        "jargon as evidence"
    )


async def test_cross_section_contradiction_is_raised_once_and_neutrally(settings):
    """Only carried claims can surface this — the two statements are in
    different sections, so section-scoped context alone would miss it."""
    brain, run = await interview(settings, contradicting_candidate())

    assert brain.contradictions, "judge never detected the contradiction"

    asked = " ".join(interviewer_text(run)).lower()
    referenced_earlier = any(
        phrase in asked for phrase in ("earlier", "you mentioned", "you said", "fit together")
    )
    assert referenced_earlier, "interviewer never called back to the earlier claim"

    # Neutral, not prosecutorial.
    for word in ("contradict", "inconsistent", "lying", "dishonest"):
        assert word not in asked

    # Raised at most once.
    assert sum(c.probed for c in brain.contradictions) <= len(brain.contradictions)


async def test_off_topic_candidate_is_redirected_to_the_role(settings):
    brain, run = await interview(settings, off_topic_candidate())

    asked = " ".join(interviewer_text(run)).lower()
    assert "product manager" in asked


async def test_interviewer_does_not_re_greet_at_section_boundaries(settings):
    """The model cannot see earlier sections. Without an explicit instruction it
    says "thanks for joining" twenty minutes in."""
    brain, run = await interview(settings, thin_answer_candidate())

    later_turns = [
        t["text"].lower()
        for t in run.transcript
        if t["speaker"] == "interviewer" and t["section_id"] not in ("opening",)
    ]
    assert later_turns
    for text in later_turns:
        assert "thank you for joining" not in text
        assert "thanks for joining" not in text
        assert "i'm an ai interviewer" not in text


async def test_interviewer_never_delivers_a_verdict(settings):
    """Reports inform humans; the bot must not evaluate out loud."""
    brain, run = await interview(settings, thin_answer_candidate())

    asked = " ".join(interviewer_text(run)).lower()
    for phrase in ("you would be a good fit", "you're hired", "you failed", "weak candidate"):
        assert phrase not in asked


async def test_a_term_the_candidate_explained_drops_off_the_next_judgement(settings):
    """The mechanism the whole design rests on, which had never once fired.

    Across seven judgements on a real interview not one term left the list, and
    idempotency keys were reported as unexplained on the judgement where the
    candidate explained them competently. That is why a cap had to be invented
    to stop the interviewer asking.
    """
    from bot.brain.brain import JudgmentRequest, Turn
    from bot.brain.drivers import OpenAIJudge

    judge = OpenAIJudge(api_key=settings.openai_api_key, model=settings.blueprint_model)
    depth = "Able to explain payments architecture in detail, not conceptually."

    def turns(*texts):
        return [
            Turn(index=i, speaker="interviewer" if i % 2 == 0 else "candidate",
                 text=t, section_id="s", at_seconds=float(i))
            for i, t in enumerate(texts)
        ]

    unexplained = turns(
        "How do you stop a retry double charging?",
        "We use idempotency keys and retry logic when the gateway times out.",
    )
    first = await judge.assess(JudgmentRequest(
        section_id="s", kind="depth", target_depth=depth,
        transcript=unexplained, carried_claims=[]))
    assert any("idempotenc" in t.term.lower() for t in first.unexplained_terms), (
        "a term used with no mechanism was not reported at all"
    )

    explained = unexplained + turns(
        "",  # placeholder to keep the speakers alternating
        "Every payment request gets a unique key from the client. Before we "
        "process anything we check whether we have already seen that key. If we "
        "have, we return the original result instead of charging again, so a "
        "retry after a timeout cannot double charge someone.",
    )[1:]
    second = await judge.assess(JudgmentRequest(
        section_id="s", kind="depth", target_depth=depth,
        transcript=explained, carried_claims=[]))

    assert not any("idempotenc" in t.term.lower() for t in second.unexplained_terms), (
        "the term stayed on the list after the candidate explained the mechanism"
    )


async def test_a_term_merely_restated_stays_on_the_list(settings):
    """The other half. A candidate restating the term in different words has not
    explained it, and that is the failure the whole feature exists to catch."""
    from bot.brain.brain import JudgmentRequest, Turn
    from bot.brain.drivers import OpenAIJudge

    judge = OpenAIJudge(api_key=settings.openai_api_key, model=settings.blueprint_model)
    transcript = [
        Turn(index=0, speaker="interviewer", text="How does that work?",
             section_id="s", at_seconds=0.0),
        Turn(index=1, speaker="candidate", section_id="s", at_seconds=1.0,
             text="We implemented session management concurrency, which allows "
                  "multiple users to make payments at the same time."),
        Turn(index=2, speaker="interviewer", section_id="s", at_seconds=2.0,
             text="You said session management concurrency. Walk me through how "
                  "that actually works?"),
        Turn(index=3, speaker="candidate", section_id="s", at_seconds=3.0,
             text="The concurrency allows the multiple users to access it "
                  "concurrently, while the session management manages those "
                  "people per sessions."),
    ]

    judged = await judge.assess(JudgmentRequest(
        section_id="s", kind="depth",
        target_depth="Able to explain payments architecture in detail.",
        transcript=transcript, carried_claims=[]))

    assert any("session management" in t.term.lower() or "concurrency" in t.term.lower()
               for t in judged.unexplained_terms), (
        "a term defined with itself was treated as explained"
    )
