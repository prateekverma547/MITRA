"""Which transcription vendor runs, and why the choice exists.

CLAUDE.md's STT decision rule fired on measurement: OpenAI's finalisation came
in at a median of 1.04s with a p90 of 1.08s, a tight distribution rather than a
noisy average, which put it in the "run the Deepgram trial" bucket.

The reason is a protocol gap, not accuracy. Ending a turn needs to know the
transcript is complete, and a provider says so by confirming finalisation.
OpenAI's service never calls `confirm_finalize()`; Deepgram's does. Without that
confirmation Pipecat waits out a fixed timer on every single turn, however
quickly the words actually arrived.
"""

import pytest

from bot.services import stt


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch):
    """The developer's own .env must not decide what these tests measure."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)


# -- selection ---------------------------------------------------------------


def test_openai_is_used_when_no_deepgram_key_is_set():
    assert stt.using_deepgram() is False
    assert stt.stt_provider() == "openai"

    service = stt.build_stt(api_key="sk-test")
    assert type(service).__name__ == "OpenAIRealtimeSTTService"


def test_deepgram_is_used_when_its_key_is_set(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")

    assert stt.using_deepgram() is True
    assert stt.stt_provider() == "deepgram"

    service = stt.build_stt(api_key="sk-test")
    assert type(service).__name__ == "DeepgramSTTService"


def test_a_blank_key_is_not_a_key(monkeypatch):
    """An empty variable left in the environment must not silently switch
    vendors halfway through a comparison."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "   ")

    assert stt.using_deepgram() is False


# -- the reason the trial exists ---------------------------------------------


def test_only_deepgram_confirms_finalisation():
    """The whole argument, asserted against the library rather than repeated
    from a comment. If OpenAI ever starts confirming, this fails and the trial
    can be reconsidered."""
    import inspect

    from pipecat.services.deepgram import stt as deepgram_stt
    from pipecat.services.openai import stt as openai_stt

    assert "confirm_finalize()" not in inspect.getsource(openai_stt)
    assert "confirm_finalize()" in inspect.getsource(deepgram_stt)


def test_deepgram_waits_far_less_for_a_transcript():
    """Not a preference. Pipecat's own defaults are 1.66s for OpenAI and 0.35s
    for Deepgram, which is the same claim expressed as a number."""
    assert stt.DEEPGRAM_FINALIZATION_WAIT_SECONDS < stt.STT_FINALIZATION_WAIT_SECONDS


def test_the_deepgram_wait_clears_its_measured_maximum():
    """Measured max was 0.52s across 14 samples from two full interviews.

    Below that the timer fires before Deepgram has confirmed, so one spoken
    sentence arrives as several turns: words are corrupted at the split and the
    turn counts that end a section are inflated. The old 0.35 was under every
    sample taken, which is how that shipped unnoticed.
    """
    assert stt.DEEPGRAM_FINALIZATION_WAIT_SECONDS > 0.52


def test_the_openai_wait_still_clears_its_measured_maximum():
    """Measured max was 1.08s. Below that the timer fires on a fragment and the
    interviewer answers half a sentence, which costs more than the latency
    saves."""
    assert stt.STT_FINALIZATION_WAIT_SECONDS > 1.08


# -- neither provider is allowed to decide when a turn ended -----------------


def test_neither_vendor_does_its_own_endpointing(monkeypatch):
    """Turn-taking belongs to bot/turn_taking.py, using VAD plus semantic turn
    detection. Both vendors offer silence-based endpointing, and letting either
    have an opinion would undo the mid-thought pause tolerance that whole design
    exists to provide."""
    import inspect

    source = inspect.getsource(stt)

    assert "turn_detection=False" in source
    assert "endpointing=False" in source


def test_the_session_records_which_vendor_ran(monkeypatch):
    """A latency comparison across sessions is worthless if it cannot say which
    provider produced which numbers."""
    import inspect

    from bot import run_bot

    assert '"stt_provider": stt_provider()' in inspect.getsource(run_bot)
