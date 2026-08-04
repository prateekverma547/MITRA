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

#: How long Pipecat waits for a final transcript once the turn model says the
#: candidate is done. Deepgram confirms finalisation, so unlike the OpenAI figure
#: above this is a **safety net rather than the thing gating a turn**: when
#: confirmation lands at 0.48s the turn fires at 0.48s, whether the ceiling here
#: is 0.35 or 0.65. Raising it therefore costs no latency on a healthy turn.
#:
#: This was Pipecat's own default of 0.35, left alone deliberately until a real
#: session produced a number, exactly as this comment used to say it should be.
#: Two full interviews have now produced one. Fourteen `stt_lag` samples,
#: nova-3, from Greater Noida, India:
#:
#:     0.46  0.46  0.46  0.47  0.47  0.47  0.47
#:     0.48  0.48  0.48  0.48  0.49  0.51  0.52
#:
#:     median 0.475   p90 0.51   max 0.52   min 0.46   n=14
#:
#: **Every sample exceeds 0.35.** This is not a slow tail, it is a consistent
#: floor around 0.48s that the old ceiling never reached, so the timer fired
#: first on every single turn. The same tight-distribution shape the OpenAI
#: measurement showed, at a different number.
#:
#: What that cost: the timeout expiring before the transcript was complete split
#: one spoken sentence into several turns. Words at the split were corrupted
#: ("session management" became "recession management"), and the extra turns
#: inflated the counts that decide when a section has had enough, so sections
#: ended early. A batch-API run of the same speaker saying the same phrases
#: produced none of those errors, which is what ruled out accent and pointed
#: here.
#:
#: 0.65 is the measured max plus headroom, the same rule that put the OpenAI
#: figure at 1.25 against a measured max of 1.08.
#:
#: **These samples are from India.** Finalisation latency includes a network
#: round trip to Deepgram, so a deployment closer to their infrastructure will
#: measure lower and one further away may measure higher. This number belongs to
#: where the candidates are, not to the vendor.
#:
#: Would change the answer: `stt_lag` samples that sit clear of 0.65 on the
#: deployment being run, or a Deepgram change that makes confirmation slower. Do
#: NOT lower it below the measured max. Re-measure with `stt_lag` first.
DEEPGRAM_FINALIZATION_WAIT_SECONDS = 0.65


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
