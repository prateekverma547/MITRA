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
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData, TurnMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed

from bot.turn_taking import SMART_TURN_STOP_SECS


#: A turn that ends and is resumed within this many seconds did not end because
#: the candidate finished. Set above the tolerated-pause range so a genuine
#: think-then-continue is not counted, and well below the gap a real reply
#: would leave.
FALSE_TURN_END_SECONDS = 2.0


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
    #: Input tokens the composition call actually sent. Recorded because a
    #: rising prompt and a contended event loop look identical in a latency
    #: number, and separating them once took a whole session and a lucky
    #: observation about timer precision.
    prompt_tokens: int | None = None
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
            "prompt_tokens": self.prompt_tokens,
        }


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _ms(value: float | None) -> str:
    """A duration for the log line, which must never be the thing that fails."""
    return "n/a" if value is None else f"{value:.0f}ms"


class TurnLatencyObserver(_DedupObserver):
    """Measures per-turn responsiveness and pause tolerance."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._turns: list[TurnRecord] = []
        self._current: TurnRecord | None = None
        self._turn_index = 0
        self._bot_speaking = False
        self._turn_ended_at: float | None = None
        self._false_turn_ends: list[float] = []
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
            # A turn that ended and is resumed almost immediately did not end
            # because the candidate had finished. It ended because smart-turn
            # read a clause boundary as the end of an answer, and the candidate
            # simply carried on. Session `int_0a7ca5d0aca5` did this 74 times in
            # 291 seconds to a speaker talking in short, complete-sounding
            # clauses, which split one answer across many turns.
            #
            # A tolerated pause is the same silence handled correctly. The only
            # difference is whether the turn survived it, so measuring both
            # together is what tells us the endpointing is set wrong rather than
            # the candidate being slow.
            if self._turn_ended_at is not None:
                gap = time.time() - self._turn_ended_at
                if gap <= FALSE_TURN_END_SECONDS:
                    self._false_turn_ends.append(gap)
                    logger.info(
                        f"[turn {self._turn_index}] ended, but the candidate "
                        f"resumed {gap:.2f}s later; the answer was still coming"
                    )
            self._turn_ended_at = None
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
            self._turn_ended_at = time.time()
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
                elif isinstance(item, LLMUsageMetricsData) and self._turns:
                    # Attached to the turn that just closed, not the open one.
                    # A record is finished the moment first audio goes out, and
                    # usage arrives after the model has finished generating, so
                    # by the time this lands `_current` is already None.
                    self._turns[-1].prompt_tokens = item.value.prompt_tokens

    def _finish_turn(self):
        if not self._current:
            return
        record = self._current
        self._turns.append(record)
        if record.ttfa_ms is not None:
            # The parts can be missing when the whole is not. A turn that was never
            # finalised (no transcript arrived, so the turn detector never committed)
            # still has a speech end and a first audio when the bot speaks anyway, for
            # example a silence nudge. Formatting None as a number raised inside the
            # observer and took the latency record down with it (int_3e4f8bb5b499).
            logger.info(
                f"[turn {record.index}] ttfa={record.ttfa_ms:.0f}ms "
                f"(endpointing={_ms(record.endpointing_ms)} + "
                f"generation={_ms(record.generation_ms)})"
            )
        self._current = None

    def summary(self) -> dict:
        """Aggregate the DoD numbers.

        **Time to first audio is bimodal and a median cannot describe it.** A
        turn either ends because the smart-turn model was confident, at roughly
        1.7s, or because it was unsure and the four-second fallback expired, at
        roughly 5.4s. There is almost nothing in between: measured across five
        real interviews the two modes were 1570-1815ms and 4808-5469ms, and both
        were *identical* in every one of them.

        Only the mix moves. It went from 36-47% fallback to 64% when a different
        candidate sat down, and because that crosses one half, the median stepped
        from one mode to the other in a single jump: 2.04s to 5.27s. The panel
        reported a 2.6x regression in a system that had not changed, and
        diagnosing it took a session.

        So the median stays, because it is the DoD number and it is comparable
        to every figure already recorded, but it is no longer reported alone. The
        mean moves with the mix instead of stepping across it, and the two mode
        medians say whether a mode itself moved or only how often each was hit.
        Those are different facts: the first is the system getting slower, the
        second is this candidate being waited for more.
        """
        ttfas = sorted(t.ttfa_ms for t in self._turns if t.ttfa_ms is not None)
        endpointings = [t.endpointing_ms for t in self._turns if t.endpointing_ms is not None]
        pauses = [p for t in self._turns for p in t.tolerated_pauses]
        stt_lags = sorted(s for t in self._turns for s in t.stt_lag_s)
        prompt_tokens = sorted(
            t.prompt_tokens for t in self._turns if t.prompt_tokens is not None
        )

        return {
            "turns_measured": len(ttfas),
            "ttfa_median_ms": _round(_median(ttfas)),
            "ttfa_p90_ms": _round(_percentile(ttfas, 0.9)),
            "ttfa_max_ms": _round(max(ttfas)) if ttfas else None,
            # The mean, and the two modes kept apart. If a mode itself moves the
            # system got slower; if only the share moves, this candidate was
            # waited for more. Those need different answers.
            **bimodal_latency([t.to_dict() for t in self._turns]),
            # Evidence for telling a growing prompt apart from a busy event
            # loop, which look the same in a latency number.
            "prompt_tokens_median": _round(_median(prompt_tokens)),
            "prompt_tokens_max": max(prompt_tokens) if prompt_tokens else None,
            "endpointing_median_ms": _round(_median(sorted(endpointings))),
            # Evidence for tuning ttfs_p99_latency in services/stt.py.
            "stt_lag_median_s": _round(_median(stt_lags), 2),
            "stt_lag_p90_s": _round(_percentile(stt_lags, 0.9), 2),
            "stt_lag_max_s": _round(max(stt_lags), 2) if stt_lags else None,
            "stt_samples": len(stt_lags),
            "tolerated_pauses_s": [round(p, 2) for p in pauses],
            "longest_tolerated_pause_s": _round(max(pauses), 2) if pauses else None,
            "interruptions_by_candidate": sum(1 for t in self._turns if t.was_interrupted),
            # Turns that ended while the answer was still coming. High here means
            # endpointing, not the candidate: see the note in on_push_frame.
            # What smart-turn actually said, so TURN_END_CONFIDENCE can be set
            # from a real session rather than from taste.
            "turn_end_probabilities": sorted(
                round(t.model_probability, 2)
                for t in self._turns
                if t.model_probability is not None
            ),
            "false_turn_ends": len(self._false_turn_ends),
            "turns_started": self._turn_index,
            "false_turn_end_rate": (
                _round(len(self._false_turn_ends) / self._turn_index, 2)
                if self._turn_index
                else None
            ),
        }


#: Endpointing at or above this counts as having waited out the fallback rather
#: than having been released by the turn model. Derived from
#: `SMART_TURN_STOP_SECS`, not chosen: the fallback fires within 5ms of that
#: constant, and the fast mode sits three and a half seconds below it.
_FALLBACK_ENDPOINTING_MS = SMART_TURN_STOP_SECS * 1000 * 0.9


def bimodal_latency(turns: list[dict]) -> dict:
    """The two modes of time to first audio, from stored per-turn samples.

    A pure function over the dicts that `TurnRecord.to_dict` produces, so it can
    be computed when a session ends and again when an old one is read back.
    Interviews recorded before these fields existed still have their per-turn
    samples, and those are all this needs.
    """
    ttfas = sorted(t["ttfa_ms"] for t in turns if t.get("ttfa_ms") is not None)
    if not ttfas:
        return {
            "ttfa_mean_ms": None,
            "ttfa_median_ready_ms": None,
            "ttfa_median_waiting_ms": None,
            "turns_waiting": 0,
            "fallback_share": None,
        }
    waiting = sorted(
        t["ttfa_ms"] for t in turns
        if t.get("ttfa_ms") is not None
        and (t.get("endpointing_ms") or 0) >= _FALLBACK_ENDPOINTING_MS
    )
    ready = sorted(
        t["ttfa_ms"] for t in turns
        if t.get("ttfa_ms") is not None
        and (t.get("endpointing_ms") or 0) < _FALLBACK_ENDPOINTING_MS
    )
    return {
        "ttfa_mean_ms": _round(sum(ttfas) / len(ttfas)),
        "ttfa_median_ready_ms": _round(_median(ready)),
        "ttfa_median_waiting_ms": _round(_median(waiting)),
        "turns_waiting": len(waiting),
        "fallback_share": _round(len(waiting) / len(ttfas), 2),
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

    def __init__(self, section_id_now=None, **kwargs):
        """`section_id_now` is asked which section is live, at append time.

        **A callable rather than the brain itself.** The observer would
        otherwise have to import the brain to read one attribute, and the two
        have no other business with each other: this file is full of Pipecat
        frames, which `bot/brain/` is not allowed to know about in either
        direction. A zero-argument function is the whole of the contract, so a
        test can pass `lambda: "alpha"` and a script can pass nothing.

        None means no labelling, which is what every caller did before and what
        the scripts still do.
        """
        super().__init__(**kwargs)
        self._section_id_now = section_id_now
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
        # **Asked now, not reconstructed later.** The stored transcript is built
        # from these frames rather than from the brain's own turn list, so
        # `section_id` was absent on every persisted turn and a query grouping
        # stored turns by section returned one empty group. The brain knows
        # which section is live at the moment a frame arrives; nothing else can
        # work it out afterwards, because the timestamps here and the section
        # boundaries there are recorded against different clocks.
        section_id = None
        if self._section_id_now is not None:
            try:
                section_id = self._section_id_now()
            except Exception as exc:  # noqa: BLE001
                # A label is worth less than a transcript. This runs inside a
                # frame handler on the live pipeline.
                logger.warning(f"could not label a transcript turn with its section: {exc}")
        self._turns.append(
            {
                "speaker": speaker,
                "text": text,
                "at_seconds": round(time.time() - self._started_at, 2),
                "section_id": section_id,
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

    # The words themselves are candidate speech. They go to the database and the session
    # file, never to the log: log output leaves the container and is kept for weeks.
    logger.info(f"[{session_id}] transcript saved: {len(transcript.turns)} turn(s)")
    logger.info(f"===== LATENCY =====\n{json.dumps(latency.summary(), indent=2)}\n")
    if silence_events:
        logger.info(f"===== SILENCE =====\n{json.dumps(silence_events, indent=2)}\n")
    logger.info(f"Session artifacts written to {path}")
    return path


class UsageObserver(_DedupObserver):
    """Counts what the pipeline was billed for, from the usage events it emits.

    Nothing here estimates. Live LLM tokens and TTS characters arrive as metrics the
    services publish when usage metrics are on. Candidate speech is the span between
    voice-activity start and stop: a diagnostic, because the STT bill is for the open
    stream, not for speech (see shared/usage.py).
    """

    def __init__(self, clock=time.monotonic, **kwargs):
        super().__init__(**kwargs)
        self._clock = clock
        self.llm_calls = 0
        self.llm_prompt_tokens = 0
        self.llm_completion_tokens = 0
        self.tts_characters = 0
        self.candidate_speech_seconds = 0.0
        self._speech_started_at: float | None = None

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if not self._is_new(frame):
            return

        if isinstance(frame, MetricsFrame):
            for item in frame.data:
                if isinstance(item, LLMUsageMetricsData):
                    self.llm_calls += 1
                    self.llm_prompt_tokens += item.value.prompt_tokens or 0
                    self.llm_completion_tokens += item.value.completion_tokens or 0
                elif isinstance(item, TTSUsageMetricsData):
                    self.tts_characters += int(item.value or 0)
        elif isinstance(frame, UserStartedSpeakingFrame):
            if self._speech_started_at is None:
                self._speech_started_at = self._clock()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            if self._speech_started_at is not None:
                self.candidate_speech_seconds += self._clock() - self._speech_started_at
                self._speech_started_at = None
