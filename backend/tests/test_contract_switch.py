"""Round 6 (C-84): the bot writes Session C's contract fields, not its stand-ins, and
interviews saved with the stand-ins (rounds 1 to 5) still load and still read. No keys."""

import json
from pathlib import Path
from types import SimpleNamespace

from bot.live_events import LiveEvents
from bot.realtime_coordinator import RealtimeCoordinator
from feedback.clean import declared_name_fixes, stored_cleaned_transcript
from feedback.score import _render_sections, build_report
from shared.contracts import InterviewBlueprint, Speaker, Transcript, TranscriptTurn
from shared.interview_contracts import InterviewEvent

FIXTURE = Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json"


def blueprint():
    return InterviewBlueprint.model_validate(json.loads(FIXTURE.read_text()))


class Emitter:
    def __init__(self):
        self.events = []

    async def start(self):
        pass

    def emit(self, event):
        assert isinstance(event, InterviewEvent)
        self.events.append(event)
        return True

    async def close(self):
        return {}


class Spy:
    def __getattr__(self, name):
        def call(*a, **k):
            async def done():
                return None
            return done()
        return call


async def live_and_coordinator():
    live = LiveEvents("int_0123456789ab", clock_zero=0.0, blueprint=blueprint(), emitter=Emitter())
    await live.start()
    coord = RealtimeCoordinator(blueprint=blueprint(), llm=Spy())
    coord.attach_events(live)
    return live, coord


# -- C-A1: each turn carries its area -------------------------------------------------------

async def test_every_turn_is_sent_with_its_area_id():
    live, coord = await live_and_coordinator()
    live.turn(speaker="interviewer", text="Hello.", start_wall=1.0, end_wall=2.0)
    coord._area = 2
    live.turn(speaker="candidate", text="We bet on routing.", start_wall=3.0, end_wall=4.0)
    coord._area = len(blueprint().competency_plans) + 2
    live.turn(speaker="interviewer", text="Thank you.", start_wall=5.0, end_wall=6.0)
    turns = [e.payload for e in live._emitter.events if e.payload.kind == "transcript_turn"]
    assert [t.area_id for t in turns] == ["opening", blueprint().competency_plans[0].competency_id, "closing"]
    assert live.invalid == 0


# -- C-A2 / C-A3: the ending and the End button ---------------------------------------------

async def test_the_end_button_is_sent_as_its_own_reason_with_the_ending():
    live, coord = await live_and_coordinator()
    await coord.end_by_candidate_button()
    live.interview_ended(coord.reason, 61.0, ending=coord.ending["ending"], hang_up_by="bot_stopped_speaking")
    [ended] = [e.payload for e in live._emitter.events if e.payload.kind == "interview_ended"]
    assert ended.ended_because.value == "candidate_ended_button"
    assert ended.ending == "candidate_ended_button" and ended.hang_up_by == "bot_stopped_speaking"


def test_run_realtime_passes_the_ending_and_what_hung_up():
    import inspect

    from bot import run_realtime

    src = inspect.getsource(run_realtime)
    assert 'ending=(coordinator.ending or {}).get("ending")' in src
    assert 'hang_up_by=(coordinator.ending or {}).get("hang_up_by")' in src


# -- old interviews saved with the stand-ins -----------------------------------------------

#: A section outcome as rounds 3 to 5 stored it: the four values in the "auditor" block.
OLD_OUTCOME = {"schema_version": 1, "section_id": "product_strategy", "kind": "competency",
               "competency_id": "product_strategy", "coverage": "partial", "depth_rationale": "x",
               "turns_spent": 6, "declined_turns": 1, "shortfall_reason": "s",
               "auditor": {"area_name": "Product strategy and vision", "questions_asked": ["Q2.1", "Q2.3"],
                           "skipped_question_ids": ["Q2.2"], "declined_question_ids": ["Q2.4"],
                           "easier_variants": 1, "labels": [], "claims": [], "contradictions": []}}
NEW_OUTCOME = {**{k: v for k, v in OLD_OUTCOME.items() if k != "auditor"},
               "questions_asked": ["Q2.1", "Q2.3"], "skipped_question_ids": ["Q2.2"],
               "declined_question_ids": ["Q2.4"], "easier_variants": 1,
               "auditor": {"area_name": "Product strategy and vision", "labels": [], "claims": [],
                           "contradictions": []}}


def report_gaps(outcome):
    turns = [TranscriptTurn(index=0, speaker=Speaker.INTERVIEWER, text="q", at_seconds=0.0),
             TranscriptTurn(index=1, speaker=Speaker.CANDIDATE, text="an answer", at_seconds=5.0)]
    report = build_report(interview_id="int_1", blueprint_id="bp", spec=blueprint().evaluation_spec,
                          transcript=Transcript(interview_id="int_1", turns=turns, duration_seconds=10.0),
                          payload={"competency_scores": [], "summary": "s", "recommendation": "limited_evidence"},
                          section_outcomes=[outcome])
    return [g for g in report.coverage_gaps if g.startswith("Product strategy and vision:")]


