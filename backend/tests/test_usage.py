"""Cost telemetry: what is counted, and that the counts are right.

These tests check counting, not prices. Prices are applied by scripts/interview_cost.py
from environment variables, and the arithmetic for that is tested here too.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    MetricsFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData, TTSUsageMetricsData

from bot.observers import UsageObserver
from shared.usage import USAGE_SCHEMA_VERSION, InterviewUsage, ModelUsage


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def push(observer, frame):
    await observer.on_push_frame(SimpleNamespace(frame=frame))


def llm_usage(prompt, completion):
    return LLMUsageMetricsData(
        processor="OpenAILLMService",
        value=LLMTokenUsage(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        ),
    )


# --- the observer --------------------------------------------------------------


async def test_live_llm_tokens_are_summed_across_calls():
    observer = UsageObserver()
    await push(observer, MetricsFrame(data=[llm_usage(1200, 40)]))
    await push(observer, MetricsFrame(data=[llm_usage(1300, 25)]))

    assert observer.llm_calls == 2
    assert observer.llm_prompt_tokens == 2500
    assert observer.llm_completion_tokens == 65


async def test_tts_characters_are_summed():
    observer = UsageObserver()
    await push(
        observer,
        MetricsFrame(data=[TTSUsageMetricsData(processor="ElevenLabsTTSService", value=120)]),
    )
    await push(
        observer,
        MetricsFrame(data=[TTSUsageMetricsData(processor="ElevenLabsTTSService", value=80)]),
    )

    assert observer.tts_characters == 200


async def test_a_frame_seen_on_every_hop_is_counted_once():
    """The same frame is seen at each processor it passes through. Counting it per hop
    would double the bill on paper."""
    observer = UsageObserver()
    frame = MetricsFrame(data=[llm_usage(500, 10)])
    for _ in range(4):
        await push(observer, frame)

    assert observer.llm_calls == 1
    assert observer.llm_prompt_tokens == 500


async def test_candidate_speech_time_is_the_span_between_start_and_stop():
    clock = FakeClock()
    observer = UsageObserver(clock=clock)

    await push(observer, UserStartedSpeakingFrame())
    clock.now += 12.5
    await push(observer, UserStoppedSpeakingFrame())
    clock.now += 60  # silence between answers does not count as speech
    await push(observer, UserStartedSpeakingFrame())
    clock.now += 7.5
    await push(observer, UserStoppedSpeakingFrame())

    assert observer.candidate_speech_seconds == pytest.approx(20.0)


async def test_a_stop_without_a_start_adds_nothing():
    clock = FakeClock()
    observer = UsageObserver(clock=clock)
    await push(observer, UserStoppedSpeakingFrame())
    assert observer.candidate_speech_seconds == 0.0


# --- the record -------------------------------------------------------------


def test_the_usage_record_is_versioned():
    record = InterviewUsage()
    assert record.schema_version == USAGE_SCHEMA_VERSION == 1


def test_the_usage_record_survives_a_round_trip_through_json():
    original = InterviewUsage(
        llm_live=ModelUsage(calls=3, prompt_tokens=900, completion_tokens=40),
        tts_characters=512,
        stt_stream_seconds=1500.0,
        call_seconds=1500.0,
        models={"live": "gpt-4.1-mini"},
    )
    restored = InterviewUsage.model_validate(original.model_dump(mode="json"))
    assert restored == original


def build_usage(**kwargs):
    """Load run_bot's helper without running the bot."""
    module_path = Path(__file__).resolve().parents[1] / "bot" / "run_bot.py"
    spec = importlib.util.spec_from_file_location("run_bot_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("run_bot_under_test", module)
    spec.loader.exec_module(module)
    return module.build_usage(**kwargs)


def test_the_judge_totals_come_from_its_own_counters_including_failed_attempts():
    judge = SimpleNamespace(
        calls=5, prompt_tokens=4000, completion_tokens=300, cached_tokens=1000
    )
    observer = UsageObserver()

    record = build_usage(
        usage_observer=observer,
        judge=judge,
        live_model="gpt-4.1-mini",
        judge_model="gpt-4.1",
        stt="deepgram",
        call_seconds=2400.0,
    )

    assert record["llm_judgment"] == {
        "calls": 5,
        "prompt_tokens": 4000,
        "completion_tokens": 300,
        "cached_tokens": 1000,
    }
    assert record["stt_stream_seconds"] == 2400.0
    assert record["call_seconds"] == 2400.0
    assert record["models"] == {"live": "gpt-4.1-mini", "judgment": "gpt-4.1"}
    assert record["providers"]["stt"] == "deepgram"


def test_an_interview_without_a_judge_records_zero_judgement_not_a_missing_key():
    record = build_usage(
        usage_observer=UsageObserver(),
        judge=None,
        live_model="gpt-4.1-mini",
        judge_model="gpt-4.1",
        stt="openai",
        call_seconds=0.0,
    )
    assert record["llm_judgment"]["calls"] == 0


# --- feedback scoring ----------------------------------------------------------


def test_the_feedback_scorer_counts_its_own_calls():
    from feedback.score import FeedbackScorer

    scorer = FeedbackScorer(api_key="sk-test", model="gpt-4.1")
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=7000,
            completion_tokens=900,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2000),
        )
    )
    scorer._record(response)
    scorer._record(response)

    assert scorer.usage() == ModelUsage(
        calls=2, prompt_tokens=14000, completion_tokens=1800, cached_tokens=4000
    )


