"""Round 14: one voice (the model's), Mitra decides through tools, code keeps the
guarantees and talks to her only through private notes. Deterministic, no keys: the
REAL RealtimeCoordinator, driven through the frames it observes and its tool handlers,
over a stand-in realtime service that records notes and reply requests.

Written from the decisions in the round 14 prompt, not from the code:
  - no fixed spoken line, no exactly(), no per-reply instructions;
  - one owner per decision (code never detects a withdrawal, never ends the normal
    interview, never moves her between areas on the clock);
  - notes never while she or the candidate is speaking, except the hard stop and the
    End button (her reply cancelled first); nothing during a drop;
  - end_interview's two light guards, refused calmly;
  - a clarification is the same question, never a follow-up.
"""

import ast
import asyncio
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed

import bot.realtime_coordinator as rc
from bot.realtime_coordinator import (
    COMPLETED,
    ENDING_COMPLETED,
    ENDING_END_BUTTON,
    ENDING_FORCED,
    ENDING_SILENCE,
    ENDING_WITHDREW,
    REALTIME_TOOLS,
    WITHDREW,
    RealtimeCoordinator,
    nearly_up_seconds,
)
from bot.realtime_prompt import NOTE_PREFIX, build_area_instruction, build_base_instruction
from shared.contracts import InterviewBlueprint

BACKEND = Path(__file__).resolve().parents[1]
BP = InterviewBlueprint.model_validate(json.loads((BACKEND / "bot" / "fixtures" / "pm_senior.json").read_text()))
#: The realtime engine's own modules: everything that can put words in Mitra's mouth.
REALTIME_MODULES = ["bot/realtime_coordinator.py", "bot/realtime_prompt.py", "bot/run_realtime.py",
                    "bot/rejoin.py", "bot/services/realtime.py"]


class Service:
    """Records what code makes happen: notes, reply requests, cancels, swaps."""

    def __init__(self):
        self.notes, self.replies, self.cancels, self.paused, self.swaps = [], [], 0, [], []
        self.reply_of_call, self.reply_spoke, self.reply_text = {}, {}, {}
        self.events: list[str] = []

    async def insert_note(self, text):
        self.notes.append(text)
        self.events.append("note")
        return f"n{len(self.notes)}"

    async def delete_note(self, item_id):
        pass

    async def request_reply(self):
        self.replies.append(self.notes[-1] if self.notes else None)
        self.events.append("reply")

    async def cancel_reply(self):
        self.cancels += 1
        self.events.append("cancel")

    def set_audio_input_paused(self, paused):
        self.paused.append(paused)

    async def update_instructions(self, text):
        self.swaps.append(text)


class Worker:
    def __init__(self):
        self.ended = 0

    async def queue_frames(self, frames):
        self.ended += 1


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(rc, "_ENDED_WHILE_SILENT_SECONDS", 0.05)
    monkeypatch.setattr(rc, "_GOODBYE_TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(rc, "FORCED_END_REPLY_CAP_SECONDS", 0.3)
    monkeypatch.setattr(rc, "_URGENT_DRAIN_SECONDS", 0.2)
    monkeypatch.setattr(rc, "_REPLY_RECORD_WAIT_SECONDS", 0.2)


def make(bp=BP, area_instruction=None):
    service, worker = Service(), Worker()
    c = RealtimeCoordinator(blueprint=bp, llm=service, session_id="t", area_instruction=area_instruction)
    c.attach(worker)
    c._started_at = time.monotonic()
    return c, service, worker


async def push(c, frame):
    await c.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))


async def candidate(c, text):
    """The candidate speaks (VAD first, as live, then the transcript)."""
    await push(c, UserStartedSpeakingFrame())
    await push(c, UserStoppedSpeakingFrame())
    await push(c, TranscriptionFrame(text=text, user_id="u", timestamp="t"))


async def mitra(c, service, response_id, text="Okay."):
    """One of her replies that spoke."""
    await push(c, LLMFullResponseStartFrame())
    service.reply_spoke[response_id], service.reply_text[response_id] = True, text
    await push(c, LLMFullResponseEndFrame())


async def call(c, service, tool, *, reply, spoke=True, **arguments):
    """She calls a tool in reply `reply`; returns (result, properties)."""
    box = {}

    async def cb(result, properties=None):
        box.update(result=result, properties=properties)

    call_id = f"call_{reply}"
    service.reply_of_call[call_id] = reply
    service.reply_spoke[reply] = spoke
    handler = c.tool_handlers()[tool]
    await handler(SimpleNamespace(arguments=arguments, result_callback=cb, tool_call_id=call_id))
    return box.get("result"), box.get("properties")


def runs_llm(properties) -> bool:
    return properties is None or getattr(properties, "run_llm", True) is not False


async def goodbye_reply(c, service, rid="g1", text="Thank you for your time, Asha. Goodbye."):
    """Her goodbye reply as it arrives live: it starts, speaks, ends, and its audio stops."""
    await push(c, LLMFullResponseStartFrame())
    service.reply_spoke[rid], service.reply_text[rid] = True, text
    await push(c, BotStartedSpeakingFrame())
    await push(c, LLMFullResponseEndFrame())
    await push(c, BotStoppedSpeakingFrame())


# -- 1. the note mechanism ----------------------------------------------------------------------


async def test_a_note_waits_while_she_speaks_and_while_the_candidate_speaks():
    c, service, _ = make()
    await push(c, BotStartedSpeakingFrame())
    task = asyncio.create_task(c.tell("hello", kind="welcome_back"))
    await asyncio.sleep(0.15)
    assert service.notes == [], "never while she is speaking"
    await push(c, BotStoppedSpeakingFrame())
    await push(c, UserStartedSpeakingFrame())
    await asyncio.sleep(0.15)
    assert service.notes == [], "never while the candidate is speaking"
    await push(c, UserStoppedSpeakingFrame())
    await asyncio.wait_for(task, 1)
    assert service.events == ["note", "reply"]
    assert service.notes[0].startswith(NOTE_PREFIX) and service.cancels == 0


