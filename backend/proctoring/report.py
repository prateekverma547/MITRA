"""The IntegrityReport for one interview, assembled from what was stored.

Built on demand from the stored events, so the summary always matches them:
the contract itself refuses a summary that does not (`IntegrityReport`'s
validator), and `IntegritySummary.from_events` is the only way it is made.
"""

from datetime import UTC, datetime

from app.db import get_sessionmaker
from proctoring.intake import stored_events
from proctoring.models import IntegrityAccommodationRecord
from shared.interview_contracts import (
    IntegrityAccommodation,
    IntegrityReport,
    IntegritySource,
    IntegritySummary,
)


def after_call_results(interview_id: str):
    """(status, second-voice signal, after_call integrity events) from Session D's
    result folder (aftercall/store.py), or empty when nothing has run. Never
    raises: a missing or unreadable result is reported as none."""
    try:
        from aftercall import store

        return store.read_status(interview_id), store.read_signal(interview_id), store.read_events(interview_id)
    except Exception as exc:  # noqa: BLE001  (the report must still assemble)
        from loguru import logger

        logger.error(f"[{interview_id}] after-call results not readable ({type(exc).__name__}); shown as none")
        return None, None, []


async def assemble(interview_id: str) -> IntegrityReport:
    events = await stored_events(interview_id)
    async with get_sessionmaker()() as session:
        record = await session.get(IntegrityAccommodationRecord, interview_id)
    accommodation = (
        IntegrityAccommodation(gaze_reminders_off=record.gaze_reminders_off, note=record.note)
        if record is not None else IntegrityAccommodation()
    )
    # Session D's after-call results (D-58): their integrity events join the
    # browser's (by event id, never twice); the status and the signal ride along.
    status, signal, after_call = after_call_results(interview_id)
    seen = {e.event_id for e in events}
    events = events + [e for e in after_call if e.event_id not in seen]
    analysed = any(e.source is IntegritySource.AFTER_CALL for e in events) or bool(
        status and any(c.state.value == "done" for c in status.checks))
    return IntegrityReport(
        interview_id=interview_id,
        generated_at=datetime.now(UTC),
        analysed_after_call=analysed,
        accommodation=accommodation,
        events=events,
        summary=IntegritySummary.from_events(events),
        after_call_status=status,
        second_voice=signal,
    )


#: Plain names for the kinds, for the summary lines. Behaviour, never intent.
_KIND_WORDS = {
    "distance_out_of_band": "distance from the screen",
    "looking_away": "looking away from the screen",
    "face_missing": "face not in view",
    "second_face": "a second face in view",
    "tab_hidden": "interview tab hidden",
    "tab_unfocused": "interview window not in focus",
    "share_stopped": "screen share stopped",
    "share_not_whole_screen": "a window shared instead of the whole screen",
    "second_monitor": "a second screen attached",
    "left_full_screen": "left full screen",
    "paste": "paste on the interview page",
    "second_voice": "a second voice",
    "screen_content_flagged": "screen content noted after the call",
}


def _times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def summary_lines(report: IntegrityReport) -> list[str]:
    """The report's summary in plain, neutral sentences.

    Integrity reminders and technical prompts are separate lines: a prompt to
    share the screen again is about the set-up, and is never counted with, or
    worded like, a reminder.
    """
    lines = []
    for kind, n in sorted(report.summary.reminders_by_kind.items()):
        lines.append(f"Reminder shown {_times(n)}: {_KIND_WORDS.get(kind.value, kind.value)}.")
    for kind, n in sorted(report.summary.technical_prompts_by_kind.items()):
        lines.append(f"Prompt to share the screen shown {_times(n)} ({_KIND_WORDS.get(kind.value, kind.value)}). "
                     "A set-up prompt, not an integrity reminder.")
    if report.accommodation.gaze_reminders_off:
        lines.append("Gaze reminders were turned off at consent, as the candidate chose; gaze is still recorded.")
    return lines
