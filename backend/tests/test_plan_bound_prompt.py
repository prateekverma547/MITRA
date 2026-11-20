"""Plan-bound questioning on the realtime engine (decision 22, task 18).

The instructions are a stable base plus the CURRENT AREA block, which is always
last, so the base stays byte-identical (and cached) across area swaps. Only the
current area is shown in full; the others appear by name. The coordinator swaps
the instructions at each area change, never while a reply is live.
"""

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame
from pipecat.observers.base_observer import FramePushed

from bot.realtime_coordinator import RealtimeCoordinator
from bot.realtime_prompt import (
    AREA_BLOCK_HEADER,
    ONE_QUESTION_RULE_HEADER,
    area_count,
    build_area_block,
    build_area_instruction,
    as_goal,
    build_base_instruction,
)
from shared.contracts import FIRST_COMPETENCY_AREA, InterviewBlueprint

FIXTURES = sorted((Path(__file__).resolve().parents[1] / "bot" / "fixtures").glob("*.json"))
WHO = {"first_name": "Asha", "time_of_day": "afternoon"}


def blueprint(path) -> InterviewBlueprint:
    return InterviewBlueprint.model_validate(json.loads(Path(path).read_text()))


def every_area(bp):
    return [build_area_instruction(bp, **WHO, area=a) for a in range(1, area_count(bp) + 1)]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_area_block_is_the_last_thing_in_the_instructions(path):
    bp = blueprint(path)
    for area, text in enumerate(every_area(bp), start=1):
        assert text.endswith(build_area_block(bp, area))
        assert text.count(f"\n{AREA_BLOCK_HEADER}\n") == 1
        assert text.rindex("PRIVATE NOTES AND TOOLS") < text.rindex(f"\n{AREA_BLOCK_HEADER}\n")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_everything_before_the_area_block_is_byte_identical_across_swaps(path):
    bp = blueprint(path)
    base = build_base_instruction(bp, **WHO)
    for text in every_area(bp):
        assert text[: len(base)] == base
    prefixes = {text[: text.rindex(f"\n{AREA_BLOCK_HEADER}\n")] for text in every_area(bp)}
    assert len(prefixes) == 1


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_only_the_current_area_is_shown_in_full(path):
    bp = blueprint(path)
    for index, plan in enumerate(bp.competency_plans):
        area = index + FIRST_COMPETENCY_AREA
        text = build_area_instruction(bp, **WHO, area=area)
        for other in bp.competency_plans:
            assert other.name in text, "every area appears by name (the interview's shape)"
            # Round 15: each question is shown as a goal (as_goal), meaning unchanged.
            shown = [q for q in other.seed_questions if as_goal(q) in text]
            if other is plan:
                assert shown == other.seed_questions
            else:
                assert shown == [], f"{other.name}'s questions leaked into area {area}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_question_ids_are_rendered_with_their_questions(path):
    bp = blueprint(path)
    for index, plan in enumerate(bp.competency_plans):
        block = build_area_block(bp, index + FIRST_COMPETENCY_AREA)
        for qid, question in plan.numbered_questions():
            assert f"  {qid} Find out: {as_goal(question)}" in block
            assert qid.startswith(f"Q{index + FIRST_COMPETENCY_AREA}.")


def test_the_rules_of_decision_22_are_in_the_base():
    text = build_base_instruction(blueprint(FIXTURES[0]), **WHO)
    assert "there is no fourth kind" in text
    # The decided decline behaviour (A-46): an easier question on the same thing is
    # the one allowed exception to "a plan question or a follow-up".
    assert "after they decline a question, one easier question about the same thing" in text
    assert "At most 3 follow-ups" in text or "at most 3 follow-ups" in text.lower()
    assert "never two questions in one turn" in text


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_one_question_rule_is_in_the_stable_base_not_the_area_block(path):
    """Task 18 follow-up: one question per turn; a two-part plan question is asked one part
    at a time. It is the same in every area, so it lives in the cached prefix (the base),
    which stays byte-identical across swaps (the test above)."""
    bp = blueprint(path)
    base = build_base_instruction(bp, **WHO)
    assert f"\n{ONE_QUESTION_RULE_HEADER}\n" in base
    assert "Ask about the first part only" in base
    assert "Never add a second question of your own" in base
    for area in range(1, area_count(bp) + 1):
        assert ONE_QUESTION_RULE_HEADER not in build_area_block(bp, area)
        text = build_area_instruction(bp, **WHO, area=area)
        assert text.index(ONE_QUESTION_RULE_HEADER) < text.rindex(f"\n{AREA_BLOCK_HEADER}\n")


