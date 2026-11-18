"""The realtime engine: one OpenAI Realtime model instead of STT -> LLM -> TTS.

Selected with `INTERVIEW_ENGINE=realtime`. The default (`pipeline`) is the
engine on `main`, untouched. Step 1 of docs/REALTIME_ARCHITECTURE.md.

    Daily in -> [Silero VAD, measurement only] -> user aggregator
             -> OpenAI Realtime (hears, decides, speaks) -> Daily out

What carries over unchanged: the Daily transport, presence, the silence ladder
(spoken through the realtime model instead of TTS), the transcript and latency
observers, persistence and feedback scoring.

What is different and why:
- **Turn-taking is OpenAI's** (`semantic_vad`, eagerness low). Our Silero VAD
  still runs, but only so the latency observer can measure true end of speech
  the same way as on the pipeline engine. Pipecat runs realtime services with
  external turn strategies, so local VAD frames never start or end a turn.
- **The brain does not drive yet.** The model has the whole plan in one prompt
  (`bot/realtime_prompt.py`) and the coordinator keeps it on time with notes
  (`bot/realtime_coordinator.py`). The brain and judge return as the supervisor
  in step 3.
- **The candidate transcript comes from OpenAI's own transcription.** Step 3
  moves the hiring record to an independent Deepgram transcript.
"""

import asyncio

from loguru import logger
from pipecat.frames.frames import LLMRunFrame, MetricsFrame
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.workers.runner import WorkerRunner

from bot.blueprint_source import resolve_blueprint
from bot.brain.brain import InterviewBrain
from bot.config import SESSIONS_DIR, Settings
from bot.greeting import time_of_day
from bot.observers import TranscriptObserver, TurnLatencyObserver, write_session_artifacts
from bot.persistence import build_transcript, save_interview_result
from bot.presence import RoomPresence, attach as attach_presence
from bot.realtime_coordinator import REALTIME_TOOLS, RealtimeCoordinator
from bot.realtime_prompt import build_realtime_instruction
from bot.services.daily import build_transport
from bot.services.realtime import build_realtime_llm, realtime_session_dict
from bot.silence import SilenceEscalation
from bot.turn_taking import build_vad_analyzer
from shared.branding import BOT_NAME

#: Same reason and value as run_bot.GREETING_SETTLE_SECONDS: anything spoken
#: before the candidate's browser has subscribed to our track is lost.
GREETING_SETTLE_SECONDS = 1.0
IDLE_TIMEOUT_SECS = 300.0

#: OpenAI list prices, USD per 1M tokens, gpt-realtime-2 and 2.1 (OpenAI model
#: page, read 2026-11-13). An estimate until checked against the invoice.
PRICE_PER_MILLION = {
    "audio_in": 32.0,
    "audio_in_cached": 0.40,
    "text_in": 4.0,
    "text_in_cached": 0.40,
    "audio_out": 64.0,
    "text_out": 24.0,
}


class RealtimeUsageObserver(BaseObserver):
    """Adds up token usage per response, so cost is measured, not estimated.

    `main` has never had cost telemetry. The realtime engine reports usage on
    every `response.done`, so this one is nearly free.
    """

    def __init__(self):
        super().__init__()
        self._seen: set = set()
        self.totals = {
            "responses": 0,
            "input_tokens": 0,
            "input_audio_tokens": 0,
            "cached_input_tokens": 0,
            "cached_input_audio_tokens": 0,
            "output_tokens": 0,
            "output_audio_tokens": 0,
        }

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if not isinstance(frame, MetricsFrame) or frame.id in self._seen:
            return
        self._seen.add(frame.id)
        for item in frame.data:
            if not isinstance(item, LLMUsageMetricsData):
                continue
            usage = item.value
            t = self.totals
            t["responses"] += 1
            t["input_tokens"] += usage.prompt_tokens or 0
            t["input_audio_tokens"] += usage.input_audio_tokens or 0
            t["cached_input_tokens"] += usage.cache_read_input_tokens or 0
            t["cached_input_audio_tokens"] += usage.cache_read_input_audio_tokens or 0
            t["output_tokens"] += usage.completion_tokens or 0
            t["output_audio_tokens"] += usage.output_audio_tokens or 0

    def summary(self, minutes: float) -> dict:
        t = self.totals
        cached_audio = t["cached_input_audio_tokens"]
        cached_text = max(t["cached_input_tokens"] - cached_audio, 0)
        audio_in = max(t["input_audio_tokens"] - cached_audio, 0)
        text_in = max(t["input_tokens"] - t["input_audio_tokens"] - cached_text, 0)
        audio_out = t["output_audio_tokens"]
        text_out = max(t["output_tokens"] - audio_out, 0)
        p = PRICE_PER_MILLION
        usd = (
            audio_in * p["audio_in"]
            + cached_audio * p["audio_in_cached"]
            + text_in * p["text_in"]
            + cached_text * p["text_in_cached"]
            + audio_out * p["audio_out"]
            + text_out * p["text_out"]
        ) / 1_000_000
        return {
            **t,
            "estimated_usd": round(usd, 4),
            "minutes": round(minutes, 2),
            "estimated_usd_per_minute": round(usd / minutes, 4) if minutes > 0 else None,
        }


