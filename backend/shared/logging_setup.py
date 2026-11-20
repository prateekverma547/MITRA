"""Logging for both processes: the web app and each interview's bot.

One configuration, so both write the same shape to stdout. The container runtime
collects stdout (CloudWatch Logs on AWS), and that is where these lines are read.

Environment:

    LOG_LEVEL   INFO (default), DEBUG, WARNING or ERROR
    LOG_FORMAT  text (default, for a developer's terminal) or json (for deployment)

In json, each line is one object with: ts, level, logger, process, msg, and where
known: interview_id, request_id, duration_ms, status, method, path, exc.

**Redaction is the second line of defence.** The first is not logging secrets at all.
Every line still passes through `redact()`: fields with a secret-shaped name are
dropped, and strings shaped like API keys, bearer tokens and Daily's JWT meeting
tokens are masked. Candidate speech and CV text are never logged; see the observer
that writes transcripts to the database and the session file, not the log.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import traceback
from datetime import UTC

from loguru import logger

#: The request ID for the request being handled, set by the web app's middleware.
#: A context variable, so concurrent requests each see their own.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

#: Field names whose values are never written, whatever they contain.
SECRET_FIELD_NAMES = frozenset(
    {"password", "token", "bot_token", "secret", "cookie", "authorization", "api_key", "meeting_password"}
)

_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk-***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer ***"),
    # Daily meeting tokens are JWTs: three base64url segments starting with "eyJ".
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"), "***jwt***"),
    (re.compile(r"(?i)\b(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+"), r"\1\2***"),
]


def redact(text: str) -> str:
    """Mask secret-shaped substrings. Idempotent, so running it twice is harmless."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _clean_value(value):
    if isinstance(value, str):
        return redact(value)
    return value


def _patch(record: dict) -> None:
    """Runs on every record before any sink sees it: redact the message and attach the
    request ID from the context, if the request does not already carry one."""
    record["message"] = redact(record["message"])
    request_id = request_id_var.get()
    if request_id and "request_id" not in record["extra"]:
        record["extra"]["request_id"] = request_id


def format_json(record: dict) -> str:
    """One record as one JSON line. Pure, so it can be tested without a sink."""
    line: dict = {
        "ts": record["time"].astimezone(UTC).isoformat(timespec="milliseconds"),
        "level": record["level"].name,
        "logger": record["name"],
        "msg": redact(record["message"]),
    }
    for key, value in record["extra"].items():
        if key.lower() in SECRET_FIELD_NAMES:
            continue
        line[key] = _clean_value(value)
    if record["exception"] is not None:
        exc = record["exception"]
        line["exc"] = redact("".join(traceback.format_exception(exc.type, exc.value, exc.traceback)))
    return json.dumps(line, default=str, ensure_ascii=False)


def _json_sink(message) -> None:
    sys.stdout.write(format_json(message.record) + "\n")
    sys.stdout.flush()


TEXT_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
    "<cyan>{extra[process]}</cyan> | <level>{message}</level>"
)


class _InterceptHandler(logging.Handler):
    """Routes standard-library logging (uvicorn, SQLAlchemy, asyncpg) into loguru, so
    everything arrives in one place and one format."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(process: str, *, interview_id: str | None = None) -> None:
    """Set up logging for this process. Call once, at startup.

    `process` is "web" or "bot". `interview_id` is bound to every line of a bot, so the
    whole call can be filtered by one ID.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = os.environ.get("LOG_FORMAT", "text").lower()
    if fmt not in ("text", "json"):
        raise ValueError(f"LOG_FORMAT must be 'text' or 'json', not {fmt!r}")

    extra: dict = {"process": process}
    if interview_id:
        extra["interview_id"] = interview_id

    logger.remove()
    logger.configure(extra=extra, patcher=_patch)
    if fmt == "json":
        logger.add(_json_sink, level=level, format="{message}")
    else:
        logger.add(sys.stdout, level=level, format=TEXT_FORMAT, colorize=sys.stdout.isatty())

    # Everything that uses the standard library goes through loguru as well.
    logging.root.handlers = [_InterceptHandler()]
    logging.root.setLevel(logging.DEBUG)
    # uvicorn.access is deliberately left alone. The web app's own access line replaces it,
    # and uvicorn's `--no-access-log` depends on that logger not propagating.
    for name in ("uvicorn", "uvicorn.error", "sqlalchemy.engine"):
        library = logging.getLogger(name)
        library.handlers = []
        library.propagate = True
    # SQL statements are noise at INFO. They are shown only when asked for explicitly.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if level == "DEBUG" else logging.WARNING
    )

