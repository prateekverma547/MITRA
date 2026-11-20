"""Round 2 contract additions (Session A's C-A1 to C-A6, the safety-net record,
the console's reads). Requirements: every addition is additive, so events,
outcomes, plans and interviews stored before it still load; the new fields hold
exactly what Session A stores today in its stand-ins (session_metrics, the
auditor block), so switching is a move, not a reshaping; nothing new carries
the meeting password.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.contracts import InterviewBlueprint, QuestionCut, SectionKind, SectionOutcome
from shared.interview_contracts import (
    CheckIntelReport,
    CleanedTranscript,
    EndReason,
    InterviewEnded,
    InterviewEvent,
    InterviewHeader,
    QuestionLabel,
    QuestionLabelled,
    RecordingInfo,
    TranscriptTurnEvent,
    WatchRefusal,
)

FIXTURES = Path(__file__).resolve().parents[1] / "shared" / "fixtures"


def _events() -> list[dict]:
    return json.loads((FIXTURES / "interview_events.json").read_text())


def _old(event: dict, *keys: str) -> dict:
    """The event as stored before round 2: the new payload keys absent."""
    old = json.loads(json.dumps(event))
    for key in keys:
        old["payload"].pop(key, None)
    return old


# -- C-A1: the area on a transcript turn ---------------------------------------------------


def test_c_a1_a_turn_carries_its_area_and_old_turns_load_without_one():
    turn = next(e for e in _events() if e["kind"] == "transcript_turn")
    assert InterviewEvent.model_validate(turn).payload.area_id == turn["payload"]["area_id"]
    old = InterviewEvent.model_validate(_old(turn, "area_id"))
    assert old.payload.area_id is None
    assert TranscriptTurnEvent(turn_index=3, speaker="interviewer", text="Hi", area_id="closing").area_id == "closing"


# -- C-A2 and C-A3: how the interview ended -------------------------------------------------


def test_c_a2_the_end_event_carries_the_ending_path_and_old_ones_load():
    ended = next(e for e in _events() if e["kind"] == "interview_ended")
    new = InterviewEvent.model_validate(ended).payload
    assert (new.ending, new.hang_up_by) == ("model_spoke", "bot_stopped_speaking")
    old = InterviewEvent.model_validate(_old(ended, "ending", "hang_up_by")).payload
    assert old.ending is None and old.hang_up_by is None


@pytest.mark.parametrize("ending", ["model_silent_code_goodbye", "model_spoke", "forced_at_hard_stop",
                                    "withdrawal", "candidate_ended_button"])
def test_c_a2_every_ending_the_coordinator_records_fits(ending):
    # The values in bot/realtime_coordinator.py ENDING_* (read, not imported, so
    # this test does not depend on Session A's module loading).
    assert InterviewEnded(ended_because="interview_complete", duration_seconds=1, ending=ending).ending == ending


def test_c_a3_the_end_button_is_its_own_reason():
    assert EndReason("candidate_ended_button") is EndReason.CANDIDATE_ENDED_BUTTON
    e = InterviewEnded(ended_because="candidate_ended_button", duration_seconds=12.5,
                       ending="candidate_ended_button", hang_up_by="bot_stopped_speaking")
    assert e.model_dump(mode="json")["ended_because"] == "candidate_ended_button"
    # The value the bot sends before switching still loads.
    assert InterviewEnded(ended_because="candidate_withdrew", duration_seconds=1).ended_because \
        is EndReason.CANDIDATE_WITHDREW


# -- C-A4: the easier-variant label ---------------------------------------------------------


def test_c_a4_easier_variant_names_the_declined_question():
    label = QuestionLabelled(turn_index=9, label="easier_variant", question_id="Q2.1", area_id="metrics")
    assert label.label is QuestionLabel.EASIER_VARIANT
    with pytest.raises(ValidationError):
        QuestionLabelled(turn_index=9, label="easier_variant")  # which question was declined?
    with pytest.raises(ValidationError):
        QuestionLabelled(turn_index=9, label="easier_variant", question_id="Q2.1", follow_up_number=1)


def test_c_a4_the_fixture_has_an_easier_variant_and_old_labels_still_load():
    labels = [e for e in _events() if e["kind"] == "question_labelled"]
    assert any(e["payload"]["label"] == "easier_variant" for e in labels)
    for e in labels:
        InterviewEvent.model_validate(e)


# -- C-A5: what the auditor read in each section ---------------------------------------------


def _a_auditor_outcome() -> dict:
    """A section outcome as bot/auditor.py `section_outcomes` stores it today:
    the contract's fields plus an extra `auditor` block (A-44 point 4)."""
    out = SectionOutcome(section_id="metrics", kind=SectionKind.COMPETENCY, competency_id="metrics",
                         declined_turns=1).model_dump(mode="json")
    for key in ("questions_asked", "skipped_question_ids", "declined_question_ids", "easier_variants"):
        out.pop(key)  # as stored before round 2
    out["auditor"] = {"area_name": "Metrics", "questions_asked": ["Q4.1", "Q4.2"],
                      "labels": [{"label": "plan", "question_id": "Q4.1"}],
                      "skipped_question_ids": ["Q4.3", None], "declined_question_ids": ["Q4.2"],
                      "easier_variants": 1, "claims": [], "contradictions": []}
    return out


