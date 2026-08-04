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
import time
import uuid

from loguru import logger
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import (
    RTVIObserver,
    RTVIObserverParams,
    RTVIProcessor,
)
from pipecat.utils.text.base_text_aggregator import AggregationType
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
from bot.persistence import (
    build_transcript,
    record_recording_failed,
    record_recording_started,
    save_interview_result,
)
from bot.persona import build_system_instruction
from bot.services.daily import build_transport, start_recording, stop_recording
from bot.ending import SessionEnder
from bot.tools import TOOLS, register as register_tools
from bot.presence import RoomPresence, attach as attach_presence
from bot.silence import SilenceEscalation
from bot.services.llm import build_llm
from bot.services.stt import build_stt, stt_provider
from bot.services.tts import VoiceUnavailable, build_tts, verify_voice
from bot.turn_taking import build_turn_strategies, build_vad_analyzer

# The name the candidate sees in the call, kept in step with the spoken
# introduction — see shared/branding.py.
from bot.greeting import time_of_day  # noqa: E402
from shared.branding import BOT_NAME  # noqa: E402



# How long to let the audio path settle before the first word.
#
# `on_client_connected` fires the instant a participant joins, which is before
# their browser has finished subscribing to our audio track. Anything spoken in
# that window is not carried: reported live as the greeting starting mid-word,
# with "Good" missing from "Good morning".
#
# This was invisible while the greeting went through the model, because the
# round trip took a second or two and the path settled inside it. Speaking
# immediately removed that accidental delay and exposed the gap, so here it is
# deliberately, at a fraction of the size.
#
# Daily exposes no "ready to receive" event, so the value is empirical, and it
# errs long on purpose. Losing the first word is a far worse first impression
# than waiting another fraction of a second, and the path this replaced took
# roughly seven seconds to produce the same sentence. If a first word ever
# goes missing again, raise this before looking anywhere else.
GREETING_SETTLE_SECONDS = 1.0

# Cancel the session after this long with neither party speaking. A candidate
# gathering their thoughts must never trip this, so it sits far above any
# plausible pause; it exists only to reap a session someone abandoned.
IDLE_TIMEOUT_SECS = 300.0


