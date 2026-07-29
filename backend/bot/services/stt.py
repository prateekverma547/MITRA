"""Speech-to-text adapter.

**Vendor swap point.** Per CLAUDE.md the POC builds against OpenAI only — no
dual paths, no runtime provider switch. Deepgram is a documented fallback and
nothing more. If a swap ever happens, it happens in this file and nowhere else.

Diagnostic note: this file is responsible for *wrong words* in the transcript.
It is NOT responsible for the bot cutting people off — that is endpointing, and
lives in `bot/turn_taking.py`. Do not tune one to fix the other.
"""

from pipecat.services.openai.stt import OpenAIRealtimeSTTService
from pipecat.transcriptions.language import Language

# Low-latency streaming transcription model. Alternatives on the same service:
# "gpt-4o-transcribe", "gpt-4o-mini-transcribe" (both slower to finalize).
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


def build_stt(*, api_key: str) -> OpenAIRealtimeSTTService:
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
