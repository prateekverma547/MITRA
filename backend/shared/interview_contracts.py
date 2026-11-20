"""Track I's new shared contracts: TwoLinks, IntegrityReport, InterviewEvent.

A note for the other engineer. These are the agreed shapes Track I hands to
Track H, or to its own screens, written now so both sides can build against the
JSON fixtures in backend/shared/fixtures/ (docs/DEV_PLAN.md section 5). They live
in their own file so Track H's additions to backend/shared/ never collide with
ours. contracts.py is imported from here, never changed by it.

| Contract        | Produced by (Track I)                    | Consumed by                                  |
| --------------- | ---------------------------------------- | -------------------------------------------- |
| TwoLinks        | book_technical (phase 2)                 | Track H's HR screen, "Book technical"        |
| IntegrityReport | proctoring, browser + after the call     | <InterviewReport> (I), embedded by H's results page |
| InterviewEvent  | bot, coordinator, browser, auditor, server | Track I's live view, Check Intelligence, analytics |
| CleanedTranscript | the bot after the call (feedback/clean.py) | the report; interviews.cleaned_transcript |
| WatchRefusal, InterviewHeader, RecordingInfo, CheckIntelReport | live/routes.py | Track I's console |

Rules for every contract here:
- Additive only. A new OPTIONAL field is a normal PR. Renaming or removing a
  field bumps schema_version, and both engineers must agree.
- Every top-level type has schema_version.
- Every claim about a person carries its evidence (integrity events carry their
  time, and optional thumbnail or clip references).
- Nothing here ever holds a credential, a token or a password. URLs are checked:
  no user:password@, no token- or password-like query parameter. Evidence is a
  storage key, never a signed URL (signed URLs carry tokens).
- IntegrityReport is evidence for a person, never a score: no field is a score,
  rating or similar (a test enforces it), and wording describes behaviour,
  never intent or emotion (decision 12).

How to add a field: give it a default (None, an empty list, or a value that
keeps old JSON valid), update the fixture, run tests/test_interview_contracts.py,
and regenerate the TypeScript types (frontend/ui's generator). For a new
InterviewEvent kind: add the EventKind value, a payload model with
`kind: Literal[...]`, add it to Payload and to ALLOWED_SOURCES, and add an
example to fixtures/interview_events.json.

Times: every *_seconds field is on the interview clock, the same clock as
TranscriptTurn.at_seconds (contracts.py), so events, transcript and recording
line up.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import PurePosixPath
from enum import StrEnum
from typing import Annotated, Literal, Union
from urllib.parse import parse_qsl, urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.contracts import (
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
    REJOIN_COUNTS_FROM_SECONDS,
    REJOIN_LIMIT,
    SCHEMA_VERSION,
    DropDetection,
    HRFindingsRefinement,
    Speaker,
)

# -- shared checks -------------------------------------------------------------------

#: Query parameter names that would put a credential in a URL.
_CREDENTIAL_PARAMS = {"password", "pass", "pwd", "passcode", "token", "access_token", "t",
                      "secret", "key", "api_key", "auth", "signature", "sig"}

#: Words that describe intent or emotion rather than behaviour (decision 12).
#: Rejected in integrity wording, so a report can never accuse.
_NOT_NEUTRAL = {"cheat", "cheating", "cheated", "suspicious", "suspicion", "dishonest", "lying",
                "lied", "nervous", "anxious", "anxiety", "guilty", "deceptive", "fraud",
                "fraudulent", "evasive", "malicious", "intent", "intended"}


def _no_credentials_in_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ValueError("must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise ValueError("must not carry a user name or password (user:pass@)")
    bad = {k for k, _ in parse_qsl(parts.query, keep_blank_values=True)
           if k.lower() in _CREDENTIAL_PARAMS}
    if bad:
        raise ValueError(f"must not carry credentials in the query ({', '.join(sorted(bad))})")
    return url


def _neutral(text: str | None) -> str | None:
    if text:
        words = {w.strip(".,;:!?'\"()").lower() for w in text.split()}
        found = words & _NOT_NEUTRAL
        if found:
            raise ValueError(
                f"describe behaviour, not intent or emotion (decision 12): {', '.join(sorted(found))}")
    return text


# -- 1. TwoLinks ------------------------------------------------------------------------


class TwoLinks(BaseModel):
    """What book_technical returns to Track H's HR screen.

    Carries the meeting ID (an identifier the candidate types, not a secret)
    but NOT the password. The password is returned once, to the admin who
    booked, by the booking response that already exists (app/interviews.py
    InterviewCreated.password) and is never put in a shared contract or a URL.
    The live-view link holds no token: watchers sign in through the console.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    meeting_id: str = Field(description="Spoken-friendly ID the candidate types, e.g. 428-193-756.")
    candidate_join_url: str = Field(description="Where the candidate joins. No password in it.")
    live_view_url: str = Field(description="Where HR and the BU head watch. Behind the console sign-in.")
    duration_minutes: int = Field(ge=MIN_DURATION_MINUTES, le=MAX_DURATION_MINUTES)
    expires_at: AwareDatetime | None = Field(
        default=None, description="The room's existing 24-hour lifetime. Timezone-aware.")

    @field_validator("candidate_join_url", "live_view_url")
    @classmethod
    def _urls(cls, v):
        return _no_credentials_in_url(v)


# -- 2. IntegrityReport -------------------------------------------------------------------


class IntegrityKind(StrEnum):
    """Behaviour that was measured. Never intent, never emotion."""

    DISTANCE_OUT_OF_BAND = "distance_out_of_band"
    LOOKING_AWAY = "looking_away"
    FACE_MISSING = "face_missing"
    SECOND_FACE = "second_face"
    TAB_HIDDEN = "tab_hidden"
    TAB_UNFOCUSED = "tab_unfocused"
    SHARE_STOPPED = "share_stopped"
    SHARE_NOT_WHOLE_SCREEN = "share_not_whole_screen"
    SECOND_MONITOR = "second_monitor"
    LEFT_FULL_SCREEN = "left_full_screen"
    PASTE = "paste"
    # After the call, from the recording.
    SECOND_VOICE = "second_voice"
    SCREEN_CONTENT_FLAGGED = "screen_content_flagged"


class IntegritySource(StrEnum):
    BROWSER = "browser"
    AFTER_CALL = "after_call"


#: Only found by analysing the recording after the call.
AFTER_CALL_ONLY = {IntegrityKind.SECOND_VOICE, IntegrityKind.SCREEN_CONTENT_FLAGGED}
#: Re-checked after the call as well as seen live (DEV_PLAN.md section 7).
EITHER_SOURCE = {IntegrityKind.LOOKING_AWAY}


