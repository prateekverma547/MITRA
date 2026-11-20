"""Check Intelligence data for one interview, against a real recorded session.

The fixture is the task 19 live run on the real realtime route (scripted voice
as the candidate): real item ids, a real cut reply, real linked waits, real
usage. Expected numbers below are worked out by hand from that file, not from
the code under test.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from live import check_intel
from live.emit import make_event
from live.store import insert_events
from shared.interview_contracts import AreaChanged, EventSource, QuestionLabelled
from tests.live.support import ADMIN_PASSWORD, seed_interview

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "check_intel" / "live_run_int_99bdc4598805.json"
RUN = json.loads(FIXTURE.read_text())
IID = RUN["interview_id"]


def test_percentile_is_linear_interpolation():
    assert check_intel.percentile([], 0.5) is None
    assert check_intel.percentile([4.0], 0.95) == 4.0
    assert check_intel.percentile([1, 2, 3, 4], 0.5) == 2.5
    assert check_intel.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 9.55


def _build(events=(), metrics=None, blueprint=None):
    return check_intel.build(
        interview_id=IID, status="completed", started_at=None, ended_at=None,
        transcript=RUN["transcript"], session_metrics=metrics or RUN["session_metrics"],
        blueprint=blueprint, events=list(events),
    )


def test_real_session_rows_waits_and_cost():
    data = _build()
    # Interviewer turns in the run: 0, 2, 4 (cut), 6.
    assert [r["turn_index"] for r in data["rows"]] == [0, 2, 4, 6]
    waits = {r["turn_index"]: r["wait"] for r in data["rows"]}
    assert waits[0] is None  # the greeting follows no answer
    # From the file: reply 2 had ttfa 9776.3 ms = 8774.6 detect + 1001.7 model.
    assert waits[2] == {"detect_s": 8.775, "model_s": 1.002, "total_s": 9.776, "interrupted": False}
    totals = sorted(w["wait"]["total_s"] for w in data["rows"] if w["wait"])
    raw = sorted(round(w["ttfa_ms"] / 1000, 3) for w in RUN["session_metrics"]["turns"])
    assert totals == raw
    tile = data["tiles"]["reply_wait"]
    assert tile["n"] == 3 and tile["median_s"] == raw[1]
    assert tile["p95_s"] == round(raw[1] + (raw[2] - raw[1]) * 0.9, 3)
    assert data["chart"] == [{"turn_index": r["turn_index"], **r["wait"]}
                             for r in data["rows"] if r["wait"]]
    cut = next(r for r in data["rows"] if r["turn_index"] == 4)
    assert any(n["type"] == "cut" for n in cut["notes"])
    assert data["tiles"]["cost"]["realtime_usd"] == RUN["session_metrics"]["usage"]["estimated_usd"]


def test_without_the_auditor_labels_say_so_and_the_gaps_are_listed():
    data = _build()
    assert {r["label"] for r in data["rows"]} == {"not yet labelled"}
    assert data["labels_note"].startswith("Labels are the auditor's reading after the fact")
    assert data["tiles"]["off_plan_count"] is None and data["tiles"]["follow_ups"] is None
    unfilled = " ".join(u["field"] for u in data["unfilled"])
    for gap in ("label", "off_plan_count", "follow_ups", "coverage.asked", "area_id", "report_usd"):
        assert gap in unfilled


def test_events_fill_labels_areas_and_follow_ups():
    events = [
        make_event(IID, AreaChanged(area_id="strategy", area_name="Product strategy"),
                   source=EventSource.COORDINATOR, at_seconds=30.0),
        make_event(IID, QuestionLabelled(turn_index=2, label="plan", question_id="Q2.1",
                                         area_id="strategy"), source=EventSource.AUDITOR, at_seconds=40.0),
        make_event(IID, QuestionLabelled(turn_index=4, label="follow_up", question_id="Q2.1",
                                         follow_up_number=1, area_id="strategy"),
                   source=EventSource.AUDITOR, at_seconds=47.0),
        make_event(IID, QuestionLabelled(turn_index=6, label="off_plan"),
                   source=EventSource.AUDITOR, at_seconds=55.0),
    ]
    data = _build(events)
    rows = {r["turn_index"]: r for r in data["rows"]}
    assert rows[2]["label"] == "plan" and rows[2]["question_id"] == "Q2.1"
    assert rows[4]["label"] == "follow_up" and rows[4]["follow_up_number"] == 1
    assert rows[6]["area_id"] == "strategy"  # from the area change before it
    assert rows[0]["area_id"] is None        # before any area change
    assert data["tiles"]["off_plan_count"] == 1
    assert data["tiles"]["follow_ups"] == {"per_question": {"Q2.1": 1}, "cap": 3, "over_cap": []}
    assert data["tiles"]["coverage"]["asked_question_ids"] == ["Q2.1"]
    assert any(n["type"] == "area_switch" for n in rows[2]["notes"])


def test_time_notes_tools_and_report_cost_from_metrics():
    metrics = {**RUN["session_metrics"],
               "coordinator_events": [{"kind": "note", "at_seconds": 44.0, "text": "about 5 minutes left"},
                                      {"kind": "end_interview_completed", "at_seconds": 50.5}],
               "silence_events": [{"at_seconds": 20.0, "stage": 1, "action": "nudge"}],
               "text_model_calls": [{"job": "feedback", "model": "gpt-4.1", "cost_usd": 0.0123},
                                    {"job": "feedback", "model": "x", "cost_usd": None}]}
    data = _build(metrics=metrics)
    rows = {r["turn_index"]: r for r in data["rows"]}
    assert [n["type"] for n in rows[2]["notes"]] == ["silence_prompt"]
    assert [n["type"] for n in rows[6]["notes"]] == ["time_note", "tool"]  # 44.0 and 50.5 precede 50.59
    cost = data["tiles"]["cost"]
    assert cost["report_usd"] == 0.0123 and cost["calls_with_unknown_price"] == 1
    assert cost["total_usd"] == round(RUN["session_metrics"]["usage"]["estimated_usd"] + 0.0123, 6)


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/ci.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        c.portal.call(lambda: seed_interview(IID, status="completed", ended_ago=60,
                                             transcript=RUN["transcript"],
                                             session_metrics=RUN["session_metrics"]))
        yield c
    await db.reset_engine()


def test_the_route_reads_a_stored_interview(client):
    assert client.get(f"/api/live/interviews/{IID}/check-intel").status_code == 401
    client.post("/admin/login", json={"password": ADMIN_PASSWORD})
    client.portal.call(insert_events, [make_event(
        IID, QuestionLabelled(turn_index=2, label="plan", question_id="Q2.1"),
        source=EventSource.AUDITOR, at_seconds=40.0, event_id=f"{IID}:label2")])
    r = client.get(f"/api/live/interviews/{IID}/check-intel")
    assert r.status_code == 200
    data = r.json()
    assert data["schema"] == "check_intel.v1" and data["events_stored"] == 1
    assert data["interview"]["ended_because"] == "candidate_dropped"
    assert [r["label"] for r in data["rows"]] == ["not yet labelled", "plan", "not yet labelled",
                                                   "not yet labelled"]
    assert client.get("/api/live/interviews/int_nope/check-intel").status_code == 404


# -- round 2: skips, declines, the closing area, easier variants, the model (B-56) -----------


def _skip_decline_metrics():
    """As the bot stores them: the coordinator's records (no question id) and the
    auditor's lists (question id, same order). Mitra turns: 0 at 8.5 s, 2 at
    35.2 s, 4 at 43.9 s, 6 at 50.6 s."""
    return {**RUN["session_metrics"],
            "coordinator_events": [
                {"kind": "question_declined", "at_seconds": 20.0, "source": "coordinator",
                 "payload": {"area": 2, "area_name": "Strategy", "said": "I'd rather not"}},
                {"kind": "question_skipped", "at_seconds": 40.6, "source": "coordinator",
                 "payload": {"area": 2, "area_name": "Strategy", "said": "skip this one"}},
                {"kind": "question_skipped", "at_seconds": 45.9, "source": "coordinator",
                 "payload": {"area": 3, "area_name": "Metrics", "said": "next please"}}],
            "auditor": {"status": "ok",
                        "declines": [{"area": 2, "after_turn": 0, "question_id": "Q2.1"}],
                        "skips": [{"area": 2, "after_turn": 2, "question_id": "Q2.2"},
                                  {"area": 3, "after_turn": 4, "question_id": None}]}}


def test_skips_and_declines_after_the_call_are_notes_on_their_own_question():
    # C-A9: each on the row of the turn that was skipped or declined (the
    # auditor's after_turn), not on the next question's row as before.
    data = _build(metrics=_skip_decline_metrics())
    notes = {r["turn_index"]: [(n["type"], n["text"], n["question_id"]) for n in r["notes"]
                               if n["type"] in ("skip", "decline")] for r in data["rows"]}
    assert notes == {0: [("decline", "question declined", "Q2.1")], 2: [("skip", "question skipped", "Q2.2")],
                     4: [("skip", "question skipped: question not identified", None)], 6: []}
    assert data["tiles"]["skips_and_declines"] == {"skips": 2, "declines": 1, "easier_variants": None}


def test_a_skip_event_is_a_note_while_the_call_runs_and_is_not_counted_twice_after():
    from shared.interview_contracts import Skip

    skip = make_event(IID, Skip(area_id="strategy", question_id="Q2.2"), source=EventSource.COORDINATOR,
                      at_seconds=40.6)
    running = _build(events=[skip], metrics={k: v for k, v in RUN["session_metrics"].items()
                                             if k not in ("coordinator_events",)})
    # Live: no labelled row for Q2.2 and no skipped turn known, so the skip sits on
    # the turn where it happened (the question asked at 35.2 s), not the next row.
    row2 = next(r for r in running["rows"] if r["turn_index"] == 2)
    assert [(n["type"], n["question_id"]) for n in row2["notes"]] == [("skip", "Q2.2")]
    after = _build(events=[skip], metrics=_skip_decline_metrics())
    assert after["tiles"]["skips_and_declines"]["skips"] == 2  # the event and the record are one skip


def test_a_closing_turn_is_in_the_closing_area_not_the_last_competency():
    events = [
        make_event(IID, AreaChanged(area_id="metrics", area_name="Metrics"), source=EventSource.COORDINATOR,
                   at_seconds=30.0),
        make_event(IID, QuestionLabelled(turn_index=6, label="closing"), source=EventSource.AUDITOR,
                   at_seconds=52.0),  # the auditor's closing label carries no area
        make_event(IID, QuestionLabelled(turn_index=0, label="opening"), source=EventSource.AUDITOR,
                   at_seconds=10.0),
    ]
    areas = {r["turn_index"]: r["area_id"] for r in _build(events=events)["rows"]}
    assert areas[6] == "closing" and areas[0] == "opening"
    assert areas[4] == "metrics"  # unlabelled: still the area it was spoken in


def test_a_turns_own_area_c_a1_is_used_when_it_has_no_label():
    from shared.interview_contracts import TranscriptTurnEvent

    turn = make_event(IID, TranscriptTurnEvent(turn_index=4, speaker="interviewer", text="x", area_id="ownership"),
                      source=EventSource.BOT, at_seconds=44.0)
    changed = make_event(IID, AreaChanged(area_id="metrics", area_name="Metrics"),
                         source=EventSource.COORDINATOR, at_seconds=30.0)
    areas = {r["turn_index"]: r["area_id"] for r in _build(events=[changed, turn])["rows"]}
    assert areas[4] == "ownership" and areas[2] == "metrics"


def test_easier_variants_are_counted_from_the_labels():
    events = [make_event(IID, QuestionLabelled(turn_index=4, label="easier_variant", question_id="Q2.1"),
                         source=EventSource.AUDITOR, at_seconds=45.0),
              make_event(IID, QuestionLabelled(turn_index=2, label="plan", question_id="Q2.1"),
                         source=EventSource.AUDITOR, at_seconds=36.0)]
    data = _build(events=events)
    assert data["tiles"]["skips_and_declines"]["easier_variants"] == 1
    assert {r["turn_index"]: r["label"] for r in data["rows"]}[4] == "easier_variant"


def test_the_response_is_the_contract_model_with_every_field_present():
    from shared.interview_contracts import CheckIntelReport

    data = _build()
    assert CheckIntelReport.model_validate(data).model_dump(mode="json", by_alias=True) == data
    # Every field is sent, so the console's generated types need no optional keys.
    assert set(data["tiles"]["coverage"]) == {"plan_areas", "asked_question_ids", "asked", "plan_questions"}
    assert all("question_id" in n for r in data["rows"] for n in r["notes"])


# -- the ending detail (B-110): a time-limit ending is told from a normal completion -----------

#: Real values: Session A's hard-stop run on the realtime route
#: (track_i_notes/A_checks/ending_audio/hardstop_1/result.json, its `ending`), and a stored
#: realtime session ended with the End button (backend/sessions/int_b9ab94dffb22.json).
REAL_HARD_STOP = {"ending": "forced_at_hard_stop", "hang_up_by": "bot_stopped_speaking", "skips": 0}
REAL_END_BUTTON = {"ending": "candidate_ended_button", "hang_up_by": "bot_stopped_speaking"}


def _ended(ended_because, ending):
    return _build(metrics={**RUN["session_metrics"], "ended_because": ended_because, "ending": ending})["interview"]


def test_a_time_limit_ending_is_told_from_a_normal_completion():
    limit = _ended("interview_complete", REAL_HARD_STOP)
    normal = _ended("interview_complete", {"ending": "completed", "hang_up_by": "bot_stopped_speaking"})
    assert limit["ended_because"] == normal["ended_because"] == "interview_complete"
    assert (limit["ending"], limit["hang_up_by"]) == ("forced_at_hard_stop", "bot_stopped_speaking")
    assert normal["ending"] == "completed"
    button = _ended("candidate_ended_button", REAL_END_BUTTON)
    assert (button["ending"], button["hang_up_by"]) == ("candidate_ended_button", "bot_stopped_speaking")


@pytest.mark.parametrize("stored", [None, "forced_at_hard_stop", ["x"], {}, {"ending": 3, "hang_up_by": None},
                                    {"ending": "", "hang_up_by": {"a": 1}}])
def test_an_ending_not_recorded_or_oddly_shaped_is_none_never_an_error(stored):
    """Older interviews and the pipeline engine store no detail; the real run in
    this file's fixture has none either."""
    interview = _ended("interview_complete", stored)
    assert interview["ending"] is None and interview["hang_up_by"] is None
    real = _build()["interview"]
    assert "ending" not in RUN["session_metrics"] and real["ending"] is None and real["hang_up_by"] is None


