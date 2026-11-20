"""The auditor (task 23): record only, off the reply path, bounded. Deterministic, no keys."""

import asyncio
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import BotStartedSpeakingFrame, TranscriptionFrame
from pipecat.observers.base_observer import FramePushed

import bot.auditor as auditor_module
from bot.auditor import RealtimeAuditor, make_turn_listener
from bot.live_events import LiveEvents, TurnEventObserver
from bot.realtime_coordinator import RealtimeCoordinator
from shared.contracts import InterviewBlueprint, SectionOutcome
from shared.interview_contracts import InterviewEvent

FIXTURES = Path(__file__).resolve().parents[1] / "bot" / "fixtures"


def blueprint():
    return InterviewBlueprint.model_validate(json.loads((FIXTURES / "pm_senior.json").read_text()))


def fake_request(script, delay=0.0, fail_on=()):
    """A stand-in for shared.text_models.request_structured: returns `script` readings in order."""
    calls = {"n": 0}

    async def request(**kwargs):
        calls["n"] += 1
        n = calls["n"]
        if delay:
            await asyncio.sleep(delay)
        if n in fail_on:
            raise RuntimeError("text model down")
        reading = script[(n - 1) % len(script)]
        return SimpleNamespace(payload={"coverage": "partial", "new_claims": [], "contradictions": [], **reading},
                               record=SimpleNamespace(cost_usd=0.001))

    request.calls = calls
    return request


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


