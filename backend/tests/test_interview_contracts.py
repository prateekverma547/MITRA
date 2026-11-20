"""The three new shared contracts and their fixtures (backend/shared/interview_contracts.py).

Track H builds against the fixtures, so every fixture must validate and survive a
JSON round trip unchanged, and the rules in the module docstring are enforced
here: no score in an IntegrityReport, no credential anywhere, schema_version on
every top-level type, neutral wording.
"""

import copy
import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from shared.interview_contracts import (
    ALLOWED_SOURCES,
    EventKind,
    IntegrityEvent,
    IntegrityReport,
    IntegritySummary,
    InterviewEvent,
    TwoLinks,
)

FIXTURES = Path(__file__).resolve().parents[1] / "shared" / "fixtures"
TOP_LEVEL = [TwoLinks, IntegrityReport, InterviewEvent]
# Wave 1's top-level shapes: the same schema-version and no-credential checks.
from shared.contracts import HRFindingsForTechnical  # noqa: E402
from shared.interview_contracts import (  # noqa: E402
    AfterCallStatus, CaptureManifest, SecondVoiceSignal, WatcherNotesSection)
TOP_LEVEL += [WatcherNotesSection, CaptureManifest, AfterCallStatus, SecondVoiceSignal, HRFindingsForTechnical]


def load(name):
    return json.loads((FIXTURES / name).read_text())


# -- fixtures --------------------------------------------------------------------------

SINGLE = [("two_links.json", TwoLinks), ("integrity_report.json", IntegrityReport),
          ("integrity_report_quiet.json", IntegrityReport)]


@pytest.mark.parametrize("name,model", SINGLE, ids=[s[0] for s in SINGLE])
def test_each_fixture_validates_and_round_trips_unchanged(name, model):
    raw = load(name)
    assert model.model_validate(raw).model_dump(mode="json") == raw
    assert model.model_validate_json(json.dumps(raw)).model_dump(mode="json") == raw


def test_every_event_in_the_stream_fixture_validates_and_round_trips():
    for raw in load("interview_events.json"):
        assert InterviewEvent.model_validate(raw).model_dump(mode="json") == raw


def test_the_stream_fixture_has_an_example_of_every_event_kind():
    kinds = {e["kind"] for e in load("interview_events.json")}
    assert kinds == {k.value for k in EventKind}
    assert set(ALLOWED_SOURCES) == set(EventKind)


def test_integrity_signals_in_the_stream_point_at_real_integrity_events():
    report_ids = {e["event_id"] for e in load("integrity_report.json")["events"]}
    for e in load("interview_events.json"):
        if e["kind"] == "integrity_signal":
            assert e["payload"]["integrity_event_id"] in report_ids


# -- the rules ---------------------------------------------------------------------------


