"""The ending samples for Sessions B and D (tests/fixtures/endings, round 16): each one
parses against the current contracts, says honestly whether it is real, and carries no
credential."""

import json
import re
from pathlib import Path

import pytest

from shared.contracts import FeedbackReport, Transcript
from shared.interview_contracts import EndReason

DIR = Path(__file__).resolve().parent / "fixtures" / "endings"
EXPECTED = {"completed": ("interview_complete", "completed", True),
            "withdrew": ("candidate_withdrew", "withdrew", True),
            "end_button": ("candidate_ended_button", "candidate_ended_button", True),
            "did_not_return": ("candidate_did_not_return", "candidate_did_not_return", True),
            "silence_close": ("abandoned_by_candidate", "silence_close", True),
            "hard_stop_CONSTRUCTED": ("interview_complete", "forced_at_hard_stop", False)}


#: Karandeep's choice (round 16): these two were not re-run on round 16's code. Their records
#: are round 15's, whose harness kept a trimmed transcript (no at_seconds), so they get a
#: RELAXED transcript check and must say so in how_made.
TRIMMED = {"did_not_return", "silence_close"}
TRIMMED_NOTE = "ROUND 15 CODE AND A TRIMMED TRANSCRIPT"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_sample_parses_and_says_what_it_is(name):
    d = json.loads((DIR / f"{name}.json").read_text())
    reason, ending, real = EXPECTED[name]
    assert d["ended_because"] == reason and EndReason(reason)
    assert d["ending"]["ending"] == ending and d["real"] is real
    assert ("CONSTRUCTED" in d["how_made"]) is (not real)
    turns = d["transcript_turns"]
    if name in TRIMMED:
        assert d["transcript"] == "trimmed" and TRIMMED_NOTE in d["how_made"], "a trimmed record says so"
        assert turns and all({"index", "speaker", "text"} <= set(t) for t in turns)
        assert all(t["speaker"] in ("interviewer", "candidate") for t in turns)
    else:
        assert d["transcript"] == "stored" and TRIMMED_NOTE not in d["how_made"]
        Transcript.model_validate({"interview_id": d["interview_id"], "turns": turns})
    if d["feedback_report"]:
        FeedbackReport.model_validate(d["feedback_report"])


def test_no_sample_carries_a_credential():
    pattern = re.compile(r'(?i)("password"|"token"\s*:|bearer |sk-[a-z0-9]{10,}|eyJ[a-zA-Z0-9_-]{20,})')
    for f in DIR.glob("*.json"):
        assert not pattern.search(f.read_text()), f.name
    assert pattern.search('{"token": "x"}'), "the search can find one"


def test_the_readme_lists_every_sample():
    readme = (DIR / "README.md").read_text()
    assert all(f"`{name}.json`" in readme for name in EXPECTED)
