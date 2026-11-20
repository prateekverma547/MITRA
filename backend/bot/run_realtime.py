"""The realtime engine: one OpenAI Realtime model instead of STT -> LLM -> TTS.

The default engine since 2026-11-18 (`bot/config.interview_engine`);
`INTERVIEW_ENGINE=pipeline` selects the fallback. Step 1 of
docs/REALTIME_ARCHITECTURE.md, plus what `dev` carried over from stg: candidate
captions (`bot/realtime_captions.py`), presence that knows the interview is
over, and the recording switch.

    Daily in -> [Silero VAD, measurement only] -> user aggregator
             -> OpenAI Realtime (hears, decides, speaks) -> Daily out

What carries over unchanged: the Daily transport, presence, the silence ladder
(spoken through the realtime model instead of TTS), the transcript and latency
observers, persistence and feedback scoring.

What is different and why:
- **Turn-taking is OpenAI's** (`semantic_vad`, eagerness medium). Our Silero VAD
  still runs, but only so the latency observer can measure true end of speech
  the same way as on the pipeline engine. Pipecat runs realtime services with
  external turn strategies, so local VAD frames never start or end a turn.
- **The plan, the coordinator and the transcript:** see docs/DEV_PLAN.md section 3.
"""

import asyncio
import os
import time

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

