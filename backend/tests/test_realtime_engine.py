"""The realtime engine as the default, and what dev carried over to it from stg.

`realtime-poc` shipped with no tests of its own, and dev merges it onto stg,
which rewrote most of the modules it calls. A green suite said nothing about it.
These cover the parts that can be checked without a live call: which engine
runs, that the whole-interview prompt and clock build from every blueprint the
repo carries, and that captions reach the candidate page in the shape it reads.

Whether the realtime interview *sounds* right is a listening session, not this.
"""

import pathlib
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputTransportMessageUrgentFrame,
    TTSTextFrame,
)
from pipecat.utils.text.base_text_aggregator import AggregationType

from bot import config
from bot.brain.brain import InterviewBrain
from bot.realtime_captions import LABEL, RealtimeCaptions
from bot.realtime_coordinator import RealtimeCoordinator
from bot.realtime_prompt import as_goal, area_count, build_area_instruction
from shared.contracts import FIRST_COMPETENCY_AREA, InterviewBlueprint

FIXTURES = sorted((pathlib.Path(__file__).parents[1] / "bot" / "fixtures").glob("*.json"))


# ---- which engine runs ------------------------------------------------------


def test_realtime_is_the_default_engine(monkeypatch):
    monkeypatch.delenv(config.INTERVIEW_ENGINE_ENV, raising=False)
    assert config.interview_engine() == "realtime"


@pytest.mark.parametrize("value", ["pipeline", " PIPELINE ", "Pipeline"])
def test_pipeline_is_still_selectable_as_the_fallback(monkeypatch, value):
    monkeypatch.setenv(config.INTERVIEW_ENGINE_ENV, value)
    assert config.interview_engine() == "pipeline"


@pytest.mark.parametrize("value", ["", "  ", "realtme", "speech-to-speech"])
def test_a_blank_or_mistyped_engine_falls_back_to_the_default_rather_than_crashing(
    monkeypatch, value
):
    monkeypatch.setenv(config.INTERVIEW_ENGINE_ENV, value)
    assert config.interview_engine() == "realtime"


