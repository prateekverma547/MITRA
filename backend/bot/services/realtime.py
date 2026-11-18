"""OpenAI Realtime adapter: one model that hears, decides and speaks.

**Vendor swap point** for the realtime engine (`INTERVIEW_ENGINE=realtime`).
It replaces three services of the pipeline engine at once: STT, the live LLM
and TTS. See docs/REALTIME_ARCHITECTURE.md for why, and for what it costs.

Everything the session is configured with lives in `realtime_session_dict`, so
`scripts/realtime_smoke.py` checks exactly the configuration the bot uses.
A setting OpenAI rejects kills the whole session, which on a live call looks
like the interviewer never speaking, so run the smoke check after changing
anything here.
"""

import os

from loguru import logger
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.services.openai.realtime import events
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService

#: The only input rate OpenAI Realtime accepts for PCM.
REALTIME_SAMPLE_RATE = 24000

DEFAULT_MODEL = "gpt-realtime-2.1"
#: Cedar was the Burj kiosk's choice and the one that sounded best there.
DEFAULT_VOICE = "cedar"
#: "low lets the user take their time to speak" (OpenAI VAD guide). An
#: interview answer has thinking pauses mid-thought; "auto" (= medium) suited a
#: kiosk's short questions, not this. Not interrupting outranks speed.
DEFAULT_EAGERNESS = "low"
#: Thinking before speaking is latency the candidate hears as dead air.
#: CLAUDE.md forbids a reasoning model on the live path; this model always
#: reasons, so it runs at the lowest effort that is accepted. Smoke check on
#: 2026-11-13: "minimal" accepted, text replies 0.72-0.87s against 0.80-1.02s
#: at "low".
DEFAULT_REASONING_EFFORT = "minimal"
#: Candidates sit at a laptop, not in a headset. OpenAI: near_field for
#: close-talking microphones, far_field for laptops and meeting rooms.
DEFAULT_NOISE_REDUCTION = "far_field"
TRANSCRIPTION_MODEL = "gpt-realtime-whisper"


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def realtime_model() -> str:
    return _env("REALTIME_MODEL", DEFAULT_MODEL)


def realtime_session_dict(
    *,
    instructions: str,
    model: str | None = None,
    tools: list[dict] | None = None,
) -> dict:
    """The session config as OpenAI receives it. Shared with the smoke check."""
    from bot.tools import TOOLS
    from pipecat.adapters.services.open_ai_realtime_adapter import OpenAIRealtimeLLMAdapter

    if tools is None:
        tools = OpenAIRealtimeLLMAdapter().from_standard_tools(TOOLS)

    return {
        "type": "realtime",
        "model": model or realtime_model(),
        "instructions": instructions,
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": _env("REALTIME_EAGERNESS", DEFAULT_EAGERNESS),
                    "create_response": True,
                    "interrupt_response": True,
                },
                "noise_reduction": {
                    "type": _env("REALTIME_NOISE_REDUCTION", DEFAULT_NOISE_REDUCTION)
                },
                "transcription": {"model": TRANSCRIPTION_MODEL},
            },
            "output": {"voice": _env("REALTIME_VOICE", DEFAULT_VOICE)},
        },
        "reasoning": {"effort": _env("REALTIME_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)},
        "tools": tools,
        "tool_choice": "auto",
    }


class MitraRealtimeService(OpenAIRealtimeLLMService):
    """Pipecat's Realtime service plus the two things MITRA needs from it.

    1. **Resampling.** The pipeline listens at 16 kHz because Silero VAD, which
       we keep for measuring true end of speech, only runs at 8 or 16 kHz.
       OpenAI only accepts 24 kHz. Upsampling speech that was captured for a
       16 kHz path loses nothing a transcriber or a voice model uses.
    2. **Notes.** Pipecat's `LLMMessagesAppendFrame` is not implemented for
       Realtime (it logs "NEED TO IMPLEMENT MESSAGES APPEND"), so the
       coordinator inserts and deletes its notes through raw client events.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._resampler = create_stream_resampler()

    async def _send_user_audio(self, frame: InputAudioRawFrame):
        if frame.sample_rate != REALTIME_SAMPLE_RATE:
            audio = await self._resampler.resample(
                frame.audio, frame.sample_rate, REALTIME_SAMPLE_RATE
            )
            # A stream resampler holds back its first few milliseconds while it
            # fills its filter, so early calls return nothing. OpenAI rejects an
            # empty append as "Invalid 'audio'" and the error is fatal: found
            # live, the session died at the candidate's first word and the
            # interviewer never spoke again.
            if not audio:
                return
            frame = InputAudioRawFrame(
                audio=audio, sample_rate=REALTIME_SAMPLE_RATE, num_channels=frame.num_channels
            )
        await super()._send_user_audio(frame)

    async def reconnect(self) -> None:
        """Open a fresh connection to OpenAI, configured from scratch.

        Pipecat's `reset_conversation()` does the same but assumes a
        conversation context already exists and raises on a fresh service,
        which is exactly when this is needed: the candidate has just joined.
        """
        await self._disconnect()
        self._llm_needs_conversation_setup = True
        await self._connect()

    async def insert_note(self, text: str) -> str:
        """Add a system message to the live conversation. Returns its item id.

        The model reads it before its next reply. It never interrupts a reply
        already being spoken.
        """
        item = events.ConversationItem(
            type="message",
            role="system",
            content=[events.ItemContent(type="input_text", text=text)],
        )
        # Tell the base class this item is ours, so its echo from the server
        # is not mistaken for the start of an assistant reply.
        self._messages_added_manually[item.id] = True
        await self.send_client_event(events.ConversationItemCreateEvent(item=item))
        return item.id

    async def delete_note(self, item_id: str) -> None:
        await self.send_client_event(events.ConversationItemDeleteEvent(item_id=item_id))

    async def say(self, instructions: str) -> None:
        """Ask for one spoken reply with instructions for that reply only.

        Used for the silence ladder's nudges, which on the pipeline engine are
        spoken straight to TTS. There is no separate TTS here.
        """
        await self.send_client_event(
            events.ResponseCreateEvent(
                response=events.ResponseProperties(
                    output_modalities=["audio"], instructions=instructions
                )
            )
        )


def build_realtime_llm(*, api_key: str, instructions: str, tools) -> MitraRealtimeService:
    """The live interviewer for `INTERVIEW_ENGINE=realtime`."""
    config = realtime_session_dict(instructions=instructions, tools=[])
    audio_in = config["audio"]["input"]
    session = events.SessionProperties(
        model=config["model"],
        instructions=instructions,
        audio=events.AudioConfiguration(
            input=events.AudioInput(
                turn_detection=events.SemanticTurnDetection(**audio_in["turn_detection"]),
                noise_reduction=events.InputAudioNoiseReduction(**audio_in["noise_reduction"]),
                transcription=events.InputAudioTranscription(**audio_in["transcription"]),
            ),
            output=events.AudioOutput(voice=config["audio"]["output"]["voice"]),
        ),
        reasoning=events.Reasoning(effort=config["reasoning"]["effort"]),
        tools=tools,
        tool_choice="auto",
    )
    logger.info(
        f"realtime engine: model={config['model']} "
        f"voice={config['audio']['output']['voice']} "
        f"eagerness={audio_in['turn_detection']['eagerness']} "
        f"reasoning={config['reasoning']['effort']} "
        f"noise_reduction={audio_in['noise_reduction']['type']}"
    )
    return MitraRealtimeService(
        api_key=api_key,
        settings=MitraRealtimeService.Settings(
            model=config["model"],
            system_instruction=instructions,
            session_properties=session,
        ),
    )