def test_c_a5_an_outcome_stored_with_the_stand_in_block_still_loads():
    old = SectionOutcome.model_validate(_a_auditor_outcome())
    assert old.questions_asked == [] and old.skipped_question_ids == [] and old.easier_variants == 0


def test_c_a5_the_stand_in_block_moves_into_the_fields_unchanged():
    stored = _a_auditor_outcome()
    block = stored.pop("auditor")
    moved = SectionOutcome.model_validate({**stored, **{k: block[k] for k in (
        "questions_asked", "skipped_question_ids", "declined_question_ids", "easier_variants")}})
    assert moved.questions_asked == ["Q4.1", "Q4.2"]
    assert moved.skipped_question_ids == ["Q4.3", None]  # an unknown question stays None
    assert moved.declined_question_ids == ["Q4.2"] and moved.easier_variants == 1
    assert SectionOutcome.model_validate(moved.model_dump(mode="json")) == moved
    with pytest.raises(ValidationError):
        SectionOutcome(section_id="x", kind=SectionKind.OPENING, easier_variants=-1)


# -- C-A6: the cleaned transcript -------------------------------------------------------------


RAW = {"interview_id": "int_x", "turns": [
    {"index": 0, "speaker": "interviewer", "text": "Hello, I'm Mitra.", "at_seconds": 1.0},
    {"index": 1, "speaker": "candidate", "text": "um so I I led the Kafka migration", "at_seconds": 4.0,
     "audio_start_seconds": 3.1, "audio_end_seconds": 6.0, "item_id": "item_1"},
]}


async def test_c_a6_session_as_cleaned_transcript_loads_into_the_contract_as_it_is():
    """feedback/clean.py's real output (Session A's code, called read-only with a
    stand-in model reply) validates against CleanedTranscript with no reshaping."""
    from types import SimpleNamespace

    from feedback.clean import clean_transcript

    async def model(**kwargs):
        return SimpleNamespace(payload={"turns": [{"index": 1, "text": "So I led the Kafka migration.",
                                                   "corrections": [], "uncertain": []}]},
                               record=SimpleNamespace(cost_usd=0.0001))

    from shared.contracts import Transcript

    # As the bot stores it (every TranscriptTurn field present), and as an
    # older record without pre_interview: both must load.
    current = Transcript.model_validate(RAW).model_dump(mode="json")
    for raw in (current, RAW):
        stored = await clean_transcript(raw, request=model)
        assert stored["status"] == "ok"
        cleaned = CleanedTranscript.model_validate(stored)
        assert [t.guard for t in cleaned.turns] == ["copied", "ok"]
        assert cleaned.turns[1].text == "So I led the Kafka migration."
        assert cleaned.turns[1].audio_start_seconds == 3.1 and cleaned.turns[1].item_id == "item_1"
        assert [t.pre_interview for t in cleaned.turns] == [False, False]
    failed = await clean_transcript(RAW, request=_raises)
    assert CleanedTranscript.model_validate(failed).status == "missing"


