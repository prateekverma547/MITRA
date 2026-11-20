"""The B + C contract additions: end_seconds on integrity signals, the technical
prompt state, the skip event; old data still loads; the accommodation reaches
the report."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from live import relay
from live.emit import make_event
from live.store import all_events, insert_events
from proctoring.report import summary_lines
from shared.interview_contracts import (
    _NOT_NEUTRAL,
    EventSource,
    IntegrityEvent,
    IntegrityReport,
    IntegritySignal,
    IntegritySummary,
    InterviewEvent,
    Skip,
)
from tests.live.support import ADMIN_PASSWORD, fresh_db, seed_interview
from tests.proctoring.test_intake import IID, client, post  # noqa: F401  (client is a fixture)

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"


def share(event_id="sp1", *, reminder="technical_prompt", index=None, kind="share_stopped", **kw):
    return {"event_id": event_id, "kind": kind, "source": "browser", "start_seconds": 30.0,
            "end_seconds": None, "repeats": 1, "reminder": reminder, "reminder_index": index,
            "detail": "screen share stopped; prompt to share again shown", "evidence": [], **kw}


# -- end_seconds on integrity signals ------------------------------------------------------


def _signal(at, end):
    return make_event("int_x", IntegritySignal(integrity_event_id="e1", integrity_kind="tab_hidden",
                                               reminder="recorded_only", end_seconds=end),
                      source=EventSource.BROWSER, at_seconds=at)


def test_end_seconds_filled_missing_and_before_the_start():
    assert _signal(40.0, 52.5).payload.end_seconds == 52.5
    assert _signal(40.0, 40.0).payload.end_seconds == 40.0  # at the start is allowed
    assert _signal(40.0, None).payload.end_seconds is None
    with pytest.raises(ValidationError, match="cannot end before it starts"):
        _signal(40.0, 39.9)


def test_old_stream_events_without_end_seconds_still_load():
    old = json.loads((FIXTURES / "interview_events.json").read_text())
    signal = next(e for e in old if e["kind"] == "integrity_signal")
    signal["payload"].pop("end_seconds")
    assert InterviewEvent.model_validate(signal).payload.end_seconds is None


# -- the technical prompt state ------------------------------------------------------------


def test_the_technical_prompt_is_valid_only_for_the_share_kinds_and_without_an_index():
    assert IntegrityEvent.model_validate(share()).reminder.value == "technical_prompt"
    assert IntegrityEvent.model_validate(share(kind="share_not_whole_screen")).kind.value == "share_not_whole_screen"
    with pytest.raises(ValidationError, match="reminder_index is only for nudged"):
        IntegrityEvent.model_validate(share(index=1))  # a prompt carries no index at all
    with pytest.raises(ValidationError, match="not a technical prompt"):
        IntegrityEvent.model_validate(share(kind="tab_hidden"))


def test_technical_prompts_never_count_as_reminders():
    events = [IntegrityEvent.model_validate(share(f"sp{i}")) for i in range(5)] + [
        IntegrityEvent.model_validate({**share("n1", reminder="nudged", index=1), "kind": "tab_hidden",
                                       "detail": "interview tab hidden"})]
    s = IntegritySummary.from_events(events)
    assert s.technical_prompts_by_kind == {"share_stopped": 5}
    assert s.reminders_by_kind == {"tab_hidden": 1}
    assert s.events_by_kind == {"share_stopped": 5, "tab_hidden": 1}


def test_old_reports_without_the_prompt_count_still_load():
    old = json.loads((FIXTURES / "integrity_report.json").read_text())
    old["summary"].pop("technical_prompts_by_kind")
    report = IntegrityReport.model_validate(old)
    assert report.summary.technical_prompts_by_kind == {}


def test_the_report_line_for_a_prompt_is_its_own_and_neutral():
    events = [IntegrityEvent.model_validate(share(f"sp{i}")) for i in range(2)]
    report = IntegrityReport(interview_id="i", generated_at=datetime.now(UTC), events=events,
                             summary=IntegritySummary.from_events(events))
    lines = summary_lines(report)
    assert lines == ["Prompt to share the screen shown 2 times (screen share stopped). "
                     "A set-up prompt, not an integrity reminder."]
    words = {w.strip(".,;:()").lower() for line in lines for w in line.split()}
    assert not words & _NOT_NEUTRAL


def test_the_intake_takes_any_number_of_prompts_and_the_interim_form(client):
    body = post(client, [share(f"sp{i}") for i in range(6)]).json()  # past any 1-3 cap
    assert body["stored"] == 6 and body["refused"] == []
    interim = post(client, [share("old1", reminder="recorded_only")]).json()  # B's interim form
    assert interim["stored"] == 1
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    report = IntegrityReport.model_validate(client.get(f"/api/proctoring/interviews/{IID}/report").json())
    assert report.summary.technical_prompts_by_kind == {"share_stopped": 6}
    assert report.summary.reminders_by_kind == {}
    assert report.summary.events_by_kind == {"share_stopped": 7}


# -- the skip event -------------------------------------------------------------------------


SKIP_IID = "int_skip00000001"


def skip_event(i: int, question_id=None):
    return make_event(SKIP_IID, Skip(area_id="applied_llm_depth", question_id=question_id),
                      source=EventSource.COORDINATOR, at_seconds=100.0 + i, event_id=f"{SKIP_IID}:s{i}")


def test_skip_shape_and_sources():
    e = skip_event(1)
    assert e.model_dump(mode="json")["payload"] == {"kind": "skip", "area_id": "applied_llm_depth",
                                                    "question_id": None}
    assert skip_event(2, "Q2.1").payload.question_id == "Q2.1"
    with pytest.raises(ValidationError):
        make_event(SKIP_IID, Skip(area_id="a"), source=EventSource.BROWSER, at_seconds=1.0)


async def _never():
    return False


async def test_skip_through_the_store_and_the_relay_with_resume(tmp_path, monkeypatch):
    db = await fresh_db(tmp_path, monkeypatch, "skip.db")
    monkeypatch.setattr(relay, "END_GRACE_SECONDS", 0.05)
    try:
        await seed_interview(SKIP_IID)
        from shared.interview_contracts import InterviewEnded

        ended = make_event(SKIP_IID, InterviewEnded(ended_because="interview_complete", duration_seconds=200),
                           source=EventSource.BOT, at_seconds=200.0, event_id=f"{SKIP_IID}:end")
        await insert_events([skip_event(i) for i in range(4)] + [ended])
        stored = await all_events(SKIP_IID)
        assert [e.kind.value for e in stored] == ["skip"] * 4 + ["interview_ended"]

        async def read(after):
            out = []
            async for chunk in relay.stream(SKIP_IID, after, is_disconnected=_never, poll_seconds=0.01):
                if chunk.startswith("id: "):
                    seq, data = chunk.strip().split("\n")
                    out.append((int(seq[4:]), json.loads(data[6:])))
            return out

        everything = await read(0)
        assert [p["kind"] for _, p in everything] == ["skip"] * 4 + ["interview_ended"]
        resumed = await read(everything[1][0])  # Last-Event-ID after the second skip
        assert resumed == everything[2:]
    finally:
        await db.reset_engine()


# -- the accommodation in the report ----------------------------------------------------------


def test_the_first_batch_accommodation_reaches_the_report(client):
    gaze = {"event_id": "g1", "kind": "looking_away", "source": "browser", "start_seconds": 50.0,
            "end_seconds": 58.0, "repeats": 1, "reminder": "recorded_only", "reminder_index": None,
            "detail": "looked away from the screen (gaze reminders off: accommodation)", "evidence": []}
    first = post(client, [gaze], accommodation={"gaze_reminders_off": True,
                                                "note": "Gaze reminders turned off at consent; gaze still recorded."})
    assert first.json()["accommodation_set"] is True
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    report = IntegrityReport.model_validate(client.get(f"/api/proctoring/interviews/{IID}/report").json())
    assert report.accommodation.gaze_reminders_off is True
    assert report.summary.events_by_kind == {"looking_away": 1}       # recorded
    assert report.summary.reminders_by_kind == {}                     # not shown as a reminder
    assert "Gaze reminders were turned off at consent" in " ".join(summary_lines(report))  # marked
