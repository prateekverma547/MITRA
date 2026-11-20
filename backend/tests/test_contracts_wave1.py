"""Wave 1 contracts (phase 3): rejoin, HR findings, watchers' notes, capture,
after-call results. Requirements: each shape validates its fixture exactly;
old data (events, reports, plans, Check Intelligence) still loads; the rules
each shape states are enforced; the JSON schema carries them all; no shared
shape holds a credential (the rejoin pass shapes are route models, C-115).
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.contracts import (
    REJOIN_COUNTS_FROM_SECONDS,
    ConnectionRecord,
    DropRecord,
    FeedbackReport,
    HRFindingsForTechnical,
    HRFindingsRefinement,
    HRFindingUse,
    InterviewBlueprint,
)
from shared.interview_contracts import (
    AfterCallCheck,
    AfterCallStatus,
    CandidateAudioCapture,
    CandidateReturned,
    CaptureManifest,
    CheckIntelReport,
    EndReason,
    EventKind,
    InterviewEvent,
    SecondVoiceSignal,
    WatcherNotesSection,
)

FIXTURES = Path(__file__).resolve().parents[1] / "shared" / "fixtures"
WAVE1 = json.loads((FIXTURES / "wave1.json").read_text())
SHAPES = {"connection_record": ConnectionRecord, "hr_findings_for_technical": HRFindingsForTechnical,
          "hr_findings_refinement": HRFindingsRefinement, "watcher_notes": WatcherNotesSection,
          "capture_manifest": CaptureManifest, "after_call_status": AfterCallStatus,
          "second_voice_signal": SecondVoiceSignal}


@pytest.mark.parametrize("key", sorted(SHAPES))
def test_each_wave1_fixture_round_trips_exactly(key):
    model = SHAPES[key]
    assert model.model_validate(WAVE1[key]).model_dump(mode="json") == WAVE1[key]


# -- old data still loads ------------------------------------------------------------------------


def test_reports_and_plans_from_before_wave1_load_with_nothing_new():
    report = json.loads((FIXTURES / "feedback_report.json").read_text())
    report.pop("connection")
    assert FeedbackReport.model_validate(report).connection is None
    plan = json.loads((FIXTURES / "interview_blueprint.json").read_text())
    plan.pop("hr_findings")
    assert InterviewBlueprint.model_validate(plan).hr_findings is None


def test_the_event_fixture_has_every_new_kind_and_the_old_events_are_unchanged():
    events = json.loads((FIXTURES / "interview_events.json").read_text())
    kinds = {e["kind"] for e in events}
    assert {"candidate_dropped", "candidate_returned", "rejoin_limit_reached", "note_added"} <= kinds
    for e in events:
        InterviewEvent.model_validate(e)


def test_check_intel_reports_from_before_wave1_load():
    data = {"schema": "check_intel.v1", "interview": {"interview_id": "i", "status": "completed"},
            "labels_note": "n", "tiles": {"reply_wait": {"method": "m"}, "coverage": {}, "cost": {"source": "events"}}}
    assert CheckIntelReport.model_validate(data).hr_findings is None


# -- rejoin ---------------------------------------------------------------------------------------


def test_the_new_ending_reasons():
    assert EndReason("candidate_did_not_return") is EndReason.CANDIDATE_DID_NOT_RETURN
    assert EndReason("rejoin_limit_reached") is EndReason.REJOIN_LIMIT_REACHED


@pytest.mark.parametrize("gap,counted,number,ok", [
    (14.0, True, 1, True), (4.5, False, None, True), (REJOIN_COUNTS_FROM_SECONDS, True, 2, True),
    (4.5, True, 1, False),      # a blip cannot count
    (14.0, False, None, False),  # 10 s or more always counts
    (14.0, True, None, False),   # a counted return has its number
])
def test_a_return_counts_exactly_from_ten_seconds(gap, counted, number, ok):
    make = lambda: CandidateReturned(gap_seconds=gap, same_participant=True, counted=counted,  # noqa: E731
                                     rejoin_number=number)
    if ok:
        assert make().counted is counted
    else:
        with pytest.raises(ValidationError):
            make()


def test_drop_records_are_consistent_and_the_count_matches():
    record = ConnectionRecord.model_validate(WAVE1["connection_record"])
    assert record.rejoin_count == 1 and record.grace_seconds == 90 and record.rejoin_limit == 3
    assert record.drops[2].ended_seconds is None  # never came back
    bad = copy.deepcopy(WAVE1["connection_record"])
    bad["rejoin_count"] = 2
    with pytest.raises(ValidationError, match="rejoin_count"):
        ConnectionRecord.model_validate(bad)
    with pytest.raises(ValidationError, match="gap_seconds"):
        DropRecord(started_seconds=10, ended_seconds=30, gap_seconds=5, detected_by="daily_left", counted=False)
    with pytest.raises(ValidationError, match="no return"):
        DropRecord(started_seconds=10, detected_by="daily_error", counted=True)


@pytest.mark.parametrize("kind,source", [("candidate_dropped", "browser"), ("note_added", "bot"),
                                         ("rejoin_limit_reached", "server")])
def test_the_new_kinds_come_only_from_their_sources(kind, source):
    events = json.loads((FIXTURES / "interview_events.json").read_text())
    raw = copy.deepcopy(next(e for e in events if e["kind"] == kind))
    raw["source"] = source
    with pytest.raises(ValidationError, match="cannot come from"):
        InterviewEvent.model_validate(raw)


def test_the_rejoin_pass_shapes_are_route_models_and_never_hold_the_password():
    from app.interviews import JoinResponse, RejoinExchangeRequest, RejoinExchangeResponse

    for model in (JoinResponse, RejoinExchangeRequest, RejoinExchangeResponse):
        assert not any("password" in name for name in model.model_fields), model
    assert JoinResponse.model_fields["rejoin_pass"].default is None  # old join answers still fit
    import shared.interview_contracts as shared_module

    assert not hasattr(shared_module, "RejoinExchangeResponse")  # credentials stay out of shared/


# -- HR findings ------------------------------------------------------------------------------------


def test_hr_findings_input_mirrors_track_h_with_no_cap():
    """C-A13: Track H does not cap the findings, so neither does the contract."""
    assert HRFindingsForTechnical(candidate_id="c").findings_for_technical == []  # none: plan unchanged
    many = [f"topic {i}" for i in range(12)]
    assert HRFindingsForTechnical(candidate_id="c", findings_for_technical=many).findings_for_technical == many
    assert len(HRFindingsRefinement(findings=[{"finding": f, "outcome": "not_technical"} for f in many]).findings) == 12
    with pytest.raises(ValidationError):
        HRFindingsForTechnical(candidate_id="c", findings_for_technical=[""])  # an empty finding is still refused
    from shared import contracts

    assert contracts.MAX_HR_FINDINGS is None  # the name stays for old imports; no cap


def test_each_finding_is_probed_with_an_area_and_question_or_left_out_with_neither():
    assert HRFindingUse(finding="f", outcome="probed", area_id="metrics", question_id="Q5.2").question_id == "Q5.2"
    with pytest.raises(ValidationError, match="names its area"):
        HRFindingUse(finding="f", outcome="probed", area_id="metrics")
    with pytest.raises(ValidationError, match="maps to no area"):
        HRFindingUse(finding="f", outcome="not_technical", area_id="metrics")


def test_check_intelligence_carries_the_plans_hr_record():
    from live import check_intel

    plan = json.loads((FIXTURES / "interview_blueprint.json").read_text())
    plan["hr_findings"] = WAVE1["hr_findings_refinement"]
    data = check_intel.build(interview_id="i", status="completed", started_at=None, ended_at=None,
                             transcript=None, session_metrics=None, blueprint=plan, events=[])
    assert data["hr_findings"] == WAVE1["hr_findings_refinement"]


# -- notes, capture, after-call ------------------------------------------------------------------------


def test_watchers_notes_are_never_scored_and_belong_to_one_interview():
    section = WatcherNotesSection.model_validate(WAVE1["watcher_notes"])
    assert section.scored is False
    bad = copy.deepcopy(WAVE1["watcher_notes"])
    bad["scored"] = True
    with pytest.raises(ValidationError):
        WatcherNotesSection.model_validate(bad)
    bad = copy.deepcopy(WAVE1["watcher_notes"])
    bad["notes"][0]["interview_id"] = "int_other"
    with pytest.raises(ValidationError, match="belongs to this interview"):
        WatcherNotesSection.model_validate(bad)


@pytest.mark.parametrize("path", ["/srv/x.wav", "../x.wav", "a/../../x.wav", "https://cdn/x.wav", "a\\..\\x.wav"])
def test_capture_paths_are_relative_and_cannot_escape(path):
    with pytest.raises(ValidationError):
        CandidateAudioCapture(relative_path=path, start_seconds=0, duration_seconds=1)


def test_capture_has_room_for_screen_frames_later():
    manifest = CaptureManifest.model_validate(WAVE1["capture_manifest"])
    assert manifest.screen_frames == [] and manifest.candidate_audio.sample_rate_hz == 16000


def test_a_failed_or_skipped_check_says_why_and_each_check_appears_once():
    with pytest.raises(ValidationError, match="says why"):
        AfterCallCheck(check="second_voice", state="failed")
    bad = copy.deepcopy(WAVE1["after_call_status"])
    bad["checks"].append(copy.deepcopy(bad["checks"][0]))
    with pytest.raises(ValidationError, match="once"):
        AfterCallStatus.model_validate(bad)


def test_second_voice_wording_is_neutral_and_confidence_is_bounded():
    raw = copy.deepcopy(WAVE1["second_voice_signal"])
    raw["wording"] = "The candidate was cheating with a helper."
    with pytest.raises(ValidationError, match="not intent or emotion"):
        SecondVoiceSignal.model_validate(raw)
    raw = copy.deepcopy(WAVE1["second_voice_signal"])
    raw["confidence"] = 1.5
    with pytest.raises(ValidationError):
        SecondVoiceSignal.model_validate(raw)


# -- the JSON schema carries everything ---------------------------------------------------------------


def test_the_json_schema_includes_every_wave1_shape_and_value():
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema([(m, "serialization") for m in (
        FeedbackReport, InterviewBlueprint, InterviewEvent, CheckIntelReport, *SHAPES.values())])
    text = json.dumps(schema)
    for name in ("ConnectionRecord", "DropRecord", "HRFindingsRefinement", "HRFindingUse", "HRFindingsForTechnical",
                 "WatcherNote", "WatcherNotesSection", "CaptureManifest", "CandidateAudioCapture", "ScreenFrameCapture",
                 "AfterCallStatus", "AfterCallCheck", "SecondVoiceSignal", "TimeRange", "CandidateDropped",
                 "CandidateReturned", "RejoinLimitReached", "NoteAdded"):
        assert f'"{name}"' in text, name
    for value in ("candidate_did_not_return", "rejoin_limit_reached", "status_readings", "daily_left",
                  "daily_error", "skipped", "second_voice", "note_added"):
        assert f'"{value}"' in text, value
    assert {k.value for k in EventKind} >= {"candidate_dropped", "candidate_returned", "rejoin_limit_reached",
                                            "note_added"}


# -- the bot's token is never on its command line (D-42) ------------------------------------------------


async def test_the_bot_is_started_with_its_token_on_standard_input_not_its_command_line(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app import interviews
    from tests.live.support import fresh_db, seed_interview

    await fresh_db(tmp_path, monkeypatch, "spawn.db")
    await seed_interview("int_spawn0000001", status="scheduled", started_ago=None)
    seen: dict = {}

    class Stdin:
        def __init__(self):
            self.data, self.closed = b"", False

        def write(self, b):
            self.data += b

        async def drain(self):
            pass

        def close(self):
            self.closed = True

    async def fake_exec(*argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        seen["stdin"] = Stdin()
        return SimpleNamespace(stdin=seen["stdin"], pid=1, returncode=None, wait=lambda: None)

    async def fake_token(**kw):
        return "probe-token-value"

    monkeypatch.setattr(interviews.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(interviews, "create_meeting_token", fake_token)
    monkeypatch.setattr(interviews, "watch_bot_exit", lambda *a, **k: _never())
    monkeypatch.setattr(interviews.registry, "register", lambda *a: None)
    await interviews._start_bot(interview_id="int_spawn0000001", candidate_id="cand_live1", room_url="u",
                                room_name="r", settings=SimpleNamespace(daily_api_key="k"))
    assert "probe-token-value" not in " ".join(map(str, seen["argv"]))
    assert "--token-stdin" in seen["argv"] and "--token" not in seen["argv"]
    assert seen["kwargs"]["stdin"] is interviews.asyncio.subprocess.PIPE
    assert seen["stdin"].data == b"probe-token-value\n" and seen["stdin"].closed
    from app import db

    async with db.get_sessionmaker()() as s:  # the process id is recorded for a restart (app/startup.py)
        assert (await s.get(db.Interview, "int_spawn0000001")).bot_pid == 1
    await db.reset_engine()


async def _never():
    return None


def test_the_bot_reads_its_token_from_standard_input(monkeypatch):
    import io
    import sys

    import bot.run_bot as run_bot

    got: dict = {}

    async def fake_run_bot(**kw):
        got.update(kw)

    monkeypatch.setattr(run_bot, "run_bot", fake_run_bot)
    monkeypatch.setattr(sys, "argv", ["run_bot", "--room-url", "u", "--token-stdin", "--session-id", "s"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("probe-token-value\n"))
    run_bot.main()
    assert got["token"] == "probe-token-value"
    with pytest.raises(SystemExit):  # the old flag is gone: a token can no longer be put on the command line
        monkeypatch.setattr(sys, "argv", ["run_bot", "--room-url", "u", "--token", "x"])
        run_bot.main()


async def test_a_bot_that_dies_before_its_token_is_sent_is_still_watched_and_nothing_leaks(monkeypatch):
    from types import SimpleNamespace

    from loguru import logger

    from app import interviews

    class DeadStdin:
        def write(self, b):
            pass

        async def drain(self):
            raise BrokenPipeError("the bot is gone")

        def close(self):
            pass

    process = SimpleNamespace(stdin=DeadStdin())
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        await interviews._send_bot_token("int_dead", process, "probe-token-value")  # does not raise
    finally:
        logger.remove(sink)
    assert any("exited before it could be given its meeting token" in l for l in lines)
    assert not any("probe-token-value" in l for l in lines)


# -- the clarification label (C-146 option 1) -----------------------------------------------------


def test_a_clarification_names_the_repeated_question_and_has_no_follow_up_number():
    from shared.interview_contracts import QuestionLabel, QuestionLabelled

    label = QuestionLabelled(turn_index=7, label="clarification", question_id="Q2.1", area_id="product_strategy")
    assert label.label is QuestionLabel.CLARIFICATION and label.follow_up_number is None
    with pytest.raises(ValidationError, match="needs question_id"):
        QuestionLabelled(turn_index=7, label="clarification")
    with pytest.raises(ValidationError, match="follow_up_number"):
        QuestionLabelled(turn_index=7, label="clarification", question_id="Q2.1", follow_up_number=1)
    events = json.loads((FIXTURES / "interview_events.json").read_text())
    labels = [e for e in events if e["kind"] == "question_labelled"]
    assert any(e["payload"]["label"] == "clarification" for e in labels)
    for e in labels:  # every older label still loads
        InterviewEvent.model_validate(e)


def test_a_clarification_never_counts_toward_the_follow_up_cap():
    from live import check_intel
    from live.emit import make_event
    from shared.interview_contracts import EventSource, QuestionLabelled

    run = json.loads((Path(__file__).resolve().parent / "fixtures" / "check_intel" /
                      "live_run_int_99bdc4598805.json").read_text())
    iid = run["interview_id"]
    turns = [{"index": i, "speaker": "interviewer" if i % 2 == 0 else "candidate", "text": f"t{i}",
              "at_seconds": float(i * 10)} for i in range(14)]
    labels = [(0, {"label": "plan", "question_id": "Q2.1"}),
              (2, {"label": "clarification", "question_id": "Q2.1"}),
              (4, {"label": "follow_up", "question_id": "Q2.1", "follow_up_number": 1}),
              (6, {"label": "clarification", "question_id": "Q2.1"}),
              (8, {"label": "follow_up", "question_id": "Q2.1", "follow_up_number": 2}),
              (10, {"label": "follow_up", "question_id": "Q2.1", "follow_up_number": 3}),
              (12, {"label": "clarification", "question_id": "Q2.1"})]  # after the 3rd: must not make it 4
    events = [make_event(iid, QuestionLabelled(turn_index=t, **fields), source=EventSource.AUDITOR, at_seconds=t * 10 + 1)
              for t, fields in labels]
    data = check_intel.build(interview_id=iid, status="completed", started_at=None, ended_at=None,
                             transcript={"interview_id": iid, "turns": turns}, session_metrics={}, blueprint=None,
                             events=events)
    follow_ups = data["tiles"]["follow_ups"]
    assert follow_ups["per_question"] == {"Q2.1": 3} and follow_ups["over_cap"] == []  # 2 clarifications ignored
    assert [r["label"] for r in data["rows"]][:2] == ["plan", "clarification"]
    assert data["tiles"]["off_plan_count"] == 0