class SpyService:
    """A realtime service that records every call: the auditor must cause none."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)

            async def done():
                return "x"

            return done()

        return record


async def run_turns(a, turns):
    for speaker, text in turns:
        if speaker == "candidate":
            a.candidate_turn(len(a.turn_area) * 2 + 1, text)
        else:
            a.mitra_turn(len(a.turn_area) * 2, text)
    await a.finish(timeout=5)


async def test_labels_follow_ups_skips_and_declines_reach_the_section_results():
    area = {"n": 2}
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: area["n"], request=fake_request([
        {"label": "plan", "question_id": "Q2.1"},
        {"label": "follow_up", "question_id": "Q2.1"},
        {"label": "plan", "question_id": "Q2.2"},
        {"label": "plan", "question_id": "Q2.3"},
        {"label": "plan", "question_id": "Q2.4"},
        {"label": "easier_variant", "question_id": "Q2.4"},
    ]))
    a.start()
    a.mitra_turn(0, "What was the bet behind a product strategy you set?")
    a.candidate_turn(1, "We bet on multi-acquirer routing.")
    a.mitra_turn(2, "Why that bet?")
    a.candidate_turn(3, "Failures were costing us.")
    a.mitra_turn(4, "What did you believe about the market that others on your team did not?")
    a.candidate_turn(5, "I'd rather skip this one, if that's okay.")
    a.note_skip(2)
    a.mitra_turn(6, "Looking back, what part of that strategy turned out to be wrong?")
    a.candidate_turn(7, "Some acquirers were flaky.")
    a.mitra_turn(8, "How did you decide what your product was deliberately not going to be?")
    a.candidate_turn(9, "I'd rather not answer that.")
    a.note_decline(2)
    a.mitra_turn(10, "What is one thing you chose to leave out?")
    summary = await a.finish(timeout=5)
    assert summary["status"] == "complete"
    assert summary["labels"]["2"]["follow_up_number"] == 1
    # A-63 (round 10): each carries where it came from; here the question's own area.
    assert summary["skips"] == [{"area": 2, "after_turn": 4, "question_id": "Q2.2", "spoken_in_area": 2,
                                 "area_source": "question"}]
    assert summary["declines"] == [{"area": 2, "after_turn": 8, "question_id": "Q2.4", "spoken_in_area": 2,
                                    "area_source": "question"}]
    [outcome] = a.section_outcomes()
    SectionOutcome.model_validate({k: v for k, v in outcome.items() if k != "auditor"})
    assert outcome["coverage"] == "partial" and outcome["declined_turns"] == 1
    # C-A5 (round 6): the contract's own fields, validated, not the stand-in block.
    validated = SectionOutcome.model_validate({k: v for k, v in outcome.items() if k != "auditor"})
    assert validated.questions_asked == ["Q2.1", "Q2.2", "Q2.3", "Q2.4"]
    assert validated.skipped_question_ids == ["Q2.2"]
    assert validated.declined_question_ids == ["Q2.4"] and validated.easier_variants == 1
    assert not {"questions_asked", "skipped_question_ids", "declined_question_ids", "easier_variants"} & set(outcome["auditor"])
    assert "skipped Q2.2" in outcome["shortfall_reason"] and "declined Q2.4" in outcome["shortfall_reason"]


async def test_labels_go_out_as_question_labelled_from_the_auditor():
    live = LiveEvents("int_0123456789ab", clock_zero=0.0, blueprint=blueprint(), emitter=Emitter())
    await live.start()
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: 2, live=live, request=fake_request([
        {"label": "plan", "question_id": "Q2.1"}, {"label": "follow_up", "question_id": "Q2.1"},
        {"label": "easier_variant", "question_id": "Q2.1"}]))
    a.start()
    for i in range(3):
        a.mitra_turn(i * 2, "q")
    await a.finish(timeout=5)
    sent = [e for e in live._emitter.events]
    assert [e.source.value for e in sent] == ["auditor", "auditor", "auditor"]
    assert sent[2].payload.label.value == "easier_variant", "C-A4 exists: an easier variant is sent live (round 4)"
    assert sent[2].payload.question_id == "Q2.1" and sent[2].payload.follow_up_number is None
    assert sent[0].payload.model_dump() == {"kind": "question_labelled", "turn_index": 0, "label": "plan",
                                            "question_id": "Q2.1", "follow_up_number": None,
                                            "area_id": "product_strategy", "auditors_reading": True}
    assert sent[1].payload.follow_up_number == 1


async def test_the_auditor_never_writes_into_the_realtime_session():
    spy = SpyService()
    coordinator = RealtimeCoordinator(blueprint=blueprint(), llm=spy)
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: coordinator.current_area,
                        request=fake_request([{"label": "plan", "question_id": "Q2.1"}]))
    coordinator.attach_auditor(a)
    a.start()
    await coordinator._on_candidate_turn("I'd rather skip this one, if that's okay.")
    await coordinator._on_candidate_turn("I'd rather not answer that.")
    for i in range(3):
        a.mitra_turn(i, "What was the bet?")
    await a.finish(timeout=5)
    assert spy.calls == [], f"the auditor caused calls on the realtime service: {spy.calls}"
    source = inspect.getsource(auditor_module)
    for name in ("_llm", "say(", "insert_note", "update_instructions", "send_client_event", "cancel_reply",
                 "queue_frames", "set_audio_input_paused"):
        assert name not in source, f"bot/auditor.py must not reach the session: {name}"


async def test_a_slow_auditor_never_slows_the_turn_observer():
    live = LiveEvents(None, clock_zero=0.0)
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: 2,
                        request=fake_request([{"label": "plan", "question_id": "Q2.1"}], delay=0.5))
    a.start()
    reply = SimpleNamespace(item_id="m1", audio_bytes=10, text="What was the bet?", playback_start_wall=1.0,
                            playback_end_wall=2.0, cut=False, cut_wall=None, heard=lambda: "What was the bet?")
    timeline = SimpleNamespace(user_window_wall=lambda i: (3.0, 4.0), assistants={"m1": reply})
    observer = TurnEventObserver(events=live, timeline=timeline, on_turn=make_turn_listener(a))
    started = time.monotonic()
    await observer.on_push_frame(FramePushed(source=None, destination=None, frame=BotStartedSpeakingFrame(),
                                             direction=None, timestamp=0))
    for pending in observer.flush_replies():
        await pending
    frame = TranscriptionFrame(text="We bet on routing.", user_id="u", timestamp="t")
    await observer.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))
    assert time.monotonic() - started < 0.1, "the observer waited for a label"
    await a.finish(timeout=5)
    assert a.labels[0]["question_id"] == "Q2.1", "and the label still arrives, in the background"


async def test_an_auditor_error_never_stops_the_call_or_the_auditor():
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: 2,
                        request=fake_request([{"label": "plan", "question_id": "Q2.1"}], fail_on={1}))
    a.start()
    a.mitra_turn(0, "first")      # its call fails
    a.mitra_turn(2, "second")     # the worker carries on
    summary = await a.finish(timeout=5)
    assert summary["stats"]["failed"] == 1 and "2" in summary["labels"]
    assert summary["status"] == "complete_with_failures"


async def test_a_full_queue_drops_and_counts_and_finish_is_bounded():
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: 2, max_queue=2,
                        request=fake_request([{"label": "plan", "question_id": "Q2.1"}], delay=3.0))
    a.start()
    for i in range(6):
        a.mitra_turn(i, "q")
    started = time.monotonic()
    summary = await a.finish(timeout=0.3)
    assert time.monotonic() - started < 1.0
    assert summary["status"] == "partial"
    assert summary["stats"]["dropped_queue_full"] >= 3


async def test_a_skip_is_mapped_from_the_bank_when_the_turn_is_unlabelled():
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: 2, request=fake_request([{}]), max_queue=1)
    a.turn_area[4] = 2
    a.labels[4] = {"label": "off_plan", "question_id": None, "area": 2,
                   "text": "What did you believe about the market that others on your team did not?", "at": None}
    assert a.question_of_turn(4) == "Q2.2"


def test_run_realtime_saves_the_auditors_section_results():
    from bot import run_realtime

    source = inspect.getsource(run_realtime)
    assert "section_outcomes=[]" not in source.replace(" ", "")
    assert "section_outcomes = auditor.section_outcomes()" in source
    assert "await auditor.finish()" in source


async def test_the_report_shows_skips_and_declines_from_the_auditor():
    """End of the chain (backend): section results -> the scorer's input and the report."""
    from feedback.score import _render_sections, build_report
    from shared.contracts import Speaker, Transcript, TranscriptTurn

    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: 2, request=fake_request([
        {"label": "plan", "question_id": "Q2.1"}, {"label": "plan", "question_id": "Q2.2"},
        {"label": "easier_variant", "question_id": "Q2.2"}]))
    a.start()
    a.mitra_turn(0, "What was the bet?")
    a.candidate_turn(1, "I'd rather skip this one, if that's okay.")
    a.note_skip(2)
    a.mitra_turn(2, "What did you believe about the market?")
    a.candidate_turn(3, "I'd rather not answer that.")
    a.note_decline(2)
    a.mitra_turn(4, "What did your customers ask for most?")
    await a.finish(timeout=5)
    outcomes = a.section_outcomes()
    lines = _render_sections(outcomes)
    assert "SKIPPED Q2.1" in lines and "DECLINED Q2.2" in lines
    turns = [TranscriptTurn(index=i, speaker=Speaker(s), text=t, at_seconds=float(i * 10)) for i, (s, t) in enumerate([
        ("interviewer", "What was the bet?"), ("candidate", "I'd rather skip this one, if that's okay."),
        ("interviewer", "What did you believe about the market?"), ("candidate", "I'd rather not answer that."),
        ("interviewer", "What did your customers ask for most?"), ("candidate", "Reliable payouts, every time.")])]
    report = build_report(interview_id="int_1", blueprint_id="bp", spec=blueprint().evaluation_spec,
                          transcript=Transcript(interview_id="int_1", turns=turns, duration_seconds=60.0),
                          payload={"competency_scores": [], "summary": "s", "recommendation": "limited_evidence"},
                          section_outcomes=outcomes)
    assert "Product strategy and vision: the candidate skipped Q2.1." in report.coverage_gaps
    assert any(g.startswith("Product strategy and vision: the candidate declined Q2.2") for g in report.coverage_gaps)


async def test_an_easier_variant_without_an_id_takes_the_declined_question():
    """C-A4: question_id is the DECLINED question's id. When the model gives none, the
    question of the turn the decline answered is used; the event is valid and sent."""
    live = LiveEvents("int_0123456789ab", clock_zero=0.0, blueprint=blueprint(), emitter=Emitter())
    await live.start()
    a = RealtimeAuditor(blueprint=blueprint(), area_now=lambda: 2, live=live, request=fake_request([
        {"label": "plan", "question_id": "Q2.1"}, {"label": "easier_variant", "question_id": None}]))
    a.start()
    a.mitra_turn(0, "What was the bet behind a product strategy you set?")
    a.candidate_turn(1, "I'd rather not answer that.")
    a.note_decline(2)
    a.mitra_turn(2, "Okay. What makes a strategy bet hard for you?")
    await a.finish(timeout=5)
    sent = live._emitter.events
    assert [e.payload.label.value for e in sent] == ["plan", "easier_variant"]
    assert sent[1].payload.question_id == "Q2.1"
    assert live.invalid == 0, "the event passed the contract's validation"
