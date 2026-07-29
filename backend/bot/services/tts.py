"""Text-to-speech adapter.

**Vendor swap point.** ElevenLabs, low-latency Flash model family, single voice
ID from env.
"""

import aiohttp
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"

# Flash family — the low-latency tier. Do not switch to a multilingual/quality
# tier without a listening session: the latency difference is audible in
# conversation even when the per-request numbers look similar.
TTS_MODEL = "eleven_flash_v2_5"

# Interviewers speak slower than assistants. At the default 1.0 the bot rattles
# through a long sentence with no room to breathe, which reads as rushed and
# makes a candidate feel hurried.
#
# Range is 0.7-1.2. This is a by-ear setting — adjust it in a live session, not
# from a spec. Note that pacing is only half prosody: sentence length matters
# just as much, which is why the brain's voice rules cap sentence length.
#
# History: 1.0 was "speaking very fast", 0.92 was still too fast by ear on two
# different voices. 0.85 is a deliberate step rather than another nudge — an
# interview is not a sales call, and a candidate needs room to think while
# listening.
TTS_SPEED = 0.85

# High stability keeps delivery even. An interviewer who swings in tone reads as
# reacting to the answer — sounding pleased with a good answer or flat with a
# weak one — and this bot must never signal a verdict. Neutrality is a feature.
#
# **These numbers are voice-specific.** They are not a global "good TTS" setting:
# each voice ships with its own tuned defaults, and carrying settings across a
# voice change is how a voice ends up sounding wrong for reasons nobody can
# place. This value is the current voice's own default, which is the sanest
# starting point before tuning by ear.
TTS_STABILITY = 0.78


class VoiceUnavailable(RuntimeError):
    """The configured voice cannot be synthesised with this API key."""


async def verify_voice(*, api_key: str, voice_id: str) -> str:
    """Check the configured voice exists before the interview starts.

    Without this, a bad `ELEVENLABS_VOICE_ID` fails at the *first spoken word*:
    the TTS websocket is rejected with a policy violation, Pipecat retries three
    times, and the bot then sits in the room silent but apparently healthy. A
    candidate would be staring at a mute interviewer with no idea why.

    Cheap enough (one GET at startup) to be worth doing on every session.

    Returns:
        The human-readable voice name, for logging.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{ELEVENLABS_API_URL}/voices/{voice_id}",
            headers={"xi-api-key": api_key},
        ) as response:
            if response.status == 200:
                return (await response.json()).get("name", voice_id)

            detail = await _error_detail(response)

            if response.status in (401, 403):
                raise VoiceUnavailable(
                    f"ELEVENLABS_API_KEY was rejected by ElevenLabs: {detail}"
                )
            # ElevenLabs answers an unknown voice with 400, not 404.
            if response.status in (400, 404):
                raise VoiceUnavailable(
                    f"ELEVENLABS_VOICE_ID '{voice_id}' is not usable with this "
                    f"API key: {detail}\n"
                    "List the voices available to your key with:\n"
                    '  curl -s -H "xi-api-key: $ELEVENLABS_API_KEY" '
                    "https://api.elevenlabs.io/v1/voices"
                )
            raise VoiceUnavailable(
                f"Could not verify ELEVENLABS_VOICE_ID '{voice_id}' "
                f"(HTTP {response.status}): {detail}"
            )


async def _error_detail(response: aiohttp.ClientResponse) -> str:
    """Pull ElevenLabs' own explanation out of an error response, if there is one."""
    try:
        body = await response.json()
    except Exception:
        return f"HTTP {response.status}"

    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


def build_tts(*, api_key: str, voice_id: str) -> ElevenLabsTTSService:
    """Streaming TTS over the ElevenLabs WebSocket API."""
    return ElevenLabsTTSService(
        api_key=api_key,
        settings=ElevenLabsTTSService.Settings(
            model=TTS_MODEL,
            voice=voice_id,
            stability=TTS_STABILITY,
            similarity_boost=0.75,
            speed=TTS_SPEED,
        ),
    )
