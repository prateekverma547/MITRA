"""The scheduled time ends the interview, whether or not the candidate is talking.

Deterministic suite: the clock is injected, the worker is a fake that records
what would have been queued, and no LLM or audio is involved.
"""

import pytest
from pipecat.frames.frames import EndWorkerFrame, LLMMessagesAppendFrame, TTSSpeakFrame

from bot.brain.brain import InterviewBrain
from bot.brain.state import BrainConfig
from bot.time_limit import END_REASON, HARD_STOP_GOODBYE, TimeLimit
from shared.contracts import (
    Competency,
    CompetencyPlan,
    EvaluationSpec,
    InterviewBlueprint,
    SectionKind,
)

DURATION_MIN = 20
GRACE_MIN = 5


def make_brain(*, live_speech: bool = False) -> InterviewBrain:
    blueprint = InterviewBlueprint(
        blueprint_id="timeup",
        evaluation_spec=EvaluationSpec(
            role_title="R",
            seniority="S",
            experience_expectation="e",
            duration_minutes=DURATION_MIN,
            overrun_grace_minutes=GRACE_MIN,
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
    return InterviewBrain(
        blueprint,
        config=BrainConfig(floor_turns=1, ceiling_turns=9),
        live_speech=live_speech,
    )


class FakeWorker:
    def __init__(self) -> None:
        self.queued: list = []

    async def queue_frames(self, frames) -> None:
        self.queued.extend(frames)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# -- brain -----------------------------------------------------------------


def test_close_starts_at_scheduled_end_minus_closing_budget_not_at_grace():
    brain = make_brain()
    closing = next(s for s in brain.sections if s.kind == SectionKind.CLOSING)

    assert brain.closing_begins_seconds == pytest.approx(DURATION_MIN * 60 - closing.budget_seconds)
    assert brain.hard_stop_seconds == pytest.approx((DURATION_MIN + GRACE_MIN) * 60)
    assert brain.closing_begins_seconds < brain.hard_stop_seconds


def test_time_is_up_needs_no_candidate_turn():
    brain = make_brain()
    finish_opening(brain)

    brain.tick(brain.closing_begins_seconds - 1)
    assert brain.begin_time_close() is False
    assert brain.current_section.kind != SectionKind.CLOSING

    brain.tick(brain.closing_begins_seconds)
    assert brain.time_is_up
    assert brain.begin_time_close() is True
    assert brain.current_section.kind == SectionKind.CLOSING


def test_the_goodbye_finishes_the_interview_after_time_up():
    brain = make_brain()
    finish_opening(brain)
    brain.tick(brain.closing_begins_seconds)
    brain.begin_time_close()

    brain.bot_finished_speaking("Thank you for your time. Goodbye.")

    assert brain.is_finished


def closing_brain(*, live_speech: bool = False) -> InterviewBrain:
    brain = make_brain(live_speech=live_speech)
    finish_opening(brain)
    brain.tick(brain.closing_begins_seconds)
    assert brain.begin_time_close() is True
    return brain


def test_a_nudge_in_the_closing_does_not_end_the_interview():
    """Regression: a silence nudge was the second closing turn and ended the call."""
    brain = closing_brain()
    brain.bot_finished_speaking("Thanks. Do you have any questions for me?")

    brain.silence_prompt_expected()
    brain.bot_finished_speaking("Take your time, there's no rush.")

    assert not brain.is_finished


def test_the_candidate_can_answer_the_closing_question_after_a_nudge():
    brain = closing_brain()
    brain.bot_finished_speaking("Thanks. Do you have any questions for me?")
    brain.silence_prompt_expected()
    brain.bot_finished_speaking("Take your time, there's no rush.")

    brain.observe(candidate_text="What does the team work on?")
    assert not brain.is_finished

    brain.bot_finished_speaking("Thanks for your time. Goodbye.")
    assert brain.is_finished


def test_a_candidate_reply_clears_a_stale_silence_prompt():
    """If the candidate speaks after a prompt was announced, the flag must not
    swallow the interviewer's next real closing turn."""
    brain = closing_brain()
    brain.silence_prompt_expected()
    brain.observe(candidate_text="Sure, go ahead.")

    brain.bot_finished_speaking("Thanks for your time. Goodbye.")

    assert brain.is_finished


def test_a_prompt_announced_before_the_closing_does_not_linger_into_it():
    brain = make_brain()
    finish_opening(brain)
    brain.silence_prompt_expected()  # in a competency: a no-op, nothing to carry

    brain.tick(brain.closing_begins_seconds)
    assert brain.begin_time_close() is True
    brain.bot_finished_speaking("Thank you for your time. Goodbye.")

    assert brain.is_finished


def test_an_answer_running_past_time_up_is_not_cut_off_by_observe():
    """A candidate still talking when time runs out gets the close, not a new topic."""
    brain = make_brain()
    finish_opening(brain)
    brain.tick(brain.closing_begins_seconds + 5)

    brain.observe(bot_text="Tell me more.", candidate_text="and then we shipped it")

    assert brain.current_section.kind == SectionKind.CLOSING


def test_begin_time_close_is_idempotent():
    brain = make_brain()
    finish_opening(brain)
    brain.tick(brain.closing_begins_seconds)

    assert brain.begin_time_close() is True
    assert brain.begin_time_close() is False


# -- clock -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_candidate_is_closed_at_the_scheduled_time():
    brain = make_brain()
    finish_opening(brain)
    clock = Clock()
    worker = FakeWorker()
    limit = TimeLimit(brain=brain, clock=clock, poll_seconds=1.0)
    limit.attach(worker)

    clock.now = brain.closing_begins_seconds
    await limit.step()
    assert brain.current_section.kind == SectionKind.CLOSING
    # Asks for the goodbye only after a quiet spell, not on the first reading.
    assert not any(isinstance(f, LLMMessagesAppendFrame) for f in worker.queued)

    clock.now += 10
    await limit.step()
    asks = [f for f in worker.queued if isinstance(f, LLMMessagesAppendFrame)]
    assert len(asks) == 1
    assert "Do not ask any further questions" in asks[0].messages[0]["content"]


@pytest.mark.asyncio
async def test_goodbye_is_not_requested_over_a_candidate_who_is_speaking():
    brain = make_brain()
    finish_opening(brain)
    clock = Clock()
    worker = FakeWorker()
    limit = TimeLimit(brain=brain, clock=clock)
    limit.attach(worker)
    limit._candidate_speaking = True

    clock.now = brain.closing_begins_seconds
    await limit.step()
    clock.now += 60
    await limit.step()

    assert not any(isinstance(f, LLMMessagesAppendFrame) for f in worker.queued)


@pytest.mark.asyncio
async def test_hard_stop_ends_the_call_even_if_closing_never_completed():
    brain = make_brain()
    finish_opening(brain)
    clock = Clock()
    worker = FakeWorker()
    limit = TimeLimit(brain=brain, clock=clock)
    limit.attach(worker)

    clock.now = brain.hard_stop_seconds
    await limit.step()

    ends = [f for f in worker.queued if isinstance(f, EndWorkerFrame)]
    assert len(ends) == 1
    assert ends[0].reason == END_REASON
    assert limit.ended


@pytest.mark.asyncio
async def test_the_hard_stop_says_goodbye_before_it_hangs_up():
    """Regression: the wall ended the call with no goodbye, mid-question if need be."""
    brain = make_brain()
    finish_opening(brain)
    clock = Clock()
    worker = FakeWorker()
    limit = TimeLimit(brain=brain, clock=clock)
    limit.attach(worker)

    clock.now = brain.hard_stop_seconds
    await limit.step()

    spoken = [i for i, f in enumerate(worker.queued) if isinstance(f, TTSSpeakFrame)]
    ends = [i for i, f in enumerate(worker.queued) if isinstance(f, EndWorkerFrame)]
    assert spoken and ends and spoken[0] < ends[0]
    assert worker.queued[spoken[0]].text == HARD_STOP_GOODBYE
    assert "Thank you for your time" in HARD_STOP_GOODBYE and "Goodbye" in HARD_STOP_GOODBYE


@pytest.mark.asyncio
async def test_finished_interview_is_left_to_the_ending_observer():
    brain = make_brain()
    finish_opening(brain)
    brain.tick(brain.closing_begins_seconds)
    brain.begin_time_close()
    brain.bot_finished_speaking("Goodbye.")
    assert brain.is_finished

    clock = Clock()
    clock.now = brain.hard_stop_seconds + 100
    worker = FakeWorker()
    limit = TimeLimit(brain=brain, clock=clock)
    limit.attach(worker)
    await limit.step()

    assert worker.queued == []
    assert not limit.ended


def finish_opening(brain: InterviewBrain) -> None:
    """Get past the opening warm-up, however many turns it takes."""
    t = 10.0
    while brain.current_section.kind == SectionKind.OPENING:
        t += 10.0
        brain.tick(t)
        brain.observe(bot_text="How are you?", candidate_text="Fine, thanks.")


def test_a_silent_candidate_does_not_hold_a_section_past_its_budget():
    brain = make_brain()
    finish_opening(brain)
    first = brain.current_section
    assert first.kind == SectionKind.COMPETENCY

    brain.tick(first.started_at + first.budget_seconds - 1)
    assert brain.section_budget_spent() is False

    brain.tick(first.started_at + first.budget_seconds)
    assert brain.section_budget_spent() is True
    assert brain.current_section is not first
    outcome = {o.section_id: o for o in brain.outcomes()}[first.id]
    assert outcome.coverage_shortfall
    assert outcome.shortfall_reason.startswith("Section ended on budget_exhausted")


async def test_the_clock_moves_a_silent_candidate_past_an_expired_section():
    brain = make_brain()
    finish_opening(brain)
    clock = Clock()
    worker = FakeWorker()
    limit = TimeLimit(brain=brain, clock=clock, poll_seconds=1.0)
    limit.attach(worker)
    first = brain.current_section

    clock.now = first.started_at + first.budget_seconds
    await limit.step()

    assert brain.current_section.id != first.id
    assert not brain.is_finished


def test_the_budget_does_not_run_before_the_interviewer_has_spoken():
    brain = make_brain()
    brain.tick(10_000.0)

    assert brain.section_budget_spent() is False
    assert brain.current_section.kind == SectionKind.OPENING


def test_live_closing_question_then_reply_does_not_end_the_interview():
    """Regression: the reply's context frame re-counted the closing question, so the
    interview ended before the candidate had answered it."""
    brain = closing_brain(live_speech=True)
    question = "Thanks. Do you have any questions for me?"
    brain.bot_finished_speaking(question)

    brain.observe(candidate_text="Yes, what does the team work on?", bot_text=question)
    assert not brain.is_finished

    brain.bot_finished_speaking("Thanks for your time. Goodbye.")
    assert brain.is_finished


def test_a_competency_question_carried_into_the_closing_is_not_a_closing_turn():
    """Regression: the first closing frame still carried the last competency question."""
    brain = closing_brain(live_speech=True)
    brain.observe(
        candidate_text="And there are many other tools, like Zapier.",
        bot_text="Which automation tool did you pick for that process?",
    )
    assert not brain.is_finished

    brain.bot_finished_speaking("Thanks. Do you have any questions for me?")
    assert not brain.is_finished
