"""Optional structured (JSON) log lines for the web process (phase 4, D-41.7).

Off by default: plain text, as today. With LOG_JSON set to 1/true/yes/on, every
log line on standard error is one JSON object (loguru's serialised record: the
message, level, time, module, function, line and extras), so log analysis has
something to read later. Nothing new is logged; the same lines change form.
The bot's own logging (bot/run_bot.py configure_logging) is Session A's to
switch the same way (C-132).
"""

import os
import sys

from loguru import logger

LOG_JSON_ENV = "LOG_JSON"
_TRUTHY = {"1", "true", "yes", "on"}


def json_logs_enabled() -> bool:
    return os.environ.get(LOG_JSON_ENV, "").strip().lower() in _TRUTHY


def configure_web_logging() -> bool:
    """Switch the standard-error sink to JSON when asked. Returns whether it did."""
    if not json_logs_enabled():
        return False
    logger.remove()
    logger.add(sys.stderr, serialize=True, level=os.environ.get("LOG_LEVEL", "INFO").upper())
    return True
