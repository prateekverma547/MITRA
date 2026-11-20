"""Round 1 of the A + C step: the bot connects to the rest of the system.

Deterministic, no keys: live events in Session C's contract (through the real
make_event), never breaking the interview; the transcript zero written to the
interview row; the booked duration; the End button; the no-question rule; declines.
"""

import asyncio
import json
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
)
from pipecat.observers.base_observer import FramePushed

from bot.app_messages import is_candidate_end_request
from bot.booked_time import apply_booked_timing
from bot.live_events import LiveEvents, TurnEventObserver
from bot.realtime_coordinator import (
    ENDING_END_BUTTON,
    FORCED_END_BY_MINUTES,
    REALTIME_TOOLS,
    RealtimeCoordinator,
)
from bot.realtime_prompt import NOTE_PREFIX, build_base_instruction
from shared.contracts import InterviewBlueprint
from shared.interview_contracts import InterviewEvent

FIXTURES = Path(__file__).resolve().parents[1] / "bot" / "fixtures"
INTERVIEW = "int_0123456789ab"


def blueprint(name="pm_senior") -> InterviewBlueprint:
    return InterviewBlueprint.model_validate(json.loads((FIXTURES / f"{name}.json").read_text()))


class FakeEmitter:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    async def start(self):
        pass

    def emit(self, event):
        if self.fail:
            raise RuntimeError("store down and emit broken")
        assert isinstance(event, InterviewEvent)
        self.events.append(event)
        return True

    async def close(self):
        return {"emitted": len(self.events), "dropped": 0}


async def live_events(fail=False, bp=None):
    live = LiveEvents(INTERVIEW, clock_zero=0.0, blueprint=bp or blueprint(), emitter=FakeEmitter(fail))
    await live.start()
    return live


class FakeService:
    def __init__(self):
        self.said, self.paused, self.cancelled = [], [], 0
        self.reply_of_call, self.reply_spoke, self.reply_text = {}, {}, {}

    def set_audio_input_paused(self, paused):
        self.paused.append(paused)

    async def request_reply(self):
        self.said.append("<reply requested>")

    async def register_function(self, *a, **k):
        pass

    async def update_instructions(self, text):
        pass

    async def insert_note(self, text):
        self.notes = getattr(self, "notes", []) + [text]
        return "note"

    async def delete_note(self, item_id):
        pass

    async def cancel_reply(self):
        self.cancelled += 1


async def push(c, frame):
    await c.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))


def kinds(live):
    return [e.kind.value for e in live._emitter.events]


# -- 1. events ---------------------------------------------------------------------------------


async def test_the_coordinator_sends_area_changes_notes_and_skips_in_the_contract():
    """Round 14: Mitra moves on with move_on; code's time notes are information."""
    live = await live_events()
    c = RealtimeCoordinator(blueprint=blueprint(), llm=FakeService(), area_instruction=lambda a: "x")
    c.attach_events(live)
    c._started_at = 0.0  # far in the past: the clock owes its halfway note

    async def cb(result, properties=None):
        pass

    await c._handle_move_on(SimpleNamespace(arguments={}, result_callback=cb, tool_call_id="m1"))
    c._started_at = __import__("time").monotonic() - 21 * 60
    c._time_notes_sent = set()
    await c.check_time_now()
    await c._on_candidate_turn("I'd rather skip this one, if that's okay.")
    assert kinds(live) == ["area_changed", "tool_called", "time_note_sent", "skip"]
    area, tool, note, skip = live._emitter.events
    assert tool.payload.model_dump() == {"kind": "tool_called", "tool": "move_on", "outcome": "moved"}  # C-156
    assert area.payload.area_id == "product_strategy" and area.payload.area_name == "Product strategy and vision"
    assert note.source.value == "coordinator" and "half of the booked" in note.payload.text
    # C-64: Skip(area_id, question_id=None), source coordinator.
    assert skip.source.value == "coordinator"
    assert skip.payload.model_dump() == {"kind": "skip", "area_id": "product_strategy", "question_id": None}


async def test_withdrawal_and_tool_calls_are_sent():
    """Round 14: Mitra asked, the candidate answered, she called end_interview(withdrew)."""
    live = await live_events()
    service = FakeService()
    c = RealtimeCoordinator(blueprint=blueprint(), llm=service)
    c.attach_events(live)

    async def cb(result, properties=None):
        pass

    await c._on_candidate_turn("I want to end the interview.")
    service.reply_spoke["r1"] = True
    await push(c, LLMFullResponseEndFrame())          # "Would you like to end here?"
    await c._on_candidate_turn("Yes, please.")
    service.reply_of_call["t1"], service.reply_spoke["r2"] = "r2", True
    await c._handle_end_interview(SimpleNamespace(arguments={"reason": "withdrew"}, result_callback=cb,
                                                  tool_call_id="t1"))
    payloads = [e.payload.model_dump() for e in live._emitter.events]
    assert {"kind": "withdrawal", "stage": "confirmed", "by": "model"} in payloads
    assert {"kind": "tool_called", "tool": "end_interview", "outcome": "accepted_withdrew"} in payloads


