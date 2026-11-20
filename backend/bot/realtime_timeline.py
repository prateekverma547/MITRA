"""What was spoken when, on the realtime engine (task 19, F-064 to F-068).

The realtime transcript used to be built from text *arrival*: a candidate's
answer was stamped when its transcription came back (answer A, spoken
9.32-18.64 s, was stamped 27.83 s), Mitra's turns at the end of playback, and an
interrupted reply was stored in full although the candidate heard half of it.

This module only RECORDS. `MitraRealtimeService`'s existing event handlers and
the transcript observer call it with ids and times they already have; every
call is a few dictionary writes in memory and returns at once. Nothing here
awaits, sends, or sits in the reply path (decision 18). Ordering, cutting and
filtering are done later, at save time, by `bot/persistence.build_transcript`.

Times are wall-clock seconds (`time.time()`); persistence converts them to the
interview clock. OpenAI reports speech in milliseconds of *its* audio clock,
which counts only appended audio: it starts at the first append after a
connection and stands still while input is paused (the greeting, F-065). So the
clock is kept per connection as anchors (audio_ms, wall): the first append, and
again after any gap in appending (a pause). A time maps through the last anchor
at or before it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 24 kHz, 16-bit mono: the only input format OpenAI Realtime takes.
BYTES_PER_MS = 48

#: A gap in appending longer than this is a pause, not jitter. Real-time
#: streaming appends every ~20 ms; the investigation measured a 19 ms median
#: residual and a 51 ms maximum (F-065).
PAUSE_GAP_SECONDS = 0.30

#: OpenAI's speech window is padded (F-066, one session): it starts about 0.45 s
#: before the speech and ends about 0.73-0.83 s after it. Used only when no
#: Silero edge is found inside the window. Re-measured on the live run (T19-).
OPENAI_START_PAD_SECONDS = 0.45
OPENAI_END_PAD_SECONDS = 0.78

#: A Silero edge may sit this far outside OpenAI's window and still belong to it.
SILERO_TOLERANCE_SECONDS = 0.25

#: How long the read-back of a cut reply may take before the proportional cut is used.
READBACK_TIMEOUT_SECONDS = 3.0


def proportional_cut(text: str, played_ms: float, total_ms: float) -> str:
    """The fallback for a cut reply: the share of the text that was played, to a
    word boundary. F-068: 1,886 of 3,600 ms gave "...about the market"."""
    if total_ms <= 0 or played_ms >= total_ms:
        return text
    keep = int(len(text) * max(0.0, played_ms) / total_ms)
    head = text[:keep]
    if keep < len(text) and not text[keep].isspace():
        head = head.rsplit(" ", 1)[0] if " " in head else ""
    return head.rstrip(" ,;:")


@dataclass
class AudioClock:
    """OpenAI's audio clock for one connection."""

    generation: int
    anchors: list[tuple[float, float]] = field(default_factory=list)  # (audio_ms, wall)
    appended_ms: float = 0.0

    def on_append(self, ms: float, wall: float) -> None:
        if not self.anchors:
            self.anchors.append((0.0, wall))
        else:
            anchor_ms, anchor_wall = self.anchors[-1]
            expected = anchor_wall + (self.appended_ms - anchor_ms) / 1000
            if wall - expected > PAUSE_GAP_SECONDS:
                self.anchors.append((self.appended_ms, wall))
        self.appended_ms += ms

    def to_wall(self, audio_ms: float) -> float | None:
        if not self.anchors:
            return None
        anchor_ms, anchor_wall = self.anchors[0]
        for ms, wall in self.anchors:
            if ms <= audio_ms:
                anchor_ms, anchor_wall = ms, wall
        return anchor_wall + (audio_ms - anchor_ms) / 1000


@dataclass
class UserItem:
    item_id: str
    generation: int
    speech_start_ms: float | None = None
    speech_end_ms: float | None = None
    transcript: str | None = None


@dataclass
class AssistantItem:
    item_id: str
    generation: int
    previous_item_id: str | None = None
    first_delta_wall: float | None = None
    last_text_wall: float | None = None
    audio_bytes: int = 0
    text_parts: list[str] = field(default_factory=list)
    playback_start_wall: float | None = None
    playback_end_wall: float | None = None
    #: OpenAI's audio_end_ms on the truncate we sent, and when we sent it.
    truncated_ms: int | None = None
    cut_wall: float | None = None
    heard_text: str | None = None
    #: "readback" (the server's truncated item) or "proportional" (the fallback).
    heard_source: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()

    @property
    def duration_ms(self) -> float:
        return self.audio_bytes / BYTES_PER_MS

    @property
    def cut(self) -> bool:
        return self.truncated_ms is not None

    def heard(self) -> str:
        """What the candidate heard: the read-back, else the proportional cut."""
        if not self.cut:
            return self.text
        if self.heard_text is not None:
            return self.heard_text
        return proportional_cut(self.text, self.truncated_ms or 0, self.duration_ms)


