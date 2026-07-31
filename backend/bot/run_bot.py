"""Bot process entrypoint: joins a Daily room and conducts a spoken conversation.

One process per interview (CLAUDE.md). It joins the room, runs the voice
pipeline for the session, writes the transcript on exit, and terminates.

Run directly for local testing, though normally `scripts/dev_interview.py`
creates the room and spawns this:

    uv run python -m bot.run_bot --room-url https://you.daily.co/abc123
"""

import argparse
import asyncio
import os
import sys
import uuid

from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.workers.runner import WorkerRunner

from bot.blueprint_source import BlueprintUnavailable, resolve_blueprint
from bot.brain.brain import InterviewBrain
from bot.brain.drivers import OpenAIJudge
from bot.brain_director import BrainDirector
from bot.config import SESSIONS_DIR, MissingConfig, Settings
from bot.observers import (
    TranscriptObserver,
    TurnLatencyObserver,
    write_session_artifacts,
)
from bot.persistence import build_transcript, save_interview_result
from bot.persona import build_system_instruction
from bot.services.daily import build_transport
from bot.ending import SessionEnder
from bot.presence import RoomPresence, attach as attach_presence
from bot.silence import SilenceEscalation
from bot.services.llm import build_llm
from bot.services.stt import build_stt
from bot.services.tts import VoiceUnavailable, build_tts, verify_voice
from bot.turn_taking import build_turn_strategies, build_vad_analyzer

# The name the candidate sees in the call, kept in step with the spoken
# introduction — see shared/branding.py.
from bot.greeting import time_of_day  # noqa: E402
from shared.branding import BOT_NAME  # noqa: E402



# Cancel the session after this long with neither party speaking. A candidate
# gathering their thoughts must never trip this, so it sits far above any
# plausible pause; it exists only to reap a session someone abandoned.
IDLE_TIMEOUT_SECS = 300.0