def test_a_long_ending_is_cut_to_64_characters_as_the_live_event_is():
    interview = _ended("session_ended", {"ending": "x" * 100, "hang_up_by": "y" * 65})
    assert interview["ending"] == "x" * 64 and interview["hang_up_by"] == "y" * 64


async def test_the_route_sends_the_stored_ending(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/ending.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    await db.reset_engine()
    metrics = {**RUN["session_metrics"], "ended_because": "interview_complete", "ending": REAL_HARD_STOP}
    with TestClient(main.app) as c:
        c.portal.call(lambda: seed_interview(IID, status="completed", ended_ago=60, transcript=RUN["transcript"],
                                             session_metrics=metrics))
        c.post("/admin/login", json={"password": ADMIN_PASSWORD})
        interview = c.get(f"/api/live/interviews/{IID}/check-intel").json()["interview"]
    await db.reset_engine()
    assert interview["ended_because"] == "interview_complete"
    assert (interview["ending"], interview["hang_up_by"]) == ("forced_at_hard_stop", "bot_stopped_speaking")


# -- C-A20: the end_interview design; older finish_interview records still read ----------------


@pytest.mark.parametrize("kind", ["end_interview_completed", "end_interview_withdrew", "end_interview_refused",
                                  "end_interview_after_end", "finish_interview_called"])
def test_ending_tool_records_are_tool_notes_with_their_own_code(kind):
    metrics = {**RUN["session_metrics"], "coordinator_events": [{"kind": kind, "at_seconds": 50.5}]}
    data = _build(metrics=metrics)
    notes = data["after_last_turn"] + [n for r in data["rows"] for n in r["notes"]]
    assert [(n["type"], n["code"]) for n in notes if n["type"] == "tool"] == [("tool", kind)]


def test_the_fake_bot_ends_with_end_interview_as_the_real_one_does():
    source = (Path(__file__).resolve().parents[2] / "scripts" / "fake_bot.py").read_text()
    assert 'ToolCalled(tool="end_interview", outcome="accepted_completed")' in source
    assert 'tool="finish_interview"' not in source
