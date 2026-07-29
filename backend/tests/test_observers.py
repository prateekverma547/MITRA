"""Text-mode tests for the session instrumentation.

The Milestone 1 DoD is stated in numbers (median TTFA, tolerated pause length),
so the code producing those numbers needs to be correct for reasons other than
"it looked right in the log". These tests drive the observers with synthetic
frames — no audio, no network, no providers.
"""

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.utils.text.base_text_aggregator import AggregationType

from bot import observers
from bot.observers import TranscriptObserver, TurnLatencyObserver


def spoken(text: str) -> TTSTextFrame:
    """Text as handed to the synthesiser — what the candidate actually hears."""
    return TTSTextFrame(text=text, aggregated_by=AggregationType.SENTENCE)


VAD_STOP_SECS = 0.2
VAD_START_SECS = 0.2


class FakeClock:
    """Deterministic stand-in for wall-clock time."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(observers.time, "time", fake)
    return fake


class _Pushed:
    """Minimal stand-in for FramePushed — observers only read `.frame`."""

    def __init__(self, frame):
        self.frame = frame


async def feed(observer, frame):
    await observer.on_push_frame(_Pushed(frame))


def vad_stopped(clock: FakeClock) -> VADUserStoppedSpeakingFrame:
    """VAD reports silence now, having required VAD_STOP_SECS to confirm it."""
    return VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS, timestamp=clock.now)


def vad_started(clock: FakeClock) -> VADUserStartedSpeakingFrame:
    return VADUserStartedSpeakingFrame(start_secs=VAD_START_SECS, timestamp=clock.now)


async def test_ttfa_measured_from_true_end_of_speech(clock):
    """TTFA must exclude the VAD confirmation delay, not include it.

    The candidate genuinely stops at t=0. VAD needs 0.2s to be sure, so it
    reports at t=0.2. Endpointing finishes at t=0.5, first audio at t=1.0.
    TTFA is 1.0s from the true stop — not 0.8s from the VAD report.
    """
    observer = TurnLatencyObserver()

    await feed(observer, UserStartedSpeakingFrame())
    clock.advance(5.0)  # candidate speaks for 5s, truly stops at this instant
    true_stop = clock.now

    clock.advance(VAD_STOP_SECS)
    await feed(observer, vad_stopped(clock))

    clock.advance(0.3)  # smart-turn deliberates
    await feed(observer, UserStoppedSpeakingFrame())

    clock.advance(0.5)  # LLM + TTS
    await feed(observer, BotStartedSpeakingFrame())

    (turn,) = observer.turns
    assert turn.speech_end == pytest.approx(true_stop)
    assert turn.ttfa_ms == pytest.approx(1000.0)
    assert turn.endpointing_ms == pytest.approx(500.0)
    assert turn.generation_ms == pytest.approx(500.0)
    # The parts must account for the whole.
    assert turn.endpointing_ms + turn.generation_ms == pytest.approx(turn.ttfa_ms)


async def test_three_second_thinking_pause_is_recorded_and_survived(clock):
    """The DoD case: candidate pauses 3s mid-answer, then continues."""
    observer = TurnLatencyObserver()

    await feed(observer, UserStartedSpeakingFrame())
    clock.advance(2.0)

    # Candidate trails off mid-thought.
    clock.advance(VAD_STOP_SECS)
    await feed(observer, vad_stopped(clock))

    # Three seconds of silence, then they resume. No UserStoppedSpeakingFrame
    # was emitted, i.e. smart-turn kept the turn open.
    clock.advance(3.0 - VAD_STOP_SECS + VAD_START_SECS)
    await feed(observer, vad_started(clock))

    clock.advance(4.0)  # rest of the answer
    clock.advance(VAD_STOP_SECS)
    await feed(observer, vad_stopped(clock))
    clock.advance(0.3)
    await feed(observer, UserStoppedSpeakingFrame())
    clock.advance(0.5)
    await feed(observer, BotStartedSpeakingFrame())

    (turn,) = observer.turns
    assert turn.tolerated_pauses == pytest.approx([3.0])
    assert observer.summary()["longest_tolerated_pause_s"] == pytest.approx(3.0)
    # TTFA must be measured from the *final* stop, not the pause.
    assert turn.ttfa_ms == pytest.approx(1000.0)


async def test_summary_reports_median_over_turns(clock):
    """Median, not mean — one slow cold-start turn shouldn't move the verdict."""
    observer = TurnLatencyObserver()

    for generation_secs in (0.4, 0.5, 3.0):
        await feed(observer, UserStartedSpeakingFrame())
        clock.advance(1.0)
        clock.advance(VAD_STOP_SECS)
        await feed(observer, vad_stopped(clock))
        await feed(observer, UserStoppedSpeakingFrame())
        clock.advance(generation_secs)
        await feed(observer, BotStartedSpeakingFrame())
        await feed(observer, BotStoppedSpeakingFrame())

    summary = observer.summary()
    assert summary["turns_measured"] == 3
    # Each TTFA is generation + the 0.2s VAD confirmation delay.
    assert summary["ttfa_median_ms"] == pytest.approx(700.0)
    assert summary["ttfa_max_ms"] == pytest.approx(3200.0)