class ReminderState(StrEnum):
    NUDGED = "nudged"
    RECORDED_ONLY = "recorded_only"
    #: A technical prompt about the set-up, shown every time it applies ("Share
    #: your whole screen to carry on"), not an integrity reminder: uncapped, no
    #: reminder_index, never counted with reminders. Only for TECHNICAL_PROMPT_KINDS.
    TECHNICAL_PROMPT = "technical_prompt"


#: The kinds whose prompt is technical: the screen share is a precondition of the
#: interview, so asking for it again is about the set-up, not about the person.
#: Track H, 2026-11-20 (product owner): a face out of view too. The candidate sees a
#: warning for as long as their face is not visible, every time, uncapped, like the
#: share prompt; each time is still recorded for the report.
TECHNICAL_PROMPT_KINDS = {IntegrityKind.SHARE_STOPPED, IntegrityKind.SHARE_NOT_WHOLE_SCREEN,
                          IntegrityKind.FACE_MISSING}


class EvidenceTrack(StrEnum):
    CAMERA = "camera"
    SCREEN = "screen"
    AUDIO = "audio"


class IntegrityEvidence(BaseModel):
    """Where a person can look. Storage keys only: never a signed URL."""

    track: EvidenceTrack
    thumbnail_key: str | None = Field(default=None, description="Storage key of a still, not a URL.")
    clip_start_seconds: float | None = Field(default=None, ge=0)
    clip_end_seconds: float | None = Field(default=None, ge=0)

    @field_validator("thumbnail_key")
    @classmethod
    def _key_not_url(cls, v):
        if v is not None and "://" in v:
            raise ValueError("a storage key, not a URL (signed URLs carry tokens)")
        return v

    @model_validator(mode="after")
    def _clip_order(self):
        if (self.clip_start_seconds is not None and self.clip_end_seconds is not None
                and self.clip_end_seconds < self.clip_start_seconds):
            raise ValueError("clip_end_seconds is before clip_start_seconds")
        return self


class IntegrityEvent(BaseModel):
    event_id: str
    kind: IntegrityKind
    source: IntegritySource
    start_seconds: float = Field(ge=0, description="Interview clock, same as transcript times.")
    end_seconds: float | None = Field(default=None, ge=0)
    repeats: int = Field(default=1, ge=1, description="How many times it happened in this event.")
    reminder: ReminderState
    reminder_index: int | None = Field(
        default=None, ge=1, le=3, description="1 to 3 when nudged; none when recorded only.")
    detail: str = Field(default="", max_length=120, description='Short and neutral, e.g. "about 34 cm".')
    evidence: list[IntegrityEvidence] = Field(default_factory=list)
    #: Track H integration (Session D, D-92), all optional and additive:
    #: how sure the room's detector was, 0 to 1; never combined into anything.
    confidence: float | None = Field(default=None, ge=0, le=1)
    #: Set by the SERVER when it stores the event (whatever a client sends is replaced):
    #: when it arrived, and the interview clock at that moment, so a reviewer can see how
    #: far the room's own times (start_seconds, end_seconds) sat from the server's.
    received_at: AwareDatetime | None = None
    received_interview_seconds: float | None = Field(default=None, ge=0)

    @field_validator("detail")
    @classmethod
    def _detail_neutral(cls, v):
        return _neutral(v)

    @model_validator(mode="after")
    def _consistent(self):
        if self.reminder is ReminderState.NUDGED and self.reminder_index is None:
            raise ValueError("a nudged event needs reminder_index (1 to 3)")
        if self.reminder is not ReminderState.NUDGED and self.reminder_index is not None:
            raise ValueError("reminder_index is only for nudged events")
        if self.reminder is ReminderState.TECHNICAL_PROMPT and self.kind not in TECHNICAL_PROMPT_KINDS:
            raise ValueError(f"{self.kind} is not a technical prompt; only the screen-share kinds are")
        if self.end_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds is before start_seconds")
        if self.kind in AFTER_CALL_ONLY and self.source is not IntegritySource.AFTER_CALL:
            raise ValueError(f"{self.kind} is only found after the call")
        if (self.source is IntegritySource.AFTER_CALL
                and self.kind not in AFTER_CALL_ONLY | EITHER_SOURCE):
            raise ValueError(f"{self.kind} is only measured in the browser")
        if self.source is IntegritySource.AFTER_CALL and self.reminder is not ReminderState.RECORDED_ONLY:
            raise ValueError("nothing found after the call was shown to the candidate")
        return self


class IntegrityAccommodation(BaseModel):
    gaze_reminders_off: bool = Field(default=False, description="Switched off at consent.")
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def _note_neutral(cls, v):
        return _neutral(v)


class IntegritySummary(BaseModel):
    events_by_kind: dict[IntegrityKind, int] = Field(default_factory=dict)
    #: Integrity reminders shown (nudged). Technical prompts are never counted here.
    reminders_by_kind: dict[IntegrityKind, int] = Field(default_factory=dict)
    #: Technical prompts shown (the screen-share prompt), counted on their own line.
    #: Reports stored before it existed load with {} (C-63).
    technical_prompts_by_kind: dict[IntegrityKind, int] = Field(default_factory=dict)

    @classmethod
    def from_events(cls, events: list[IntegrityEvent]) -> "IntegritySummary":
        counts: dict[IntegrityKind, int] = {}
        reminders: dict[IntegrityKind, int] = {}
        prompts: dict[IntegrityKind, int] = {}
        for e in events:
            counts[e.kind] = counts.get(e.kind, 0) + 1
            if e.reminder is ReminderState.NUDGED:
                reminders[e.kind] = reminders.get(e.kind, 0) + 1
            elif e.reminder is ReminderState.TECHNICAL_PROMPT:
                prompts[e.kind] = prompts.get(e.kind, 0) + 1
        return cls(events_by_kind=counts, reminders_by_kind=reminders,
                   technical_prompts_by_kind=prompts)