async def test_a_reply_being_generated_also_holds_a_note():
    c, service, _ = make()
    await push(c, LLMFullResponseStartFrame())
    task = asyncio.create_task(c.tell("x", kind="quiet_1"))
    await asyncio.sleep(0.15)
    assert service.notes == []
    await push(c, LLMFullResponseEndFrame())
    await asyncio.wait_for(task, 1)
    assert service.events == ["note", "reply"]


async def test_nothing_is_sent_while_the_candidate_is_away():
    c, service, _ = make()
    await c.candidate_away(True)
    assert await c.tell("x", kind="quiet_1") is False
    assert service.notes == [] and service.replies == []


async def test_a_time_note_asks_for_no_reply():
    """Time notes are information she reads at her next turn; a reply request now
    would speak into the candidate's thinking pause."""
    c, service, _ = make()
    c._started_at = time.monotonic() - 21 * 60
    await c.check_time_now()
    assert len(service.notes) == 1 and "half of the booked" in service.notes[0]
    assert service.replies == []


async def test_time_notes_are_few_and_information():
    c, _, _ = make()
    marks = c._time_marks()
    names = [m[1] for m in marks]
    assert names == ["halfway", "ten_left", "three_left", "booked_over", "hard_stop"]
    for _, name, text in marks:
        # Karandeep (round 14): the booked-time-up note says plainly to wrap up; every
        # other time note is information only, and none ever moves her on.
        if name == "booked_over":
            assert "Wrap up now" in text and "move on" not in text.lower()
            continue
        assert not re.search(r"(?i)\b(move on|call \w+_interview|close the interview now|finish this|wrap up)\b", text)


async def test_the_80_percent_note_is_told_once_and_she_is_not_moved(fast):
    """Round 15: a concrete note at 80% of the area's allowance ("about one more
    question in this area, then move on; N areas remain after this, about M minutes
    each"). Information: code does not move her for it."""
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await call(c, service, "move_on", reply="r0", spoke=False)
    entered = c._area_log[-1]["entered_seconds"]
    note_at, backstop_at = c.allowance(2, entered)
    c._started_at = time.monotonic() - (entered + (note_at + backstop_at) / 2 * 60)
    await c.check_time_now()
    await c.check_time_now()
    notes = [n for n in service.notes if "Time in this area" in n and "is nearly used" in n]
    assert len(notes) == 1 and "more area(s) remain after this one" in notes[0] and "minute(s) each" in notes[0]
    assert c.current_area == 2 and service.replies == []


def test_the_allowance_keeps_the_closings_time_and_the_plans_weighting():
    """On schedule the note comes near 80% of the plan and the backstop at 1.5x it, one
    exchange early; behind
    schedule both shrink, and the backstop never leaves later areas less than half their
    plan or the closing less than its minutes plus the margin."""
    c, _, _ = make()
    plan = BP.competency_plans[0].time_budget_minutes
    on_time = BP.opening_minutes * 60
    note_at, backstop_at = c.allowance(2, on_time)
    # The closing's minutes and the margin are set aside, so even on schedule the share
    # is a little under the plan: the note comes a little before 80% of the plan.
    assert 0.7 * plan <= note_at <= rc.NOTE_POINT * plan
    # The backstop fires one measured exchange early, for the lag before a move lands.
    # The decided values (round 15: 80% and 1.5x), written as numbers so that changing the
    # constants fails here rather than being compared against themselves.
    assert rc.NOTE_POINT == 0.8 and rc.BACKSTOP_FACTOR == 1.5
    assert backstop_at == pytest.approx(1.5 * plan - c.exchange_minutes())
    late_note, late_backstop = c.allowance(2, on_time + 10 * 60)
    assert late_note < note_at and late_backstop < backstop_at
    booked, closing = BP.evaluation_spec.duration_minutes, BP.closing_minutes
    floors_after = sum(rc.AREA_FLOOR * p.time_budget_minutes for p in BP.competency_plans[1:])
    for late in (0, 5, 10, 15):
        entered = on_time + late * 60
        _, b = c.allowance(2, entered)
        assert entered / 60 + b <= booked - closing - rc.CLOSING_MARGIN_MINUTES - floors_after + 1e-9 or \
            b <= c.allowance(2, entered)[0] + rc._BACKSTOP_AFTER_NOTE_MINUTES + 1e-9


# -- 2. one owner per decision -----------------------------------------------------------------------


async def test_code_never_ends_on_the_candidates_words(fast):
    """The candidate asks to stop and says yes: code does nothing; only her tool ends it."""
    c, service, worker = make()
    for said in ["I want to stop the interview.", "Yes, please end it.", "Bas, I'm done."]:
        await candidate(c, said)
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.7)
    assert not c.is_over and worker.ended == 0 and service.notes == [] and service.replies == []


async def test_the_booked_time_note_says_plainly_the_plan_is_unfinished():
    c, service, _ = make()
    c._started_at = time.monotonic() - (BP.evaluation_spec.duration_minutes * 60 + 5)
    c._time_notes_sent = {"halfway", "ten_left", "three_left"}
    await c.check_time_now()
    assert "The plan is unfinished" in service.notes[-1] and "Wrap up now" in service.notes[-1]
    assert service.replies == [], "read at her next turn, never into a thinking pause"


async def test_code_never_moves_her_between_areas_on_the_clock():
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    c._started_at = time.monotonic() - 30 * 60          # past every area's planned end
    await c.check_time_now()
    assert c.current_area == 1 and service.swaps == []


