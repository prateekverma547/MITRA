"""Greeting the candidate on their own clock.

The bot read `datetime.now()`, which on Railway is UTC. Candidates are in India,
five and a half hours ahead, so the greeting was right through the morning and
wrong every afternoon and evening. These pin the fix at the boundaries where it
actually broke.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from bot.greeting import DEFAULT_TIMEZONE, resolve_timezone, time_of_day


def at_utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 31, hour, minute, tzinfo=UTC)


# -- the bug -----------------------------------------------------------------


@pytest.mark.parametrize(
    "utc_hour, utc_minute, ist_clock, expected",
    [
        (3, 30, "09:00", "morning"),
        (8, 30, "14:00", "afternoon"),  # server said morning
        (13, 0, "18:30", "evening"),    # server said afternoon
        (16, 30, "22:00", None),        # too late for any greeting
    ],
)
def test_the_greeting_follows_indian_time_not_the_servers(
    utc_hour, utc_minute, ist_clock, expected
):
    assert time_of_day("Asia/Kolkata", now=at_utc(utc_hour, utc_minute)) == expected


def test_the_server_clock_and_the_candidates_disagree():
    """The whole point: same instant, two different answers."""
    moment = at_utc(13, 0)  # 18:30 in Delhi

    assert time_of_day("UTC", now=moment) == "afternoon"
    assert time_of_day("Asia/Kolkata", now=moment) == "evening"


# -- untrusted input ---------------------------------------------------------


@pytest.mark.parametrize("sent", [None, "", "Mars/Olympus", "not a timezone", "../etc/passwd"])
def test_an_unusable_timezone_falls_back_instead_of_raising(sent):
    """It arrives from a browser. Refusing to start someone's interview over a
    greeting would be a far larger problem than getting the greeting wrong."""
    assert time_of_day(sent, now=at_utc(13, 0)) == "evening"  # the IST answer


def test_the_fallback_is_india_not_the_server():
    assert str(resolve_timezone(None)) == DEFAULT_TIMEZONE


def test_the_default_can_be_configured(monkeypatch):
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Europe/London")

    assert str(resolve_timezone(None)) == "Europe/London"
    # 13:00 UTC is 14:00 in London in July.
    assert time_of_day(None, now=at_utc(13, 0)) == "afternoon"


def test_a_valid_timezone_beats_the_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Europe/London")

    assert time_of_day("Asia/Kolkata", now=at_utc(13, 0)) == "evening"


# -- hours where no greeting fits --------------------------------------------


def in_delhi(hour: int) -> datetime:
    """Built in the candidate's own zone, so there is no offset arithmetic here
    to get wrong. The first version of these tests did, and passed anyway."""
    return datetime(2026, 7, 31, hour, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


@pytest.mark.parametrize("hour", [0, 1, 3, 4, 22, 23])
def test_no_time_greeting_in_the_small_hours(hour):
    """"Good morning" at three in the morning is worse than "Hello"."""
    assert time_of_day("Asia/Kolkata", now=in_delhi(hour)) is None


@pytest.mark.parametrize("hour", [5, 6, 9, 11, 13, 16, 18, 21])
def test_a_greeting_is_given_through_the_working_day(hour):
    assert time_of_day("Asia/Kolkata", now=in_delhi(hour)) is not None


@pytest.mark.parametrize(
    "hour, expected",
    [(5, "morning"), (11, "morning"), (12, "afternoon"), (16, "afternoon"),
     (17, "evening"), (21, "evening")],
)
def test_the_boundaries_land_where_a_person_would_put_them(hour, expected):
    assert time_of_day("Asia/Kolkata", now=in_delhi(hour)) == expected