class IntegrityReport(BaseModel):
    """Proctoring evidence for a person to review. Never part of a score."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    generated_at: AwareDatetime
    analysed_after_call: bool = Field(default=False, description="True once the recording was analysed.")
    accommodation: IntegrityAccommodation = Field(default_factory=IntegrityAccommodation)
    events: list[IntegrityEvent] = Field(default_factory=list)
    summary: IntegritySummary = Field(default_factory=IntegritySummary)
    #: Where the after-call checks stand (Session D's runner, D-58). None when
    #: none has run (they are off by default) and on reports from before.
    after_call_status: "AfterCallStatus | None" = None
    #: The second-voice signal behind an after_call second_voice event: ranges,
    #: the method's own confidence, and neutral wording. Evidence for a person,
    #: never a score. None when there is none.
    second_voice: "SecondVoiceSignal | None" = None

    @model_validator(mode="after")
    def _summary_matches_events(self):
        expected = IntegritySummary.from_events(self.events)
        if self.summary != expected:
            raise ValueError("summary does not match events (use IntegritySummary.from_events)")
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError("event_id values must be unique")
        return self


# -- 2b. CandidateAppConfig -----------------------------------------------------------


class CandidateAppConfig(BaseModel):
    """What the candidate app reads before consent (GET /interview/config).

    Public and unauthenticated: it is read before the candidate has typed
    anything, so it carries no secret and nothing about any interview or person.
    `retention_days` is RECORDING_RETENTION_DAYS (contracts.py), the same
    constant the recording sweep deletes by, so the consent notice and the job
    that keeps its promise cannot disagree.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    retention_days: int = Field(ge=1)
    #: Whether this interview will be recorded: the RECORDING_ENABLED switch,
    #: read the same way `begin_recording` reads it before asking Daily to record, so the
    #: consent notice describes what will actually happen. None only in a config
    #: written before the field existed; the app treats that as unknown and
    #: does not let the candidate agree.
    recording_enabled: bool | None = None
    #: Whether the bot saves the candidate's own audio track during the call
    #: (CANDIDATE_AUDIO_CAPTURE, ON unless set to 0/false/no/off; bot/capture.py), kept
    #: for the same retention and used only by the after-call checks. Read the same way
    #: the bot reads it. The consent notice must say so when true (C-171). Additive.
    candidate_audio_capture: bool | None = None
    #: Whether the after-call checks (a second voice in the candidate's own audio) run
    #: on this server now (AFTERCALL_ENABLED, OFF by default; aftercall/settings.py).
    #: The consent notice must say so when true (C-171). Additive.
    after_call_checks: bool | None = None


# -- 3. InterviewEvent ---------------------------------------------------------------------


class EventSource(StrEnum):
    BOT = "bot"
    COORDINATOR = "coordinator"
    BROWSER = "browser"
    AUDITOR = "auditor"
    SERVER = "server"


class EventKind(StrEnum):
    INTERVIEW_STARTED = "interview_started"
    INTERVIEW_ENDED = "interview_ended"
    TRANSCRIPT_TURN = "transcript_turn"
    REPLY_WAIT = "reply_wait"
    AREA_CHANGED = "area_changed"
    TIME_NOTE_SENT = "time_note_sent"
    QUESTION_LABELLED = "question_labelled"
    TOOL_CALLED = "tool_called"
    WITHDRAWAL = "withdrawal"
    SILENCE_PROMPT = "silence_prompt"
    INTEGRITY_SIGNAL = "integrity_signal"
    INTEGRITY_STATUS = "integrity_status"
    PARTICIPANT = "participant"
    SKIP = "skip"
    COST_UPDATE = "cost_update"
    ERROR = "error"
    # Wave 1.
    CANDIDATE_DROPPED = "candidate_dropped"
    CANDIDATE_RETURNED = "candidate_returned"
    REJOIN_LIMIT_REACHED = "rejoin_limit_reached"
    NOTE_ADDED = "note_added"


class EndReason(StrEnum):
    """The values the code records today as ended_because."""

    INTERVIEW_COMPLETE = "interview_complete"
    CANDIDATE_WITHDREW = "candidate_withdrew"
    CANDIDATE_DROPPED = "candidate_dropped"
    ABANDONED_BY_CANDIDATE = "abandoned_by_candidate"
    CANDIDATE_LEFT_THE_CALL = "candidate_left_the_call"
    SESSION_ENDED = "session_ended"
    FAILED_TO_START = "failed_to_start"
    #: The candidate pressed the End button and confirmed (C-A3). Before this
    #: existed the bot sent the End button as candidate_withdrew.
    CANDIDATE_ENDED_BUTTON = "candidate_ended_button"
    #: Wave 1: the candidate dropped and did not come back within the grace window.
    CANDIDATE_DID_NOT_RETURN = "candidate_did_not_return"
    #: Wave 1: a counted drop beyond the rejoin limit; ended with a fixed, kind goodbye.
    REJOIN_LIMIT_REACHED = "rejoin_limit_reached"
    #: The web process restarted and the interview's bot was no longer alive
    #: (app/startup.py). A problem on our side, never the candidate's.
    INTERRUPTED_BY_RESTART = "interrupted_by_restart"


class InterviewStarted(BaseModel):
    kind: Literal["interview_started"] = "interview_started"
    engine: Literal["realtime", "pipeline"]
    booked_minutes: int | None = Field(default=None, ge=1)


class InterviewEnded(BaseModel):
    kind: Literal["interview_ended"] = "interview_ended"
    ended_because: EndReason
    duration_seconds: float = Field(ge=0)
    #: How the ending ran (C-A2), the coordinator's record of it, e.g.
    #: "model_silent_code_goodbye", "model_spoke", "forced_at_hard_stop",
    #: "withdrawal", "candidate_ended_button". None when not known, and on
    #: events stored before this existed.
    ending: str | None = Field(default=None, max_length=64)
    #: What hung up (C-A2), e.g. "bot_stopped_speaking", "goodbye_timeout",
    #: "silent_timer". None when not known, and on older events.
    hang_up_by: str | None = Field(default=None, max_length=64)


class TranscriptTurnEvent(BaseModel):
    kind: Literal["transcript_turn"] = "transcript_turn"
    turn_index: int = Field(ge=0, description="TranscriptTurn.index: the key every turn event uses.")
    speaker: Speaker
    text: str
    audio_start_seconds: float | None = Field(default=None, ge=0)
    audio_end_seconds: float | None = Field(default=None, ge=0)
    cut: bool = Field(default=False, description="Mitra's reply was interrupted; text is what was heard.")
    #: The area the turn was spoken in (C-A1): CompetencyPlan.competency_id, or
    #: "opening" / "closing". None when not known, and on events stored before
    #: this existed (readers fall back to the last area_changed event).
    area_id: str | None = None

    @model_validator(mode="after")
    def _order(self):
        if (self.audio_start_seconds is not None and self.audio_end_seconds is not None
                and self.audio_end_seconds < self.audio_start_seconds):
            raise ValueError("audio_end_seconds is before audio_start_seconds")
        return self


class ReplyWait(BaseModel):
    """The wait before Mitra's reply, split as the latency observer measures it."""

    kind: Literal["reply_wait"] = "reply_wait"
    turn_index: int = Field(ge=0, description="The Mitra turn this wait preceded (TranscriptTurn.index).")
    end_of_speech_detect_seconds: float | None = Field(default=None, ge=0)
    model_to_first_sound_seconds: float | None = Field(default=None, ge=0)
    total_seconds: float | None = Field(default=None, ge=0)
    interrupted: bool = False


class AreaChanged(BaseModel):
    kind: Literal["area_changed"] = "area_changed"
    area_id: str = Field(description='CompetencyPlan.competency_id, or "opening" / "closing".')
    area_name: str
    budget_minutes: float | None = Field(default=None, ge=0)


