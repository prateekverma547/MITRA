"""Session instrumentation: turn latency, pause tolerance, and transcript capture.

Two observers, both non-intrusive (they watch frames rather than sitting in the
pipeline).

`TurnLatencyObserver` exists to answer the Milestone 1 DoD questions with
numbers instead of impressions. The number that matters is **time-to-first-audio
measured from the true end of candidate speech** — not from when our turn
detector made up its mind. Those are different by hundreds of milliseconds, and
optimising the wrong one leads you to tune the wrong knob.

We can recover the true end of speech exactly: `VADUserStoppedSpeakingFrame`
carries both the wall-clock instant VAD reached its verdict and the `stop_secs`
of silence it required to get there, so:

    true_end_of_speech = frame.timestamp - frame.stop_secs

The same trick on `VADUserStartedSpeakingFrame` recovers the true resumption of
speech, which lets us measure **tolerated pauses**: any silence gap the bot sat
through without seizing the turn. That is the direct, per-turn evidence for
"does not interrupt a 3-second thinking pause".
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TurnMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed


class _DedupObserver(BaseObserver):
    """Observers see each frame on every processor hop; count each one once."""

    def __init__(self, max_frames: int = 2000, **kwargs):
        super().__init__(**kwargs)
        self._seen: set[int] = set()
        self._order: list[int] = []
        self._max_frames = max_frames

    def _is_new(self, frame) -> bool:
        if frame.id in self._seen:
            return False
        self._seen.add(frame.id)
        self._order.append(frame.id)
        if len(self._order) > self._max_frames:
            self._seen.discard(self._order.pop(0))
        return True


@dataclass
class TurnRecord:
    """Latency and pause measurements for one candidate turn."""

    index: int
    #: Silence gaps (seconds) the bot sat through without taking the turn.
    tolerated_pauses: list[float] = field(default_factory=list)
    #: Wall-clock instant the candidate actually stopped making sound.
    speech_end: float | None = None
    #: Instant our turn detector finalised the turn.
    turn_finalized: float | None = None
    #: Instant the first byte of bot audio hit the transport.
    first_audio: float | None = None
    #: Smart-turn model verdict that ended the turn, if it was the model.
    model_probability: float | None = None
    was_interrupted: bool = False
    #: Seconds from true end of speech until the STT transcript for that
    #: segment arrived. This is the quantity Pipecat's `ttfs_p99_latency`
    #: is supposed to describe; measuring ours lets us set it from evidence
    #: instead of using the generic built-in default.
    stt_lag_s: list[float] = field(default_factory=list)

    @property
    def endpointing_ms(self) -> float | None:
        """How long we took to *decide* the candidate had finished."""
        if self.speech_end is None or self.turn_finalized is None:
            return None
        return (self.turn_finalized - self.speech_end) * 1000

    @property
    def generation_ms(self) -> float | None:
        """How long the STT->LLM->TTS chain took once the turn was ours."""
        if self.turn_finalized is None or self.first_audio is None:
            return None
        return (self.first_audio - self.turn_finalized) * 1000

    @property
    def ttfa_ms(self) -> float | None:
        """The DoD metric: true end of candidate speech -> first bot audio."""
        if self.speech_end is None or self.first_audio is None:
            return None
        return (self.first_audio - self.speech_end) * 1000

    def to_dict(self) -> dict:
        return {
            "turn": self.index,
            "ttfa_ms": _round(self.ttfa_ms),
            "endpointing_ms": _round(self.endpointing_ms),
            "generation_ms": _round(self.generation_ms),
            "tolerated_pauses_s": [round(p, 2) for p in self.tolerated_pauses],
            "model_probability": _round(self.model_probability, 3),
            "was_interrupted": self.was_interrupted,
            "stt_lag_s": [round(s, 2) for s in self.stt_lag_s],
        }


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


class TurnLatencyObserver(_DedupObserver):
    """Measures per-turn responsiveness and pause tolerance."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._turns: list[TurnRecord] = []
        self._current: TurnRecord | None = None
        self._turn_index = 0
        self._bot_speaking = False
        # Instant the candidate fell silent, while we are still deciding
        # whether that silence is a pause or the end of the turn.
        self._pending_silence_start: float | None = None
        # True end of speech for the segment whose transcript we are waiting on.
        self._awaiting_transcript_since: float | None = None

    @property
    def turns(self) -> list[TurnRecord]:
        return self._turns

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if not self._is_new(frame):
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            self._turn_index += 1
            self._current = TurnRecord(index=self._turn_index)
            self._pending_silence_start = None
            if self._bot_speaking:
                self._current.was_interrupted = True

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            # Candidate fell silent. This may or may not end the turn — that is
            # smart-turn's call. Record when the silence truly began.
            self._pending_silence_start = frame.timestamp - frame.stop_secs
            self._awaiting_transcript_since = self._pending_silence_start
            if self._current:
                self._current.speech_end = self._pending_silence_start

        elif isinstance(frame, VADUserStartedSpeakingFrame):
            # Speech resumed. If we were sitting in a silence, the bot just
            # tolerated a pause instead of barging in — this is the DoD case.
            if self._pending_silence_start is not None and self._current:
                resumed_at = frame.timestamp - frame.start_secs
                pause = resumed_at - self._pending_silence_start
                if pause > 0:
                    self._current.tolerated_pauses.append(pause)
                    logger.info(
                        f"[turn {self._current.index}] tolerated a {pause:.2f}s "
                        f"pause without interrupting"
                    )
                self._pending_silence_start = None

        elif isinstance(frame, UserStoppedSpeakingFrame):
            # The turn detector has committed: the candidate is done.
            self._pending_silence_start = None
            if self._current:
                self._current.turn_finalized = time.time()

        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            if self._current and self._current.first_audio is None:
                self._current.first_audio = time.time()
                self._finish_turn()

        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        elif isinstance(frame, TranscriptionFrame):
            # How long the STT took to return text for the segment that just
            # went silent. Drives the `ttfs_p99_latency` setting in services/stt.py.
            if self._awaiting_transcript_since is not None and self._current:
                lag = time.time() - self._awaiting_transcript_since
                if lag > 0:
                    self._current.stt_lag_s.append(lag)
                self._awaiting_transcript_since = None

        elif isinstance(frame, MetricsFrame):
            for item in frame.data:
                if isinstance(item, TurnMetricsData) and self._current:
                    self._current.model_probability = item.probability

    def _finish_turn(self):
        if not self._current:
            return
        record = self._current
        self._turns.append(record)
        if record.ttfa_ms is not None:
            logger.info(
                f"[turn {record.index}] ttfa={record.ttfa_ms:.0f}ms "
                f"(endpointing={record.endpointing_ms:.0f}ms + "
                f"generation={record.generation_ms:.0f}ms)"
            )
        self._current = None

    def summary(self) -> dict:
        """Aggregate the DoD numbers."""
        ttfas = sorted(t.ttfa_ms for t in self._turns if t.ttfa_ms is not None)
        endpointings = [t.endpointing_ms for t in self._turns if t.endpointing_ms is not None]
        pauses = [p for t in self._turns for p in t.tolerated_pauses]
        stt_lags = sorted(s for t in self._turns for s in t.stt_lag_s)
        return {
            "turns_measured": len(ttfas),
            "ttfa_median_ms": _round(_median(ttfas)),
            "ttfa_p90_ms": _round(_percentile(ttfas, 0.9)),
            "ttfa_max_ms": _round(max(ttfas)) if ttfas else None,
            "endpointing_median_ms": _round(_median(sorted(endpointings))),
            # Evidence for tuning ttfs_p99_latency in services/stt.py.
            "stt_lag_median_s": _round(_median(stt_lags), 2),
            "stt_lag_p90_s": _round(_percentile(stt_lags, 0.9), 2),
            "stt_lag_max_s": _round(max(stt_lags), 2) if stt_lags else None,
            "stt_samples": len(stt_lags),
            "tolerated_pauses_s": [round(p, 2) for p in pauses],
            "longest_tolerated_pause_s": _round(max(pauses), 2) if pauses else None,
            "interruptions_by_candidate": sum(1 for t in self._turns if t.was_interrupted),
        }


