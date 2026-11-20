"""Evidence keys and the short-lived links that open them.

A report never stores a URL for evidence: signed URLs carry tokens, and a
stored token outlives its purpose (shared/interview_contracts.py). It stores a
**key**, and a reviewer's screen asks this module for a link when they click.

The key scheme. Every key names its interview, so a key can be checked against
the interview it is being opened for:

| Key                                         | Means                                   | Today        |
| ------------------------------------------- | --------------------------------------- | ------------ |
| `recording/<interview_id>/at/<seconds>`     | a moment in the recording, on the        | supported    |
|                                             | interview clock (transcript seconds)    |              |
| `thumbnail/<interview_id>/<name>`           | a still saved by proctoring analysis    | reserved     |
| `clip/<interview_id>/<start>-<end>`         | a stretch of the recording              | reserved     |

The link for a recording moment is `/api/live/interviews/<id>/recording/media
#t=<video seconds>`: the signed-in recording route (no token, no public
address; every view recorded). Until round 2 it was a token address that
needed no sign-in, `/api/live/evidence/media/<token>`; that route is gone. The
`#t=` fragment is a standard media fragment, so a plain `<video src>` (which
sends the sign-in cookie) opens at the moment. Video seconds are interview
seconds minus the measured `recording_offset_seconds`:
file_second = at_seconds - offset (D-05, about 0.08 s).
"""

import re
from dataclasses import dataclass

_ID = r"(?P<interview>[A-Za-z0-9_-]{1,64})"
_SECONDS = r"\d{1,6}(?:\.\d{1,3})?"
_PATTERNS = {
    "recording": re.compile(rf"recording/{_ID}/at/(?P<seconds>{_SECONDS})"),
    "thumbnail": re.compile(rf"thumbnail/{_ID}/(?P<name>[A-Za-z0-9_.-]{{1,128}})"),
    "clip": re.compile(rf"clip/{_ID}/(?P<start>{_SECONDS})-(?P<end>{_SECONDS})"),
}
SUPPORTED = {"recording"}


class EvidenceKeyError(ValueError):
    """`status` is what the route answers: 400 malformed, 404 not here."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EvidenceKey:
    scheme: str
    interview_id: str
    seconds: float | None = None


def recording_key(interview_id: str, seconds: float) -> str:
    """The key for one moment, as a report would store it."""
    return f"recording/{interview_id}/at/{round(max(0.0, seconds), 3):g}"


def parse_key(key: str, *, for_interview: str) -> EvidenceKey:
    """Parse and check a key. Refuses unknown schemes and other interviews' keys."""
    for scheme, pattern in _PATTERNS.items():
        match = pattern.fullmatch(key or "")
        if match is None:
            continue
        if match["interview"] != for_interview:
            # 404, not 403: whether another interview has evidence is not
            # something this request gets to learn.
            raise EvidenceKeyError(404, "No such evidence for this interview.")
        if scheme not in SUPPORTED:
            raise EvidenceKeyError(404, f"{scheme} evidence is not stored yet.")
        return EvidenceKey(scheme, match["interview"], float(match["seconds"]))
    raise EvidenceKeyError(400, "Not an evidence key this server knows.")


def video_seconds(interview_seconds: float, offset_seconds: float | None) -> float:
    return round(max(0.0, interview_seconds - (offset_seconds or 0.0)), 2)


def media_path(interview_id: str) -> str:
    """The signed-in recording route (live/routes.py `recording_media`)."""
    return f"/api/live/interviews/{interview_id}/recording/media"


def link_for(interview_id: str, at_interview_seconds: float, offset_seconds: float | None) -> dict:
    at = video_seconds(at_interview_seconds, offset_seconds)
    return {
        "url": f"{media_path(interview_id)}#t={at:g}",
        #: Signed-in links do not expire; kept (null) so the shape is unchanged.
        "expires_at": None,
        "video_seconds": at,
        "interview_seconds": at_interview_seconds,
        "offset_seconds": offset_seconds,
        # Without a measured offset the moment may be early by the bot's join
        # delay (a few seconds). Said, not hidden.
        "offset_known": offset_seconds is not None,
    }