async def test_code_never_ends_the_normal_interview(fast):
    """Every area reached and the booked time over: still running until she calls."""
    c, service, worker = make(area_instruction=lambda a: f"area {a}")
    for i in range(len(BP.competency_plans) + 1):
        await call(c, service, "move_on", reply=f"m{i}", spoke=False)
    c._started_at = time.monotonic() - (BP.evaluation_spec.duration_minutes + 1) * 60
    await c.check_time_now()
    await asyncio.sleep(0.2)
    assert not c.is_over and worker.ended == 0


SPOKEN_FIXED = [
    (r"\bexactly\(", "the exactly() helper"),
    (r"\.say\(", "a say() call"),
    (r"(?i)word for word", "a word-for-word instruction"),
    (r"(?i)say exactly", "a say-exactly instruction"),
    (r"ResponseProperties\([^)]*instructions\s*=", "per-reply instructions"),
    (r"TTSSpeakFrame", "a TTS line"),
    (r"(?i)\bfinish_interview\b", "the old ending tool"),
]


@pytest.mark.parametrize("path", REALTIME_MODULES)
def test_no_spoken_fixed_line_anywhere_in_the_realtime_engine(path):
    """A test that finds any spoken fixed string: every way code has ever made Mitra
    say its words (exactly(), say(), per-reply instructions, TTS, a word-for-word
    prompt line) is searched for in every realtime module, comments excluded."""
    source = (BACKEND / path).read_text()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    tree = ast.parse(source)
    docstrings = {ast.get_docstring(n) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    for doc in docstrings - {None}:
        code = code.replace(doc, "")
    found = [what for pattern, what in SPOKEN_FIXED if re.search(pattern, code)]
    assert found == [], f"{path}: {found}"


def test_the_search_finds_a_fixed_line_when_one_is_there():
    """Positive control for the search above."""
    planted = 'await self._llm.say(exactly("Welcome back."))\nx = "Say exactly this, word for word"'
    found = [what for pattern, what in SPOKEN_FIXED if re.search(pattern, planted)]
    assert len(found) >= 3


@pytest.mark.parametrize("path", REALTIME_MODULES)
def test_the_realtime_engine_uses_no_stop_detection(path):
    """Code's own stop detection (patterns, the confirmation line, the opening line)
    belongs to the pipeline engine only; no realtime module imports any of it."""
    tree = ast.parse((BACKEND / path).read_text())
    banned = {"asked_explicitly", "confirms_stopping", "declines_stopping", "wants_to_stop", "classify",
              "asked_to_skip_question", "CONFIRMATION_QUESTION", "InterviewBrain", "opening_line"}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert (names | attrs) & banned == set()


def test_the_prompt_gives_her_no_fixed_sentence_to_say():
    text = build_area_instruction(BP, first_name="Asha", time_of_day="afternoon", area=2)
    assert not re.search(r"(?i)word for word|say exactly|exactly this|the system (asks|says|thanks)", text)
    assert "end_interview" in text and "move_on" in text and "finish_interview" not in text


def test_the_tools_are_end_interview_and_move_on_only():
    tools = {t.name: t for t in REALTIME_TOOLS.standard_tools}
    assert set(tools) == {"end_interview", "move_on"}
    assert tools["end_interview"].properties["reason"]["enum"] == [WITHDREW, COMPLETED]


# -- 3. end_interview: the guards and the hang-up ----------------------------------------------------------


async def test_withdrew_after_she_asked_and_they_answered_is_accepted(fast):
    c, service, worker = make()
    await candidate(c, "I think I'm done here.")
    await mitra(c, service, "r1", "Would you like to end the interview here?")
    await candidate(c, "Yes, please.")
    result, props = await call(c, service, "end_interview", reply="r2", reason="withdrew")
    # Round 16: accepted, and her goodbye is the FOLLOWING reply (a "Take care" if she
    # already said it); the call ends when that reply has played.
    assert result["accepted"] is True and runs_llm(props) and "goodbye" in result["next"]
    assert c.is_over and c.reason == "candidate_withdrew" and c.ending["ending"] == ENDING_WITHDREW
    assert worker.ended == 0
    await goodbye_reply(c, service, text="Take care, Asha.")
    assert worker.ended == 1 and c.ending["hang_up_by"] == "bot_stopped_speaking"


async def test_withdrew_with_no_answer_since_her_last_reply_is_refused(fast):
    """She asked whether they want to stop, and calls before they answered."""
    c, service, _ = make()
    await candidate(c, "I think I'm done here.")
    await mitra(c, service, "r1", "Would you like to end the interview here?")
    result, props = await call(c, service, "end_interview", reply="r2", reason="withdrew")
    assert result["accepted"] is False and "ask them once" in result["why"] and runs_llm(props)
    assert not c.is_over and c.ending["end_interview_refused"] == 1


async def test_withdrew_on_her_very_first_reply_is_refused(fast):
    c, service, _ = make()
    await candidate(c, "I want to stop.")
    result, _ = await call(c, service, "end_interview", reply="r1", reason="withdrew")
    assert result["accepted"] is False and not c.is_over


async def test_a_late_transcript_never_counts_as_a_fresh_answer(fast):
    """Live, transcripts arrive seconds late. The flow follows OpenAI's VAD frames, so
    the wish's own transcript arriving after her confirmation is not an answer."""
    c, service, _ = make()
    await push(c, UserStartedSpeakingFrame())               # "I want to stop" (VAD)
    await push(c, UserStoppedSpeakingFrame())
    await mitra(c, service, "r1", "Would you like to end here?")
    await push(c, TranscriptionFrame(text="I want to stop", user_id="u", timestamp="t"))  # late
    result, _ = await call(c, service, "end_interview", reply="r2", reason="withdrew")
    assert result["accepted"] is False


async def test_completed_before_the_plan_is_covered_is_refused(fast):
    c, service, _ = make(area_instruction=lambda a: "x")
    await call(c, service, "move_on", reply="m1", spoke=False)
    result, props = await call(c, service, "end_interview", reply="r1", reason="completed")
    assert result["accepted"] is False and "1 of" in result["why"] and runs_llm(props)
    assert not c.is_over


async def test_completed_in_the_closing_is_accepted(fast):
    c, service, worker = make(area_instruction=lambda a: "x")
    for i in range(len(BP.competency_plans) + 1):
        await call(c, service, "move_on", reply=f"m{i}", spoke=False)
    result, _ = await call(c, service, "end_interview", reply="r1", reason="completed")
    assert result["accepted"] is True and c.reason == "interview_complete"
    assert c.ending["ending"] == ENDING_COMPLETED
    await asyncio.sleep(0.15)
    assert worker.ended == 0, "round 16: never on the call itself"
    await goodbye_reply(c, service)
    assert worker.ended == 1


async def test_completed_when_time_is_nearly_up_is_accepted_wherever_she_is(fast):
    c, service, _ = make()
    c._started_at = time.monotonic() - nearly_up_seconds(BP) - 1
    result, _ = await call(c, service, "end_interview", reply="r1", reason="completed")
    assert result["accepted"] is True


def test_the_nearly_up_threshold_comes_from_the_booked_end_and_the_minute_58_cap():
    assert nearly_up_seconds(BP) == (BP.evaluation_spec.duration_minutes - BP.closing_minutes) * 60
    raw = BP.model_dump(mode="json")
    raw["evaluation_spec"]["duration_minutes"] = 90
    assert nearly_up_seconds(InterviewBlueprint.model_validate(raw)) == (58 - BP.closing_minutes) * 60


async def test_a_call_without_a_goodbye_is_told_to_say_it_and_the_call_ends_after_it(fast):
    c, service, worker = make()
    c._started_at = time.monotonic() - nearly_up_seconds(BP) - 1
    result, props = await call(c, service, "end_interview", reply="r1", spoke=False, reason="completed")
    assert result["accepted"] is True and "goodbye" in result["next"] and runs_llm(props)
    assert c.ending["ended_without_goodbye"] == 1
    await goodbye_reply(c, service)
    assert worker.ended == 1


async def test_a_goodbye_that_never_comes_still_hangs_up_silently(fast):
    c, service, worker = make()
    c._started_at = time.monotonic() - nearly_up_seconds(BP) - 1
    await call(c, service, "end_interview", reply="r1", spoke=False, reason="completed")
    await asyncio.sleep(0.8)
    assert worker.ended == 1 and c.ending["hang_up_by"] == "goodbye_timeout" and service.notes == []


async def test_a_reason_that_is_not_allowed_is_refused(fast):
    c, service, _ = make()
    result, _ = await call(c, service, "end_interview", reply="r1", reason="bored")
    assert result["accepted"] is False and not c.is_over


# -- 4. code's own endings: a note, her goodbye, then the hang-up ---------------------------------------------


async def test_the_hard_stop_cancels_her_reply_then_notes_and_cuts_at_the_cap(fast):
    c, service, worker = make()
    await push(c, LLMFullResponseStartFrame())
    c._force_task = asyncio.create_task(c._force_end())
    await asyncio.sleep(0.05)
    assert service.events[:3] == ["cancel", "note", "reply"]
    assert "Time is up" in service.notes[0] and c.ending["ending"] == ENDING_FORCED
    await push(c, LLMFullResponseStartFrame())                # her goodbye starts...
    await push(c, BotStartedSpeakingFrame())                  # ...and never stops
    await asyncio.sleep(0.4)
    assert worker.ended == 1 and c.ending["hang_up_by"] == "hard_stop_cap"


async def test_the_hard_stop_goodbye_that_finishes_hangs_up_on_its_end(fast):
    c, service, worker = make()
    c._force_task = asyncio.create_task(c._force_end())
    await asyncio.sleep(0.05)
    await goodbye_reply(c, service)
    assert worker.ended == 1 and c.ending["hang_up_by"] == "bot_stopped_speaking"


async def test_the_end_button_cancels_her_reply_and_she_says_goodbye(fast):
    c, service, worker = make()
    await push(c, LLMFullResponseStartFrame())
    await c.end_by_candidate_button()
    assert service.events == ["cancel", "note", "reply"] and "pressed End" in service.notes[0]
    assert c.ending["ending"] == ENDING_END_BUTTON
    await goodbye_reply(c, service)
    assert worker.ended == 1


async def test_the_long_silence_close_is_her_words_then_the_hang_up(fast):
    c, service, worker = make()
    await c.close_for_silence()
    assert "no sound from the candidate for about two minutes" in service.notes[0]
    assert service.replies and c.ending["ending"] == ENDING_SILENCE and c.reason == "abandoned_by_candidate"
    await goodbye_reply(c, service)
    assert worker.ended == 1


async def test_a_code_ending_answered_with_the_tool_alone_still_gets_her_goodbye(fast):
    """Round 14 finding: after the long-silence note she once called end_interview
    without a word. She is now asked for the goodbye; the call ends after it."""
    c, service, worker = make()
    await c.close_for_silence()
    await push(c, LLMFullResponseStartFrame())                # her reply: the tool only
    result, props = await call(c, service, "end_interview", reply="r1", spoke=False, reason="completed")
    assert result["accepted"] is True and "goodbye" in result["next"] and runs_llm(props)
    assert c.ending["ended_without_goodbye"] == 1 and worker.ended == 0
    await push(c, LLMFullResponseEndFrame())
    await goodbye_reply(c, service)
    assert worker.ended == 1


async def test_the_quiet_notes_leave_the_choice_to_her():
    c, service, _ = make()
    await c.quiet_note(1, 20)
    await c.quiet_note(2, 40)
    assert "You decide whether to keep waiting" in service.notes[0]
    assert "microphone may not be reaching you" in service.notes[1] and len(service.replies) == 2


async def test_the_silence_ladder_on_the_realtime_engine_sends_notes_not_lines():
    from bot.silence import SilenceEscalation

    seen = []
    ladder = SilenceEscalation(session_id="t", notify=lambda stage, s: _rec(seen, ("note", stage)),
                               close=lambda reason: _rec(seen, ("close", reason)))
    injected = []

    async def inject(frame):
        injected.append(type(frame).__name__)

    ladder._inject = inject
    for _ in range(3):
        await ladder.handle_idle(None)
    assert seen == [("note", 1), ("note", 2), ("close", "abandoned_by_candidate")]
    assert "TTSSpeakFrame" not in injected and "LLMMessagesAppendFrame" not in injected


async def _rec(seen, item):
    seen.append(item)


# -- 5. records -----------------------------------------------------------------------------------------


def test_the_small_talk_before_the_first_plan_question_is_pre_interview():
    from bot.persistence import build_transcript

    turns = [{"speaker": "interviewer", "text": "Good afternoon, Asha. I'm Mitra, an AI interviewer.", "at_seconds": 1},
             {"speaker": "candidate", "text": "I'm well, thanks.", "at_seconds": 5},
             {"speaker": "interviewer", "text": "Let's begin. Tell me about a strategy you set.", "at_seconds": 30},
             {"speaker": "candidate", "text": "We moved to multi-acquirer routing.", "at_seconds": 40}]
    t = build_transcript(interview_id="int_x", turns=turns, duration_seconds=50, clock_zero=1000.0,
                         plan_started_wall=1020.0)
    assert [x.pre_interview for x in t.turns] == [True, True, False, False]
    assert [x.text for x in t.interview_turns()][0].startswith("Let's begin")


def test_the_backend_sends_ending_codes_only_and_never_its_own_sentence():
    """Round 15: one source for the ending words, Session B's (ui/src/report/endingWords.ts,
    typed against the contract). The bot records codes; no backend module words an
    ending, so the two can never drift apart."""
    import importlib.util

    assert importlib.util.find_spec("feedback.ending_words") is None
    run = (BACKEND / "bot" / "run_realtime.py").read_text()
    assert "in_words" not in run and "ending_in_words" not in run
    c, _, _ = make()
    assert set(c.ending) == {"ending", "hang_up_by", "skips", "areas", "notes", "backstop_moves",
                             "ended_without_goodbye", "end_interview_refused"}
    # The words live in the product's console (Track I's ui/src/report/endingWords.ts,
    # copied unchanged but for its import paths, to frontend/console).
    words = (BACKEND.parent / "frontend" / "console" / "src" / "features" / "results" / "endingWords.ts").read_text()
    for code in (ENDING_FORCED, ENDING_WITHDREW):
        assert f"{code}:" in words, f"B's words must cover the bot's ending code {code}"


async def test_a_clarification_is_the_same_question_never_a_follow_up():
    """C-146 option 1: the same plan question asked again is labelled clarification,
    with its id and no follow-up number; it is sent live and does not use up a follow-up."""
    from bot.auditor import RealtimeAuditor
    from bot.live_events import LiveEvents
    from shared.interview_contracts import InterviewEvent

    q = dict(BP.competency_plans[0].numbered_questions())
    simpler = "Let me put it better: what direction did you set for the product?"
    readings = {q["Q2.1"]: {"label": "plan", "question_id": "Q2.1"},
                simpler: {"label": "plan", "question_id": "Q2.1"},
                "And who disagreed?": {"label": "follow_up", "question_id": "Q2.1"}}

    async def request(**kw):
        text = kw["messages"][1]["content"].split("INTERVIEWER TURN\n  ", 1)[1]
        reading = {"label": "off_plan", "question_id": None, "new_claims": [], "contradictions": [],
                   **readings.get(text, {})}
        return SimpleNamespace(payload={"coverage": "partial", **reading}, record=SimpleNamespace(cost_usd=0.0))

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

    live = LiveEvents("int_0123456789ab", clock_zero=0.0, blueprint=BP, emitter=Emitter())
    await live.start()
    a = RealtimeAuditor(blueprint=BP, area_now=lambda: 2, live=live, request=request)
    a.start()
    await a.label_now(0, q["Q2.1"])
    a.candidate_turn(1, "Sorry, I didn't understand the question.")
    await a.label_now(2, simpler)
    a.candidate_turn(3, "Ah. We moved payouts to multi-acquirer routing.")
    await a.label_now(4, "And who disagreed?")
    await a.finish(timeout=5)
    assert a.labels[2]["label"] == "clarification" and a.labels[2]["question_id"] == "Q2.1"
    assert "follow_up_number" not in a.labels[2]
    assert a.labels[4]["label"] == "follow_up" and a.labels[4]["follow_up_number"] == 1, "not the 2nd"
    sent = [(e.payload.label.value, e.payload.question_id, e.payload.follow_up_number)
            for e in live._emitter.events if e.payload.kind == "question_labelled"]
    assert sent == [("plan", "Q2.1", None), ("clarification", "Q2.1", None), ("follow_up", "Q2.1", 1)]
    assert live.invalid == 0


# -- 6. the pacing backstop (pre-agreed guarantee: an area at twice its plan) ----------------------------


def past_twice_the_plan(c, area=2):
    """Past the backstop's point (1.5x the plan at most, round 15) for the area."""
    planned = BP.competency_plans[area - 2].time_budget_minutes
    c._area_log[-1]["entered_seconds"] = 60.0
    c._started_at = time.monotonic() - (60 + planned * 2 * 60 + 1)
    assert planned * 2 > c.allowance(area, 60.0)[1]


async def in_area_two(c, service):
    await call(c, service, "move_on", reply="m0", spoke=False)
    assert c.current_area == 2


async def test_the_backstop_moves_one_area_at_a_quiet_moment_and_tells_her(fast):
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await in_area_two(c, service)
    past_twice_the_plan(c)
    await c.check_time_now()
    await asyncio.wait_for(c._backstop_task, 1)
    assert c.current_area == 3 and c.ending["backstop_moves"] == 1 and service.swaps[-1] == "area 3"
    note = service.notes[-1]
    assert "used its share of the time" in note and "own words" in note
    assert "you are in it now" in note and "do not call move_on for it" in note
    assert service.replies == [], "her transition comes at her next turn, never into a pause"
    await c.check_time_now()
    assert c.current_area == 3, "a new area, a fresh clock"


async def test_move_on_and_the_backstop_at_the_same_instant_move_one_area_only(fast):
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await in_area_two(c, service)
    past_twice_the_plan(c)
    await c.check_time_now()
    c._reply_area = 2   # her reply began while area 2 was shown; she calls move_on in it
    await asyncio.gather(call(c, service, "move_on", reply="m1", spoke=False), c._backstop_task)
    assert c.current_area == 3, "both asked to leave area 2: one move"
    assert [a["area"] for a in c.area_log()] == [1, 2, 3]
    ignored = [e for e in c.events if e["kind"] in ("area_move_ignored", "backstop_move_ignored")]
    assert len(ignored) == 1


async def test_area_moves_reach_the_live_view_as_move_on_tool_calls(fast):
    """C-A17 / C-156: move_on, a refused move_on and an ignored duplicate are sent as
    ToolCalled(move_on) with outcomes moved / refused / ignored, through the real
    LiveEvents (validated against the contract). A backstop move is never a tool call."""
    from bot.live_events import LiveEvents
    from shared.interview_contracts import InterviewEvent

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

    live = LiveEvents("int_0123456789ab", clock_zero=0.0, blueprint=BP, emitter=Emitter())
    await live.start()
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    c.attach_events(live)
    await call(c, service, "move_on", reply="m0", spoke=False)            # 1 -> 2: moved
    c._reply_area = 1
    await call(c, service, "move_on", reply="m1", spoke=False)            # still "from 1": ignored
    past_twice_the_plan(c)
    await c.check_time_now()
    await asyncio.wait_for(c._backstop_task, 1)                           # 2 -> 3 by code
    c._reply_area = None
    for _ in range(len(BP.competency_plans)):
        await call(c, service, "move_on", reply="mx", spoke=False)
    await call(c, service, "move_on", reply="mz", spoke=False)            # in the closing: refused
    tools = [e.payload.outcome for e in live._emitter.events
             if e.payload.kind == "tool_called" and e.payload.tool == "move_on"]
    assert tools[:2] == ["moved", "ignored"] and tools[-1] == "refused"
    assert tools.count("moved") == 1 + len(BP.competency_plans) - 1, "the backstop's move is not a tool call"
    assert live.invalid == 0


async def test_the_same_instant_the_other_way_round_also_moves_one_area(fast):
    """move_on first, then the backstop that was due: still one move."""
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await in_area_two(c, service)
    past_twice_the_plan(c)
    c._reply_area = 2
    await call(c, service, "move_on", reply="m1", spoke=False)
    await c.check_time_now()
    await asyncio.sleep(0.1)
    assert c.current_area == 3 and c.ending["backstop_moves"] == 0


async def test_the_backstop_waits_while_the_candidate_is_talking(fast):
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await in_area_two(c, service)
    past_twice_the_plan(c)
    await push(c, UserStartedSpeakingFrame())
    await asyncio.wait_for(c.check_time_now(wait=False), 0.5)   # the clock is never held
    await asyncio.sleep(0.2)
    assert c.current_area == 2, "never while the candidate is talking"
    await push(c, UserStoppedSpeakingFrame())
    await asyncio.wait_for(c._backstop_task, 1)
    assert c.current_area == 3


async def test_the_backstop_waits_during_a_drop_until_the_return(fast):
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await in_area_two(c, service)
    past_twice_the_plan(c)
    await c.candidate_away(True)
    await c.check_time_now()
    await asyncio.sleep(0.2)
    assert c.current_area == 2
    await c.candidate_away(False)
    await asyncio.wait_for(c._backstop_task, 1)
    assert c.current_area == 3


async def test_at_the_hard_stop_the_hard_stop_wins(fast):
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await in_area_two(c, service)
    past_twice_the_plan(c)
    await push(c, UserStartedSpeakingFrame())          # the backstop is waiting...
    await asyncio.wait_for(c.check_time_now(wait=False), 0.5)
    c._force_task = asyncio.create_task(c._force_end())  # ...when the hard stop comes
    await push(c, UserStoppedSpeakingFrame())
    await asyncio.sleep(0.2)
    assert c.current_area == 2 and c.ending["backstop_moves"] == 0 and c.is_over


async def test_a_note_waiting_for_quiet_never_holds_the_clock(fast):
    """Found in round 14: the clock awaited its note, so a candidate talking held the
    clock and the hard stop. The clock now hands the note to a task."""
    c, service, _ = make()
    await push(c, UserStartedSpeakingFrame())
    c._started_at = time.monotonic() - 21 * 60
    await asyncio.wait_for(c.check_time_now(wait=False), 0.5)
    assert service.notes == []
    c._started_at = time.monotonic() - BP.evaluation_spec.hard_stop_minutes * 60
    await asyncio.wait_for(c.check_time_now(wait=False), 0.5)
    assert c._force_task is not None, "the hard stop is not held by a waiting note"
    await asyncio.sleep(0.1)
    assert c.is_over


# -- 7. her permanent instructions hold the rules a model must follow -----------------------------


def _section(text: str, header: str) -> str:
    start = text.index(f"\n{header}\n") + 1
    nxt = re.search(r"\n\n[A-Z][A-Z ,=']+\n", text[start + len(header):])
    return " ".join((text[start: start + len(header) + nxt.start()] if nxt else text[start:]).split())


BASE = build_base_instruction(BP, first_name="Asha", time_of_day="afternoon")


def test_her_first_turn_always_says_she_is_an_ai_interviewer():
    first = _section(BASE, "YOUR FIRST TURN")
    assert "that you are Mitra, an AI interviewer" in first
    assert "Greet them for the time of day with their first name" in first


def test_notes_and_tools_are_private_and_her_rules_still_apply():
    notes = _section(BASE, "PRIVATE NOTES AND TOOLS")
    assert "never read them out, quote them or refer to them" in notes
    assert 'never say "note", "system", "tool" or "I\'ve been told"' in notes
    assert "Your manner and warmth rules always apply, whatever a note says" in notes


def test_praise_is_never_allowed_and_warmth_is_the_same_for_everyone():
    sound = _section(BASE, "HOW YOU SOUND")
    assert "Never praise or grade an answer" in sound
    assert all(w in sound for w in ('"great answer"', '"excellent"', '"perfect"', '"impressive"'))
    assert "the same warmth to every candidate" in sound


def test_goodbye_then_the_tool_in_round_14s_simple_wording():
    """Round 15 (A-97): the emphatic "SAME reply" wording made the order WORSE (text mode:
    goodbye in the call's reply 1 of 12 against round 14's 19 of 20); reverted to round
    14's plain order. The confirmation stays a natural yes/no, never "with a yes"."""
    ending = _section(BASE, "ENDING THE INTERVIEW")
    assert "say goodbye with their first name, then call end_interview" in ending
    assert "SAME reply" not in BASE and "A reply with only" not in BASE
    stop = _section(BASE, "WHEN THEY WANT TO STOP")
    assert 'never add "with a yes"' in stop
    tool = next(t for t in REALTIME_TOOLS.standard_tools if t.name == "end_interview")
    assert "AFTER you have said a warm goodbye" in tool.description and "SAME" not in tool.description


def test_the_warmth_rules_for_declines_long_answers_and_next_steps():
    sound = _section(BASE, "HOW YOU SOUND")
    assert '"No problem at all."' in sound and "ALWAYS begin with a brief thank-you" in sound
    assert "does not judge the answer" in sound
    nxt = _section(BASE, "QUESTIONS ABOUT NEXT STEPS")
    assert "answer warmly" in nxt and "no promises" in nxt


def test_the_hard_stop_note_asks_for_one_sentence():
    assert "in ONE short sentence" in rc.NOTE_HARD_STOP and "Do not ask anything" in rc.NOTE_HARD_STOP


def test_the_name_only_in_the_greeting_and_the_goodbye_and_no_promises():
    assert "Use it once in your greeting and once in your goodbye, and nowhere in between" in BASE
    nxt = _section(BASE, "QUESTIONS ABOUT NEXT STEPS")
    assert "Do not say that anyone will contact them, when, or how" in nxt and "no promises" in nxt


async def test_a_note_is_a_system_item_that_is_never_taken_for_her_reply(monkeypatch):
    """A note must never come back as an assistant reply (and so into her transcript):
    the service marks its own items, so the server's echo is not read as her speaking."""
    from bot.services import realtime as svc

    sent = []

    async def capture(self, event):
        sent.append(event)

    monkeypatch.setattr(svc.MitraRealtimeService, "send_client_event", capture)
    llm = svc.build_realtime_llm(api_key="test", instructions="x", tools=None)
    item_id = await llm.insert_note("PRIVATE NOTE: hello")
    item = sent[-1].item
    assert item.role == "system" and item.content[0].text == "PRIVATE NOTE: hello"
    assert llm._messages_added_manually.get(item_id) is True
    await llm.request_reply()
    assert getattr(sent[-1].response, "instructions", None) in (None, ""), "no per-reply instructions"


async def test_the_real_calls_sequence_goodbye_first_then_the_call_is_accepted(fast):
    """Replays round 14's real call (int_0d55a3bd8e3a): she asked, they said yes, she said
    goodbye WITHOUT the tool; later she called it in a reply with no speech. Accepted (the
    guard, A-91); round 16: the tool result asks for her goodbye, a brief "Take care" being
    enough, and the call ends after that reply has played."""
    c, service, worker = make()
    await candidate(c, "Honestly, I think I'm done here.")
    await mitra(c, service, "r1", "Would you like to end the interview here?")
    await candidate(c, "Yes, please.")
    await mitra(c, service, "r2", "Thank you for your time this afternoon. Take care and goodbye.")
    result, props = await call(c, service, "end_interview", reply="r3", spoke=False, reason="withdrew")
    assert result["accepted"] is True and runs_llm(props) and "Take care" in result["next"]
    assert c.reason == "candidate_withdrew" and worker.ended == 0
    await goodbye_reply(c, service, text="Take care, Asha.")
    assert worker.ended == 1


async def test_the_backstop_into_the_closing_asks_for_the_goodbye_not_a_question(fast):
    """Round 15 finding (A-96): the backstop's note into the closing said "ask its first
    question"; she announced a goodbye instead of saying one. The closing's note says
    what the closing is: a one-sentence goodbye, then end_interview in the same reply."""
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    last = len(BP.competency_plans) + 1
    for i in range(last - 1):
        await call(c, service, "move_on", reply=f"m{i}", spoke=False)
    assert c.current_area == last
    c._area_log[-1]["entered_seconds"] = 0.0
    c._started_at = time.monotonic() - 39 * 60
    await c.check_time_now()
    await asyncio.wait_for(c._backstop_task, 1)
    note = service.notes[-1]
    assert c.current_area == last + 1 and "moved on to the closing" in note
    assert "goodbye" in note and "ask its first question" not in note


async def test_the_opening_is_never_cut_short_by_the_backstop(fast):
    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    c._started_at = time.monotonic() - 10 * 60
    await c.check_time_now()
    await asyncio.sleep(0.1)
    assert c.current_area == 1 and c._backstop_task is None


# -- round 16: hang up only after the reply that FOLLOWS an accepted end_interview ------------


async def test_a_wrap_up_line_with_the_tool_is_never_the_last_thing_heard(fast):
    """A-97, from the real calls: "Thanks for staying with me. I'll wrap up now." and the
    tool in one reply; the call ended there, with no goodbye. Now the tool result asks for
    her goodbye, and the call ends only after that following reply has played."""
    c, service, worker = make()
    c._area = len(BP.competency_plans) + 2                     # the closing
    await candidate(c, "I think that covers it.")
    await push(c, LLMFullResponseStartFrame())                 # her reply: wrap-up words + tool
    await push(c, BotStartedSpeakingFrame())
    result, props = await call(c, service, "end_interview", reply="r1", spoke=True, reason="completed")
    assert result["accepted"] is True and runs_llm(props) and "goodbye" in result["next"]
    service.reply_spoke["r1"], service.reply_text["r1"] = True, "Thanks for staying with me. I'll wrap up now."
    await push(c, LLMFullResponseEndFrame())
    await push(c, BotStoppedSpeakingFrame())                   # the wrap-up line finished playing
    await asyncio.sleep(0.15)
    assert worker.ended == 0, "never hang up on the reply that carried the call"
    await push(c, LLMFullResponseStartFrame())                 # the goodbye reply
    service.reply_spoke["r2"], service.reply_text["r2"] = True, "Thank you for your time, Asha. Goodbye."
    await push(c, BotStartedSpeakingFrame())
    await push(c, LLMFullResponseEndFrame())
    assert worker.ended == 0, "never while the goodbye plays"
    await push(c, BotStoppedSpeakingFrame())
    assert worker.ended == 1 and c.ending["hang_up_by"] == "bot_stopped_speaking"


async def test_a_reply_that_only_calls_a_tool_never_counts_as_the_goodbye(fast):
    """After an ending note, a reply with no speech (a tool only) must not arm the hang-up;
    the next spoken reply does."""
    c, service, worker = make()
    await c.close_for_silence()
    await push(c, LLMFullResponseStartFrame())                 # tool only, no speech
    service.reply_spoke["r1"] = False
    await push(c, LLMFullResponseEndFrame())
    await asyncio.sleep(0.15)
    assert worker.ended == 0
    await push(c, LLMFullResponseStartFrame())
    service.reply_spoke["r2"] = True
    await push(c, BotStartedSpeakingFrame())
    await push(c, LLMFullResponseEndFrame())
    await push(c, BotStoppedSpeakingFrame())
    assert worker.ended == 1


async def test_a_gap_before_the_goodbye_audio_never_hangs_up_on_the_line_before(fast, monkeypatch):
    """Round 16 real calls (completed x2): "I'll wrap up briefly" + the tool; the goodbye
    reply was generated while that line still played; a 0.9 s gap between the two
    clips raised "stopped speaking", and the call hung up as the goodbye began ("Thanks
    for your"). The hang-up must wait for the goodbye's own audio to start and stop."""
    monkeypatch.setattr(rc, "_GOODBYE_GAP_SECONDS", 0.2)
    c, service, worker = make()
    c._area = len(BP.competency_plans) + 2
    await candidate(c, "That covers it.")
    await push(c, LLMFullResponseStartFrame())                 # "Thanks… I'll wrap up briefly." + tool
    await push(c, BotStartedSpeakingFrame())
    await call(c, service, "end_interview", reply="r1", spoke=True, reason="completed")
    service.reply_spoke["r1"] = True
    await push(c, LLMFullResponseEndFrame())
    await push(c, LLMFullResponseStartFrame())                 # the goodbye, generated while r1 plays
    service.reply_spoke["r2"] = True
    await push(c, LLMFullResponseEndFrame())
    await push(c, BotStoppedSpeakingFrame())                   # the gap after the wrap-up line
    await asyncio.sleep(0.05)
    await push(c, BotStartedSpeakingFrame())                   # the goodbye's audio begins
    await asyncio.sleep(0.3)
    assert worker.ended == 0, "never on the gap before the goodbye"
    await push(c, BotStoppedSpeakingFrame())
    assert worker.ended == 1


async def test_one_continuous_stretch_of_audio_still_hangs_up_after_it(fast, monkeypatch):
    """The goodbye's audio followed the line before with no gap: one "stopped speaking" at
    the very end; with no new start within the wait, that stop ended the goodbye too."""
    monkeypatch.setattr(rc, "_GOODBYE_GAP_SECONDS", 0.2)
    c, service, worker = make()
    c._area = len(BP.competency_plans) + 2
    await candidate(c, "That covers it.")
    await push(c, LLMFullResponseStartFrame())
    await push(c, BotStartedSpeakingFrame())
    await call(c, service, "end_interview", reply="r1", spoke=True, reason="completed")
    service.reply_spoke["r1"] = True
    await push(c, LLMFullResponseEndFrame())
    await push(c, LLMFullResponseStartFrame())
    service.reply_spoke["r2"] = True
    await push(c, LLMFullResponseEndFrame())
    await push(c, BotStoppedSpeakingFrame())                   # the end of both, played back to back
    assert worker.ended == 0
    await asyncio.sleep(0.35)
    assert worker.ended == 1 and c.ending["hang_up_by"] == "bot_stopped_speaking"