class TimeNoteSent(BaseModel):
    kind: Literal["time_note_sent"] = "time_note_sent"
    text: str


class QuestionLabel(StrEnum):
    PLAN = "plan"
    FOLLOW_UP = "follow_up"
    OFF_PLAN = "off_plan"
    OPENING = "opening"
    CLOSING = "closing"
    #: The candidate declined a question and Mitra asked a different, easier one
    #: on the same topic (C-A4). question_id is the DECLINED question's id.
    EASIER_VARIANT = "easier_variant"
    #: The candidate did not understand, and Mitra asked the SAME question again,
    #: in other words (C-146 option 1). question_id is the repeated question's id;
    #: no follow_up_number. Never counted toward the follow-up cap.
    CLARIFICATION = "clarification"


class QuestionLabelled(BaseModel):
    """The auditor's reading after the fact, never the realtime model's reasoning."""

    kind: Literal["question_labelled"] = "question_labelled"
    turn_index: int = Field(ge=0)
    label: QuestionLabel
    question_id: str | None = Field(default=None, description='Plan question number, e.g. "Q2.1".')
    #: Round 18 (A-115): no upper bound. The cap of 3 follow-ups limits what Mitra does,
    #: not what is recorded: a 4th or 5th follow-up is recorded as 4 or 5 (it used to be
    #: refused, and lost from the live record). Readers that assumed at most 3 still parse.
    follow_up_number: int | None = Field(default=None, ge=1)
    area_id: str | None = None
    auditors_reading: Literal[True] = True

    @model_validator(mode="after")
    def _shape(self):
        if (self.label in (QuestionLabel.PLAN, QuestionLabel.FOLLOW_UP, QuestionLabel.EASIER_VARIANT,
                           QuestionLabel.CLARIFICATION)
                and not self.question_id):
            raise ValueError(f"a {self.label} label needs question_id")
        if (self.label is QuestionLabel.FOLLOW_UP) != (self.follow_up_number is not None):
            raise ValueError("follow_up_number is required for a follow-up, and only for one")
        if self.label is QuestionLabel.OFF_PLAN and self.question_id:
            raise ValueError("an off-plan question has no question_id")
        return self


class ToolCalled(BaseModel):
    """A tool the interviewer called, and what the handler did. `move_on` (realtime,
    round 14): Mitra has finished the current area and asks for the next one;
    outcome as Session A's coordinator records it, e.g. "moved", "refused" (already
    the closing), "ignored" (a duplicate). Additive (C-156); the area itself
    arrives as `area_changed`."""

    kind: Literal["tool_called"] = "tool_called"
    #: end_interview: Mitra ends the interview; outcomes accepted_completed,
    #: accepted_withdrew, refused, after_end (round 14, A-95). finish_interview: the
    #: tool before it, accepted only so older stored events still load (C-A20).
    tool: Literal["end_interview", "finish_interview", "move_on"]
    outcome: str = Field(description='What the handler did, e.g. "confirmation_spoken", "ended".')


class Withdrawal(BaseModel):
    kind: Literal["withdrawal"] = "withdrawal"
    stage: Literal["offered", "confirmed", "declined", "skip_not_withdrawal"]
    by: Literal["model", "code", "patterns"] | None = None


class SilencePrompt(BaseModel):
    kind: Literal["silence_prompt"] = "silence_prompt"
    stage: int = Field(ge=1, le=3)
    action: str
    dead_air_seconds: float = Field(ge=0)


class IntegritySignal(BaseModel):
    kind: Literal["integrity_signal"] = "integrity_signal"
    integrity_event_id: str = Field(description="IntegrityEvent.event_id in the IntegrityReport.")
    integrity_kind: IntegrityKind
    reminder: ReminderState
    reminder_index: int | None = Field(default=None, ge=1, le=3)
    #: When the integrity event ended, interview seconds; at or after the
    #: envelope's at_seconds (its start). Set on the signal sent when the event
    #: closes; None while it is open, and on events stored before this existed.
    #: Events stored before it existed load with None (C-63).
    end_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _reminder(self):
        if (self.reminder is ReminderState.NUDGED) != (self.reminder_index is not None):
            raise ValueError("reminder_index is required when nudged, and only then")
        return self


class IntegrityStatus(BaseModel):
    """The browser's current reading, for the live view's tiles. Not an event."""

    kind: Literal["integrity_status"] = "integrity_status"
    distance_cm: float | None = Field(default=None, ge=0)
    distance_in_band: bool | None = None
    faces_in_view: int | None = Field(default=None, ge=0)
    page_visible: bool | None = None


class Participant(BaseModel):
    kind: Literal["participant"] = "participant"
    change: Literal["joined", "left"]
    role: Literal["candidate", "watcher", "bot"]
    participant_id: str
    reason: str | None = None


class Skip(BaseModel):
    """The candidate asked to skip, and Mitra moved on to the next plan question
    (recorded by the coordinator when the candidate's words ask to skip).

    The interview time is the envelope's at_seconds. The report reads skips in
    the auditor task (task 23); here the live stream carries them.
    """

    kind: Literal["skip"] = "skip"
    area_id: str = Field(description='CompetencyPlan.competency_id, or "opening" / "closing".')
    question_id: str | None = Field(default=None, description='Plan question number, e.g. "Q2.1"; None when not known.')


class CostUpdate(BaseModel):
    kind: Literal["cost_update"] = "cost_update"
    component: Literal["realtime", "text_model"]
    model: str
    job: str | None = Field(default=None, description='Text-model job, e.g. "feedback".')
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    usd: float | None = Field(default=None, ge=0, description="None when the price is unknown; never guessed.")


class ErrorEvent(BaseModel):
    kind: Literal["error"] = "error"
    component: str = Field(description='e.g. "realtime", "transport", "text_model", "recording".')
    code: str
    message: str = Field(default="", max_length=300)
    fatal: bool


class CandidateDropped(BaseModel):
    """The candidate's connection dropped (wave 1). The interview pauses for the
    grace window; Mitra stays silent. The time is the envelope's at_seconds."""

    kind: Literal["candidate_dropped"] = "candidate_dropped"
    detected_by: DropDetection
    participant_id: str | None = Field(default=None, description="Daily participant id, when known.")


