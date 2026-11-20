"""The price feed: parsing, caching, failure, and how a price is applied to usage.

No network. The fetch function is injected, so a test decides what the feed returns.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from shared.pricing import load_feed, parse_feed

SAMPLE = {
    "updated_at": "2026-10-26",
    "prices": [
        {"id": "gpt-4.1", "vendor": "openai", "name": "GPT-4.1", "input": 2, "output": 8, "input_cached": 0.5},
        {"id": "gpt-4.1-mini", "vendor": "openai", "name": "GPT-4.1 Mini", "input": 0.4, "output": 1.6, "input_cached": 0.1},
        {"id": "no-cache-model", "vendor": "x", "name": "No Cache", "input": 1, "output": 2, "input_cached": None},
        {"id": "broken", "vendor": "x", "name": "Missing output", "input": 1},  # malformed: skipped
    ],
}


def load_cost_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "interview_cost.py"
    name = "interview_cost_pricing_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fetch_returning(payload):
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return payload

    fetch.calls = calls
    return fetch


def fetch_failing(url, timeout):
    raise OSError("network down")


# --- parsing -----------------------------------------------------------------------


def test_the_feed_is_parsed_per_model_and_malformed_entries_are_counted_not_guessed():
    updated, models, skipped = parse_feed(SAMPLE)

    assert updated == "2026-10-26"
    assert models["gpt-4.1"].input == 2 and models["gpt-4.1"].output == 8
    assert models["gpt-4.1"].input_cached == 0.5
    assert models["no-cache-model"].input_cached is None
    assert "broken" not in models
    assert skipped == 1


# --- caching and failure --------------------------------------------------------------


def test_a_fresh_network_fetch_is_cached_and_used_next_time(tmp_path):
    cache = tmp_path / "prices.json"
    fetch = fetch_returning(SAMPLE)

    first = load_feed(cache_path=cache, fetch=fetch, now=1_000_000)
    second = load_feed(cache_path=cache, fetch=fetch, now=1_000_100)

    assert first.source == "network"
    assert second.source == "cache"
    assert len(fetch.calls) == 1  # the second run did not touch the network


def test_an_expired_cache_is_refreshed(tmp_path):
    cache = tmp_path / "prices.json"
    load_feed(cache_path=cache, fetch=fetch_returning(SAMPLE), now=1_000_000)

    later = load_feed(cache_path=cache, fetch=fetch_returning(SAMPLE), now=1_000_000 + 2 * 86_400)

    assert later.source == "network"


def test_a_failed_fetch_uses_an_old_cache_and_says_so(tmp_path):
    cache = tmp_path / "prices.json"
    load_feed(cache_path=cache, fetch=fetch_returning(SAMPLE), now=1_000_000)

    feed = load_feed(cache_path=cache, fetch=fetch_failing, now=1_000_000 + 3 * 86_400)

    assert feed is not None and feed.source == "cache"


def test_with_no_cache_and_no_network_there_is_no_feed_not_a_guess(tmp_path):
    assert load_feed(cache_path=tmp_path / "missing.json", fetch=fetch_failing) is None


# --- applying a price to usage --------------------------------------------------------


def _usage(model_block: dict, models: dict) -> dict:
    return {"llm_live": model_block, "models": models}


def test_each_model_is_priced_from_the_feed_entry_for_the_model_it_used(tmp_path):
    cost = load_cost_script()
    _, models, _ = parse_feed(SAMPLE)
    feed = load_feed(cache_path=tmp_path / "p.json", fetch=fetch_returning(SAMPLE))
    rates = cost.Rates(feed=feed)

    block = {"calls": 1, "prompt_tokens": 1_000_000, "completion_tokens": 0, "cached_tokens": 0}
    lines = cost.estimate(_usage(block, {"live": "gpt-4.1-mini"}), rates)

    assert lines["llm_live"] == pytest.approx(0.40)  # the mini model's own input rate


def test_cached_input_tokens_are_billed_at_the_cached_rate(tmp_path):
    cost = load_cost_script()
    feed = load_feed(cache_path=tmp_path / "p.json", fetch=fetch_returning(SAMPLE))
    rates = cost.Rates(feed=feed)
    # 1M prompt tokens, 400k of them cached, no output.
    block = {"calls": 1, "prompt_tokens": 1_000_000, "completion_tokens": 0, "cached_tokens": 400_000}

    lines = cost.estimate(_usage(block, {"live": "gpt-4.1"}), rates)

    assert lines["llm_live"] == pytest.approx(0.6 * 2.0 + 0.4 * 0.5)


def test_a_model_missing_from_the_feed_is_reported_unpriced_not_estimated(tmp_path):
    cost = load_cost_script()
    feed = load_feed(cache_path=tmp_path / "p.json", fetch=fetch_returning(SAMPLE))
    rates = cost.Rates(feed=feed)
    block = {"calls": 1, "prompt_tokens": 1_000_000, "completion_tokens": 0}

    usage = _usage(block, {"live": "some-model-not-in-feed"})
    lines = cost.estimate(usage, rates)
    notes = cost.llm_pricing_notes(usage, rates)

    assert lines["llm_live"] is None
    assert any("NOT PRICED" in n for n in notes)


def test_an_env_override_for_a_role_still_wins_over_the_feed(tmp_path):
    cost = load_cost_script()
    feed = load_feed(cache_path=tmp_path / "p.json", fetch=fetch_returning(SAMPLE))
    rates = cost.Rates(feed=feed, role_llm={"llm_live": (9.0, 9.0)})
    block = {"calls": 1, "prompt_tokens": 1_000_000, "completion_tokens": 0}

    lines = cost.estimate(_usage(block, {"live": "gpt-4.1-mini"}), rates)

    assert lines["llm_live"] == pytest.approx(9.0)


def test_the_feed_date_is_quoted_in_the_notes(tmp_path):
    cost = load_cost_script()
    feed = load_feed(cache_path=tmp_path / "p.json", fetch=fetch_returning(SAMPLE))
    rates = cost.Rates(feed=feed)
    block = {"calls": 1, "prompt_tokens": 1, "completion_tokens": 0}

    notes = cost.llm_pricing_notes(_usage(block, {"live": "gpt-4.1-mini"}), rates)

    assert "feed, updated 2026-10-26" in notes[0]


def _report_inputs(feed):
    cost = load_cost_script()
    rates = cost.Rates(feed=feed, tts_kchar=0.3, stt_min=0.0077, call_min=0.004)
    return cost, rates


def test_an_unused_role_does_not_make_an_interview_look_unpriced(tmp_path):
    """A call that failed before scoring has no feedback calls. Its feedback model is not in
    the usage record's models, and that must not count against the interview."""
    from shared.cost import build_report
    from shared.pricing import PriceFeed, ModelPrice

    feed = PriceFeed("2026-10-26", "2026-11-06T00:00:00+00:00", "cache", {
        "gpt-4.1-mini": ModelPrice("gpt-4.1-mini", "openai", "m", 0.4, 1.6, None),
    })
    _, rates = _report_inputs(feed)
    usage = {
        "llm_live": {"calls": 1, "prompt_tokens": 1000, "completion_tokens": 10, "cached_tokens": 0},
        "tts_characters": 100, "stt_stream_seconds": 10.0, "call_seconds": 60.0,
        "models": {"live": "gpt-4.1-mini"},
    }

    report = build_report([{"id": "int_1", "status": "failed", "created_at": "2026-11-06", "usage": usage}], rates)

    assert report["interviews"][0]["complete"] is True
    assert report["totals"]["complete"] == 1


