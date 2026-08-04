"""Captions: the interviewer's speech reaches the browser, the candidate's does not.

Deterministic. The pipeline wiring is asserted by construction and by reading
`run_bot`, the way `test_ending.py` already asserts observer placement. There is
no JS harness in this repo, so what the browser does with these messages is not
covered here and ships verified only by a real session.

The load-bearing test is the one that says the candidate's own words are never
sent. That is a product decision, not an oversight: nobody needs to read what
they just said, and our transcripts still fragment under some conditions, so
showing somebody their own answer breaking up mid-sentence would be unkind.
"""

import inspect

from pipecat.processors.frameworks.rtvi import (
    RTVIObserver,
    RTVIObserverParams,
    RTVIProcessor,
)
from pipecat.utils.text.base_text_aggregator import AggregationType

from bot import run_bot


def rtvi_params_block() -> str:
    """The RTVIObserverParams(...) call as written in run_bot."""
    source = inspect.getsource(run_bot)
    start = source.index("RTVIObserverParams(")
    return source[start : source.index("),\n    )", start)]


# -- nothing about the candidate leaves the pipeline -------------------------


def test_the_candidates_own_words_are_never_sent():
    """The whole point of the scope. If this fails, a candidate is watching
    their own speech appear while they are trying to think."""
    params = rtvi_params_block()

    for switch in (
        "user_transcription_enabled=False",
        "user_llm_enabled=False",
        "user_speaking_enabled=False",
        "user_audio_level_enabled=False",
    ):
        assert switch in params, f"{switch} is not set, so the candidate is being sent their own words"


def test_the_defaults_would_have_sent_them():
    """Why the switches above are not decoration: left alone, RTVI sends the
    candidate's transcription, their speaking state and their LLM input."""
    defaults = RTVIObserverParams()

    assert defaults.user_transcription_enabled is True
    assert defaults.user_llm_enabled is True
    assert defaults.user_speaking_enabled is True


# -- what does get sent ------------------------------------------------------


def test_the_interviewers_speech_is_sent():
    params = rtvi_params_block()

    assert "bot_output_enabled=True" in params


def test_the_bot_speaking_signal_stays_on_because_captions_depend_on_it():
    """Not cosmetic. The observer holds each finished sentence until
    BotStartedSpeaking flushes it, so turning this off produces no captions at
    all rather than fewer messages."""
    params = rtvi_params_block()

    assert "bot_speaking_enabled=True" in params


def test_only_whole_sentences_are_sent():
    """The page never sends a client-ready handshake, so the observer treats it
    as an old client and stops suppressing word and token aggregations by
    itself. Without this the captions arrive a word at a time."""
    params = rtvi_params_block()

    assert "skip_aggregator_types" in params
    assert "AggregationType.WORD" in params
    assert "AggregationType.TOKEN" in params


# -- wiring ------------------------------------------------------------------


def test_the_processor_is_in_the_pipeline_and_the_observer_is_not():
    """Same distinction `test_ending.py` guards for the session ender, and the
    same cost for getting it wrong: the observer needs to see frames from the
    output transport, the processor needs to push frames into the pipeline."""
    source = inspect.getsource(run_bot)

    pipeline_block = source[
        source.index("pipeline = Pipeline(") : source.index("worker = PipelineWorker(")
    ]
    assert "rtvi," in pipeline_block
    assert "rtvi_observer" not in pipeline_block

    observers = source[source.index("observers=[") :]
    observers = observers[: observers.index("]") + 1]
    assert "rtvi_observer" in observers


def test_the_processor_and_observer_construct_together():
    """Cheap, and it is what would break on a pipecat upgrade that renamed a
    parameter: the params are keyword arguments and a typo is silent otherwise."""
    rtvi = RTVIProcessor()
    observer = RTVIObserver(
        rtvi,
        params=RTVIObserverParams(
            bot_output_enabled=True,
            bot_speaking_enabled=True,
            user_transcription_enabled=False,
            skip_aggregator_types=[AggregationType.WORD, AggregationType.TOKEN],
        ),
    )

    # Pipecat keeps observers in a set. A dataclass without eq=False is
    # unhashable and registration dies, which is how the session ender once
    # stopped the call ever ending.
    assert isinstance(hash(observer), int)


# -- the stored transcript is untouched --------------------------------------


def test_captions_do_not_touch_the_stored_transcript():
    """Display only. The record of truth is still the transcript observer, and
    what is persisted still comes from it."""
    source = inspect.getsource(run_bot)

    assert "transcript=build_transcript(" in source
    assert "turns=transcript_observer.turns" in source
    # The RTVI pieces are nowhere near the persistence call.
    persistence_block = source[source.index("await save_interview_result(") : source.index("write_session_artifacts(")]
    assert "rtvi" not in persistence_block


def test_daily_transcription_stays_off():
    """A second vendor transcribing the same audio, billed separately, showing
    the candidate words that differ from the ones stored against their name."""
    from bot.services import daily

    assert "transcription_enabled=False" in inspect.getsource(daily)
