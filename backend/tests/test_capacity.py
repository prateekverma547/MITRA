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


def test_reserving_beyond_the_limit_raises(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "2")
    registry = BotRegistry()

    assert registry.reserve("int_1") is True
    registry.register("int_1", FakeProcess())
    assert registry.reserve("int_2") is True
    registry.register("int_2", FakeProcess())

    with pytest.raises(AtCapacity):
        registry.reserve("int_3")


def test_a_finished_bot_frees_its_slot(monkeypatch):
    """Counted from live processes, so slots come back without bookkeeping."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")
    registry = BotRegistry()

    first = FakeProcess()
    registry.register("int_1", first)
    with pytest.raises(AtCapacity):
        registry.reserve("int_2")

    first.exit()
    assert registry.reserve("int_2") is True  # no longer raises
    registry.release("int_2")
    assert registry.live_count() == 0


def test_a_crashed_bot_does_not_leak_a_slot(monkeypatch):
    """A bot that dies never writes `completed`, so a status-based count would
    drift up until the cap refused everything forever."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")
    registry = BotRegistry()

    crashed = FakeProcess()
    registry.register("int_1", crashed)
    crashed.exit(code=139)  # segfault

    assert registry.reserve("int_2") is True
    registry.release("int_2")
    assert registry.live_count() == 0


# --- the race these were written for ----------------------------------------


def test_a_pending_reservation_holds_its_seat(monkeypatch):
    """The bug: a join checked the count, then awaited for several seconds before
    registering its process. Every join that arrived in that window also saw a free
    seat, so five running bots plus three simultaneous joins started eight.

    A reservation is taken before the first await, so it counts straight away."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "2")
    registry = BotRegistry()

    assert registry.reserve("int_1") is True  # not yet registered: still counts
    assert registry.reserve("int_2") is True
    with pytest.raises(AtCapacity):
        registry.reserve("int_3")


def test_the_same_interview_cannot_hold_two_seats(monkeypatch):
    """Double-click or two tabs: the second request must not start a second bot."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "6")
    registry = BotRegistry()

    assert registry.reserve("int_1") is True
    assert registry.reserve("int_1") is False


def test_a_failed_join_gives_its_seat_back(monkeypatch):
    """A join that reserved and then failed before spawning must not leave a phantom
    bot counted against the cap."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")
    registry = BotRegistry()

    assert registry.reserve("int_1") is True
    registry.release("int_1")

    assert registry.reserve("int_2") is True


def test_release_never_removes_a_live_bot(monkeypatch):
    """Releasing a seat that now belongs to a running bot would free capacity that is
    still in use, and let one more bot start than the cap allows."""
    monkeypatch.setenv("MAX_CONCURRENT_INTERVIEWS", "1")
    registry = BotRegistry()

    registry.register("int_1", FakeProcess())
    registry.release("int_1")

    assert registry.is_running("int_1") is True
    with pytest.raises(AtCapacity):
        registry.reserve("int_2")