class CandidateReturned(BaseModel):
    """The candidate came back (wave 1). Code says "Welcome back. Let me repeat
    the last question." and Mitra repeats it."""

    kind: Literal["candidate_returned"] = "candidate_returned"
    gap_seconds: float = Field(ge=0)
    same_participant: bool = Field(description="True: the same Daily participant reconnected; False: a new one (a reload).")
    counted: bool = Field(description=f"Counts as a rejoin: a gap of {REJOIN_COUNTS_FROM_SECONDS} s or more.")
    rejoin_number: int | None = Field(default=None, ge=1, description="1, 2, … for a counted rejoin; None otherwise.")

    @model_validator(mode="after")
    def _counted(self):
        if self.counted != (self.gap_seconds >= REJOIN_COUNTS_FROM_SECONDS):
            raise ValueError(f"a return counts exactly when the gap is {REJOIN_COUNTS_FROM_SECONDS} s or more")
        if self.counted != (self.rejoin_number is not None):
            raise ValueError("rejoin_number is set for a counted return, and only then")
        return self


class RejoinLimitReached(BaseModel):
    """A counted drop beyond the limit: the interview ends with a fixed, kind goodbye."""

    kind: Literal["rejoin_limit_reached"] = "rejoin_limit_reached"
    rejoins: int = Field(ge=0, description="Counted rejoins, including the one that went past the limit.")
    limit: int = REJOIN_LIMIT


class NoteAdded(BaseModel):
    """A watcher typed a note in the live view (wave 1). The time is the
    envelope's at_seconds, set by code. Nothing reaches Mitra."""

    kind: Literal["note_added"] = "note_added"
    note_id: str
    author: str = Field(description='The watcher id, e.g. "watcher:admin"; a user id after the merge.')
    text: str = Field(min_length=1, max_length=2000)
    area_id: str | None = Field(default=None, description="The area under discussion when it was typed.")


Payload = Annotated[
    Union[
        InterviewStarted, InterviewEnded, TranscriptTurnEvent, ReplyWait, AreaChanged,
        TimeNoteSent, QuestionLabelled, ToolCalled, Withdrawal, SilencePrompt,
        IntegritySignal, IntegrityStatus, Participant, CostUpdate, ErrorEvent, Skip,
        CandidateDropped, CandidateReturned, RejoinLimitReached, NoteAdded,
    ],
    Field(discriminator="kind"),
]

#: Who may emit each kind. The auditor alone labels questions; only the browser
#: and the server (after-call analysis) report integrity.
ALLOWED_SOURCES: dict[EventKind, set[EventSource]] = {
    EventKind.INTERVIEW_STARTED: {EventSource.BOT, EventSource.COORDINATOR},
    EventKind.INTERVIEW_ENDED: {EventSource.BOT, EventSource.COORDINATOR, EventSource.SERVER},
    EventKind.TRANSCRIPT_TURN: {EventSource.BOT},
    EventKind.REPLY_WAIT: {EventSource.BOT},
    EventKind.AREA_CHANGED: {EventSource.COORDINATOR, EventSource.BOT},
    EventKind.TIME_NOTE_SENT: {EventSource.COORDINATOR},
    EventKind.QUESTION_LABELLED: {EventSource.AUDITOR},
    EventKind.TOOL_CALLED: {EventSource.COORDINATOR, EventSource.BOT},
    EventKind.WITHDRAWAL: {EventSource.COORDINATOR, EventSource.BOT},
    EventKind.SILENCE_PROMPT: {EventSource.BOT},
    EventKind.INTEGRITY_SIGNAL: {EventSource.BROWSER, EventSource.SERVER},
    EventKind.INTEGRITY_STATUS: {EventSource.BROWSER},
    EventKind.PARTICIPANT: {EventSource.BOT, EventSource.SERVER},
    EventKind.SKIP: {EventSource.COORDINATOR, EventSource.BOT},
    EventKind.COST_UPDATE: {EventSource.BOT, EventSource.SERVER},
    EventKind.ERROR: set(EventSource),
    # Drops are noticed by the bot (Daily left, errors) or the web process
    # (missing status readings, D-30); returns likewise.
    EventKind.CANDIDATE_DROPPED: {EventSource.BOT, EventSource.SERVER},
    EventKind.CANDIDATE_RETURNED: {EventSource.BOT, EventSource.SERVER},
    EventKind.REJOIN_LIMIT_REACHED: {EventSource.BOT, EventSource.COORDINATOR},
    # Notes are written by the web process when a watcher sends one.
    EventKind.NOTE_ADDED: {EventSource.SERVER},
}


class InterviewEvent(BaseModel):
    """One envelope for everything that happens in a call."""

    schema_version: Literal[1] = SCHEMA_VERSION
    event_id: str
    interview_id: str
    at_seconds: float = Field(ge=0, description="Interview clock, same as transcript times.")
    wall_time: AwareDatetime = Field(description="UTC.")
    source: EventSource
    kind: EventKind
    payload: Payload

    @field_validator("wall_time")
    @classmethod
    def _utc(cls, v):
        if v.utcoffset() != timedelta(0):
            raise ValueError("wall_time must be UTC")
        return v

    @model_validator(mode="after")
    def _payload_matches_kind(self):
        if self.payload.kind != self.kind.value:
            raise ValueError(f"payload is {self.payload.kind!r} but kind is {self.kind.value!r}")
        if self.source not in ALLOWED_SOURCES[self.kind]:
            raise ValueError(f"{self.kind.value} cannot come from {self.source.value}")
        end = getattr(self.payload, "end_seconds", None)
        if self.kind is EventKind.INTEGRITY_SIGNAL and end is not None and end < self.at_seconds:
            raise ValueError("an integrity signal cannot end before it starts (end_seconds < at_seconds)")
        return self


# -- 4. CleanedTranscript (C-A6) ------------------------------------------------------------


class CleanedCorrection(BaseModel):
    """A correction the cleaning model declared. The only way a new word can
    appear in a cleaned turn, so a correction is told apart from invention."""

    from_text: str = Field(description="The raw words, exactly as in the raw turn.")
    to_text: str


class CleanedTurn(BaseModel):
    index: int = Field(ge=0, description="TranscriptTurn.index of the raw turn: one cleaned turn per raw turn.")
    item_id: str | None = None
    speaker: Speaker
    text: str
    corrections: list[CleanedCorrection] = Field(default_factory=list)
    uncertain: list[str] = Field(default_factory=list, description="Raw words the model could not be sure of; left as they were.")
    #: "ok": cleaned and passed every guard. "copied": not cleaned (Mitra's turns,
    #: pre-interview audio). A list of problems: a guard failed, and `text` is
    #: the raw text.
    guard: Literal["ok", "copied"] | list[str] = "copied"
    at_seconds: float | None = None
    audio_start_seconds: float | None = None
    audio_end_seconds: float | None = None
    pre_interview: bool = False

    @field_validator("pre_interview", mode="before")
    @classmethod
    def _unknown_is_not_pre_interview(cls, v):
        # A raw transcript stored before TranscriptTurn.pre_interview existed has
        # no such key; the cleaner copies it as None. Unknown means "not flagged".
        return False if v is None else v


