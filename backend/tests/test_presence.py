"""Knowing who is in the room, and what the silence ladder does about it.

Daily reports participants joining and leaving and nothing listened. So the
interviewer nudged into empty rooms, and then closed the session blaming a
silence that was really a dropped call. That distinction matters twice: it is
the difference between talking to furniture and waiting for someone, and it is
the difference between a candidate who would not speak and one who could not.
"""

import pytest

from bot.presence import RoomPresence
from bot.silence import SilenceEscalation, SilenceLadder


# -- presence ----------------------------------------------------------------


def test_the_room_is_empty_until_someone_joins():
    presence = RoomPresence()

    assert presence.candidate_present is False

    presence.joined("cand-1")
    assert presence.candidate_present is True


def test_leaving_is_counted():
    presence = RoomPresence()
    presence.joined("cand-1")
    presence.left("cand-1", "connection lost")

    assert presence.candidate_present is False
    assert presence.disconnects == 1


def test_rejoining_restores_presence_without_double_counting_the_arrival():
    """A dropped connection and a reconnect is one disconnect, not two people."""
    presence = RoomPresence()
    presence.joined("cand-1")
    presence.left("cand-1")
    presence.joined("cand-1")

    assert presence.candidate_present is True
    assert presence.disconnects == 1
    assert presence.peak_others == 1


def test_a_second_person_in_the_room_is_noticed():
    """Certain knowledge, not inference. Recorded, never policed: somebody may
    well have a partner bringing them tea."""
    presence = RoomPresence()
    presence.joined("cand-1")
    presence.joined("someone-else")

    assert presence.others_in_the_room is True
    assert presence.summary()["peak_others"] == 2


def test_leaving_without_arriving_is_ignored():
    presence = RoomPresence()
    presence.left("never-here")

    assert presence.disconnects == 0


# -- what the ladder does with it --------------------------------------------


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)

    def of_type(self, kind):
        return [f for f in self.frames if isinstance(f, kind)]


def ladder_with(presence):
    escalation = SilenceEscalation(
        ladder=SilenceLadder(), session_id="t", presence=presence
    )
    worker = FakeWorker()
    escalation.attach(worker)
    return escalation, worker


async def test_an_empty_room_is_not_nudged():
    """"Are you still there?" to somebody who disconnected is not patience."""
    from pipecat.frames.frames import TTSSpeakFrame

    presence = RoomPresence()  # nobody ever joined
    escalation, worker = ladder_with(presence)

    await escalation.handle_idle(aggregator=None)

    assert worker.of_type(TTSSpeakFrame) == []
    assert escalation.events[-1]["action"] == "waiting_for_rejoin"


async def test_a_present_candidate_is_still_nudged():
    from pipecat.frames.frames import TTSSpeakFrame

    presence = RoomPresence()
    presence.joined("cand-1")
    escalation, worker = ladder_with(presence)

    await escalation.handle_idle(aggregator=None)

    assert len(worker.of_type(TTSSpeakFrame)) == 1


async def test_a_dropped_call_ends_the_session_saying_so():
    """The record has to show the interview ended because the call dropped, not
    because the candidate sat there in silence."""
    from pipecat.frames.frames import EndWorkerFrame

    presence = RoomPresence()
    presence.joined("cand-1")
    escalation, worker = ladder_with(presence)
    presence.left("cand-1")

    for _ in range(4):  # walk the ladder out past the closing threshold
        await escalation.handle_idle(aggregator=None)

    ended = worker.of_type(EndWorkerFrame)
    assert ended and ended[0].reason == "candidate_left_the_call"
    assert any(e["action"] == "ended_call_dropped" for e in escalation.events)


async def test_a_ladder_without_presence_behaves_exactly_as_before():
    """A transport that cannot report presence degrades to guessing, not to
    breaking."""
    from pipecat.frames.frames import TTSSpeakFrame

    escalation = SilenceEscalation(ladder=SilenceLadder(), session_id="t")
    worker = FakeWorker()
    escalation.attach(worker)

    await escalation.handle_idle(aggregator=None)

    assert len(worker.of_type(TTSSpeakFrame)) == 1


# -- patience ----------------------------------------------------------------


def test_patience_stretches_only_the_first_rung():
    ladder = SilenceLadder()
    patient = ladder.thresholds(ladder.deep_question_factor)

    assert patient[0] > ladder.nudge_at_seconds
    # The later rungs are about the channel and the session, not about thinking,
    # but they still have to stay clear of a stretched first rung.
    assert patient[1] > patient[0]
    assert patient[2] > patient[1]


def test_normal_patience_is_exactly_the_configured_ladder():
    """A deliberately tight configuration must stay tight."""
    ladder = SilenceLadder(nudge_at_seconds=5, audio_check_at_seconds=9, close_at_seconds=30)

    assert ladder.thresholds(1.0) == [5, 9, 30]


def test_the_brain_is_more_patient_when_probing_than_when_greeting():
    """Someone recalling a decision they regret is not the same as someone
    being asked their name."""
    from bot.brain.brain import InterviewBrain
    from tests.test_brain import tiny_blueprint

    brain = InterviewBrain(tiny_blueprint())
    greeting_patience = brain.patience

    brain.observe(bot_text="Hello, how are you?")
    brain.observe(candidate_text="Good thanks, I have been doing this for five years.")
    brain.observe(bot_text="Tell me about a product you shipped.")
    brain.observe(candidate_text="We shipped a retrieval assistant for support agents.")
    brain.observe(bot_text="What did you have to give up to do that?")
    brain.observe(candidate_text="We delayed the reporting work by about a quarter.")

    assert brain.patience > greeting_patience


async def test_a_broken_patience_reading_never_stops_the_ladder():
    """Losing a nudge because a multiplier threw would be a silent regression
    to the behaviour this module was written to fix."""
    from pipecat.frames.frames import TTSSpeakFrame

    def explode() -> float:
        raise RuntimeError("no")

    escalation = SilenceEscalation(session_id="t", patience=explode)
    worker = FakeWorker()
    escalation.attach(worker)

    assert escalation.initial_timeout > 0
    await escalation.handle_idle(aggregator=None)
    assert len(worker.of_type(TTSSpeakFrame)) == 1
