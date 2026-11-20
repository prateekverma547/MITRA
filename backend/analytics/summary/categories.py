"""How an interview ended, as an HR category code (D_architecture 9.3). Every interview
gets exactly one category, so the counts always add up to the number of interviews."""

from __future__ import annotations

#: EndReason (shared/interview_contracts.py) -> category. Codes only; B has the words.
END_REASON_CATEGORY: dict[str, str] = {
    "interview_complete": "completed",
    "candidate_withdrew": "candidate_ended",
    "candidate_ended_button": "candidate_ended",
    "candidate_dropped": "connection_lost",
    "candidate_did_not_return": "connection_lost",
    "rejoin_limit_reached": "connection_lost",
    "candidate_left_the_call": "candidate_left",
    "abandoned_by_candidate": "candidate_silent",
    "failed_to_start": "technical_problem",
    "interrupted_by_restart": "technical_problem",
    "session_ended": "ended_other",
}

#: Statuses that decide the category on their own (the interview never ended in a call).
STATUS_CATEGORY: dict[str, str] = {
    "scheduled": "not_started",
    "in_progress": "in_progress",
    "no_show": "no_show",
    "expired": "link_expired",
}

CATEGORIES = ("completed", "candidate_ended", "connection_lost", "candidate_left", "candidate_silent",
              "technical_problem", "ended_other", "not_started", "in_progress", "no_show", "link_expired",
              "unknown")


def categorise(status: str, end_reason: str | None) -> tuple[str, str]:
    """(category, what decided it): "status", "end_reason", "failure" or "unknown"."""
    if status in STATUS_CATEGORY:
        return STATUS_CATEGORY[status], "status"
    if end_reason in END_REASON_CATEGORY:
        return END_REASON_CATEGORY[end_reason], "end_reason"
    if status == "failed":
        return "technical_problem", "failure"
    return "unknown", "unknown"