class RealtimeTimeline:
    """Ids and times for one realtime session. Recording only; see the module."""

    def __init__(self) -> None:
        self.generation = 0
        self.clocks: dict[int, AudioClock] = {}
        self.users: dict[str, UserItem] = {}
        self.assistants: dict[str, AssistantItem] = {}
        self.pauses: list[list[float | None]] = []
        self._speech_order: list[str] = []
        self._stopped_order: list[str] = []
        self._span_start: float | None = None

    # -- the audio clock ---------------------------------------------------------------

    def new_connection(self, generation: int) -> None:
        """A reconnect resets OpenAI's audio clock (and its item ids)."""
        self.generation = generation
        self.clocks[generation] = AudioClock(generation)

    def on_append(self, nbytes: int, wall: float) -> None:
        clock = self.clocks.setdefault(self.generation, AudioClock(self.generation))
        clock.on_append(nbytes / BYTES_PER_MS, wall)

    def on_pause(self, paused: bool, wall: float) -> None:
        if paused and (not self.pauses or self.pauses[-1][1] is not None):
            self.pauses.append([wall, None])
        elif not paused and self.pauses and self.pauses[-1][1] is None:
            self.pauses[-1][1] = wall

    def audio_ms_to_wall(self, generation: int, audio_ms: float | None) -> float | None:
        clock = self.clocks.get(generation)
        if clock is None or audio_ms is None:
            return None
        return clock.to_wall(audio_ms)

    # -- the candidate -------------------------------------------------------------------

    def _user(self, item_id: str) -> UserItem:
        return self.users.setdefault(item_id, UserItem(item_id, self.generation))

    def speech_started(self, item_id: str, audio_start_ms: float) -> None:
        self._user(item_id).speech_start_ms = audio_start_ms
        self._speech_order.append(item_id)

    def speech_stopped(self, item_id: str, audio_end_ms: float) -> None:
        self._user(item_id).speech_end_ms = audio_end_ms
        self._stopped_order.append(item_id)

    def transcription_completed(self, item_id: str, transcript: str) -> None:
        self._user(item_id).transcript = transcript

    def latest_user_item(self) -> str | None:
        """The candidate item whose speech ended last (else started last)."""
        if self._stopped_order:
            return self._stopped_order[-1]
        return self._speech_order[-1] if self._speech_order else None

    def user_window_wall(self, item_id: str) -> tuple[float | None, float | None]:
        item = self.users.get(item_id)
        if item is None:
            return None, None
        return (
            self.audio_ms_to_wall(item.generation, item.speech_start_ms),
            self.audio_ms_to_wall(item.generation, item.speech_end_ms),
        )

    # -- Mitra -----------------------------------------------------------------------------

    def _assistant(self, item_id: str) -> AssistantItem:
        return self.assistants.setdefault(item_id, AssistantItem(item_id, self.generation))

    def assistant_added(self, item_id: str, previous_item_id: str | None) -> None:
        self._assistant(item_id).previous_item_id = previous_item_id

    def assistant_audio(self, item_id: str, nbytes: int, wall: float) -> None:
        item = self._assistant(item_id)
        if item.first_delta_wall is None:
            item.first_delta_wall = wall
        item.audio_bytes += nbytes

    def assistant_text(self, item_id: str, delta: str, wall: float) -> None:
        item = self._assistant(item_id)
        item.text_parts.append(delta)
        item.last_text_wall = wall

    def current_assistant_item(self) -> str | None:
        """The reply whose audio arrived most recently."""
        playing = [a for a in self.assistants.values() if a.first_delta_wall is not None]
        return max(playing, key=lambda a: a.first_delta_wall).item_id if playing else None

    def playback_started(self, wall: float) -> None:
        self._span_start = wall

    def playback_stopped(self, wall: float) -> None:
        """Assign the span to the replies whose audio arrived before it ended.

        One span can hold several items (a response with more than one output
        item plays them back to back); each starts when the one before ended.
        """
        start = self._span_start if self._span_start is not None else wall
        self._span_start = None
        cursor = start
        for item in self.assistants.values():
            if item.playback_start_wall is not None or item.first_delta_wall is None:
                continue
            if item.first_delta_wall > wall or not item.audio_bytes:
                continue
            item.playback_start_wall = cursor
            natural_end = cursor + item.duration_ms / 1000
            end = min(natural_end, wall)
            if item.cut_wall is not None:
                end = min(end, max(cursor, item.cut_wall))
            item.playback_end_wall = end
            cursor = natural_end

    def truncated(self, item_id: str, audio_end_ms: int, wall: float) -> None:
        item = self._assistant(item_id)
        item.truncated_ms = audio_end_ms
        item.cut_wall = wall

    def heard(self, item_id: str, text: str, source: str) -> None:
        item = self._assistant(item_id)
        item.heard_text = text
        item.heard_source = source

    # -- the record ------------------------------------------------------------------------

    def summary(self, clock_zero: float) -> dict:
        """For session_metrics: the clocks, pauses and cuts, on the interview clock."""

        def s(wall: float | None) -> float | None:
            return None if wall is None else round(wall - clock_zero, 3)

        windows = []
        for item in self.users.values():
            start = self.audio_ms_to_wall(item.generation, item.speech_start_ms)
            end = self.audio_ms_to_wall(item.generation, item.speech_end_ms)
            windows.append({"item_id": item.item_id, "openai_window_s": [s(start), s(end)]})
        return {
            # Wall time of interview second 0, so a check on the same machine can
            # put its own clock beside these times.
            "clock_zero_wall": round(clock_zero, 3),
            # OpenAI's raw (padded) speech window per candidate item, for
            # re-measuring the padding against Silero (F-066).
            "user_windows": windows,
            "audio_clocks": [
                {"generation": c.generation, "origin_s": s(c.anchors[0][1]) if c.anchors else None,
                 "anchors": [[round(ms), s(w)] for ms, w in c.anchors],
                 "appended_ms": round(c.appended_ms)}
                for c in self.clocks.values()
            ],
            "input_pauses_s": [[s(a), s(b)] for a, b in self.pauses],
            "cuts": [
                {"item_id": a.item_id, "truncated_ms": a.truncated_ms, "cut_at_s": s(a.cut_wall),
                 "audio_ms": round(a.duration_ms), "heard_source": a.heard_source or "proportional",
                 "generated": a.text, "heard": a.heard()}
                for a in self.assistants.values() if a.cut
            ],
        }