from bot.app_messages import is_candidate_end_request, is_end_request
from bot.auditor import RealtimeAuditor, make_turn_listener
from bot.blueprint_source import resolve_blueprint
from bot.booked_time import apply_booked_timing
from bot.capture import CandidateAudioCapture, capture_enabled, mitra_speaking_from
from bot.config import SESSIONS_DIR, Settings
from bot.greeting import time_of_day
from bot.live_events import LiveEvents, TurnEventObserver
from bot.observers import (
    TranscriptObserver,
    TurnLatencyObserver,
    write_session_artifacts,
)
from bot.persistence import (
    build_transcript,
    link_waits_to_turns,
    load_booked_timing,
    save_interview_result,
    write_transcript_zero,
)
from bot.presence import RoomPresence, candidate_user_id, participant_user_id
from bot.presence import attach as attach_presence
from bot.realtime_captions import RealtimeCaptions
from bot.realtime_coordinator import REALTIME_TOOLS, RealtimeCoordinator
from bot.realtime_prompt import build_area_instruction, choose_opening, first_name_of
from bot.rejoin import RejoinController
from bot.services.daily import build_transport, stop_recording
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
    # Joint A + C: run to the booked length (live/booking.py stores it), not the
    # blueprint's default. No booking value: the blueprint as it is.
    booking = await load_booked_timing(interview_id) if interview_id else None
    if booking:
        # Track H, 2026-11-20: the stored plan keeps every area; each booking (HR picks
        # 20-60 minutes, and a candidate may have several rounds) is sized here, to the
        # minutes it runs, with Session A's size_to_booking, then re-timed.
        from blueprint.generate import opening_minutes_for
        from blueprint.refine import size_to_booking

        if blueprint.opening_goals:
            # A plan with an exploratory opening (2026-11-20) gives it its share of the
            # booked minutes, not of the plan's default length. Before sizing, so the
            # areas that fit are counted against the opening this booking really has.
            blueprint = blueprint.model_copy(
                update={"opening_minutes": opening_minutes_for(booking["planned"])})
        blueprint = size_to_booking(blueprint, booking["planned"])
        blueprint = apply_booked_timing(
            blueprint, planned_minutes=booking["planned"], grace_minutes=booking["grace"])
        logger.info(f"[{session_id}] booked timing: {booking['planned']} min + "
                    f"{booking['grace']} grace (booked {booking['booked']})")
    logger.info(
        f"[{session_id}] session starting | engine=realtime | room={room_url} | "
        f"role={blueprint.role_title} | blueprint={blueprint.blueprint_id}"
    )

    # Round 14: her first turn is her own words. Her instructions carry the facts it
    # needs: the candidate's first name and the time of day where they are.
    first_name = first_name_of(blueprint)
    greeting_time = time_of_day(timezone)
    # Decision 22: the stable base plus only the current area, which starts as the
    # opening (area 1); she moves on with move_on and the coordinator swaps it.
    # A different way into the opening each interview, chosen once (it is part of the
    # stable base, so it never changes between areas).
    opening = choose_opening()
    logger.info(f"[{session_id}] opening: {opening.angle} (invite: {opening.invite})")
    instructions = build_area_instruction(blueprint, first_name=first_name, time_of_day=greeting_time, area=1,
                                          opening=opening)

    transport = build_transport(room_url=room_url, token=token, bot_name=BOT_NAME)
    llm = build_realtime_llm(
        api_key=settings.openai_api_key, instructions=instructions, tools=REALTIME_TOOLS
    )

    coordinator = RealtimeCoordinator(
        blueprint=blueprint, llm=llm, session_id=session_id,
        area_instruction=lambda area: build_area_instruction(
            blueprint, first_name=first_name, time_of_day=greeting_time, area=area, opening=opening),
    )
    coordinator.register_tools()

    # Told when the interview is over, as on the pipeline engine (stg): an
    # interview ends with the candidate leaving, and without this every
    # interview recorded that departure as a dropped call.
    # Only the candidate drives the interview (F-028 fix); see bot/presence.py.
    # The callbacks are defined below, once the worker exists.
    presence = attach_presence(
        transport,
        RoomPresence(
            interview_over=lambda: coordinator.is_over,
            candidate_mark=candidate_user_id(interview_id) if interview_id else None,
        ),
        on_candidate_arrived=lambda participant: start_interview(participant),
        on_candidate_gone=lambda departure: candidate_gone(departure),
        on_change=lambda change, participant, reason: participant_changed(change, participant, reason),
        on_candidate_rejoined=lambda participant: rejoin.on_candidate_joined((participant or {}).get("id")),
    )

    silence = SilenceEscalation(
        session_id=session_id,
        presence=presence,
        interview_over=lambda: coordinator.is_over,
        notify=lambda stage, seconds: coordinator.quiet_note(stage, seconds),
        close=lambda reason: coordinator.close_for_silence(reason),
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

    # Task 19: both observers read the service's timeline (ids and audio
    # times recorded in memory by its handlers), so turns can be stored in
    # spoken order and waits linked to them. Recording only; see
    # bot/realtime_timeline.py.
    transcript_observer = TranscriptObserver(timeline=llm.timeline)
    # The offset between transcript time and recording time, so a click on a
    # transcript line can seek the video. Same reasoning as run_bot.py.
    transcript_clock_zero = time.time()

    # Joint A + C: the live event stream (Session C's emitter; never blocks)
    # and the transcript's zero for the server's clock route. Interview time is
    # measured from the transcript's own zero.
    live = LiveEvents(interview_id, clock_zero=transcript_observer.clock_zero, blueprint=blueprint)
    await live.start()
    coordinator.attach_events(live)
    # Phase 3 wave 1: rejoin. A drop pauses the interview for the grace window; the
    # bot alone decides the counting and the ending (bot/rejoin.py, C-121, C-126).
    from live.drops import signals_after

    rejoin = RejoinController(coordinator=coordinator, presence=presence, live=live, interview_id=interview_id,
                              clock=lambda: live.at_seconds(time.time()),
                              signals=signals_after if interview_id else None)
    silence.on_event = lambda event: live.silence_prompt(event.stage, event.action, event.dead_air_seconds)
    # Task 23: the auditor labels each of Mitra's turns in the background (record
    # only, never steers, off the reply path). AUDITOR_ENABLED=0 turns it off.
    auditor = RealtimeAuditor(blueprint=blueprint, area_now=lambda: coordinator.current_area, live=live)
    auditor_on = os.environ.get("AUDITOR_ENABLED", "1").strip().lower() not in ("0", "false", "no")
    if auditor_on:
        auditor.start()
        coordinator.attach_auditor(auditor)

    turn_events = TurnEventObserver(events=live, timeline=llm.timeline,
                                    on_turn=make_turn_listener(auditor) if auditor_on else None)
    zero_task = (asyncio.create_task(write_transcript_zero(interview_id, transcript_observer.clock_zero))
                 if interview_id else None)
    latency_observer = TurnLatencyObserver(timeline=llm.timeline)
    usage_observer = RealtimeUsageObserver()
    # Sentence captions on the candidate page. Not RTVIObserver: see the module.
    captions = RealtimeCaptions(session_id=session_id)

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

    # Phase 3 wave 1 (D-58): the candidate's own track to a WAV for the after-call
    # checks. An observer that only queues (bounded, never waits); a writer thread
    # does the file work, so nothing here touches a reply.
    capture = None
    if interview_id and capture_enabled():
        from app.recordings import recordings_root

        capture = CandidateAudioCapture(interview_id=interview_id, root=recordings_root(),
                                        is_candidate=presence.is_candidate_connection,
                                        clock=lambda: live.at_seconds(time.time()))

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[
            transcript_observer,
            turn_events,
            latency_observer,
            usage_observer,
            silence,
            coordinator,
            captions,
            *([capture] if capture else []),
        ],
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        conversation_id=session_id,
    )
    silence.attach(worker)
    coordinator.attach(worker)
    captions.attach(worker)

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await silence.handle_idle(aggregator)

    # Recording, exactly as on the pipeline engine: off unless RECORDING_ENABLED
    # is set, started when the bot joins rather than when the candidate does,
    # and a failure to start never takes the interview down.
    recording = {"on": False}

    @transport.event_handler("on_joined")
    async def on_joined(transport, data):
        from bot.run_bot import begin_recording

        recording["on"] = await begin_recording(
            transport,
            interview_id=interview_id,
            session_id=session_id,
            clock_zero=transcript_clock_zero,
        )

    async def start_interview(participant):
        """The candidate's first connection only (bot/presence.py attach)."""
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
        rejoin.start()
        live.interview_started(booking["booked"] if booking else blueprint.evaluation_spec.duration_minutes)
        # The first response. The prompt tells the model its first words,
        # word for word.
        await worker.queue_frames([LLMRunFrame()])

    async def candidate_gone(departure):
        """The last candidate connection left. Watchers never get here.

        Phase 3 wave 1: mid-interview this is a drop, not the end. The rejoin grace
        window starts (bot/rejoin.py); the session ends only if they are not back in
        time, or by the rules there."""
        if departure.interview_over:
            logger.info(f"[{session_id}] candidate left after the interview ended")
            await worker.cancel()
        else:
            logger.info(f"[{session_id}] candidate dropped mid-interview; grace window started")
            await rejoin.on_candidate_left(departure.participant_id or None)

    async def participant_changed(change, participant, reason):
        """Participants the bot can see, as live events (hidden watchers are not seen, C-53)."""
        info = participant or {}
        if info.get("local") or (info.get("info") or {}).get("isLocal"):
            return
        mark = candidate_user_id(interview_id) if interview_id else None
        role = "candidate" if mark and participant_user_id(info) == mark else "watcher"
        live.participant(change, role, str(info.get("id") or ""), reason or None)

    @transport.event_handler("on_app_message")
    async def on_app_message(transport, message, sender):
        """The End button (Session B's app): accepted only from this interview's candidate."""
        try:
            participants = transport.participants()
        except Exception:  # noqa: BLE001
            participants = {}
        if is_candidate_end_request(message, sender, participants, interview_id):
            logger.info(f"[{session_id}] the candidate confirmed End in the app")
            await coordinator.end_by_candidate_button()
        elif is_end_request(message):
            logger.warning(f"[{session_id}] End request ignored: not from this interview's candidate")

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker, frame):
        logger.error(f"[{session_id}] pipeline error: {frame}")
        live.error("pipeline", "pipeline_error", str(getattr(frame, "error", frame)), fatal=False)

    runner = WorkerRunner(handle_sigint=True)
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        await rejoin.close()
        await coordinator.stop()
        logger.info(f"[{session_id}] session ended ({coordinator.reason})")

        if recording["on"]:
            problem = await stop_recording(transport)
            if problem:
                logger.warning(
                    f"[{session_id}] {problem} Daily closes it when the room "
                    f"empties, so the file should still arrive."
                )

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
            # How it ended: model_silent_code_goodbye | model_spoke |
            # forced_at_hard_stop | withdrawal | ..., what hung up
            # (bot_stopped_speaking | silent_timer | goodbye_timeout), and skips.
            # Round 16: how many plan questions Mitra was shown as a written goal, and how
            # many in the fallback form (no goal in the plan).
            "plan_goals": {"written": sum(1 for p in blueprint.competency_plans for i in range(len(p.seed_questions))
                                          if p.goal_of(i)),
                           "fallback": sum(1 for p in blueprint.competency_plans for i in range(len(p.seed_questions))
                                           if not p.goal_of(i))},
            # Codes only (round 15): the pages word each ending from these codes, with
            # Session B's sentences (ui/src/report/endingWords.ts), the one source.
            "ending": coordinator.ending,
            # Phase 3 wave 1: every drop and return (ConnectionRecord), and the
            # controller's own log, for the report and Check Intelligence.
            "connection": rejoin.record(),
            "rejoin_events": rejoin.events,
        }

        transcript = build_transcript(
            interview_id=interview_id or session_id,
            turns=transcript_observer.turns,
            duration_seconds=coordinator.elapsed_seconds,
            timeline=llm.timeline,
            clock_zero=transcript_observer.clock_zero,
            silero_starts=transcript_observer.silero_starts,
            silero_ends=transcript_observer.silero_ends,
            plan_started_wall=coordinator.plan_started_wall,
        )
        engine["timeline"] = llm.timeline.summary(transcript_observer.clock_zero)

        # The candidate WAV is closed, then the manifest written last and atomically,
        # with Mitra's speaking times (D-58). A failure here never touches the record.
        if capture is not None:
            try:
                await asyncio.to_thread(capture.close)
                capture.write_manifest(mitra_speaking_from(transcript))
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[{session_id}] capture manifest not written: {exc!r}")
            engine["capture"] = capture.summary()

        # The last live events, then the emitter's counts into session_metrics.
        turn_events.flush_replies()
        # The auditor's pending labels (bounded, FINISH_SECONDS), then its
        # per-section results for the report. A failure leaves the rest intact.
        section_outcomes: list[dict] = []
        if auditor_on:
            engine["auditor"] = await auditor.finish()
            try:
                section_outcomes = auditor.section_outcomes()
            except Exception as exc:  # noqa: BLE001
                engine["auditor"]["status"] = f"failed: {exc!r}"
                logger.error(f"[{session_id}] auditor results not built: {exc!r}")
        else:
            engine["auditor"] = {"status": "disabled"}
        live.reply_waits([t.to_dict() for t in latency_observer.turns])
        live.cost(engine["usage"], config["model"])
        live.interview_ended(coordinator.reason, coordinator.elapsed_seconds,
                             ending=(coordinator.ending or {}).get("ending"),
                             hang_up_by=(coordinator.ending or {}).get("hang_up_by"))  # C-A2
        mismatches = sum(1 for t in transcript.turns
                         if t.item_id in live.turn_index_of_item and live.turn_index_of_item[t.item_id] != t.index)
        engine["live_events"] = {**(await live.close()), "turn_index_mismatches": mismatches}
        engine["transcript_zero_stored"] = bool(zero_task and await zero_task)
        if interview_id:
            await save_interview_result(
                interview_id=interview_id,
                transcript=transcript,
                section_outcomes=section_outcomes,
                session_metrics={
                    "latency_summary": latency_observer.summary(),
                    # Each wait keyed by transcript turn index (F-060).
                    "turns": link_waits_to_turns(
                        [t.to_dict() for t in latency_observer.turns], transcript
                    ),
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