async def test_a_turn_is_sent_with_its_times_and_a_running_index():
    live = await live_events()
    timeline = SimpleNamespace(user_window_wall=lambda item: (10.0, 14.5), assistants={})
    observer = TurnEventObserver(events=live, timeline=timeline)
    frame = TranscriptionFrame(text="before Mitra spoke", user_id="u", timestamp="t")
    await observer.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))
    assert live._emitter.events == [], "audio before Mitra first speaks is the pre-interview slot"
    await observer.on_push_frame(FramePushed(source=None, destination=None, frame=BotStartedSpeakingFrame(),
                                             direction=None, timestamp=0))
    frame = TranscriptionFrame(text="I lead the payouts team.", user_id="u", timestamp="t")
    frame.result = SimpleNamespace(item_id="item_c1")
    await observer.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))
    reply = SimpleNamespace(item_id="item_m1", audio_bytes=100, text="What was the bet?", playback_start_wall=16.0,
                            playback_end_wall=18.0, cut=False, cut_wall=None, heard=lambda: "What was the bet?")
    timeline.assistants = {"item_m1": reply}
    observer.flush_replies()
    turns = [e.payload for e in live._emitter.events]
    assert [(t.turn_index, t.speaker.value, t.audio_start_seconds, t.audio_end_seconds) for t in turns] == [
        (0, "candidate", 10.0, 14.5), (1, "interviewer", 16.0, 18.0)]
    assert live.turn_index_of_item == {"item_c1": 0, "item_m1": 1}


async def test_a_broken_emitter_never_breaks_the_interview():
    live = await live_events(fail=True)
    service = FakeService()
    c = RealtimeCoordinator(blueprint=blueprint(), llm=service, area_instruction=lambda a: "x")
    c.attach_events(live)
    async def cb(result, properties=None):
        pass

    await c._handle_move_on(SimpleNamespace(arguments={}, result_callback=cb, tool_call_id="m1"))
    await c._on_candidate_turn("Can we skip this one?")
    await c.end_by_candidate_button()
    assert service.said == ["<reply requested>"], "the interview carried on to its ending"
    assert live.invalid >= 3


async def test_a_malformed_event_is_counted_not_raised():
    live = await live_events()
    assert live.send("Skip", {"area_id": "x", "question_id": None}, source="browser") is False
    assert live.invalid == 1


async def test_without_an_interview_id_nothing_is_sent():
    live = LiveEvents(None, clock_zero=0.0)
    await live.start()
    live.time_note("x")
    assert live.enabled is False and live.disabled_skipped == 1


async def test_the_ending_is_sent_with_a_contract_reason():
    live = await live_events()
    live.interview_ended("candidate_withdrew", 61.0)
    live.interview_ended("forced_whatever", 1.0)  # not an EndReason: sent as session_ended
    assert [e.payload.ended_because.value for e in live._emitter.events] == ["candidate_withdrew", "session_ended"]


# -- 2. the transcript zero ---------------------------------------------------------------------------


@pytest.fixture
async def scratch_db(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/round1.db")
    await db.reset_engine()
    from live.dev import ensure_demo_interview

    await ensure_demo_interview(INTERVIEW)
    yield db
    await db.reset_engine()


async def test_the_transcript_zero_is_written_and_the_clock_turns_exact(scratch_db):
    from live.clock import interview_time_now
    from bot.persistence import write_transcript_zero

    db = scratch_db
    async with db.get_sessionmaker()() as session:
        before = interview_time_now(await session.get(db.Interview, INTERVIEW))
    assert before["estimate"] is True
    zero = 1_700_000_000.25
    assert await write_transcript_zero(INTERVIEW, zero) is True
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, INTERVIEW)
        stored = interview.transcript_zero_at
        after = interview_time_now(interview)
    assert stored.replace(tzinfo=UTC).timestamp() == pytest.approx(zero, abs=1e-3)
    assert after["estimate"] is False and after["basis"] == "transcript_zero"


async def test_writing_the_zero_never_raises(monkeypatch):
    from bot.persistence import write_transcript_zero

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////nonexistent/dir/x.db")
    from app import db

    await db.reset_engine()
    assert await write_transcript_zero(INTERVIEW, 1.0) is False
    await db.reset_engine()


async def test_the_booking_is_read_from_the_interview_row(scratch_db):
    from bot.persistence import load_booked_timing

    db = scratch_db
    assert await load_booked_timing(INTERVIEW) is None, "no booking value: the blueprint as it is"
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, INTERVIEW)
        interview.booked_duration_minutes, interview.planned_duration_minutes = 45, 45
        interview.planned_grace_minutes = 5
        await session.commit()
    assert await load_booked_timing(INTERVIEW) == {"booked": 45, "planned": 45, "grace": 5}


# -- 3. the booked duration ----------------------------------------------------------------------------


