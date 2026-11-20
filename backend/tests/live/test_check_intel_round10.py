"""C-A9: skip and decline markers sit on the row of the question they belong to.

Fixture: Session A's round 10 replay (labels, question ids, the auditor's
skips and declines with after_turn), with constructed timings and candidate
words (see the fixture's "_about"). The three cases from A-67: a carried-over
skip (Q2.2, asked in prioritization but a product-strategy question), a
carried-over decline (Q2.3), and an ordinary skip (Q3.1).
"""

import copy
import json
from pathlib import Path

from live import check_intel
from live.emit import make_event
from shared.interview_contracts import AreaChanged, EventSource, QuestionLabelled

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "check_intel"
                      / "round10_carried_over.json").read_text())
IID = FIXTURE["interview_id"]


def _events():
    events = [make_event(IID, AreaChanged(area_id=a["area_id"], area_name=a["area_name"]),
                         source=EventSource.COORDINATOR, at_seconds=a["at_seconds"]) for a in FIXTURE["areas"]]
    for lab in FIXTURE["labels"]:
        fields = {k: lab[k] for k in ("turn_index", "label", "question_id", "follow_up_number", "area_id")}
        events.append(make_event(IID, QuestionLabelled(**fields), source=EventSource.AUDITOR,
                                 at_seconds=lab["at_seconds"]))
    return events


def _build(metrics=None):
    return check_intel.build(interview_id=IID, status="completed", started_at=None, ended_at=None,
                             transcript=FIXTURE["transcript"], session_metrics=metrics or FIXTURE["session_metrics"],
                             blueprint=None, events=_events())


def _placed(data) -> dict[int, list[tuple]]:
    return {r["turn_index"]: [(n["type"], n["question_id"], n["text"]) for n in r["notes"]
                              if n["type"] in ("skip", "decline")]
            for r in data["rows"] if any(n["type"] in ("skip", "decline") for n in r["notes"])}


def test_each_marker_is_on_the_row_of_its_own_question():
    data = _build()
    rows = {r["turn_index"]: r for r in data["rows"]}
    assert _placed(data) == {
        8: [("skip", "Q2.2", "question skipped")],        # carried over: on Q2.2's row, not Q3.1's
        10: [("skip", "Q3.1", "question skipped")],       # ordinary: on Q3.1's row
        14: [("decline", "Q2.3", "question declined")],   # carried over: on Q2.3's row, not the next one
    }
    assert rows[8]["question_id"] == "Q2.2" and rows[14]["question_id"] == "Q2.3"
    # The carried-over questions were asked in prioritization: the rows keep that
    # area; the marker is on the question, wherever it was asked.
    assert rows[8]["area_id"] == "prioritization" and rows[14]["area_id"] == "prioritization"


def test_this_differs_from_where_check_intelligence_put_them_in_as_replay():
    before = {(r["row_question"], n["question_id"]) for r in FIXTURE["a_replay_check_intel_before"]
              for n in r["notes"]}
    assert ("Q3.1", "Q2.2") in before and ("Q3.2", "Q2.3") in before  # the defect A-67 recorded
    now = {(next(r["question_id"] for r in _build()["rows"] if r["turn_index"] == t), q)
           for t, notes in _placed(_build()).items() for _, q, _ in notes}
    assert now == {("Q2.2", "Q2.2"), ("Q3.1", "Q3.1"), ("Q2.3", "Q2.3")}


def test_counts_easier_variant_and_closing_area_stay_right():
    data = _build()
    assert data["tiles"]["skips_and_declines"] == {"skips": 2, "declines": 1, "easier_variants": 1}
    rows = {r["turn_index"]: r for r in data["rows"]}
    assert rows[16]["label"] == "easier_variant" and rows[16]["question_id"] == "Q2.3"
    assert rows[18]["area_id"] == "closing"


def test_without_the_skipped_turn_the_question_id_finds_its_latest_row():
    """Q2.2 was asked twice (turn 6 in product strategy, turn 8 carried over):
    the skip at 96 s belongs to the latest asking before it."""
    metrics = copy.deepcopy(FIXTURE["session_metrics"])
    for entry in metrics["auditor"]["skips"] + metrics["auditor"]["declines"]:
        entry["after_turn"] = None
    assert _placed(_build(metrics)) == {8: [("skip", "Q2.2", "question skipped")],
                                        10: [("skip", "Q3.1", "question skipped")],
                                        14: [("decline", "Q2.3", "question declined")]}


def test_an_unknown_question_stays_on_the_turn_where_it_happened():
    metrics = copy.deepcopy(FIXTURE["session_metrics"])
    metrics["coordinator_events"].append(
        {"kind": "question_declined", "at_seconds": 126.0, "source": "coordinator",
         "payload": {"area": 3, "area_name": "Prioritization and trade-offs", "said": "Let's not go there."}})
    metrics["auditor"]["declines"].append({"area": 3, "after_turn": 12, "question_id": None,
                                           "spoken_in_area": 3, "area_source": "current_area"})
    data = _build(metrics)
    assert _placed(data)[12] == [("decline", None, "question declined: question not identified")]
    assert data["tiles"]["skips_and_declines"]["declines"] == 2
