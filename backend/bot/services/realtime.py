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

import asyncio
import os
import time

from loguru import logger
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.services.openai.realtime import events
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService

from bot.config import (
    DEFAULT_REALTIME_MODEL,
    DEFAULT_REALTIME_TRANSCRIPTION_MODEL,
    realtime_transcription_delay,
    realtime_transcription_model,
)
from bot.config import realtime_model as _realtime_model_setting
from bot.realtime_timeline import READBACK_TIMEOUT_SECONDS, RealtimeTimeline

#: The only input rate OpenAI Realtime accepts for PCM.
REALTIME_SAMPLE_RATE = 24000

# The model, the transcription model and the transcription delay are settings
# (bot/config.py: REALTIME_MODEL, REALTIME_TRANSCRIPTION_MODEL,
# REALTIME_TRANSCRIPTION_DELAY). The defaults are kept here as names only.
DEFAULT_MODEL = DEFAULT_REALTIME_MODEL
#: Cedar was the Burj kiosk's choice and the one that sounded best there.
DEFAULT_VOICE = "cedar"
#: Was "low" ("lets the user take their time to speak", OpenAI VAD guide). Live
#: interviews (Track H, 2026-11-20, 48 replies): 14 of the 16 replies over 3 s were
#: slow because the end of speech was still being decided, up to 9.2 s, while the
#: model's own part was 0.7 s median. Measured on spoken answers streamed in real
#: time, 2 trials each: a complete answer was decided in 1.3-1.6 s at both settings;
#: a 3 s "um" pause mid-sentence ended the turn at neither; a trailing "and also..."
#: pause ended it 0.4 s in at BOTH (unchanged by this); the worst wait fell from 8.8 s
#: to 5.0 s. So "medium": no added interruption seen, the long waits halved.
#: REALTIME_EAGERNESS=low restores the old setting.
DEFAULT_EAGERNESS = "medium"
#: Thinking before speaking is latency the candidate hears as dead air.
#: CLAUDE.md forbids a reasoning model on the live path; this model always
#: reasons, so it runs at the lowest effort that is accepted. Smoke check on
#: 2026-11-13: "minimal" accepted, text replies 0.72-0.87s against 0.80-1.02s
#: at "low".
DEFAULT_REASONING_EFFORT = "minimal"
#: Candidates sit at a laptop, not in a headset. OpenAI: near_field for
#: close-talking microphones, far_field for laptops and meeting rooms.
DEFAULT_NOISE_REDUCTION = "far_field"
TRANSCRIPTION_MODEL = DEFAULT_REALTIME_TRANSCRIPTION_MODEL

#: The only OpenAI error code this service downgrades from fatal to a warning.
NON_FATAL_ERROR_CODE = "item_delete_invalid_item_id"


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def realtime_model() -> str:
    return _realtime_model_setting()


def _transcription() -> dict:
    """Unset delay sends nothing, so OpenAI's default applies (F-036: accepted
    values are validated in bot/config.py)."""
    settings = {"model": realtime_transcription_model()}
    delay = realtime_transcription_delay()
    if delay:
        settings["delay"] = delay
    return settings