async def begin_recording(
    transport,
    *,
    interview_id: str | None,
    session_id: str,
    clock_zero: float,
) -> bool:
    """Turn the recording on and note that it is running. Returns whether it is.

    **A recording that will not start must not take the interview down.** The
    interview is the product and the recording is evidence about it: a candidate
    who gave forty minutes and got no report because a video service was
    unavailable has lost far more than a reviewer who has to read rather than
    watch. So every failure ends up as a logged line and a reason on the record,
    and this returns False.

    It is a function rather than the body of the event handler so that the
    failure path can actually be tested. An interview surviving a broken
    recording is a claim, and this codebase has a history of claims of that shape
    turning out to be untrue.
    """
    failure = await start_recording(transport)
    if failure:
        logger.error(f"[{session_id}] NOT RECORDING: {failure}")
        if interview_id:
            await record_recording_failed(interview_id=interview_id, reason=failure)
        return False

    offset = time.time() - clock_zero
    logger.info(f"[{session_id}] recording started ({offset:.1f}s into the session)")
    if interview_id:
        # Written now, mid-session, not at the end. A bot that is killed never
        # reaches its `finally` block, and a recording nobody knows to collect is
        # also one nobody deletes.
        await record_recording_started(interview_id=interview_id, offset_seconds=offset)
    return True


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
        f"| llm={settings.llm_model} | stt={stt_provider()}"
    )
    tts = build_tts(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice_id,
    )

    # Daily reports who is in the room; without listening, the ladder was
    # nudging empty rooms and then blaming the candidate's silence for it.
    #
    # It is told when the interview is over for the same reason the ladder is:
    # an interview ends with the candidate leaving, so counting that departure
    # recorded a dropped call on every interview that ever ran.
    presence = attach_presence(
        transport,
        RoomPresence(
            interview_over=(lambda: brain.is_finished or brain.withdrew)
            if use_brain
            else None
        ),
    )
    silence = SilenceEscalation(
        session_id=session_id,
        presence=presence,
        # The brain knows whether it just asked a pleasantry or asked someone to
        # recall a decision they regret. The ladder does not.
        patience=(lambda: brain.patience) if use_brain else None,
        # Backstop. If the interview is over and the room has gone quiet,
        # end it rather than asking whether they are still there.
        interview_over=(lambda: brain.is_finished or brain.withdrew) if use_brain else None,
    )

    # The interviewer can end the interview itself. A phrase list only ever
    # contains what somebody thought of in advance, and measured against
    # sixteen natural ways of asking to stop it caught none of them. The model
    # reading those sentences understands every one; this gives it a way to act
    # rather than leaving the understanding trapped in prose.
    #
    # Not a second model and not a second request: the same inference call the
    # interviewer was already making, plus a few tokens of schema.
    if use_brain:
        register_tools(llm, brain)

    context = LLMContext()
    if use_brain:
        context.set_tools(TOOLS)
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

    # Captions for the candidate, and ONLY of what the interviewer says.
    #
    # A candidate who is deaf or hard of hearing cannot otherwise take this
    # interview at all, and when the audio is rough, reading the question is the
    # difference between answering it and asking for it again.
    #
    # **Their own words are deliberately never sent**, and this is a product
    # decision rather than an unfinished half. Nobody needs to read what they
    # just said, watching your own speech appear while you are still thinking is
    # a distraction in the one place this product works hardest not to create
    # one, and our transcripts still fragment under some conditions: showing a
    # candidate their own answer breaking up mid-sentence would be unkind and
    # would tell them nothing they can act on.
    #
    # So every user-side signal is off. `bot_speaking_enabled` has to stay on
    # even though the page does not use it: the observer queues each finished
    # sentence until BotStartedSpeaking flushes it, so switching it off means no
    # captions at all rather than fewer messages.
    rtvi = RTVIProcessor()
    rtvi_observer = RTVIObserver(
        rtvi,
        params=RTVIObserverParams(
            # What the interviewer says, aggregated into whole sentences and
            # timed to when it is actually spoken.
            bot_output_enabled=True,
            bot_speaking_enabled=True,
            # Everything else, and above all anything about the candidate.
            bot_llm_enabled=False,
            bot_tts_enabled=False,
            bot_audio_level_enabled=False,
            user_llm_enabled=False,
            user_speaking_enabled=False,
            user_transcription_enabled=False,
            user_audio_level_enabled=False,
            user_mute_enabled=False,
            metrics_enabled=False,
            system_logs_enabled=False,
            # Sentences only, decided here rather than filtered in the browser.
            # The page never sends a client-ready handshake, so the observer
            # treats it as an old client and stops suppressing word and token
            # aggregations on its own: captions would arrive a word at a time
            # and stutter. One source for the rule, in the place that has the
            # reason written next to it.
            skip_aggregator_types=[AggregationType.WORD, AggregationType.TOKEN],
        ),
    )

    # Ends the call once the interview is over. An observer, not a pipeline
    # processor: it has to see BotStoppedSpeakingFrame, which comes from the
    # output transport, and a processor upstream of that never does.
    ender = SessionEnder(brain=brain if use_brain else None, session_id=session_id)
    transcript_observer = TranscriptObserver()
    # Transcript turns are stamped from the moment the observer above was built,
    # and the recording starts a little later, once the bot is in the room. The
    # difference between the two is what lets a click on a transcript line seek
    # the video, so it is measured here rather than guessed at afterwards.
    #
    # Read from the clock instead of from the observer's own start, which is
    # private to a module this change may not touch. The two are set microseconds
    # apart, which is far below the accuracy a video seek needs.
    transcript_clock_zero = time.time()
    latency_observer = TurnLatencyObserver()

    pipeline = Pipeline(
        [
            processor
            for processor in [
                transport.input(),
                # Carries the caption messages out to the browser. It only ever
                # pushes frames downstream, so it sits at the top and they
                # travel to transport.output() like anything else.
                rtvi,
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
        observers=[transcript_observer, latency_observer, silence, ender, rtvi_observer],
        idle_timeout_secs=IDLE_TIMEOUT_SECS,
        conversation_id=session_id,
    )
    silence.attach(worker)
    ender.attach(worker)

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await silence.handle_idle(aggregator)

    # Whether this session is being recorded, for the `finally` block below. Set
    # once, from the one place that knows.
    recording = {"on": False}

    @transport.event_handler("on_joined")
    async def on_joined(transport, data):
        """Start the recording as soon as the bot is in the room.

        **Here rather than when the candidate connects**, and that is the whole
        point: `on_client_connected` is immediately followed by the opening
        greeting, and putting a network round trip in front of it would make
        every candidate wait in silence a little longer for the sake of the first
        few seconds of video. The bot is only spawned once someone is joining, so
        this captures their arrival and costs the interview nothing.
        """
        recording["on"] = await begin_recording(
            transport,
            interview_id=interview_id,
            session_id=session_id,
            clock_zero=transcript_clock_zero,
        )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"[{session_id}] candidate connected")

        if use_brain:
            # Spoken straight to TTS, with no inference in between. The greeting
            # was already written word for word in the opening prompt and the
            # model was reproducing it almost verbatim, so the round trip bought
            # nothing and cost several seconds of somebody sitting in silence
            # wondering whether the call had connected.
            #
            # It also means the introduction cannot be skipped or pre-empted. A
            # candidate who says hello first used to consume the opening turn,
            # and the interview would begin without ever saying who was asking.
            greeting = brain.opening_line()
            brain.observe(bot_text=greeting)
            # Let the audio path settle, or the first word is lost. See
            # GREETING_SETTLE_SECONDS.
            await asyncio.sleep(GREETING_SETTLE_SECONDS)
            await worker.queue_frames([TTSSpeakFrame(greeting)])
            return

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

        # The tidy way to close the recording. It is not the only one, and this
        # block is exactly why: a process that is killed never gets here. Daily
        # finalises a recording by itself when the room empties, which is what
        # actually covers a crash, a redeploy or capacity teardown. Verified
        # against the live API rather than assumed.
        if recording["on"]:
            problem = await stop_recording(transport)
            if problem:
                logger.warning(
                    f"[{session_id}] {problem} Daily closes it when the room "
                    f"empties, so the file should still arrive."
                )

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
                    # How much of the off-path judgement actually landed. Equal
                    # attempts and failures means no claims were extracted and
                    # nothing was carried between sections, which a report
                    # cannot distinguish from a candidate who said nothing
                    # specific — so it has to be visible here.
                    **(director.judgment_summary() if director else {}),
                    # Counted by the brain, not inferred from the transcript:
                    # guessing at our own behaviour from our own words is the
                    # least reliable source available.
                    "repairs_requested": brain.repairs_requested if director else 0,
                    "llm_model": settings.llm_model,
                    # The judge runs on the blueprint model, not the live one.
                    # Recorded because a bad value here degrades the interview
                    # while looking like an offline-only setting.
                    "judge_model": settings.blueprint_model,
                    # Which vendor produced these latency numbers. Without
                    # it a comparison across sessions depends on somebody
                    # remembering when the key was added.
                    "stt_provider": stt_provider(),
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