async def run_realtime_bot(
    *,
    room_url: str,
    token: str | None,
    session_id: str,
    blueprint_id: str | None = None,
    interview_id: str | None = None,
    timezone: str | None = None,
) -> None:
    settings = Settings.load()
    blueprint = await resolve_blueprint(blueprint_id=blueprint_id)
    logger.info(
        f"[{session_id}] session starting | engine=realtime | room={room_url} | "
        f"role={blueprint.role_title} | blueprint={blueprint.blueprint_id}"
    )

    # Only for the opening line, which is written in code rather than
    # generated (see InterviewBrain.opening_line). It does not drive anything.
    opening_line = InterviewBrain(blueprint, time_of_day=time_of_day(timezone)).opening_line()
    instructions = build_realtime_instruction(blueprint, opening_line=opening_line)

    transport = build_transport(room_url=room_url, token=token, bot_name=BOT_NAME)
    llm = build_realtime_llm(
        api_key=settings.openai_api_key, instructions=instructions, tools=REALTIME_TOOLS
    )

    coordinator = RealtimeCoordinator(blueprint=blueprint, llm=llm, session_id=session_id)
    coordinator.register_tools()

    presence = attach_presence(transport, RoomPresence())

    def exactly(text: str) -> str:
        return f'Say exactly this, word for word, and nothing else: "{text}"'

    silence = SilenceEscalation(
        session_id=session_id,
        presence=presence,
        interview_over=lambda: coordinator.is_over,
        speak=lambda text: llm.say(exactly(text)),
        prompt=llm.say,
        close=lambda text, reason: coordinator.close_with(exactly(text), reason),
    )

    context = LLMContext()
    context.set_tools(REALTIME_TOOLS)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            # No vad_analyzer and no turn strategies: Pipecat detects the
            # realtime service and lets OpenAI's turn detection drive.
            user_idle_timeout=silence.initial_timeout,
        ),
    )

    transcript_observer = TranscriptObserver()
    latency_observer = TurnLatencyObserver()
    usage_observer = RealtimeUsageObserver()

    pipeline = Pipeline(
        [
            transport.input(),
            # Measurement only: gives the latency observer the true end of
            # speech, comparable with the pipeline engine's numbers.
            VADProcessor(vad_analyzer=build_vad_analyzer()),
            user_aggregator,
            llm,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[transcript_observer, latency_observer, usage_observer, silence, coordinator],
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        conversation_id=session_id,
    )
    silence.attach(worker)
    coordinator.attach(worker)

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await silence.handle_idle(aggregator)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"[{session_id}] candidate connected")
        # A fresh OpenAI connection, opened now rather than when the bot
        # started. The bot joins first and the candidate may take minutes to
        # follow; found in testing, a connection left idle for ninety seconds
        # was dead by the time the greeting was requested ("no close frame
        # received or sent"), and the interviewer never spoke. Reconnecting
        # overlaps the settle wait below, so it costs the candidate nothing.
        await asyncio.gather(
            llm.reconnect(), asyncio.sleep(GREETING_SETTLE_SECONDS)
        )
        coordinator.start()
        # The first response. The prompt tells the model its first words,
        # word for word.
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"[{session_id}] candidate disconnected, ending session")
        await worker.cancel()

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker, frame):
        logger.error(f"[{session_id}] pipeline error: {frame}")

    runner = WorkerRunner(handle_sigint=True)
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        await coordinator.stop()
        logger.info(f"[{session_id}] session ended ({coordinator.reason})")

        minutes = coordinator.elapsed_seconds / 60
        config = realtime_session_dict(instructions="", tools=[])
        engine = {
            "engine": "realtime",
            "realtime_model": config["model"],
            "voice": config["audio"]["output"]["voice"],
            "eagerness": config["audio"]["input"]["turn_detection"]["eagerness"],
            "reasoning_effort": config["reasoning"]["effort"],
            "noise_reduction": config["audio"]["input"]["noise_reduction"]["type"],
            "usage": usage_observer.summary(minutes),
            "coordinator_events": coordinator.events,
            "ended_because": coordinator.reason,
        }

        if interview_id:
            await save_interview_result(
                interview_id=interview_id,
                transcript=build_transcript(
                    interview_id=interview_id,
                    turns=transcript_observer.turns,
                    duration_seconds=coordinator.elapsed_seconds,
                ),
                section_outcomes=[],
                session_metrics={
                    "latency_summary": latency_observer.summary(),
                    "turns": [t.to_dict() for t in latency_observer.turns],
                    "silence_events": silence.events,
                    **presence.summary(),
                    "llm_model": config["model"],
                    "stt_provider": "openai-realtime",
                    **engine,
                },
            )

        write_session_artifacts(
            directory=SESSIONS_DIR,
            session_id=session_id,
            transcript=transcript_observer,
            latency=latency_observer,
            blueprint_id=blueprint.blueprint_id,
            role_title=blueprint.role_title,
            silence_events=silence.events,
            extra=engine,
        )
