"""The closing copy of an open integrity event.

Shapes come from the candidate app's engine (frontend/candidate-app/src/
proctoring/engine.ts): openEvent records with end_seconds null and repeats 1;
closeKind records the same object again with end_seconds set (never below the
start); the repeated-gaze branch closes with repeats raised to the count.
"""

import pytest

from proctoring import intake
from proctoring.intake import CLOSE, DUPLICATE, REFUSED, classify_repeat
from shared.interview_contracts import IntegrityEvent, IntegrityReport
from tests.live.support import ADMIN_PASSWORD
from tests.proctoring.test_intake import IID, client, post  # noqa: F401  (client is a fixture)


def opened(**kw) -> dict:
    """The opening copy exactly as engine.ts openEvent builds it, then any changes."""
    return {"event_id": "ie-ab12cd-1", "kind": "tab_hidden", "source": "browser",
            "start_seconds": 40.0, "end_seconds": None, "repeats": 1, "reminder": "nudged",
            "reminder_index": 1, "detail": "interview tab hidden", "evidence": [], **kw}


def model(raw: dict) -> IntegrityEvent:
    return IntegrityEvent.model_validate(raw)


# -- the rule, on its own --------------------------------------------------------------


def test_a_close_that_only_adds_the_end_is_a_close():
    assert classify_repeat(model(opened()), model(opened(end_seconds=52.5))) == (CLOSE, None)


def test_a_repeated_gaze_close_may_raise_repeats_but_not_lower_them():
    gaze = opened(kind="looking_away", detail="looked away 3 times within a minute")
    assert classify_repeat(model(gaze), model({**gaze, "end_seconds": 70.0, "repeats": 3}))[0] == CLOSE
    raised = model({**gaze, "repeats": 3})
    assert classify_repeat(raised, model({**gaze, "end_seconds": 70.0, "repeats": 2}))[0] == REFUSED


@pytest.mark.parametrize("change", [
    {"kind": "tab_unfocused"},
    {"start_seconds": 41.0},
    {"reminder": "recorded_only", "reminder_index": None},
    {"detail": "something else"},
])
def test_a_close_that_changes_anything_else_is_refused(change):
    outcome, reason = classify_repeat(model(opened()), model(opened(end_seconds=52.5, **change)))
    assert outcome == REFUSED and reason == "closing_copy_changes_fields"


def test_a_close_that_changes_the_source_is_refused():
    # The only source change the contract allows: looking_away, not nudged.
    gaze = opened(kind="looking_away", reminder="recorded_only", reminder_index=None)
    outcome, reason = classify_repeat(model(gaze), model({**gaze, "end_seconds": 50.0,
                                                          "source": "after_call"}))
    assert outcome == REFUSED and reason == "closing_copy_changes_fields"


def test_a_second_close_is_ignored_and_a_reopen_refused():
    closed = model(opened(end_seconds=52.5))
    assert classify_repeat(closed, closed) == (DUPLICATE, None)  # identical resend
    outcome, reason = classify_repeat(closed, model(opened(end_seconds=60.0)))
    assert outcome == DUPLICATE and reason == "already_closed"
    outcome, reason = classify_repeat(closed, model(opened()))
    assert outcome == REFUSED and reason == "reopens_closed_event"


def test_an_open_copy_that_changes_an_open_event_is_refused():
    assert classify_repeat(model(opened()), model(opened(detail="other")))[0] == REFUSED
    assert classify_repeat(model(opened()), model(opened())) == (DUPLICATE, None)


# -- through the route ----------------------------------------------------------------


def report(client) -> IntegrityReport:
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    return IntegrityReport.model_validate(client.get(f"/api/proctoring/interviews/{IID}/report").json())


def test_open_then_close_stores_the_end_and_the_report_uses_the_duration(client):
    first = post(client, [opened()]).json()
    second = post(client, [opened(end_seconds=52.5)]).json()
    assert (first["stored"], second["closed"], second["duplicates"]) == (1, 1, 0)
    [event] = report(client).events
    assert (event.start_seconds, event.end_seconds) == (40.0, 52.5)
    assert event.end_seconds - event.start_seconds == 12.5


def test_open_and_close_in_one_batch(client):
    body = post(client, [opened(), opened(end_seconds=45.0)]).json()
    assert (body["stored"], body["closed"]) == (1, 1)
    assert report(client).events[0].end_seconds == 45.0


def test_a_second_close_never_overwrites_the_first(client):
    post(client, [opened()])
    post(client, [opened(end_seconds=52.5)])
    body = post(client, [opened(end_seconds=99.0)]).json()
    assert body["closed"] == 0 and body["ignored"][0]["event_id"] == "ie-ab12cd-1"
    assert report(client).events[0].end_seconds == 52.5


def test_changed_kind_start_or_source_and_reopen_are_refused_through_the_route(client):
    post(client, [opened()])
    body = post(client, [opened(end_seconds=50.0, kind="tab_unfocused"),
                         opened(end_seconds=50.0, start_seconds=39.0)]).json()
    assert body["closed"] == 0 and len(body["refused"]) == 2
    post(client, [opened(end_seconds=52.5)])
    body = post(client, [opened()]).json()  # reopen
    assert body["refused"][0]["code"] == "reopens_closed_event"
    assert report(client).events[0].end_seconds == 52.5


def test_the_database_guard_alone_refuses_a_second_close(client):
    """`close_stored` only touches a still-open row, whatever the caller decided."""
    from app.db import get_sessionmaker

    post(client, [opened()])
    post(client, [opened(end_seconds=52.5)])

    async def try_again():
        async with get_sessionmaker()() as session:
            applied = await intake.close_stored(session, IID, model(opened(end_seconds=99.0)))
            await session.commit()
            return applied

    assert client.portal.call(try_again) is False
    assert report(client).events[0].end_seconds == 52.5


def test_the_close_puts_a_second_signal_on_the_stream(client):
    from live.emit import web_emitter
    from live.store import all_events

    post(client, [opened()])
    post(client, [opened(end_seconds=52.5)])

    async def signals():
        import asyncio

        for _ in range(100):
            if web_emitter().written >= 2:
                break
            await asyncio.sleep(0.02)
        return [e for e in await all_events(IID) if e.kind.value == "integrity_signal"]

    got = client.portal.call(signals)
    assert [(s.payload.integrity_event_id, s.at_seconds, s.payload.end_seconds) for s in got] == [
        ("ie-ab12cd-1", 40.0, None), ("ie-ab12cd-1", 40.0, 52.5)]
    assert got[1].event_id.endswith(":closed")