class CleanedTranscript(BaseModel):
    """The cleaned transcript, written after the call beside the raw one, which
    is never changed (DEV_PLAN, "Transcripts: raw and cleaned"). Report quotes
    are verified against the RAW transcript, never this.

    Stored in `interviews.cleaned_transcript`. Interviews without one (older,
    or cleaning not run) have null there, and readers use the raw transcript.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    status: Literal["ok", "missing"]
    #: Why it is missing. Never the raw text of anything said.
    reason: str | None = Field(default=None, max_length=300)
    model_job: str | None = Field(default=None, description='Text-model job, e.g. "feedback".')
    model: str | None = None
    turns: list[CleanedTurn] = Field(default_factory=list)
    usd: float | None = Field(default=None, ge=0)
    guard_failures: int = Field(default=0, ge=0)
    candidate_turns: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _shape(self):
        if self.status == "missing" and self.turns:
            raise ValueError("a missing cleaned transcript has no turns: the raw one stands")
        indexes = [t.index for t in self.turns]
        if len(indexes) != len(set(indexes)):
            raise ValueError("a turn index appears twice")
        return self


# -- 5. What the console reads ----------------------------------------------------------------


class WatchRefusalReason(StrEnum):
    """Why a technical round cannot be watched live (POST /api/live/interviews/{id}/watch)."""

    NOT_STARTED = "not_started"
    FINISHED = "finished"
    PAST_HARD_STOP = "past_hard_stop"
    FAILED = "failed"
    HR_ROUND = "hr_round"


class WatchRefusal(BaseModel):
    """The 409 body of a refused watch: the message as before, plus a code the
    console can switch on. Only `not_started` is worth asking again."""

    detail: str
    reason: WatchRefusalReason


# -- Phase 5 polish: what the candidate's pages are told (PA-04, PA-15, PA-18) ---------------------


class CandidateInterviewDetails(BaseModel):
    """The real interview, for the candidate's sign-in, consent and call screens
    (PA-04). Sent by POST /interviews/check and /interviews/join ONLY after the
    meeting ID and password matched. Candidate-safe by construction: the role,
    the booked length, the plan's area NAMES in order, and the server's times;
    never HR findings, question text, scores, ids or internal codes (C-162)."""

    role_title: str
    #: What HR booked (InterviewCreated.duration_minutes); None for an older booking.
    booked_minutes: int | None = Field(default=None, ge=1)
    #: The call's areas in order, names only: "Opening", the plan's areas, "Closing".
    #: `current_area_index` in the status and clock answers indexes this list.
    areas: list[str] = Field(default_factory=list)
    #: When the server started the interview (the first join); None before it.
    started_at: AwareDatetime | None = None
    #: The server's time when it answered, so the page can correct its own clock.
    server_time: AwareDatetime


class CandidateRefusalReason(StrEnum):
    """Why the check or join route will not let the candidate in, after CORRECT
    credentials (C-163). A wrong meeting ID or password is always the one 401 with
    no reason, whatever the interview's state. Too many attempts (429) has its own
    reason and a Retry-After."""

    TAKEN_PLACE = "taken_place"
    EXPIRED = "expired"
    FAILED = "failed"
    INTERRUPTED_BY_RESTART = "interrupted_by_restart"
    NO_FREE_SLOT = "no_free_slot"
    INTERVIEWER_GONE = "interviewer_gone"
    REJOIN_LIMIT_REACHED = "rejoin_limit_reached"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    #: 500 from the join: the interviewer could not be started (the interview is
    #: marked failed, ended_because failed_to_start). Additive (B-121, C-174).
    FAILED_TO_START = "failed_to_start"


class CandidateRefusal(BaseModel):
    """The body of a refused check or join: the sentence as before, plus a code the
    page can switch on. On 429, `retry_after_seconds` (also the Retry-After header)."""

    detail: str
    reason: CandidateRefusalReason | None = None
    retry_after_seconds: int | None = Field(default=None, ge=1)


class InterviewHeader(BaseModel):
    """Who and what an interview is, for the console's headings
    (GET /api/live/interviews/{id}/header). Never the meeting password, never
    the meeting ID: the console needs neither (F-059)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    candidate_id: str
    candidate_name: str | None = Field(default=None, description="None when the CV gave no name.")
    round: str = Field(default="technical", description='"technical" until Track H\'s interviews.round.')
    status: str
    booked_duration_minutes: int | None = None