# --- the price arithmetic --------------------------------------------------------


def load_cost_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "interview_cost.py"
    name = "interview_cost_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before running: the dataclass in the script looks itself up here.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_an_unpriced_interview_says_so_rather_than_inventing_a_figure():
    cost = load_cost_script()
    usage = InterviewUsage(call_seconds=600.0).model_dump(mode="json")
    lines = cost.estimate(usage, cost.Rates(None, None, None, None, None))
    assert all(value is None for value in lines.values())


def test_the_estimate_multiplies_units_by_the_supplied_rates():
    cost = load_cost_script()
    usage = InterviewUsage(
        llm_live=ModelUsage(calls=1, prompt_tokens=1_000_000, completion_tokens=500_000),
        tts_characters=2000,
        stt_stream_seconds=600.0,
        call_seconds=600.0,
    ).model_dump(mode="json")
    rates = cost.Rates(llm_in=0.40, llm_out=1.60, tts_kchar=0.30, stt_min=0.0045, call_min=0.01)

    lines = cost.estimate(usage, rates)

    assert lines["llm_live"] == pytest.approx(0.40 + 0.80)
    assert lines["tts"] == pytest.approx(0.60)
    assert lines["stt"] == pytest.approx(10 * 0.0045)
    assert lines["call"] == pytest.approx(10 * 0.01)


def test_the_judge_is_priced_at_its_own_model_when_it_has_its_own_rate():
    """The judge runs on a more expensive model than the live conversation. One shared
    rate for both misprices the judge, so each role takes its own rate when given."""
    cost = load_cost_script()
    usage = InterviewUsage(
        llm_live=ModelUsage(calls=1, prompt_tokens=1_000_000, completion_tokens=0),
        llm_judgment=ModelUsage(calls=1, prompt_tokens=1_000_000, completion_tokens=0),
        models={"live": "gpt-4.1-mini", "judgment": "gpt-4.1"},
    ).model_dump(mode="json")
    rates = cost.Rates(
        llm_in=0.40, llm_out=1.60,
        role_llm={"llm_judgment": (2.00, 8.00)},
    )

    lines = cost.estimate(usage, rates)

    assert lines["llm_live"] == pytest.approx(0.40)      # shared rate
    assert lines["llm_judgment"] == pytest.approx(2.00)  # its own rate, not the shared one


def test_a_role_without_its_own_rate_falls_back_to_the_shared_one_and_says_so():
    cost = load_cost_script()
    usage = InterviewUsage(
        llm_live=ModelUsage(calls=1, prompt_tokens=1, completion_tokens=0),
        llm_judgment=ModelUsage(calls=1, prompt_tokens=1, completion_tokens=0),
        models={"live": "gpt-4.1-mini", "judgment": "gpt-4.1"},
    ).model_dump(mode="json")
    rates = cost.Rates(llm_in=0.40, llm_out=1.60)

    notes = cost.llm_pricing_notes(usage, rates)

    assert any("judgment model gpt-4.1 (shared rate)" in n and "WARNING" in n for n in notes)
    live_note = next(n for n in notes if n.startswith("live model"))
    assert "WARNING" not in live_note  # the live model is the reference, never flagged


def test_a_role_with_its_own_rate_is_not_flagged():
    cost = load_cost_script()
    usage = InterviewUsage(
        llm_judgment=ModelUsage(calls=1, prompt_tokens=1, completion_tokens=0),
        models={"live": "gpt-4.1-mini", "judgment": "gpt-4.1"},
    ).model_dump(mode="json")
    rates = cost.Rates(role_llm={"llm_judgment": (2.00, 8.00)})

    notes = cost.llm_pricing_notes(usage, rates)

    assert notes == ["judgment model gpt-4.1 (own rate)"]


def test_rates_read_their_own_role_variables_from_the_environment(monkeypatch):
    cost = load_cost_script()
    for name, value in {
        "PRICE_LLM_INPUT_PER_MTOK": "0.40", "PRICE_LLM_OUTPUT_PER_MTOK": "1.60",
        "PRICE_JUDGMENT_INPUT_PER_MTOK": "2.00", "PRICE_JUDGMENT_OUTPUT_PER_MTOK": "8.00",
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("PRICE_LIVE_INPUT_PER_MTOK", "PRICE_LIVE_OUTPUT_PER_MTOK",
                 "PRICE_FEEDBACK_INPUT_PER_MTOK", "PRICE_FEEDBACK_OUTPUT_PER_MTOK"):
        monkeypatch.delenv(name, raising=False)

    rates = cost.Rates.from_env()

    assert rates.llm_rate("llm_judgment") == (2.00, 8.00, "role")
    assert rates.llm_rate("llm_live") == (0.40, 1.60, "shared")
    assert rates.llm_rate("llm_feedback") == (0.40, 1.60, "shared")
