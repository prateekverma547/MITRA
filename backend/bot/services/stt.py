"""Speech-to-text adapter.

**Vendor swap point.** The whole reason this file is isolated: a change of
provider happens here and nowhere else. Nothing upstream knows which one runs.

Diagnostic note: this file is responsible for *wrong words* in the transcript.
It is NOT responsible for the bot cutting people off — that is endpointing, and
lives in `bot/turn_taking.py`. Do not tune one to fix the other.

**Currently running the Deepgram trial** that CLAUDE.md's own decision rule
called for once the OpenAI measurements came in. Deepgram is used when
`DEEPGRAM_API_KEY` is set, OpenAI otherwise, so both can be measured on
equivalent sessions with the same `stt_lag` instrumentation. This is a trial,
not an abstraction layer: when it concludes, the loser is deleted from this file
rather than left behind a switch.

Why it exists, in short. Ending a turn needs two things: knowing the candidate
stopped speaking, and knowing the transcript is complete. A provider can tell
Pipecat the second by confirming finalisation. OpenAI's Realtime STT never calls
`confirm_finalize()` — verified in Pipecat's source, where the count is zero for
OpenAI and one for Deepgram — so every turn waits out a fixed timer instead,
whether the words arrived in 200ms or 1000ms. Pipecat's own defaults say the
same thing in numbers: it waits 1.66s for OpenAI and 0.35s for Deepgram.
"""

import os

from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.transcriptions.language import Language

# -- OpenAI ------------------------------------------------------------------

# Low-latency streaming transcription model. Alternatives on the same service:
# "gpt-4o-transcribe", "gpt-4o-mini-transcribe" — both slower to finalize, and
# both considerably cheaper at $0.006 and $0.003 a minute against this one's
# $0.017. We paid the premium for speed and still wait out the timeout below,
# because none of the three confirm finalisation.
STT_MODEL = "gpt-realtime-whisper"

# How long Pipecat waits for a final transcript after the turn model says the
# candidate is done. OpenAI's Realtime STT never sets `finalized=True`, so this
# timeout — not the transcript itself — gates the end of every turn.
#
# Pipecat's built-in default is 1.66s, a generic figure, not a measurement of
# our deployment. Measured here (session dev-42b6ab2a, 12 samples):
#
#     median 1.04s   p90 1.08s   max 1.08s   min 0.83s
#
# A strikingly tight distribution: OpenAI's finalization is a consistent ~1s
# floor rather than a noisy average. 1.25s clears every observed sample with
# ~0.17s of headroom while cutting 0.41s off the default on every turn.
#
# Do NOT lower this below the measured max. If the timeout expires while only a
# stale transcript fragment is in hand, the turn fires on truncated text and the
# interviewer answers half a sentence. Truncated-answer correctness beats
# latency (CLAUDE.md). Re-measure with `stt_lag` before changing it.
STT_FINALIZATION_WAIT_SECONDS = 1.25

# -- Deepgram ----------------------------------------------------------------

DEEPGRAM_MODEL = "nova-3"

#: Deepgram confirms finalisation, so this is a safety net rather than the thing
#: gating every turn. Left at Pipecat's own default on purpose: the trial is
#: meant to measure what the vendor actually does, and tuning this first would
#: measure our guess instead. Once `stt_lag` has been recorded on a real
#: session, set it from that the way the OpenAI figure above was set.
DEEPGRAM_FINALIZATION_WAIT_SECONDS = 0.35


def using_deepgram() -> bool:
    """True when a Deepgram key is configured."""
    return bool(os.environ.get("DEEPGRAM_API_KEY", "").strip())


def stt_provider() -> str:
    """Which provider is running, for the session record.

    Written into the session metrics so a latency comparison can say which
    vendor produced which numbers, rather than depending on somebody
    remembering when the key was added.
    """
    return "deepgram" if using_deepgram() else "openai"


def build_stt(*, api_key: str):
    """The transcription service for the live conversation.

    `api_key` is the OpenAI one. Deepgram reads its own key from the
    environment, so callers never need to know which provider is in use.
    """
    if using_deepgram():
        return _build_deepgram()
    return _build_openai(api_key=api_key)


def _build_openai(*, api_key: str) -> OpenAIRealtimeSTTService:
    """Streaming STT over the OpenAI Realtime API in transcription-only mode.

    `turn_detection=False` disables OpenAI's server-side VAD. We deliberately do
    our own endpointing locally (Silero VAD + smart-turn v3) because server-side
    VAD is silence-based only and would undo the mid-thought pause tolerance
    that `bot/turn_taking.py` exists to provide.
    """
    return OpenAIRealtimeSTTService(
        api_key=api_key,
        turn_detection=False,
        ttfs_p99_latency=STT_FINALIZATION_WAIT_SECONDS,
        settings=OpenAIRealtimeSTTService.Settings(
            model=STT_MODEL,
            language=Language.EN,
            # Candidates wear headsets or sit close to a laptop mic.
            noise_reduction="near_field",
        ),
    )


def _build_deepgram() -> DeepgramSTTService:
    """Streaming STT over Deepgram.

    `endpointing=False` for exactly the reason `turn_detection=False` is set on
    the OpenAI service. Deciding when a turn has ended belongs to
    `bot/turn_taking.py`, using VAD plus semantic turn detection. Deepgram's
    endpointing is silence-based, and letting it hold an opinion too would
    either duplicate that work or quietly undo the mid-thought pause tolerance
    the whole turn-taking design exists to provide.

    `interim_results` stays on: Pipecat uses partial transcripts to know speech
    is in flight, and without them nothing downstream notices anyone is talking
    until the complete result lands.
    """
    return DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        ttfs_p99_latency=DEEPGRAM_FINALIZATION_WAIT_SECONDS,
        settings=DeepgramSTTService.Settings(
            model=DEEPGRAM_MODEL,
            language=Language.EN,
            endpointing=False,
            interim_results=True,
            smart_format=True,
            punctuate=True,
            numerals=True,
        ),
    )
