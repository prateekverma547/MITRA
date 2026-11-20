"""The realtime engine's endings and records that survive round 14, deterministic, no keys.

Round 14 removed the code goodbye, finish_interview and code's withdrawal detection
(their tests went with them; tests/test_one_voice.py tests the new design). Kept here:
- the hard stop lands inside OpenAI's 60-minute session (minute 58);
- skips: recorded with their area; jumping ahead or past-tense "skipped" is not one;
- the prompt: skips go forward;
- fixtures: one ask per question (tests/test_generate_one_ask.py reads `asks`);
- the service's record of whether a reply spoke.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import TranscriptionFrame
from pipecat.observers.base_observer import FramePushed

from bot.brain.withdrawal import skipped_this_question
from bot.realtime_coordinator import FORCED_END_BY_MINUTES, RealtimeCoordinator
from bot.realtime_prompt import build_base_instruction
from bot.services.realtime import reply_had_speech
from shared.contracts import InterviewBlueprint

FIXTURES = sorted((Path(__file__).resolve().parents[1] / "bot" / "fixtures").glob("*.json"))


def blueprint(path=None) -> InterviewBlueprint:
    return InterviewBlueprint.model_validate(json.loads(Path(path or FIXTURES[1]).read_text()))


class FakeService:
    def __init__(self):
        self.notes, self.replies, self.paused, self.cancelled = [], 0, [], 0
        self.reply_of_call, self.reply_spoke, self.reply_text = {}, {}, {}

    def set_audio_input_paused(self, paused):
        self.paused.append(paused)

    async def request_reply(self):
        self.replies += 1

    async def update_instructions(self, text):
        pass

    async def insert_note(self, text):
        self.notes.append(text)
        return f"note{len(self.notes)}"

    async def delete_note(self, item_id):
        pass

    async def cancel_reply(self):
        self.cancelled += 1


class FakeWorker:
    def __init__(self):
        self.ended = 0

    async def queue_frames(self, frames):
        self.ended += 1


def make(bp=None):
    llm, worker = FakeService(), FakeWorker()
    c = RealtimeCoordinator(blueprint=bp or blueprint(), llm=llm, session_id="t")
    c.attach(worker)
    return c, llm, worker


async def push(c, frame):
    await c.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))


def test_the_hard_stop_lands_inside_the_60_minute_session():
    raw = json.loads(FIXTURES[1].read_text())
    raw["evaluation_spec"]["duration_minutes"] = 60
    raw["evaluation_spec"]["overrun_grace_minutes"] = 5
    c, _, _ = make(InterviewBlueprint.model_validate(raw))
    marks = c._time_marks()
    times = [at for at, _, _ in marks]
    assert marks[-1][1] == "hard_stop" and max(times) == FORCED_END_BY_MINUTES * 60 and times == sorted(times)


SKIPS = ["I'd rather skip this one, if that's okay.", "Can we skip this one?", "next question please",
         "Can we move on?"]
NOT_SKIPS = ["can we skip ahead to the system design part", "I skipped that step in my project",
             "let me skip the details and give the gist", "I don't know", "I'd rather not answer that",
             "I want to end the interview"]


@pytest.mark.parametrize("said", SKIPS)
async def test_a_skip_is_recorded_with_its_area(said):
    c, _, _ = make()
    c._area = 3
    await push(c, TranscriptionFrame(text=said, user_id="u", timestamp="t"))
    skips = [e for e in c.events if e["kind"] == "question_skipped"]
    assert len(skips) == 1
    assert skips[0]["source"] == "coordinator"
    assert skips[0]["payload"]["area"] == 3 and skips[0]["payload"]["area_name"]
    assert c.ending["skips"] == 1


@pytest.mark.parametrize("said", NOT_SKIPS)
async def test_these_are_not_recorded_as_skips(said):
    c, _, _ = make()
    await push(c, TranscriptionFrame(text=said, user_id="u", timestamp="t"))
    assert not [e for e in c.events if e["kind"] == "question_skipped"]
    assert skipped_this_question(said) is False


# -- 3 and 4. the prompt --------------------------------------------------------------------


def test_a_skip_goes_to_the_next_plan_question():
    base = build_base_instruction(blueprint(), first_name="Asha", time_of_day="afternoon")
    assert "ask the next numbered question in the current area" in base
    assert "not even an easier version of it" in base
    assert "ask a different question" not in base


# -- 5. fixtures -----------------------------------------------------------------------------

_JOINED = re.compile(r",\s*and\s+(?:what|how|why|who|when|where|which|did|do|does|if)\b", re.I)
#: A bare reason belongs to its ask (A-17: "..., and why that one?" is one ask).
_BARE_REASON = re.compile(r",\s*and\s+why(?:\s+(?:that|this)\s+one)?\s*\?$", re.I)
_ASK_SENTENCE = re.compile(r"^(?:tell me|walk me|take me|talk me|describe|give me|share)\b", re.I)


def asks(question: str) -> int:
    """A deterministic count of asks: question or request sentences, plus ", and what/how..." joins."""
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", question) if s.strip()]
    count = sum(1 for s in sentences if s.endswith("?") or _ASK_SENTENCE.match(s))
    return count + len(_JOINED.findall(question)) - len(_BARE_REASON.findall(question))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_fixture_question_is_one_ask(path):
    bp = blueprint(path)
    two = [q for p in bp.competency_plans for q in p.seed_questions if asks(q) > 1]
    assert two == []


def test_the_ask_counter_catches_the_two_part_forms():
    assert asks("Tell me about a strategy you set. What was the bet?") == 2
    assert asks("Who disagreed, and what did it cost?") == 2
    assert asks("What was the bet behind a strategy you set?") == 1
    assert asks("What was the single metric you held yourself to, and why that one?") == 1
    assert asks("Where was the blast radius, and why did you accept it?") == 2


# -- the service's record of a reply ------------------------------------------------------------


def test_the_server_record_says_whether_a_reply_had_speech():
    item = lambda kind: SimpleNamespace(type=kind)  # noqa: E731
    usage = lambda audio: SimpleNamespace(output_token_details=SimpleNamespace(audio_tokens=audio))  # noqa: E731
    assert reply_had_speech(SimpleNamespace(output=[item("function_call")], usage=usage(0))) is False
    assert reply_had_speech(SimpleNamespace(output=[item("message"), item("function_call")], usage=usage(0))) is True
    assert reply_had_speech(SimpleNamespace(output=[item("function_call")], usage=usage(40))) is True


async def test_the_service_records_the_reply_before_pipecat_ends_it(monkeypatch):
    from bot.services import realtime as svc

    seen = {}

    async def parent_done(self, evt):
        seen["spoke_at_end"] = dict(self.reply_spoke)

    async def parent_args(self, evt):
        pass

    monkeypatch.setattr(svc.OpenAIRealtimeLLMService, "_handle_evt_response_done", parent_done)
    monkeypatch.setattr(svc.OpenAIRealtimeLLMService, "_handle_evt_function_call_arguments_done", parent_args)
    llm = svc.build_realtime_llm(api_key="test", instructions="x", tools=None)
    await llm._handle_evt_function_call_arguments_done(SimpleNamespace(call_id="c", response_id="r"))
    await llm._handle_evt_response_done(SimpleNamespace(response=SimpleNamespace(
        id="r", output=[SimpleNamespace(type="function_call")],
        usage=SimpleNamespace(output_token_details=SimpleNamespace(audio_tokens=0)))))
    assert llm.reply_of_call == {"c": "r"}
    assert seen["spoke_at_end"] == {"r": False}