async def run_bot(
    *,
    room_url: str,
    token: str | None,
    session_id: str,
    blueprint_id: str | None = None,
    interview_id: str | None = None,
    use_brain: bool = True,
    timezone: str | None = None,
) -> None:
    """Run one interview session to completion.

    `use_brain=False` runs the pre-wiring Milestone 1 path: one static
    whole-interview prompt, no sectioning, no director. It exists so the latency
    baseline can be measured with the brain wiring as the only variable — the
    STT finalisation tune landed at the same time, and without this the two
    changes are inseparable.
    """
    settings = Settings.load()

    # Milestone 1: a static fixture. Milestone 3: the generated blueprint from
    # Postgres. Everything downstream of this line is already blueprint-driven.
    blueprint = await resolve_blueprint(blueprint_id=blueprint_id)

    logger.info(
        f"[{session_id}] session starting | room={room_url} | "
        f"role={blueprint.role_title} | blueprint={blueprint.blueprint_id} | "
        f"candidate={blueprint.candidate_name or '(none — role-level fixture)'}"
    )

    # Fail before joining the room rather than at the first spoken word.
    voice_name = await verify_voice(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice_id,
    )
    logger.info(f"[{session_id}] voice verified: {voice_name}")

    transport = build_transport(room_url=room_url, token=token, bot_name=BOT_NAME)
    stt = build_stt(api_key=settings.openai_api_key)
    # The brain owns the interview: which section we are in, what the model sees,
    # when to move on. The system instruction below is only a starting value —
    # BrainDirector retargets it at the current section on every turn.
    brain = InterviewBrain(blueprint, time_of_day=time_of_day(timezone))

    llm = build_llm(
        api_key=settings.openai_api_key,
        system_instruction=(
            brain.plan_turn().system_instruction
            if use_brain
            else build_system_instruction(blueprint)
        ),
        model=settings.llm_model,
    )
    logger.info(
        f"[{session_id}] mode={'sectioned-brain' if use_brain else 'M1 static prompt'} "
        f"| llm={settings.llm_model}"
    )
    tts = build_tts(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice_id,
    )

    # Daily reports who is in the room; without listening, the ladder was
    # nudging empty rooms and then blaming the candidate's silence for it.
    presence = attach_presence(transport, RoomPresence())
    silence = SilenceEscalation(
        session_id=session_id,
        presence=presence,
        # The brain knows whether it just asked a pleasantry or asked someone to
        # recall a decision they regret. The ladder does not.
        patience=(lambda: brain.patience) if use_brain else None,
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=build_vad_analyzer(),
            user_turn_strategies=build_turn_strategies(),
            # Only arms after the bot finishes speaking and only while no user
            # turn is in progress, so it cannot fire during a thinking pause.
            user_idle_timeout=silence.initial_timeout,
        ),
    )

    director = (
        BrainDirector(
            brain=brain,
            llm=llm,
            judge=OpenAIJudge(
                api_key=settings.openai_api_key,
                # Off the critical path, so it gets the reasoning tier.
                model=settings.blueprint_model,
            ),
            session_id=session_id,
        )
        if use_brain
        else None
    )

    # Ends the call once the interview is over. An observer, not a pipeline
    # processor: it has to see BotStoppedSpeakingFrame, which comes from the
    # output transport, and a processor upstream of that never does.
    ender = SessionEnder(brain=brain if use_brain else None, session_id=session_id)
    transcript_observer = TranscriptObserver()
    latency_observer = TurnLatencyObserver()

    pipeline = Pipeline(
        [
            processor
            for processor in [
                transport.input(),
                stt,
                user_aggregator,
                # Sits between aggregation and inference: rewrites the context,
                # then lets it through. Generation still streams LLM -> TTS
                # untouched. Absent entirely in M1-baseline mode.
                director,
                llm,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
            if processor is not None
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            # Required for the smart-turn model to emit TurnMetricsData, which
            # the latency observer reads to report the model's confidence.
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[transcript_observer, latency_observer, silence, ender],
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        conversation_id=session_id,
    )
    silence.attach(worker)
    ender.attach(worker)

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await silence.handle_idle(aggregator)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"[{session_id}] candidate connected")
        context.add_message(
            {
                "role": "developer",
                "content": (
                    "The candidate has joined. Open the interview now, following "
                    "your opening instructions and naming the role."
                ),
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"[{session_id}] candidate disconnected — ending session")
        await worker.cancel()

    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker, frame):
        logger.error(f"[{session_id}] pipeline error: {frame}")

    runner = WorkerRunner(handle_sigint=True)
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        # Runs on clean exit, Ctrl-C, and crash alike: the transcript is the
        # auditable record and must survive whatever ended the session.
        logger.info(f"[{session_id}] session ended")

        # The database is the record of truth; the session file stays as a
        # local fallback copy in case the write below fails.
        if interview_id:
            await save_interview_result(
                interview_id=interview_id,
                transcript=build_transcript(
                    interview_id=interview_id,
                    turns=transcript_observer.turns,
                    duration_seconds=brain.elapsed_seconds if director else 0.0,
                ),
                section_outcomes=(
                    [o.model_dump(mode="json") for o in brain.outcomes()] if director else []
                ),
                session_metrics={
                    "latency_summary": latency_observer.summary(),
                    "turns": [t.to_dict() for t in latency_observer.turns],
                    "silence_events": silence.events,
                    **presence.summary(),
                    "brain_events": director.events if director else [],
                    # Counted by the brain, not inferred from the transcript:
                    # guessing at our own behaviour from our own words is the
                    # least reliable source available.
                    "repairs_requested": brain.repairs_requested if director else 0,
                    "llm_model": settings.llm_model,
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
            brain_events=director.events if director else [],
            section_outcomes=(
                [o.model_dump(mode="json") for o in brain.outcomes()] if director else []
            ),
        )


def configure_logging(level: str) -> None:
    """Quieten Pipecat's frame-level DEBUG chatter.

    At DEBUG the join URL and the turn metrics we actually care about scroll
    past in a wall of pipeline-linking noise. INFO keeps session events, voice
    verification, and the per-turn latency lines visible.

    Set LOG_LEVEL=DEBUG when diagnosing the pipeline itself.
    """
    logger.remove()
    logger.add(sys.stderr, level=level.upper())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one interview bot session.")
    parser.add_argument("--room-url", required=True, help="Daily room URL to join.")
    parser.add_argument("--token", default=None, help="Daily meeting token, if the room is private.")
    parser.add_argument(
        "--session-id",
        default=None,
        help="Identifier used in logs and artifact filenames. Generated if omitted.",
    )
    parser.add_argument(
        "--blueprint-id",
        default=None,
        help=(
            "Which interview to run: a fixture name (pm_senior, sre_staff) or a "
            "cand_... id for a generated, CV-specific blueprint. Defaults to the "
            "Senior PM fixture."
        ),
    )
    parser.add_argument(
        "--interview-id",
        default=None,
        help=(
            "Database id of the interview record. When given, the transcript and "
            "section outcomes are persisted to Postgres on exit."
        ),
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help=(
            "IANA timezone the candidate is in, e.g. Asia/Kolkata. Sent by their "
            "browser at join time so the opening greeting matches their clock "
            "rather than the server's."
        ),
    )
    parser.add_argument(
        "--no-brain",
        action="store_true",
        help=(
            "Run the pre-wiring Milestone 1 path: one static whole-interview "
            "prompt, no sectioning. Use this for the latency baseline."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Logging verbosity. Use DEBUG to see Pipecat frame internals.",
    )
    args = parser.parse_args()

    configure_logging(args.log_level)

    session_id = args.session_id or f"session-{uuid.uuid4().hex[:8]}"

    try:
        asyncio.run(
            run_bot(
                room_url=args.room_url,
                token=args.token,
                session_id=session_id,
                blueprint_id=args.blueprint_id,
                interview_id=args.interview_id,
                use_brain=not args.no_brain,
                timezone=args.timezone,
            )
        )
    except (MissingConfig, VoiceUnavailable, BlueprintUnavailable, FileNotFoundError) as exc:
        logger.error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info(f"[{session_id}] interrupted by user")


if __name__ == "__main__":
    main()
