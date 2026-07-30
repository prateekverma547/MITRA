"""What time it is where the candidate is sitting.

The bot used to greet on server time. Railway runs its containers in UTC and the
candidates are in India, so the clock it was reading was five and a half hours
behind the person it was talking to: correct through the morning and wrong every
afternoon and evening. A greeting that contradicts the daylight outside someone's
window is a bad first sentence for a nervous candidate.

The candidate's browser already knows their timezone, so the join page sends it
and the bot greets on their clock. That string arrives from a browser, which
means it is untrusted input and may be absent, misspelled or invented; every
path here falls back rather than raises. Getting the greeting wrong is a small
problem, and refusing to start someone's interview over it would be a large one.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

#: Used when the browser sends nothing usable. Every candidate so far is in
#: India, so this is a better guess than the server's own clock, which is UTC
#: and belongs to nobody.
DEFAULT_TIMEZONE = "Asia/Kolkata"

#: Outside these hours a time-of-day greeting stops sounding warm and starts
#: sounding automated. Someone joining at 03:00 does not want to be told good
#: morning; they get a plain hello instead.
GREETING_HOURS = range(5, 22)


def resolve_timezone(name: str | None) -> ZoneInfo:
    """A timezone from an untrusted string, never an exception."""
    for candidate in (name, os.environ.get("DEFAULT_TIMEZONE"), DEFAULT_TIMEZONE):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            if candidate == name:
                logger.warning(
                    f"candidate sent an unusable timezone {name!r}; falling back"
                )
    return ZoneInfo("UTC")


def time_of_day(timezone_name: str | None = None, *, now: datetime | None = None) -> str | None:
    """"morning", "afternoon", "evening", or None when no greeting fits.

    None is a real answer, not a failure: the caller opens with "Hello" instead,
    which is right at four in the morning and never wrong at any other hour.
    """
    zone = resolve_timezone(timezone_name)
    moment = now.astimezone(zone) if now else datetime.now(zone)
    hour = moment.hour

    if hour not in GREETING_HOURS:
        return None
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"