def realtime_session_dict(
    *,
    instructions: str,
    model: str | None = None,
    tools: list[dict] | None = None,
) -> dict:
    """The session config as OpenAI receives it. Shared with the smoke check."""
    from pipecat.adapters.services.open_ai_realtime_adapter import (
        OpenAIRealtimeLLMAdapter,
    )

    from bot.tools import TOOLS

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
                "transcription": _transcription(),
            },
            "output": {"voice": _env("REALTIME_VOICE", DEFAULT_VOICE)},
        },
        "reasoning": {"effort": _env("REALTIME_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)},
        "tools": tools,
        "tool_choice": "auto",
    }


class _Transcription(events.InputAudioTranscription):
    """Pipecat 1.6.0's transcription settings plus `delay`.

    Pipecat's class has no `delay` field and its constructor rejects one, so
    the setting would otherwise crash the bot at start-up. Same fields, one more;
    None is dropped on the wire (exclude_none), which keeps OpenAI's default.
    """

    delay: str | None = None

    def __init__(self, model=None, language=None, prompt=None, delay=None):
        super().__init__(model=model, language=language, prompt=prompt)
        self.delay = delay


class MitraRealtimeService(OpenAIRealtimeLLMService):
    """Pipecat's Realtime service plus the two things MITRA needs from it.

    1. **Resampling.** The pipeline listens at 16 kHz because Silero VAD, which
       we keep for measuring true end of speech, only runs at 8 or 16 kHz.
       OpenAI only accepts 24 kHz. Upsampling speech that was captured for a
       16 kHz path loses nothing a transcriber or a voice model uses.
    2. **Notes.** Pipecat's `LLMMessagesAppendFrame` is not implemented for
       Realtime (it logs "NEED TO IMPLEMENT MESSAGES APPEND"), so the
       coordinator inserts and deletes its notes through raw client events.
    3. **The timeline** (task 19). Pipecat drops the item ids and audio times
       OpenAI sends (F-064). Each handler below records them in `timeline`
       (in memory, a few dictionary writes) and then runs Pipecat's own
       handler unchanged. Nothing is awaited for it; the read-back of a cut
       reply runs as its own task (`_schedule_readback`).
    4. **What each reply contained** (the ending fix). `reply_of_call` maps a
       function call to the reply (response id) that carried it, and
       `reply_spoke` records, per finished reply, whether the server says it had
       speech. The coordinator reads both to decide whether code must speak the
       goodbye: the model ends with a reply that is only a function call
       (track_i_notes A-22, A-24). Pipecat runs function handlers in background
       tasks, so a handler can run before or after its reply's response.done;
       keying by response id makes the decision order-independent.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._resampler = create_stream_resampler()
        #: Counts OpenAI connections actually opened. Conversation item ids
        #: (the coordinator's notes) belong to one connection; a reader that
        #: sees this change knows its ids are gone.
        self.connection_generation = 0
        self.timeline = RealtimeTimeline()
        self._readbacks: set[asyncio.Task] = set()
        #: call_id -> response_id of the reply that carried it.
        self.reply_of_call: dict[str, str] = {}
        #: response_id -> True if that reply had speech (a message item, or audio
        #: tokens), False if it was only function calls. Recent replies only.
        self.reply_spoke: dict[str, bool] = {}
        #: response_id -> what that reply said (its message items' transcript).
        self.reply_text: dict[str, str] = {}

    async def _connect(self):
        before = self._websocket
        await super()._connect()
        if self._websocket is not None and self._websocket is not before:
            self.connection_generation += 1
            # A new connection restarts OpenAI's audio clock at zero.
            self.timeline.new_connection(self.connection_generation)

    # -- task 19: record ids and times in handlers that already run ----------------------

    def set_audio_input_paused(self, paused: bool):
        self.timeline.on_pause(paused, time.time())
        super().set_audio_input_paused(paused)

    async def _handle_evt_speech_started(self, evt):
        self.timeline.speech_started(evt.item_id, evt.audio_start_ms)
        await super()._handle_evt_speech_started(evt)

    async def _handle_evt_speech_stopped(self, evt):
        self.timeline.speech_stopped(evt.item_id, evt.audio_end_ms)
        await super()._handle_evt_speech_stopped(evt)

    async def handle_evt_input_audio_transcription_completed(self, evt):
        self.timeline.transcription_completed(evt.item_id, evt.transcript)
        await super().handle_evt_input_audio_transcription_completed(evt)

    async def _handle_evt_conversation_item_added(self, evt):
        if evt.item.role == "assistant":
            self.timeline.assistant_added(evt.item.id, getattr(evt, "previous_item_id", None))
        await super()._handle_evt_conversation_item_added(evt)

    async def _handle_evt_audio_delta(self, evt):
        # Length of the decoded audio without decoding it a second time.
        delta = evt.delta or ""
        nbytes = len(delta) * 3 // 4 - delta[-2:].count("=")
        self.timeline.assistant_audio(evt.item_id, nbytes, time.time())
        await super()._handle_evt_audio_delta(evt)

    async def _handle_evt_audio_transcript_delta(self, evt):
        self.timeline.assistant_text(evt.item_id, evt.delta, time.time())
        await super()._handle_evt_audio_transcript_delta(evt)

    # -- the ending fix: which reply carried a call, and whether it spoke ----------------

    async def _handle_evt_function_call_arguments_done(self, evt):
        self.reply_of_call[evt.call_id] = evt.response_id
        await super()._handle_evt_function_call_arguments_done(evt)

    async def _handle_evt_response_done(self, evt):
        # Recorded BEFORE Pipecat's handler pushes LLMFullResponseEndFrame, so a
        # coordinator deciding on that frame already sees this reply.
        response = evt.response
        self.reply_spoke[response.id] = reply_had_speech(response)
        self.reply_text[response.id] = reply_said(response)
        for record in (self.reply_spoke, self.reply_text):
            if len(record) > 64:
                record.pop(next(iter(record)))
        await super()._handle_evt_response_done(evt)

    async def cancel_reply(self) -> None:
        """Cancel the reply being generated (the forced end's cap, coordinator)."""
        await self.send_client_event(events.ResponseCancelEvent())

    def _schedule_readback(self, item_id: str) -> None:
        """Start the read-back of a cut reply as its own task. Never awaited here:
        `retrieve_conversation_item` is answered by the receive loop, so awaiting
        it inside a handler would stop the loop (and every frame) until it timed out."""
        task = asyncio.create_task(self._read_back(item_id))
        self._readbacks.add(task)
        task.add_done_callback(self._readbacks.discard)

    async def _read_back(self, item_id: str) -> None:
        """What OpenAI kept of a truncated reply: the words the candidate heard.

        On failure or timeout nothing is recorded, and the save falls back to the
        proportional cut (RealtimeTimeline.AssistantItem.heard).
        """
        try:
            item = await asyncio.wait_for(
                self.retrieve_conversation_item(item_id), READBACK_TIMEOUT_SECONDS
            )
            heard = " ".join(
                c.transcript for c in (item.content or []) if getattr(c, "transcript", None)
            ).strip()
            if heard:
                self.timeline.heard(item_id, heard, "readback")
        except Exception as exc:  # noqa: BLE001  (timeout, error event, closed socket)
            self._retrieve_conversation_item_futures.pop(item_id, None)
            logger.warning(f"{self} read-back of cut reply {item_id} failed ({exc!r}); "
                           f"the proportional cut will be used")

    async def send_client_event(self, event) -> None:
        """Pipecat's send, but serialising subclasses as themselves.

        Pydantic serialises a field by its declared type, which drops
        `_Transcription.delay` (and would drop any field a subclass adds).
        Identical output for every event that has no subclassed part.

        Task 19: a truncate (Pipecat's own, sent when the candidate speaks over
        a reply) is recorded with its exact audio_end_ms and time, and the
        read-back of what was heard is scheduled, not awaited.
        """
        if isinstance(event, events.ConversationItemTruncateEvent):
            self.timeline.truncated(event.item_id, event.audio_end_ms, time.time())
            self._schedule_readback(event.item_id)
        await self._ws_send(event.model_dump(exclude_none=True, serialize_as_any=True))

    async def update_instructions(self, instructions: str) -> None:
        """Replace the session's instructions mid-call (decision 22: area swaps).

        Sends a MINIMAL session.update (`type` and `instructions` only), so
        nothing else (turn detection, tools, voice) is re-sent mid-conversation.
        Both a full and a minimal update were accepted after audio had been
        produced (A_findings A-01); the minimal one changes only what it means
        to. Pipecat's own settings are kept in step, so any later full update
        it sends (for example on a reconnect) carries the same instructions.
        The coordinator calls this between turns, never while a reply is live.
        """
        self._settings.system_instruction = instructions
        properties = self._settings.session_properties
        if properties is not None and hasattr(properties, "instructions"):
            properties.instructions = instructions
        await self.send_client_event(events.SessionUpdateEvent(
            session=events.SessionProperties(instructions=instructions)))

    async def _maybe_handle_evt_retrieve_conversation_item_error(self, evt) -> bool:
        """Pipecat's one hook into its error handling, used for one more code.

        Pipecat treats every OpenAI `error` event as fatal and stops reading the
        connection (pipecat/services/openai/realtime/llm.py:864-874), except
        two codes and whatever this hook claims. Deleting a note whose item no
        longer exists (`item_delete_invalid_item_id`, observed in F-030) is
        harmless, so it is logged and the session carries on. Every other
        error keeps Pipecat's handling.
        """
        if getattr(evt.error, "code", None) == NON_FATAL_ERROR_CODE:
            logger.warning(f"{self} ignoring non-fatal OpenAI error: {evt.error.message}")
            return True
        return await super()._maybe_handle_evt_retrieve_conversation_item_error(evt)

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
        # Exactly the audio OpenAI's clock counts (paused input never gets here).
        self.timeline.on_append(len(frame.audio), time.time())
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

    async def request_reply(self) -> None:
        """Ask for one spoken reply, with NO per-reply instructions.

        Round 14: per-reply instructions replace the session's for that reply, so
        anything code asked for that way was said without Mitra's own rules. Code now
        tells her what happened with a private note (insert_note) and asks for a
        reply; her permanent instructions apply to every word.
        """
        await self.send_client_event(
            events.ResponseCreateEvent(response=events.ResponseProperties(output_modalities=["audio"]))
        )


def reply_had_speech(response) -> bool:
    """The server's record of one reply: True if it had a message item or any
    output audio. A reply that is only function calls is False (A-24: output
    [function_call], audio_tokens 0)."""
    if any(getattr(item, "type", None) == "message" for item in (response.output or [])):
        return True
    usage = getattr(response, "usage", None)
    details = getattr(usage, "output_token_details", None) if usage else None
    return bool(details and (getattr(details, "audio_tokens", 0) or 0) > 0)


def reply_said(response) -> str:
    """The words of a reply's message items (audio transcript, or text)."""
    words = []
    for item in response.output or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            said = getattr(part, "transcript", None) or getattr(part, "text", None)
            if said:
                words.append(said)
    return " ".join(words).strip()


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
                transcription=_Transcription(**audio_in["transcription"]),
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