def test_a_used_role_without_a_price_makes_the_interview_partial(tmp_path):
    from shared.cost import build_report
    from shared.pricing import PriceFeed

    feed = PriceFeed("2026-10-26", "2026-11-06T00:00:00+00:00", "cache", {})
    _, rates = _report_inputs(feed)
    usage = {
        "llm_live": {"calls": 1, "prompt_tokens": 1000, "completion_tokens": 10, "cached_tokens": 0},
        "tts_characters": 100, "stt_stream_seconds": 10.0, "call_seconds": 60.0,
        "models": {"live": "unlisted-model"},
    }

    report = build_report([{"id": "int_2", "status": "completed", "created_at": "2026-11-06", "usage": usage}], rates)

    assert report["interviews"][0]["complete"] is False
    assert report["totals"]["partial"] == 1
    assert report["interviews"][0]["lines"]["llm_live"] is None


def test_an_interview_with_no_usage_record_is_counted_not_priced(tmp_path):
    from shared.cost import build_report
    from shared.pricing import PriceFeed

    _, rates = _report_inputs(PriceFeed("2026-10-26", "x", "cache", {}))

    report = build_report([{"id": "int_3", "status": "failed", "created_at": "2026-11-06", "usage": None}], rates)

    assert report["totals"]["no_usage"] == 1
    assert report["interviews"][0]["estimate_usd"] is None