def _property_names(schema) -> set[str]:
    names = set()

    def walk(node):
        if isinstance(node, dict):
            names.update((node.get("properties") or {}).keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return names


def test_an_integrity_report_has_no_score_rating_or_similar_field():
    """Proctoring is evidence for a person, never a score (decision 8)."""
    banned = re.compile(r"score|rating|rank|grade|risk|suspicio|verdict|penalt|likelihood|probability")
    names = _property_names(IntegrityReport.model_json_schema())
    assert names, "the walk found the fields"
    assert not [n for n in names if banned.search(n)]


def test_no_contract_has_a_credential_field():
    # Token COUNTS (input_tokens) are fine; a field that would hold a token is not.
    banned = re.compile(r"password|passcode|secret|api_key|credential|access_token|^token$|_token$")
    for model in TOP_LEVEL:
        assert not [n for n in _property_names(model.model_json_schema()) if banned.search(n)], model


@pytest.mark.parametrize("model", TOP_LEVEL, ids=lambda m: m.__name__)
def test_every_top_level_contract_has_schema_version_1(model):
    assert model.model_fields["schema_version"].default == 1


# -- invalid cases are rejected ---------------------------------------------------------------


def event(**changes):
    raw = copy.deepcopy(load("integrity_report.json")["events"][0])
    raw.update(changes)
    return raw


@pytest.mark.parametrize("changes,message", [
    ({"reminder_index": 4}, "less than or equal to 3"),
    ({"reminder_index": None}, "needs reminder_index"),
    ({"reminder": "recorded_only"}, "only for nudged"),
    ({"end_seconds": 500}, "end_seconds is before start_seconds"),
    ({"kind": "second_voice"}, "only found after the call"),
    ({"source": "after_call"}, "only measured in the browser"),
    ({"detail": "candidate looked suspicious"}, "not intent or emotion"),
    ({"evidence": [{"track": "screen", "thumbnail_key": "https://cdn/x.jpg?sig=abc"}]}, "not a URL"),
], ids=["index_4", "nudged_no_index", "index_without_nudge", "end_before_start",
        "after_call_kind_from_browser", "browser_kind_after_call", "not_neutral", "signed_url"])
def test_invalid_integrity_events_are_rejected(changes, message):
    with pytest.raises(ValidationError, match=message):
        IntegrityEvent.model_validate(event(**changes))


def test_a_summary_that_does_not_match_its_events_is_rejected():
    raw = load("integrity_report.json")
    raw["summary"]["reminders_by_kind"]["paste"] = 1
    with pytest.raises(ValidationError, match="summary does not match"):
        IntegrityReport.model_validate(raw)
    assert IntegritySummary.from_events([]) == IntegritySummary()


def links(**changes):
    raw = load("two_links.json")
    raw.update(changes)
    return raw


@pytest.mark.parametrize("changes,message", [
    ({"expires_at": "2026-11-19T10:00:00"}, "timezone"),
    ({"candidate_join_url": "https://mitra.example.com/interview?meeting=1&password=abc"}, "credentials"),
    ({"live_view_url": "https://mitra.example.com/live?token=abc"}, "credentials"),
    ({"candidate_join_url": "https://user:pw@mitra.example.com/interview"}, "user name or password"),
    ({"duration_minutes": 200}, "less than or equal to 90"),
], ids=["naive_expires_at", "password_in_url", "token_in_url", "userinfo", "too_long"])
def test_invalid_two_links_are_rejected(changes, message):
    with pytest.raises(ValidationError, match=message):
        TwoLinks.model_validate(links(**changes))


def stream_event(index):
    return copy.deepcopy(load("interview_events.json")[index])


def test_an_unknown_event_kind_is_rejected():
    raw = stream_event(1)
    raw["kind"] = raw["payload"]["kind"] = "mood_detected"
    with pytest.raises(ValidationError):
        InterviewEvent.model_validate(raw)


def test_a_payload_that_does_not_match_its_kind_is_rejected():
    raw = stream_event(1)  # interview_started
    raw["kind"] = "interview_ended"
    with pytest.raises(ValidationError, match="payload is 'interview_started' but kind is 'interview_ended'"):
        InterviewEvent.model_validate(raw)


def test_a_payload_missing_its_kinds_fields_is_rejected():
    raw = stream_event(5)  # reply_wait
    del raw["payload"]["turn_index"]
    with pytest.raises(ValidationError):
        InterviewEvent.model_validate(raw)


@pytest.mark.parametrize("payload_changes,message", [
    ({"label": "plan", "question_id": None}, "needs question_id"),
    ({"label": "follow_up", "follow_up_number": 0}, "greater than or equal to 1"),
    ({"label": "follow_up", "follow_up_number": None}, "follow_up_number is required"),
    ({"label": "off_plan", "question_id": "Q2.1", "follow_up_number": None}, "no question_id"),
], ids=["plan_without_id", "follow_up_0", "follow_up_without_number", "off_plan_with_id"])
def test_invalid_question_labels_are_rejected(payload_changes, message):
    raw = stream_event(10)  # follow_up label
    raw["payload"].update(payload_changes)
    with pytest.raises(ValidationError, match=message):
        InterviewEvent.model_validate(raw)


def test_a_follow_up_past_the_cap_is_recorded_not_refused():
    """Round 18 (A-115): the cap of 3 limits Mitra, not the record; 4 and 5 are kept."""
    for n in (3, 4, 5, 9):
        raw = stream_event(10)
        raw["payload"].update({"label": "follow_up", "follow_up_number": n})
        assert InterviewEvent.model_validate(raw).payload.follow_up_number == n


def test_only_the_auditor_labels_questions():
    raw = stream_event(7)
    raw["source"] = "bot"
    with pytest.raises(ValidationError, match="cannot come from bot"):
        InterviewEvent.model_validate(raw)


def test_wall_time_must_be_utc():
    raw = stream_event(1)
    raw["wall_time"] = "2026-11-18T15:30:00+05:30"
    with pytest.raises(ValidationError, match="must be UTC"):
        InterviewEvent.model_validate(raw)
    raw["wall_time"] = "2026-11-18T10:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        InterviewEvent.model_validate(raw)


@pytest.mark.parametrize("model", TOP_LEVEL, ids=lambda m: m.__name__)
def test_each_contract_produces_a_json_schema_for_the_typescript_generator(model: type[BaseModel]):
    schema = model.model_json_schema()
    assert schema["type"] == "object" and "schema_version" in schema["properties"]