async def test_run_bot_hands_the_session_to_the_realtime_engine_by_default(monkeypatch):
    import bot.run_realtime
    from bot import run_bot as run_bot_module

    monkeypatch.delenv(config.INTERVIEW_ENGINE_ENV, raising=False)
    calls = []

    async def fake_realtime_bot(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(bot.run_realtime, "run_realtime_bot", fake_realtime_bot)

    def pipeline_must_not_start():
        raise AssertionError("the pipeline engine started instead of the realtime one")

    monkeypatch.setattr(run_bot_module.Settings, "load", staticmethod(pipeline_must_not_start))

    await run_bot_module.run_bot(
        room_url="https://example.daily.co/room",
        token="t",
        session_id="s1",
        blueprint_id="pm_senior",
        interview_id="int_1",
        timezone="Asia/Kolkata",
    )
    assert calls == [
        {
            "room_url": "https://example.daily.co/room",
            "token": "t",
            "session_id": "s1",
            "blueprint_id": "pm_senior",
            "interview_id": "int_1",
            "timezone": "Asia/Kolkata",
        }
    ]


# ---- the plan the realtime model is given -----------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_plan_question_is_reachable_across_the_areas(path):
    """The bot's instructions are the stable base plus the current area (decision 22,
    bot/realtime_prompt.build_area_instruction). Across the areas, the whole interview is
    there: every instruction carries what her own first turn must contain (round 14: no
    fixed opening line) and names every competency, and each competency's questions are
    all shown in that competency's own area."""
    blueprint = InterviewBlueprint.model_validate_json(path.read_text())

    areas = [
        build_area_instruction(blueprint, first_name="Asha", time_of_day="afternoon", area=area)
        for area in range(1, area_count(blueprint) + 1)
    ]

    for text in areas:
        assert "YOUR FIRST TURN" in text and "an AI interviewer" in text and "Asha" in text
        assert all(plan.name in text for plan in blueprint.competency_plans)
    for index, plan in enumerate(blueprint.competency_plans):
        own_area = areas[index + FIRST_COMPETENCY_AREA - 1]
        assert all(as_goal(question) in own_area for question in plan.seed_questions)  # round 15: goals


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_clock_schedule_is_ordered_and_inside_the_interview(path):
    blueprint = InterviewBlueprint.model_validate_json(path.read_text())
    coordinator = RealtimeCoordinator(blueprint=blueprint, llm=None, session_id="t")

    schedule = coordinator._time_marks()

    assert schedule, "a realtime interview with no clock notes cannot keep time"
    times = [at for at, _, _ in schedule]
    assert times == sorted(times)
    hard_stop = (
        blueprint.evaluation_spec.duration_minutes
        + blueprint.evaluation_spec.overrun_grace_minutes
    ) * 60
    assert all(0 <= at <= hard_stop for at in times)


# ---- captions ---------------------------------------------------------------


class _Worker:
    def __init__(self):
        self.messages: list[dict] = []

    async def queue_frames(self, frames):
        for frame in frames:
            assert isinstance(frame, OutputTransportMessageUrgentFrame)
            self.messages.append(frame.message)


def _delta(text: str) -> TTSTextFrame:
    # Exactly as Pipecat's OpenAI Realtime service pushes a transcript delta.
    frame = TTSTextFrame(text, aggregated_by=AggregationType.SENTENCE)
    frame.includes_inter_frame_spaces = True
    return frame


async def _feed(captions: RealtimeCaptions, *frames):
    for frame in frames:
        await captions.on_push_frame(SimpleNamespace(frame=frame))


def _outputs(worker: _Worker) -> list[dict]:
    return [m["data"] for m in worker.messages if m["type"] == "bot-output"]


async def test_captions_arrive_a_sentence_at_a_time_on_one_line_per_reply():
    worker = _Worker()
    captions = RealtimeCaptions(session_id="s")
    captions.attach(worker)

    await _feed(
        captions,
        LLMFullResponseStartFrame(),
        *[_delta(t) for t in ["Hel", "lo, I'm", " Mitra.", " Tell me", " about your", " role?"]],
        LLMFullResponseEndFrame(),
    )

    assert all(m["label"] == LABEL for m in worker.messages)
    assert worker.messages[0] == {"label": LABEL, "type": "bot-started-speaking"}
    texts = [d["text"] for d in _outputs(worker)]
    # Six deltas, two messages: one per completed sentence, never one per delta.
    assert texts == ["Hello, I'm Mitra.", "Hello, I'm Mitra. Tell me about your role?"]
    # The same segment id throughout, so the page updates one line in place.
    assert len({d["segment_id"] for d in _outputs(worker)}) == 1


async def test_a_reply_with_no_closing_punctuation_is_still_captioned_when_it_ends():
    worker = _Worker()
    captions = RealtimeCaptions(session_id="s")
    captions.attach(worker)

    await _feed(
        captions,
        LLMFullResponseStartFrame(),
        _delta("Take your time"),
        _delta(", there's no rush"),
        LLMFullResponseEndFrame(),
    )

    assert [d["text"] for d in _outputs(worker)] == ["Take your time, there's no rush"]


async def test_each_reply_starts_a_new_caption_turn_and_a_new_segment():
    worker = _Worker()
    captions = RealtimeCaptions(session_id="s")
    captions.attach(worker)

    for text in ["First question?", "Second question?"]:
        await _feed(captions, LLMFullResponseStartFrame(), _delta(text), LLMFullResponseEndFrame())

    types = [m["type"] for m in worker.messages]
    assert types == ["bot-started-speaking", "bot-output", "bot-started-speaking", "bot-output"]
    first, second = _outputs(worker)
    assert first["segment_id"] != second["segment_id"]
    assert second["text"] == "Second question?"


async def test_a_frame_seen_on_several_hops_is_captioned_once():
    worker = _Worker()
    captions = RealtimeCaptions(session_id="s")
    captions.attach(worker)
    start, delta, end = LLMFullResponseStartFrame(), _delta("Hello."), LLMFullResponseEndFrame()

    # Observers see a frame on every processor hop.
    await _feed(captions, start, start, delta, delta, end, end)

    assert [m["type"] for m in worker.messages] == ["bot-started-speaking", "bot-output"]


async def test_captions_carry_only_text_the_interviewer_spoke():
    # Nothing about the candidate is ever sent back to them; the observer only
    # reacts to the interviewer's own output frames.
    from pipecat.frames.frames import TranscriptionFrame

    worker = _Worker()
    captions = RealtimeCaptions(session_id="s")
    captions.attach(worker)

    await _feed(captions, TranscriptionFrame("my salary is", "user", "2026-11-18T00:00:00Z"))

    assert worker.messages == []


# ---- the whole realtime session, wired but not connected ---------------------


async def test_the_realtime_session_wires_up_and_saves_its_artifacts(monkeypatch, tmp_path):
    """Builds the real pipeline, transport and observers with fake keys, then
    stops before anything connects. Catches what the merge could break and a
    unit test cannot see: a renamed import, a changed signature, an event
    handler the transport does not have."""
    import bot.run_realtime as rt
    from bot.presence import RoomPresence

    monkeypatch.setattr(
        rt.Settings,
        "load",
        staticmethod(lambda: SimpleNamespace(openai_api_key="sk-test", daily_api_key="d")),
    )
    monkeypatch.setattr(rt, "SESSIONS_DIR", tmp_path)

    built = {}
    real_presence = RoomPresence

    def capture_presence(**kwargs):
        built["presence"] = real_presence(**kwargs)
        return built["presence"]

    monkeypatch.setattr(rt, "RoomPresence", capture_presence)

    real_worker = rt.PipelineWorker

    def capture_worker(*args, **kwargs):
        built["observers"] = list(kwargs.get("observers") or [])
        return real_worker(*args, **kwargs)

    monkeypatch.setattr(rt, "PipelineWorker", capture_worker)

    async def add_workers(self, *workers):
        pass

    async def run(self):
        return None

    monkeypatch.setattr(rt.WorkerRunner, "add_workers", add_workers)
    monkeypatch.setattr(rt.WorkerRunner, "run", run)

    await rt.run_realtime_bot(
        room_url="https://example.daily.co/room",
        token=None,
        session_id="wire-test",
        blueprint_id="pm_senior",
    )

    observers = {type(o).__name__ for o in built["observers"]}
    assert {"RealtimeCaptions", "RealtimeCoordinator", "TranscriptObserver"} <= observers
    # Presence knows when the interview is over, so a goodbye-then-hang-up is
    # not recorded as a dropped call.
    assert built["presence"].interview_over is not None
    assert (tmp_path / "wire-test.json").exists()


def test_realtime_engine_runs_without_elevenlabs_keys(monkeypatch):
    """The realtime engine never calls ElevenLabs, so its keys must not block it."""
    from bot.config import Settings

    monkeypatch.delenv("INTERVIEW_ENGINE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DAILY_API_KEY", "daily-test")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)

    settings = Settings.load()
    assert settings.elevenlabs_api_key == ""
    assert settings.elevenlabs_voice_id == ""


def test_pipeline_engine_still_requires_elevenlabs_keys(monkeypatch):
    """The fallback speaks through ElevenLabs: fail at startup, never mid-interview."""
    from bot.config import MissingConfig, Settings

    monkeypatch.setenv("INTERVIEW_ENGINE", "pipeline")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DAILY_API_KEY", "daily-test")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)

    with pytest.raises(MissingConfig, match="ELEVENLABS_API_KEY"):
        Settings.load()
