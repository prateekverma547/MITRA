"""Reading and logging Content-Security-Policy violation reports.

Two formats arrive: `report-uri` (Content-Type application/csp-report,
{"csp-report": {"effective-directive" | "violated-directive", "blocked-uri", ...}})
and the Reporting API (application/reports+json, a list of
{"type": "csp-violation", "body": {"effectiveDirective", "blockedURL", ...}}).

Only two things are kept from a report: the violated rule and the blocked
address, with the query string, fragment and any user:password stripped. A
report can carry the page address (which, for the candidate app, never holds a
credential, but other pages might), a referrer and a code sample; none of
those are logged.
"""

import json
import time
from collections import deque
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

MAX_BYTES = 8 * 1024
#: Per client address: a burst of 20, then 2 a second. A page with a real
#: problem sends a handful; anything more is noise or abuse.
RATE_PER_SECOND = 2.0
BURST = 20
#: The last reports kept in memory, for a person or a test to look at; the log
#: is the record.
recent: deque = deque(maxlen=200)


class _Bucket:
    def __init__(self) -> None:
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, at = self._state.get(key, (float(BURST), now))
        tokens = min(float(BURST), tokens + (now - at) * RATE_PER_SECOND)
        if tokens < 1.0:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - 1.0, now)
        if len(self._state) > 10_000:  # never grows without bound
            self._state.clear()
        return True

    def reset(self) -> None:
        self._state.clear()


limiter = _Bucket()


def strip(address: str) -> str:
    """The blocked address without query, fragment or credentials. Keywords
    ("inline", "eval", "wasm-eval", "data", "blob") are kept as they are."""
    address = (address or "").strip()[:500]
    parts = urlsplit(address)
    if not parts.scheme or not parts.netloc:
        return address.split("?", 1)[0].split("#", 1)[0]
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def parse(body: bytes) -> list[tuple[str, str]]:
    """(violated rule, blocked address) for each report in the body; [] if unreadable."""
    try:
        data = json.loads(body or b"null")
    except ValueError:
        return []
    found = []
    if isinstance(data, dict) and isinstance(data.get("csp-report"), dict):
        r = data["csp-report"]
        found.append((r.get("effective-directive") or r.get("violated-directive") or "?",
                      r.get("blocked-uri") or ""))
    elif isinstance(data, list):
        for item in data[:20]:
            if isinstance(item, dict) and item.get("type") == "csp-violation" and isinstance(item.get("body"), dict):
                b = item["body"]
                found.append((b.get("effectiveDirective") or "?", b.get("blockedURL") or ""))
    return [(str(d)[:64], strip(str(u))) for d, u in found]


def record(directive: str, blocked: str) -> None:
    recent.append({"directive": directive, "blocked": blocked})
    logger.warning(f"csp {_mode()} violation: directive={directive} blocked={blocked}")


def _mode() -> str:
    """What the browser did, from the header actually sent (live/interview_static.py):
    "blocked" under the enforcing rule, "report-only" while it only reports (C-125)."""
    from live.interview_static import CSP_HEADER

    return "report-only" if CSP_HEADER.endswith("Report-Only") else "blocked"