def test_an_old_and_a_new_section_outcome_read_the_same():
    assert _render_sections([OLD_OUTCOME]) == _render_sections([NEW_OUTCOME])
    assert "SKIPPED Q2.2" in _render_sections([OLD_OUTCOME]) and "DECLINED Q2.4" in _render_sections([OLD_OUTCOME])
    assert report_gaps(OLD_OUTCOME) == report_gaps(NEW_OUTCOME) == [
        "Product strategy and vision: the candidate skipped Q2.2.",
        "Product strategy and vision: the candidate declined Q2.4; an easier variant was asked 1 time(s)."]


def test_a_new_outcome_with_empty_fields_does_not_fall_back_to_a_stale_block():
    outcome = {**NEW_OUTCOME, "skipped_question_ids": [], "declined_question_ids": [],
               "auditor": {**NEW_OUTCOME["auditor"], "skipped_question_ids": ["Q9.9"]}}
    assert report_gaps(outcome) == []


CLEANED = {"status": "ok", "model_job": "feedback", "usd": 0.01, "guard_failures": 0, "candidate_turns": 1,
           "turns": [{"index": 1, "speaker": "candidate", "text": "I led payouts at Razorpay.", "guard": "ok",
                      "corrections": [{"from_text": "Razapay", "to_text": "Razorpay"}], "uncertain": []}]}


def test_an_old_interview_reads_its_cleaned_transcript_from_session_metrics():
    old = SimpleNamespace(cleaned_transcript=None, session_metrics={"cleaned_transcript": CLEANED})
    new = SimpleNamespace(cleaned_transcript=CLEANED, session_metrics={})
    none = SimpleNamespace(cleaned_transcript=None, session_metrics=None)
    assert stored_cleaned_transcript(old) == stored_cleaned_transcript(new) == CLEANED
    assert stored_cleaned_transcript(none) is None
    assert declared_name_fixes(stored_cleaned_transcript(old)) == {1: [(["razapay"], ["razorpay"])]}


async def test_an_old_interview_row_loads_and_scores_through_the_real_path(tmp_path, monkeypatch):
    """A row saved by round 5 (stand-ins in session_metrics and the auditor block, the
    column null) goes through generate_feedback: it loads, and the declared fix and the
    skip reach the report."""
    from app import db
    from feedback import run as run_module
    from live.dev import ensure_demo_interview

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/old.db")
    await db.reset_engine()
    await ensure_demo_interview("int_oldstandin01")
    async with db.get_sessionmaker()() as session:
        row = await session.get(db.Interview, "int_oldstandin01")
        row.transcript = {"interview_id": "int_oldstandin01", "duration_seconds": 10.0, "turns": [
            {"index": 0, "speaker": "interviewer", "text": "Where were you?", "at_seconds": 1.0},
            {"index": 1, "speaker": "candidate", "text": "I led payouts at Razapay.", "at_seconds": 5.0}]}
        row.session_metrics = {"cleaned_transcript": CLEANED, "ending": {"ending": "model_spoke"}}
        row.section_outcomes = [OLD_OUTCOME]
        row.cleaned_transcript = None
        row.status = db.InterviewStatus.COMPLETED
        candidate = await session.get(db.Candidate, row.candidate_id)
        candidate.blueprint = json.loads(FIXTURE.read_text())
        await session.commit()
    seen = {}

    class Scorer:
        def __init__(self, **kw):
            self.last_usage = None

        async def score(self, **kw):
            seen.update(kw)
            cid = kw["spec"].competencies[0].id
            return build_report(interview_id=kw["interview_id"], blueprint_id=kw["blueprint_id"], spec=kw["spec"],
                                transcript=kw["transcript"], section_outcomes=kw["section_outcomes"],
                                declared_fixes=kw["declared_fixes"], payload={
                                    "competency_scores": [{"competency_id": cid, "score": 3, "coverage": "partial",
                                                           "rationale": "r", "evidence": [
                                                               {"text": "I led payouts at Razorpay", "turn_index": 1}]}],
                                    "summary": "s", "recommendation": "limited_evidence"})

    monkeypatch.setattr(run_module, "FeedbackScorer", Scorer)
    report = await run_module.generate_feedback("int_oldstandin01")
    assert report is not None, "the old row loaded and scored"
    assert seen["declared_fixes"] == {1: [(["razapay"], ["razorpay"])]}
    assert [q.text for q in report.competency_scores[0].evidence] == ["I led payouts at Razorpay"]
    assert "Product strategy and vision: the candidate skipped Q2.2." in report.coverage_gaps
    await db.reset_engine()