async def test_barge_in_is_flagged(clock):
    """A turn that starts while the bot is talking is a candidate interruption."""
    observer = TurnLatencyObserver()

    await feed(observer, BotStartedSpeakingFrame())
    await feed(observer, UserStartedSpeakingFrame())

    assert observer._current.was_interrupted is True


async def test_incomplete_turn_is_not_counted(clock):
    """A turn with no bot reply (session ended mid-turn) must not skew the median."""
    observer = TurnLatencyObserver()

    await feed(observer, UserStartedSpeakingFrame())
    clock.advance(VAD_STOP_SECS)
    await feed(observer, vad_stopped(clock))
    await feed(observer, UserStoppedSpeakingFrame())
    # No BotStartedSpeakingFrame — session ended here.

    assert observer.turns == []
    assert observer.summary()["turns_measured"] == 0


async def test_frames_are_counted_once_per_id(clock):
    """Observers see each frame on every processor hop; duplicates must not count."""
    observer = TurnLatencyObserver()

    start = UserStartedSpeakingFrame()
    await feed(observer, start)
    await feed(observer, start)  # same frame, next hop
    await feed(observer, start)

    assert observer._turn_index == 1


async def test_transcript_records_both_speakers_in_order(clock):
    """Bot text is captured as spoken, aggregated per utterance."""
    observer = TranscriptObserver()

    await feed(observer, BotStartedSpeakingFrame())
    await feed(observer, spoken("Tell me about your background."))
    await feed(observer, BotStoppedSpeakingFrame())
    await feed(observer, TranscriptionFrame(
        text="I led the payments migration.", user_id="cand", timestamp="t0"
    ))

    turns = observer.turns
    assert [t["speaker"] for t in turns] == ["interviewer", "candidate"]
    assert turns[0]["text"] == "Tell me about your background."
    assert turns[1]["text"] == "I led the payments migration."


async def test_audio_before_the_interviewer_speaks_is_not_the_candidate(clock):
    """A live session recorded a bystander in the room saying "Enjoyment" as the
    candidate's opening answer. Attributing a stranger's words to a candidate in
    a hiring record is not acceptable — it is kept, but labelled."""
    observer = TranscriptObserver()

    await feed(observer, TranscriptionFrame(
        text="Enjoyment", user_id="cand", timestamp="t0"
    ))
    await feed(observer, BotStartedSpeakingFrame())
    await feed(observer, spoken("Good evening. I'm an AI interviewer."))
    await feed(observer, BotStoppedSpeakingFrame())
    await feed(observer, TranscriptionFrame(
        text="Hello, nice to meet you.", user_id="cand", timestamp="t1"
    ))

    turns = observer.turns
    assert [t["speaker"] for t in turns] == [
        "pre_interview_audio",
        "interviewer",
        "candidate",
    ]


async def test_transcript_flushes_pending_bot_speech(clock):
    """Text synthesised but not yet closed off must still reach the transcript."""
    observer = TranscriptObserver()

    await feed(observer, BotStartedSpeakingFrame())
    await feed(observer, spoken("Tell me about"))
    await feed(observer, spoken("your last role."))
    # Session ends without BotStoppedSpeakingFrame.

    turns = observer.turns
    assert len(turns) == 1
    assert turns[0]["text"] == "Tell me about your last role."