def _median(sorted_values: list[float]) -> float | None:
    if not sorted_values:
        return None
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    index = min(int(len(sorted_values) * q), len(sorted_values) - 1)
    return sorted_values[index]


class TranscriptObserver(_DedupObserver):
    """Collects the session transcript as it happens.

    The transcript is the auditable ground truth of the interview (CLAUDE.md),
    so we build it ourselves rather than relying on a vendor's copy.

    Candidate turns come from `TranscriptionFrame` (our STT output). Bot turns
    come from `TTSTextFrame` — the text actually handed to the synthesiser,
    which is what the candidate genuinely heard, rather than what the LLM
    generated. Those differ whenever the bot is interrupted mid-sentence.

    Milestone 3 replaces this with the `Transcript` contract persisted to
    Postgres; the capture points stay the same.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._turns: list[dict] = []
        self._started_at = time.time()
        self._bot_buffer: list[str] = []
        #: The interview begins when the interviewer speaks. Audio captured
        #: before that is room noise — a live session recorded a bystander
        #: saying "Enjoyment" as the candidate's opening answer. Attributing a
        #: stranger's words to a candidate in an auditable hiring record is not
        #: acceptable, so it is labelled rather than silently mixed in.
        self._interviewer_has_spoken = False

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if not self._is_new(frame):
            return

        if isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                speaker = "candidate" if self._interviewer_has_spoken else "pre_interview_audio"
                self._append(speaker, frame.text.strip())

        elif isinstance(frame, TTSTextFrame):
            if frame.text.strip():
                self._bot_buffer.append(frame.text.strip())

        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._flush_bot()

    def _flush_bot(self):
        if self._bot_buffer:
            self._append("interviewer", " ".join(self._bot_buffer))
            self._bot_buffer = []
            self._interviewer_has_spoken = True

    def _append(self, speaker: str, text: str):
        self._turns.append(
            {
                "speaker": speaker,
                "text": text,
                "at_seconds": round(time.time() - self._started_at, 2),
            }
        )

    @property
    def turns(self) -> list[dict]:
        self._flush_bot()
        return self._turns

    def render(self) -> str:
        lines = []
        for turn in self.turns:
            stamp = f"[{turn['at_seconds']:>7.2f}s]"
            speaker = f"{turn['speaker']:>11}"
            lines.append(f"{stamp} {speaker}: {turn['text']}")
        return "\n".join(lines)


def write_session_artifacts(
    *,
    directory: Path,
    session_id: str,
    transcript: TranscriptObserver,
    latency: TurnLatencyObserver,
    blueprint_id: str | None = None,
    role_title: str | None = None,
    silence_events: list[dict] | None = None,
    brain_events: list[dict] | None = None,
    section_outcomes: list[dict] | None = None,
) -> Path:
    """Persist transcript + metrics for the session and log a readable summary."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"

    payload = {
        "session_id": session_id,
        "blueprint_id": blueprint_id,
        "role_title": role_title,
        "transcript": transcript.turns,
        "latency_summary": latency.summary(),
        "silence_events": silence_events or [],
        # Section transitions and judgement results. Without these a long
        # session is undiagnosable: you cannot tell a bad interview caused by
        # bad questions from one caused by a bad transition or a silent judge.
        "brain_events": brain_events or [],
        "section_outcomes": section_outcomes or [],
        "turns": [t.to_dict() for t in latency.turns],
    }
    path.write_text(json.dumps(payload, indent=2))

    rendered = transcript.render()
    logger.info(f"\n===== TRANSCRIPT =====\n{rendered or '(no speech captured)'}\n")
    logger.info(f"===== LATENCY =====\n{json.dumps(latency.summary(), indent=2)}\n")
    if silence_events:
        logger.info(f"===== SILENCE =====\n{json.dumps(silence_events, indent=2)}\n")
    logger.info(f"Session artifacts written to {path}")
    return path
