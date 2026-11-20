"""What one interview consumed from each paid service. The unit record, not a price.

Stored as `session_metrics["usage"]` on the interview row. Units are recorded, and
prices are applied afterwards by `scripts/interview_cost.py`, because prices change and
a stored dollar figure would silently go stale.

Every figure here is a count the software observed. None is estimated from the
transcript. Where a provider bills by something other than what we count, the field
says so, so nobody mistakes one for the other.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

USAGE_SCHEMA_VERSION = 1


class ModelUsage(BaseModel):
    """Token counts for one model role. Zero when that role never ran."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


class InterviewUsage(BaseModel):
    schema_version: int = USAGE_SCHEMA_VERSION

    #: The live conversation model, counted from the pipeline's usage events.
    llm_live: ModelUsage = Field(default_factory=ModelUsage)
    #: The off-path judgement model, counted from each judge call.
    llm_judgment: ModelUsage = Field(default_factory=ModelUsage)
    #: The feedback scoring model, counted when the report is written.
    llm_feedback: ModelUsage = Field(default_factory=ModelUsage)

    #: Characters sent to text-to-speech. This is what ElevenLabs bills.
    tts_characters: int = 0

    #: Seconds of audio sent to speech-to-text while the call was connected. This is
    #: what streaming STT bills, and it includes the silences, because the stream is
    #: open the whole time. Not the same as candidate speech.
    stt_stream_seconds: float = 0.0
    #: Seconds the candidate was actually speaking, from voice-activity events. A
    #: diagnostic, not a billing figure.
    candidate_speech_seconds: float = 0.0

    #: Seconds the bot was in the room. This is what the room and call are billed on.
    call_seconds: float = 0.0

    #: Which model and provider served each role, recorded so a later price change can
    #: be applied to the right thing.
    models: dict[str, str] = Field(default_factory=dict)
    providers: dict[str, str] = Field(default_factory=dict)