class RecordingInfo(BaseModel):
    """One interview's recording, for the report's player and evidence jumps
    (GET /api/live/interviews/{id}/recording).

    `media_url` is a signed-in address on this server (no token in it); the
    video plays from it with range requests. To seek to a moment on the
    interview clock: file_second = at_seconds - offset_seconds.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    status: str = Field(description=(
        "RecordingStatus (app/db.py): not_recorded, recording, stored, unavailable, deleted, expired. "
        "Or download_failing: still at Daily, but the last 3 downloads in a row failed; the reason is "
        "in error, and it is tried again every 5 minutes. Or file_missing (C-164): stored here once, "
        "but the file is no longer on this server's disk (for example a redeploy without a volume)."))
    media_url: str | None = Field(default=None, description="Set only when the file is stored here.")
    offset_seconds: float | None = Field(
        default=None, description="file_second = at_seconds - offset_seconds. Measured error about 0.08 s (D-05).")
    offset_known: bool = False
    bytes: int | None = Field(default=None, ge=0)
    expires_at: AwareDatetime | None = Field(default=None, description="When retention deletes it.")
    error: str | None = Field(default=None, description="Why there is no recording, in words a reviewer can read.")

    @field_validator("media_url")
    @classmethod
    def _plain_path(cls, v):
        if v is not None and (not v.startswith("/") or "?" in v or "#" in v):
            raise ValueError("a plain path on this server: no query (no token), no fragment")
        return v


# -- 6. CheckIntelReport ----------------------------------------------------------------------

#: The label a row has before the auditor has labelled it.
NOT_YET_LABELLED = "not yet labelled"


class CheckIntelWait(BaseModel):
    detect_s: float | None = None
    model_s: float | None = None
    total_s: float | None = None
    interrupted: bool = False


class CheckIntelChartPoint(CheckIntelWait):
    turn_index: int = Field(ge=0)


class CheckIntelNote(BaseModel):
    at_seconds: float
    type: Literal["time_note", "tool", "ended", "silence_prompt", "area_switch", "error", "cut",
                  "skip", "decline"]
    text: str
    #: The plan question skipped or declined, when known.
    question_id: str | None = None
    #: A stable code for the page to word (C-166), never shown as is: e.g.
    #: "end_interview_withdrew", "interview_complete", "pipeline.pipeline_error". The
    #: table is in track_i_notes/C_findings.md (C-166). None for notes written in words.
    code: str | None = None


class CheckIntelRow(BaseModel):
    turn_index: int = Field(ge=0)
    at_seconds: float
    spoken_start: float
    text: str
    item_id: str | None = None
    label: QuestionLabel | Literal["not yet labelled"]
    #: Where the label came from (C-125): "live", the auditor's question_labelled
    #: event; "stored", the auditor's own record in session_metrics, used when the
    #: live event never arrived; None when there is no label.
    label_source: Literal["live", "stored"] | None = None
    question_id: str | None = None
    follow_up_number: int | None = None
    area_id: str | None = None
    wait: CheckIntelWait | None = None
    notes: list[CheckIntelNote] = Field(default_factory=list)


class CheckIntelInterview(BaseModel):
    interview_id: str
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    engine: str | None = None
    realtime_model: str | None = None
    ended_because: str | None = None
    #: The ending detail (session_metrics.ending), additive (B-110): `ending` is the
    #: free-text how (e.g. "forced_at_hard_stop" beside interview_complete, the time
    #: limit), `hang_up_by` what hung up (e.g. "bot_stopped_speaking"). None when not
    #: recorded (the pipeline engine, older interviews).
    ending: str | None = Field(default=None, max_length=64)
    hang_up_by: str | None = Field(default=None, max_length=64)


class CheckIntelReplyWait(BaseModel):
    median_s: float | None = None
    p95_s: float | None = None
    n: int = 0
    unlinked_waits: int = 0
    method: str
    source: Literal["events", "session_metrics"] | None = None


class CheckIntelFollowUps(BaseModel):
    per_question: dict[str, int] = Field(default_factory=dict)
    cap: int
    over_cap: list[str] = Field(default_factory=list)


class CheckIntelCoverage(BaseModel):
    plan_areas: int | None = None
    #: The next three are null until the auditor has labelled questions.
    asked_question_ids: list[str] | None = None
    asked: int | None = None
    plan_questions: int | None = None


class CheckIntelCost(BaseModel):
    realtime_usd: float | None = None
    realtime_is_estimate: bool = False
    report_usd: float | None = None
    text_model_usd_by_job: dict[str, float] = Field(default_factory=dict)
    calls_with_unknown_price: int = 0
    total_usd: float | None = None
    source: Literal["events", "session_metrics"]


class CheckIntelSkipsAndDeclines(BaseModel):
    """Counts from the notes, so a tile can show them without walking the rows."""

    skips: int = 0
    declines: int = 0
    #: Easier questions asked after a decline (auditor labels); null until labelled.
    easier_variants: int | None = None


class CheckIntelTiles(BaseModel):
    reply_wait: CheckIntelReplyWait
    off_plan_count: int | None = None
    follow_ups: CheckIntelFollowUps | None = None
    coverage: CheckIntelCoverage
    cost: CheckIntelCost
    skips_and_declines: CheckIntelSkipsAndDeclines = Field(default_factory=CheckIntelSkipsAndDeclines)


class CheckIntelPlanArea(BaseModel):
    area_id: str | None = None
    name: str | None = None
    budget_minutes: float | None = None
    question_ids: list[str] = Field(default_factory=list)


class CheckIntelUnfilled(BaseModel):
    #: Which part of this answer is empty (a path in this JSON, for the page's own use).
    field: str
    #: Why, in plain words a reviewer can read.
    reason: str
    #: A stable code for the page to word (C-166): no_auditor_labels, no_area_per_turn,
    #: no_reply_waits, no_plan, no_report_cost.
    code: str | None = None


class CheckIntelReport(BaseModel):
    """GET /api/live/interviews/{id}/check-intel (live/check_intel.py `build`).

    Labels are the auditor's reading after the fact, never the realtime
    model's reasoning. Nothing here is computed by a model. The JSON key for
    `schema_tag` is "schema" (a Pydantic name clash), value "check_intel.v1".
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_tag: Literal["check_intel.v1"] = Field(default="check_intel.v1", alias="schema")
    interview: CheckIntelInterview
    labels_note: str
    tiles: CheckIntelTiles
    chart: list[CheckIntelChartPoint] = Field(default_factory=list)
    plan: list[CheckIntelPlanArea] | None = None
    rows: list[CheckIntelRow] = Field(default_factory=list)
    after_last_turn: list[CheckIntelNote] = Field(default_factory=list)
    events_stored: int = Field(default=0, ge=0)
    unfilled: list[CheckIntelUnfilled] = Field(default_factory=list)
    #: How the plan was refined with Track H's HR findings (wave 1); None when
    #: no refinement ran.
    hr_findings: HRFindingsRefinement | None = None


# -- 7. Wave 1: watchers' notes ---------------------------------------------------------------


class WatcherNote(BaseModel):
    """A note a watcher typed in the live view. Code links it to the interview
    time and the area. Never scored, never evidence, never shown to Mitra."""

    note_id: str
    interview_id: str
    author: str = Field(description='The watcher id, e.g. "watcher:admin"; a user id after the merge.')
    text: str = Field(min_length=1, max_length=2000)
    at_seconds: float = Field(ge=0, description="Interview clock, when it was typed.")
    area_id: str | None = None
    created_at: AwareDatetime


class WatcherNotesSection(BaseModel):
    """The report's "Watchers' notes" section, apart from the scored evidence."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    notes: list[WatcherNote] = Field(default_factory=list)
    #: Always False: notes are people's observations, never part of a score.
    scored: Literal[False] = False

    @model_validator(mode="after")
    def _one_interview(self):
        if any(n.interview_id != self.interview_id for n in self.notes):
            raise ValueError("every note belongs to this interview")
        return self


# -- 8. Wave 1: capture for the after-call checks ------------------------------------------------


def _relative_path(v: str | None) -> str | None:
    if v is not None and (v.startswith("/") or "://" in v or ".." in PurePosixPath(v).parts or "\\" in v):
        raise ValueError("a path relative to the capture folder: not absolute, no '..', not a URL")
    return v


class CandidateAudioCapture(BaseModel):
    """The candidate's own audio track, saved by the bot (D-33 to D-35)."""

    relative_path: str = Field(description="Relative to the capture folder, e.g. int_x/candidate.wav.")
    sample_rate_hz: int = Field(default=16000, gt=0)
    channels: int = Field(default=1, ge=1)
    sample_format: Literal["s16le"] = "s16le"
    start_seconds: float = Field(ge=0, description="Interview clock at the file's first sample.")
    duration_seconds: float = Field(ge=0)
    bytes: int | None = Field(default=None, ge=0)

    @field_validator("relative_path")
    @classmethod
    def _path(cls, v):
        return _relative_path(v)


class ScreenFrameCapture(BaseModel):
    """Reserved for wave 2 (a decision is pending): one saved screen frame."""

    relative_path: str
    at_seconds: float = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def _path(cls, v):
        return _relative_path(v)