async def _raises(**kwargs):
    raise RuntimeError("model unavailable")


def test_c_a6_a_guard_failure_is_a_list_of_problems_and_missing_has_no_turns():
    turn = {"index": 1, "speaker": "candidate", "text": "raw words", "guard": ["number dropped: ['3']"]}
    assert CleanedTranscript(status="ok", turns=[turn]).turns[0].guard == ["number dropped: ['3']"]
    with pytest.raises(ValidationError):
        CleanedTranscript(status="missing", turns=[turn])
    with pytest.raises(ValidationError):
        CleanedTranscript(status="ok", turns=[turn, turn])  # one cleaned turn per raw turn


async def test_c_a6_the_column_is_added_to_an_old_database_and_old_rows_load(tmp_path, monkeypatch):
    from sqlalchemy import text

    from tests.live.support import fresh_db, seed_interview

    db = await fresh_db(tmp_path, monkeypatch, "cleaned.db")
    await seed_interview("int_cleaned00001", status="completed", ended_ago=10)
    async with db.get_engine().begin() as conn:  # the database as it was before round 2
        await conn.execute(text("ALTER TABLE interviews DROP COLUMN cleaned_transcript"))
    await db.create_all()  # what start-up runs: the missing column is added
    async with db.get_sessionmaker()() as session:
        row = await session.get(db.Interview, "int_cleaned00001")
        assert row.cleaned_transcript is None  # an old interview: readers use the raw transcript
        row.cleaned_transcript = CleanedTranscript(status="missing", reason="model unavailable").model_dump(mode="json")
        await session.commit()
    async with db.get_sessionmaker()() as session:
        row = await session.get(db.Interview, "int_cleaned00001")
        assert CleanedTranscript.model_validate(row.cleaned_transcript).reason == "model unavailable"
    await db.reset_engine()


# -- the safety net's record on the plan ------------------------------------------------------


def test_a_plan_stored_before_question_cuts_loads_with_none():
    raw = json.loads((FIXTURES / "interview_blueprint.json").read_text())
    raw.pop("question_cuts")
    assert InterviewBlueprint.model_validate(raw).question_cuts == []
    cut = QuestionCut(competency_id="c", question_id="Q2.1", original="A, and B?", cut_to="A?")
    raw["question_cuts"] = [cut.model_dump()]
    assert InterviewBlueprint.model_validate(raw).question_cuts == [cut]


# -- the console's reads ----------------------------------------------------------------------


def _field_names(model) -> set[str]:
    schema = model.model_json_schema(mode="serialization")
    names = set(schema.get("properties", {}))
    for definition in schema.get("$defs", {}).values():
        names |= set(definition.get("properties", {}))
    return names


@pytest.mark.parametrize("model", [InterviewHeader, RecordingInfo, WatchRefusal, CheckIntelReport,
                                   CleanedTranscript])
def test_no_new_console_shape_can_carry_the_meeting_password(model):
    names = {n.lower() for n in _field_names(model)}
    assert not any("password" in n or "token" in n for n in names), names
    assert "meeting_id" not in names


def test_recording_info_takes_only_a_plain_path_on_this_server():
    ok = RecordingInfo(interview_id="i", status="stored", media_url="/api/live/interviews/i/recording/media")
    assert ok.media_url.endswith("/media")
    for bad in ("/api/live/interviews/i/recording/media?token=abc", "https://x/y.mp4", "/a#t=3"):
        with pytest.raises(ValidationError):
            RecordingInfo(interview_id="i", status="stored", media_url=bad)