# -- the swap ----------------------------------------------------------------------------


class FakeService:
    def __init__(self):
        self.instructions = []
        self.notes = 0

    def set_audio_input_paused(self, paused):
        pass

    async def update_instructions(self, text):
        self.instructions.append(text)

    async def insert_note(self, text):
        self.notes += 1
        return f"n{self.notes}"

    async def delete_note(self, item_id):
        pass


def coordinator(bp):
    llm = FakeService()
    builder = lambda area: build_area_instruction(bp, **WHO, area=area)  # noqa: E731
    return RealtimeCoordinator(blueprint=bp, llm=llm, area_instruction=builder), llm, builder


async def push(c, frame):
    await c.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))


async def move_on(c):
    seen = {}

    async def cb(result, properties=None):
        seen.update(result=result, instructions_then=list(c._llm.instructions))

    await c._handle_move_on(SimpleNamespace(arguments={}, result_callback=cb, tool_call_id="m"))
    return seen


async def test_move_on_shows_her_the_next_area_before_her_reply_uses_it():
    """Round 14: she decides when to move on; the swap happens inside her tool call,
    so the reply after its result already has the new area."""
    bp = blueprint(FIXTURES[0])
    c, llm, builder = coordinator(bp)
    seen = await move_on(c)
    assert seen["instructions_then"] == [builder(2)] and c.current_area == 2
    assert seen["result"]["moved"] is True and llm.notes == 0, "no note: the tool result tells her"
    await move_on(c)
    assert llm.instructions[-1] == builder(3) and c.current_area == 3


async def test_move_on_walks_every_area_to_the_closing_and_no_further():
    bp = blueprint(FIXTURES[0])
    c, llm, builder = coordinator(bp)
    for _ in range(len(bp.competency_plans) + 1):
        await move_on(c)
    assert c.current_area == area_count(bp) and llm.instructions[-1] == builder(area_count(bp))
    refused = await move_on(c)
    assert refused["result"]["moved"] is False and c.current_area == area_count(bp)
    assert len(llm.instructions) == len(bp.competency_plans) + 1
    assert [a["area"] for a in c.area_log()] == list(range(1, area_count(bp) + 1))


async def test_without_a_builder_nothing_is_swapped():
    bp = blueprint(FIXTURES[0])
    llm = FakeService()
    c = RealtimeCoordinator(blueprint=bp, llm=llm)
    await move_on(c)
    assert llm.instructions == [] and c.current_area == 2


async def test_the_service_sends_a_minimal_update_and_keeps_its_settings_in_step():
    from bot.services.realtime import build_realtime_llm

    llm = build_realtime_llm(api_key="test", instructions="old", tools=None)
    sent = []

    async def ws_send(message):
        sent.append(message)

    llm._ws_send = ws_send
    await llm.update_instructions("new instructions")
    assert sent[0]["type"] == "session.update"
    assert sent[0]["session"] == {"type": "realtime", "instructions": "new instructions"}
    assert llm._settings.system_instruction == "new instructions"
    assert llm._settings.session_properties.instructions == "new instructions"


# -- what must not change ----------------------------------------------------------------

def test_the_pipeline_engine_is_untouched():
    """The rollback engine never sees the area swaps or the new prompt."""
    from bot import run_bot
    from bot.brain import prompting

    source = inspect.getsource(run_bot)
    for name in ("build_area_instruction", "update_instructions", "enter_area", "realtime_prompt"):
        assert name not in source
    assert "realtime_prompt" not in inspect.getsource(prompting)
