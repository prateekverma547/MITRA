"""The concurrency cap.

The cap is not about fairness or queueing. On a 1GB replica, spawning one bot
too many gets the container OOM-killed and restarted, which ends every
interview running on it. Refusing one candidate is the cheap outcome; the tests
below pin the behaviour that keeps it cheap.
"""

import pytest

from app.capacity import DEFAULT_MAX_CONCURRENT, AtCapacity, BotRegistry, max_concurrent


class FakeProcess:
    """Stands in for asyncio.subprocess.Process — only returncode is read."""

    def __init__(self, returncode=None):
        self.returncode = returncode

    def exit(self, code=0):
        self.returncode = code


def test_the_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "5")
    assert max_concurrent() == 5


def test_a_nonsense_limit_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "plenty")
    assert max_concurrent() == DEFAULT_MAX_CONCURRENT


def test_a_zero_limit_is_refused(monkeypatch):
    """Zero would refuse every interview — an outage dressed as configuration."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "0")
    assert max_concurrent() == DEFAULT_MAX_CONCURRENT


def test_claiming_beyond_the_limit_raises(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "2")
    registry = BotRegistry()

    registry.claim()
    registry.register("int_1", FakeProcess())
    registry.claim()
    registry.register("int_2", FakeProcess())

    with pytest.raises(AtCapacity):
        registry.claim()


def test_a_finished_bot_frees_its_slot(monkeypatch):
    """Counted from live processes, so slots come back without bookkeeping."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")
    registry = BotRegistry()

    first = FakeProcess()
    registry.register("int_1", first)
    with pytest.raises(AtCapacity):
        registry.claim()

    first.exit()
    registry.claim()  # no longer raises
    assert registry.live_count() == 0


def test_a_crashed_bot_does_not_leak_a_slot(monkeypatch):
    """A bot that dies never writes `completed`, so a status-based count would
    drift up until the cap refused everything forever."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")
    registry = BotRegistry()

    crashed = FakeProcess()
    registry.register("int_1", crashed)
    crashed.exit(code=139)  # segfault

    registry.claim()
    assert registry.live_count() == 0
