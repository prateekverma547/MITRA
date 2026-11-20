"""C-A18 (C-169): the report's data says how the interview ended, codes only, from the
same builder as the summary page, so the two read one place.

Requirements (Karandeep's brief and Session A's A-101): end reason, ending and who
decided; additive; codes only, no sentences (the pages word them).
Real cases: the ending records of two stored realtime sessions (End button:
int_b9ab94dffb22; did not return after a refused end_interview: int_0d55a3bd8e3a).
Constructed: Mitra's accepted end_interview, and the hard stop.
"""

import re

import pytest
from fastapi.testclient import TestClient

from tests.live.support import ADMIN_PASSWORD, seed_interview

REAL_END_BUTTON = {
    "ended_because": "candidate_ended_button",
    "ending": {"ending": "candidate_ended_button", "hang_up_by": "bot_stopped_speaking"},
    "coordinator_events": [{"kind": "end_button", "at_seconds": 115.23},
                           {"kind": "interview_over", "at_seconds": 115.23, "reason": "candidate_ended_button",
                            "ending": "candidate_ended_button"}]}
REAL_DID_NOT_RETURN = {
    "ended_because": "candidate_did_not_return",
    "ending": {"ending": "candidate_did_not_return", "hang_up_by": "no_one_to_hear"},
    "coordinator_events": [{"kind": "end_interview_refused", "at_seconds": 197.4, "reason": "withdrew",
                            "why": "Not accepted: the candidate has not answered a question from you since they "
                                   "last spoke about stopping."},
                           {"kind": "interview_over", "at_seconds": 389.13, "reason": "candidate_did_not_return",
                            "ending": "candidate_did_not_return"}]}
MITRA_COMPLETED = {"ended_because": "interview_complete",
                   "ending": {"ending": "completed", "hang_up_by": "bot_stopped_speaking"},
                   "coordinator_events": [{"kind": "end_interview_completed", "at_seconds": 1500.0}]}
TIME_LIMIT = {"ended_because": "interview_complete",
              "ending": {"ending": "forced_at_hard_stop", "hang_up_by": "bot_stopped_speaking"},
              "coordinator_events": [{"kind": "hard_stop_reached", "at_seconds": 2700.0}]}
CASES = {
    "int_end_button01": ("completed", REAL_END_BUTTON),
    "int_noreturn0001": ("completed", REAL_DID_NOT_RETURN),
    "int_mitra_done01": ("completed", MITRA_COMPLETED),
    "int_time_limit01": ("completed", TIME_LIMIT),
    "int_not_run_yet1": ("scheduled", None),
}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/ending.db")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, main

    await db.reset_engine()
    with TestClient(main.app) as c:
        for i, (iid, (status, metrics)) in enumerate(CASES.items()):
            c.portal.call(lambda iid=iid, status=status, metrics=metrics, i=i: seed_interview(
                iid, candidate_id=f"cand_end_{i}", status=status, ended_ago=60 if metrics else None,
                started_ago=600 if metrics else None, session_metrics=metrics))
        c.post("/admin/login", json={"password": ADMIN_PASSWORD})
        yield c
    await db.reset_engine()


def ending(client, iid):
    return client.get(f"/interviews/{iid}").json()["ending"]


@pytest.mark.parametrize("iid,end_reason,ending_code,owner,why", [
    ("int_end_button01", "candidate_ended_button", "candidate_ended_button", "code", "candidate_pressed_end_confirmed"),
    ("int_noreturn0001", "candidate_did_not_return", "candidate_did_not_return", "code", "did_not_return_in_grace"),
    ("int_mitra_done01", "interview_complete", "completed", "mitra", "completed"),
    ("int_time_limit01", "interview_complete", "forced_at_hard_stop", "code", "time_limit"),
])
def test_the_report_says_the_reason_the_ending_and_who_decided(client, iid, end_reason, ending_code, owner, why):
    e = ending(client, iid)
    assert (e["ending"]["end_reason"], e["ending"]["ending"]) == (end_reason, ending_code)
    assert (e["decided"]["owner"], e["decided"]["reason"]) == (owner, why)


def test_a_refused_end_interview_is_not_the_ending(client):
    """A real record: Mitra's end_interview was refused; the candidate later dropped."""
    decided = ending(client, "int_noreturn0001")["decided"]
    assert decided["action"] != "end_interview" and decided["owner"] == "code"


def test_an_interview_that_never_ran_has_no_decision(client):
    e = ending(client, "int_not_run_yet1")
    assert e["ending"]["status"] == "scheduled" and e["decided"] is None


@pytest.mark.parametrize("iid", list(CASES))
def test_the_report_and_the_summary_read_one_place(client, iid):
    report = ending(client, iid)
    summary = client.get(f"/api/analytics/summary/interviews/{iid}").json()
    assert report["ending"] == summary["ending"]
    assert report["decided"] == next((d for d in summary["decisions"] if d["phase"] == "ending"), None)


@pytest.mark.parametrize("iid", list(CASES))
def test_codes_only_no_sentences(client, iid):
    def strings(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)
        elif isinstance(o, str):
            yield o

    values = list(strings(ending(client, iid)))
    assert values and all(re.fullmatch(r"[a-z0-9_.:\[\]-]+", v) for v in values), values