class CaptureManifest(BaseModel):
    """What the bot captured for the after-call checks. Wave 1 is audio only."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    candidate_audio: CandidateAudioCapture | None = None
    #: Wave 2. Empty until screen frames are decided.
    screen_frames: list[ScreenFrameCapture] = Field(default_factory=list)
    #: When Mitra was speaking, on the interview clock (D-58.6), so the
    #: after-call runner need not read the transcript. Empty when the bot did
    #: not write it (then the runner reads the transcript's audio times).
    mitra_speaking: list["TimeRange"] = Field(default_factory=list)


# -- 9. Wave 1: after-call results ---------------------------------------------------------------


class AfterCallCheckName(StrEnum):
    SECOND_VOICE = "second_voice"
    #: Wave 2 (screen frames); listed so its status can be "skipped" until then.
    SCREEN_CONTENT = "screen_content"


class AfterCallState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class AfterCallCheck(BaseModel):
    check: AfterCallCheckName
    state: AfterCallState
    #: Why it failed or was skipped, in plain words. Required for those two.
    reason: str | None = Field(default=None, max_length=300)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _reason(self):
        if self.state in (AfterCallState.FAILED, AfterCallState.SKIPPED) and not self.reason:
            raise ValueError(f"a {self.state.value} check says why")
        return self


class AfterCallStatus(BaseModel):
    """Where each after-call check of one interview stands (Session D produces it)."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    checks: list[AfterCallCheck] = Field(default_factory=list)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _once_each(self):
        names = [c.check for c in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("each check appears once")
        return self


class TimeRange(BaseModel):
    start_seconds: float = Field(ge=0, description="Interview clock.")
    end_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _order(self):
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds is before start_seconds")
        return self


class SecondVoiceSignal(BaseModel):
    """Where a voice other than the candidate's may be heard on the candidate's
    own track, for a person to listen to. Evidence, never a verdict: it goes
    into the report's Integrity section (with an IntegrityEvent of kind
    second_voice, source after_call, recorded_only) and never into a score."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    ranges: list[TimeRange] = Field(default_factory=list)
    #: How sure the method is that the ranges hold another voice, 0 to 1. The
    #: method's own number, shown as such; never combined into anything.
    confidence: float = Field(ge=0, le=1)
    method: str = Field(description='e.g. "sherpa-onnx diarisation" (D-14).')
    method_version: str
    #: The sentence the report shows, neutral (decision 12): behaviour, never intent.
    wording: str = Field(max_length=300, description='e.g. "A second voice may be audible at 12:40-12:55."')
    #: The IntegrityEvent this signal backs, when one was written.
    integrity_event_id: str | None = None

    @field_validator("wording")
    @classmethod
    def _wording_neutral(cls, v):
        return _neutral(v)



class AfterCallResults(BaseModel):
    """One interview's after-call checks, for the console
    (GET /api/proctoring/interviews/{id}/after-call). Signals for review, never a score."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    #: Whether the checks are switched on now (AFTERCALL_ENABLED). Off by default.
    enabled: bool
    status: AfterCallStatus | None = None
    second_voice: SecondVoiceSignal | None = None
    #: Whether the checks are paused now: a file named PAUSED in the after-call
    #: folder, or AFTERCALL_PAUSED as the server started. A paused check goes back
    #: to "pending" in `status`, so this is what tells "paused" from "not run yet".
    #: Additive (B-104); the route always sends it.
    paused: bool = False


IntegrityReport.model_rebuild()
CaptureManifest.model_rebuild()


# -- 8b. The STORED integrity report (Session D, Track H integration, D-93) ----------------------


class IntegrityReportStatus(StrEnum):
    #: The interview has ended, but events may still arrive (the intake's late grace).
    AWAITING_LATE_EVENTS = "awaiting_late_events"
    #: The after-call checks are on and have not all finished; the report will be rewritten.
    AWAITING_AFTER_CALL = "awaiting_after_call"
    #: Nothing more is expected.
    FINAL = "final"


class IntegrityRange(BaseModel):
    """One integrity event as a time range on the interview clock."""

    event_id: str
    source: IntegritySource
    start_seconds: float = Field(ge=0)
    end_seconds: float | None = Field(default=None, ge=0, description="None: never closed.")
    confidence: float | None = Field(default=None, ge=0, le=1)


class IntegrityTypeTotal(BaseModel):
    """Per integrity kind: how often, for how long, and when. Signals, never a score."""

    kind: IntegrityKind
    count: int = Field(ge=0)
    #: Sum of (end - start) over the closed events; open events add nothing and are counted apart.
    total_seconds: float = Field(ge=0)
    open_events: int = Field(default=0, ge=0)
    ranges: list[IntegrityRange] = Field(default_factory=list)


class StoredIntegrityReport(BaseModel):
    """The integrity report as STORED when the interview ends, rewritten when late events or
    after-call results arrive (proctoring/stored.py). Codes, never display words: the
    consoles word them. For a person to review; never part of a score."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interview_id: str
    status: IntegrityReportStatus
    built_at: AwareDatetime
    interview_status: str = Field(description="InterviewStatus code when the report was built.")
    purpose: Literal["signals_for_review"] = "signals_for_review"
    scored: Literal[False] = False
    by_type: list[IntegrityTypeTotal] = Field(default_factory=list)
    after_call_status: AfterCallStatus | None = None
    second_voice: SecondVoiceSignal | None = None
    #: The full report (events, summary, accommodation), as the console's report reads it.
    report: IntegrityReport


# -- 9. Phase 4: the summary page (Session D) ----------------------------------------------------
# Re-exported, not moved (C-159): the definitions stay in Session D's
# analytics/summary/shapes.py, unchanged, so the routes, the builder and these names are
# one set of classes. Moving them here would need D's module to import them back (D's
# file) or leave two copies that could drift. shapes.py imports only pydantic, so this
# import makes no cycle. Session B's exporter can take them from here.
from analytics.summary.shapes import InterviewSummary, JobSummary, OperationsSummary  # noqa: E402,F401
from analytics.summary.shapes import Decision as SummaryDecision  # noqa: E402
from analytics.summary.shapes import Ending as SummaryEnding  # noqa: E402


class InterviewEnding(BaseModel):
    """How one interview ended, for the report (C-A18, C-169): codes only, no sentences
    (Session A, A-101: the pages word the codes; B's sentences are the source).

    Built by Session D's summary builder (analytics/summary/build.py
    `interview_summary`), the same function the summary page reads, so the report and
    the summary can never disagree. `ending`: status, end reason (EndReason), the
    coordinator's ending code, what hung up, and the HR category. `decided`: who
    decided the ending (owner mitra / code / unknown), the action, the reason code,
    the time and the record it came from; None when the interview never ran."""

    ending: SummaryEnding
    decided: SummaryDecision | None = None