@pytest.mark.parametrize("booked, hard_stop", [(20, 25), (45, 50), (60, 58)])
def test_the_bot_runs_to_the_booked_length(booked, hard_stop):
    from live.booking import booked_timing

    bp = blueprint()
    timing = booked_timing(plan_minutes=bp.evaluation_spec.duration_minutes,
                           grace_minutes=bp.evaluation_spec.overrun_grace_minutes, requested=booked,
                           engine="realtime")
    timed = apply_booked_timing(bp, planned_minutes=timing.planned_minutes, grace_minutes=timing.grace_minutes)
    c = RealtimeCoordinator(blueprint=timed, llm=None)
    marks = c._time_marks()
    assert marks[-1][1] == "hard_stop" and marks[-1][0] == hard_stop * 60
    budgets = [p.time_budget_minutes for p in timed.competency_plans]
    available = timing.planned_minutes - timed.opening_minutes - timed.closing_minutes
    assert sum(budgets) <= available + 1e-9 and sum(budgets) >= available - 0.1 * len(budgets)
    original = [p.time_budget_minutes for p in bp.competency_plans]
    ratios = {round(b / o, 2) for b, o in zip(budgets, original)}
    assert max(ratios) - min(ratios) <= 0.05, "every area scaled by the same factor"


def test_no_booking_keeps_the_blueprint():
    bp = blueprint()
    assert apply_booked_timing(bp, planned_minutes=None, grace_minutes=None) is bp


def test_the_minute_58_cap_holds_whatever_the_blueprint_says():
    raw = json.loads((FIXTURES / "pm_senior.json").read_text())
    raw["evaluation_spec"]["duration_minutes"], raw["evaluation_spec"]["overrun_grace_minutes"] = 90, 15
    c = RealtimeCoordinator(blueprint=InterviewBlueprint.model_validate(raw), llm=None)
    assert max(at for at, _, _ in c._time_marks()) == FORCED_END_BY_MINUTES * 60


# -- 4. the End button ---------------------------------------------------------------------------------

END = {"label": "mitra-candidate", "type": "end-interview", "reason": "end_button"}
PARTICIPANTS = {
    "s-cand": {"id": "s-cand", "info": {"userId": f"candidate:{INTERVIEW}"}},
    "s-watch": {"id": "s-watch", "info": {"userId": "watcher:usr_0123456789ab"}},
    "s-other": {"id": "s-other", "info": {"userId": "candidate:int_ffffffffffff"}},
}


@pytest.mark.parametrize("message, sender, accepted", [
    (END, "s-cand", True),
    (END, "s-watch", False),                       # a watcher (an unhidden one, if any)
    (END, "s-hidden", False),                      # a hidden watcher is not in participants()
    (END, "s-other", False),                       # another interview's candidate
    ({"type": "end-interview"}, "s-cand", False),  # malformed: no label
    ("end-interview", "s-cand", False),            # malformed: not an object
    ({**END, "type": "something-else"}, "s-cand", False),
])
def test_only_this_interviews_candidate_can_end_with_the_button(message, sender, accepted):
    assert is_candidate_end_request(message, sender, PARTICIPANTS, INTERVIEW) is accepted


async def test_the_end_button_cancels_her_reply_and_a_note_asks_for_her_goodbye():
    """Round 14: no fixed goodbye. Her reply is cancelled, then a private note and a
    reply request with no instructions of its own; no second confirmation."""
    service = FakeService()
    c = RealtimeCoordinator(blueprint=blueprint(), llm=service)
    await push(c, LLMFullResponseStartFrame())   # Mitra mid-reply: cancelled first
    await c.end_by_candidate_button()
    assert service.cancelled == 1 and service.paused == [True]
    assert service.said == ["<reply requested>"]
    assert len(service.notes) == 1 and service.notes[0].startswith(NOTE_PREFIX)
    assert "pressed End" in service.notes[0]
    # C-A3 (round 6): the End button's own reason, no longer sent as candidate_withdrew.
    assert c.ending["ending"] == ENDING_END_BUTTON and c.reason == "candidate_ended_button"
    await c.end_by_candidate_button()
    assert len(service.said) == 1


# -- 5. the no-question rule ----------------------------------------------------------------------------


def test_the_goodbye_may_not_ask_anything():
    base = " ".join(build_base_instruction(blueprint(), first_name="Asha", time_of_day="afternoon").split())
    assert "Your goodbye never contains a question and never invites questions" in base
    end = next(t for t in REALTIME_TOOLS.standard_tools if t.name == "end_interview")
    assert "AFTER you have said a warm goodbye" in end.description  # round 15: reverted to round 14's wording


# -- 6. declines -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("said, recorded", [
    ("I'd rather not answer that.", True),
    ("I'd prefer not to say.", True),
    ("I don't know", False),                         # an answer
    ("I'd rather skip this one, if that's okay.", False),  # a skip
    ("I want to end the interview.", False),         # a withdrawal
])
async def test_declines_are_recorded_and_not_sent_live(said, recorded):
    live = await live_events()
    c = RealtimeCoordinator(blueprint=blueprint(), llm=FakeService())
    c.attach_events(live)
    await c._on_candidate_turn(said)
    declines = [e for e in c.events if e["kind"] == "question_declined"]
    assert bool(declines) is recorded
    assert "question_declined" not in kinds(live)