def test_watch_refusal_needs_a_known_reason():
    assert WatchRefusal(detail="Not started yet", reason="not_started").reason.value == "not_started"
    with pytest.raises(ValidationError):
        WatchRefusal(detail="x", reason="busy")


def test_check_intel_report_serialises_its_tag_as_schema():
    report = CheckIntelReport.model_validate({
        "schema": "check_intel.v1", "interview": {"interview_id": "i", "status": "completed"},
        "labels_note": "n", "tiles": {"reply_wait": {"method": "m"}, "coverage": {}, "cost": {"source": "events"}}})
    dumped = report.model_dump(mode="json", by_alias=True)
    assert dumped["schema"] == "check_intel.v1" and "schema_tag" not in dumped
    assert dumped["tiles"]["skips_and_declines"] == {"skips": 0, "declines": 0, "easier_variants": None}


# -- C-A7: how each quote matched (QuoteMatch) ---------------------------------------------------


def test_c_a7_both_new_match_values_validate_and_are_in_the_json_schema():
    from shared.contracts import EvidenceQuote, FeedbackReport, QuoteMatch

    assert QuoteMatch("number_value") is QuoteMatch.NUMBER_VALUE
    assert QuoteMatch("declared_fix") is QuoteMatch.DECLARED_FIX
    for value in ("number_value", "declared_fix"):
        q = EvidenceQuote.model_validate({"text": "by 38%", "turn_index": 3, "at_seconds": 12.0,
                                          "speaker": "candidate", "match": value})
        assert q.model_dump(mode="json")["match"] == value
    schema = json.dumps(FeedbackReport.model_json_schema(mode="serialization"))
    assert '"number_value"' in schema and '"declared_fix"' in schema


def test_c_a7_reports_stored_before_still_load():
    """The shared fixture is a report as stored before C-A7 (exact and normalised only)."""
    from shared.contracts import FeedbackReport, QuoteMatch

    raw = json.loads((FIXTURES / "feedback_report.json").read_text())
    report = FeedbackReport.model_validate(raw)
    matches = {q.match for s in report.competency_scores for q in s.evidence}
    assert matches <= {QuoteMatch.EXACT, QuoteMatch.NORMALISED, None} and QuoteMatch.EXACT in matches
    assert report.model_dump(mode="json") == raw  # an exact round trip, unchanged


def test_c_a7_session_as_scoring_now_stores_the_precise_rule():
    """Session A's build_report (feedback/score.py, read-only, no model call) on a
    quote that matches by number value and one that matches by a declared fix:
    the report as feedback/run.py stores it (model_dump(mode="json")) carries
    number_value and declared_fix, no longer normalised."""
    from feedback.clean import declared_name_fixes
    from feedback.score import build_report
    from tests.test_quote_matching import QUOTE, RAW, cleaned, transcript

    bp = InterviewBlueprint.model_validate(json.loads(
        (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text()))
    cid = bp.evaluation_spec.competencies[0].id
    payload = {"competency_scores": [{"competency_id": cid, "score": 3, "coverage": "partial", "rationale": "r",
                                      "evidence": [{"text": QUOTE, "turn_index": 1},
                                                   {"text": "we cut failures by 38%", "turn_index": 3}]}],
               "summary": "s", "recommendation": "limited_evidence"}
    report = build_report(interview_id="int_ca7", blueprint_id="bp", spec=bp.evaluation_spec,
                          transcript=transcript(RAW, "and we cut failures by thirty-eight percent"),
                          payload=payload,
                          declared_fixes=declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")])))
    stored = report.model_dump(mode="json")
    by_text = {q["text"]: q["match"] for q in stored["competency_scores"][0]["evidence"]}
    assert by_text == {QUOTE: "declared_fix", "we cut failures by 38%": "number_value"}
