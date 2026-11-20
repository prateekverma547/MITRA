"""Round 18: fixes after Karandeep's real interview (A-113 to A-116). Written from the
requirements in the round's prompt. Deterministic: no model is called."""

import json
import re
import time
from pathlib import Path

import pytest

import bot.realtime_coordinator as rc
from blueprint.refine import size_to_booking
from bot.realtime_prompt import build_area_instruction
from tests.test_auditor_labels import BP as AUD_BP, Q, run as audit_run
from tests.test_one_voice import BP, call, candidate, make, mitra

BEGIN = re.compile(r"(?i)let['’]?s begin")


# -- 1. "let's begin" once (A-113) --------------------------------------------------------------

def test_her_permanent_instructions_never_say_lets_begin_in_any_area():
    for area in range(1, len(BP.competency_plans) + 3):
        text = build_area_instruction(BP, first_name="Karan", time_of_day="evening", area=area)
        assert not BEGIN.search(text), f"area {area}: 'let's begin' is in her permanent instructions"


async def test_no_move_ever_announces_a_start_and_the_first_bridges_into_detail():
    """Track H, 2026-11-20 (product owner): "let's begin" after a real opening sounded as
    if the system had broken. The first move bridges, in her words, from what they just
    said into more detail; no move announces a start."""
    c, service, _ = make()
    first, _ = await call(c, service, "move_on", reply="r1", spoke=False)
    assert first["moved"] is True and not BEGIN.search(first["next"])
    nxt = " ".join(first["next"].split())
    # A reaction to something specific they said (curious, never praise or a restatement),
    # then the bridge into more detail naming the first topic, then the area's question.
    assert "something specific they just told you" in nxt and "curiosity" in nxt
    assert "never praise, never repeat back what they said" in nxt
    assert "goes deeper into their work and the topics this role involves" in nxt and "name the first topic" in nxt
    assert nxt.index("1.") < nxt.index("2.") < nxt.index("3. This area's first question")
    assert "Never word it as the interview beginning or starting" in nxt
    for n in range(2, len(BP.competency_plans) + 1):
        later, _ = await call(c, service, "move_on", reply=f"r{n}", spoke=False)
        assert later["moved"] is True and not BEGIN.search(later["next"])
        assert "transition" in later["next"] and "starting over" in later["next"]


def test_the_backstop_out_of_the_opening_bridges_the_same_way():
    note = " ".join(rc.NOTE_BACKSTOP_FROM_OPENING.split())
    assert "goes deeper into their work and the topics this role involves" in note
    assert "Never word it as the interview beginning or starting" in note and not BEGIN.search(note)


# -- 2. the opening is not rushed (A-114) ---------------------------------------------------------

def _booked(minutes):
    plan = BP.model_copy(deep=True)
    plan.evaluation_spec.duration_minutes = minutes  # as the booking does, before sizing
    return size_to_booking(plan, minutes)


PLAN20 = _booked(20)


def opening_coordinator(seconds_in: float, answers: int):
    c, service, _ = make(bp=PLAN20)
    c._started_at = time.monotonic() - seconds_in
    c._candidate_turn_times = [90.0 * i for i in range(answers)]  # a long measured exchange
    return c, service


@pytest.mark.parametrize("seconds_in,answers,sent", [
    (20, 2, False),   # Karandeep's interview: the note came at about 20 s
    (59, 2, False),
    (61, 1, False),   # a minute in, but only one answer
    (61, 2, True),
])
async def test_the_opening_note_waits_a_minute_and_two_answers(seconds_in, answers, sent):
    c, service = opening_coordinator(seconds_in, answers)
    assert c.allowance(1, 0.0)[0] * 60 < 30, "on a 20-minute booking the allowance alone would send it early"
    await c.check_time_now(wait=True)
    notes = [n for n in service.notes if "Time in this area (the opening) is nearly used" in n]
    assert bool(notes) is sent, (seconds_in, answers, service.notes)


def test_her_opening_rule_acknowledges_their_answer_before_moving_on():
    text = build_area_instruction(BP, first_name="Karan", time_of_day="evening", area=1)
    rule = " ".join(text[text.index("YOUR OPENING"):text.index("HOW YOU SOUND")].split())
    # A short, genuine reaction (or one natural follow-up) to what they actually said;
    # never a restatement or praise (rounds 19-20; Track H 2026-11-20: any opening angle).
    assert "respond to what they actually said" in rule and "shows curiosity" in rule
    assert "Never repeat back what they said, and never praise it" in rule
    assert rule.index("respond to what they actually said") < rule.index("Then call move_on"), "react, then move on"
    # The kind of reaction is described; no example sentence she could copy.
    reaction = rule[rule.index("respond to what they actually said"):rule.index("Then call move_on")]
    assert '"' not in reaction and "\u201c" not in reaction, "no quoted example in the reaction rule"


def test_the_move_results_quote_nothing():
    """Described, never quoted: nothing she could copy as a line (2026-11-20)."""
    for text in (rc.MOVE_ON_FIRST, rc.MOVE_ON_NEXT, rc.NOTE_BACKSTOP_FROM_OPENING):
        assert not re.findall(r'"([^"]+)"', text), text


# -- 3. the auditor (A-112, A-115) ---------------------------------------------------------------

async def test_a_competency_area_turn_is_never_labelled_opening():
    """'Let's begin. <the area's first question>' read by the model as opening."""
    turn = "Let's begin. " + Q["Q2.1"]
    a, live, sent = await audit_run([(2, "mitra", turn)], {turn: {"label": "opening"}})
    assert a.labels[0]["label"] == "plan" and a.labels[0]["question_id"] == "Q2.1"
    assert [(s.label.value, s.question_id) for s in sent] == [("plan", "Q2.1")]


async def test_an_opening_label_with_nothing_like_a_question_here_becomes_off_plan_not_opening():
    turn = "Let's begin. What is your favourite colour?"
    a, _, sent = await audit_run([(2, "mitra", turn)], {turn: {"label": "opening"}})
    assert a.labels[0]["label"] == "off_plan" and [s.label.value for s in sent] == ["off_plan"]


async def test_the_opening_area_still_labels_opening():
    turn = "Good evening, Karan. How are you doing?"
    a, _, _ = await audit_run([(1, "mitra", turn)], {turn: {"label": "opening"}})
    assert a.labels[0]["label"] == "opening"


# -- 4. follow-ups past the cap reach the live record (A-115) -------------------------------------

async def test_a_fourth_and_fifth_follow_up_are_sent_live():
    script = [(2, "mitra", Q["Q2.1"])] + [(2, "mitra", f"And then what happened, part {i}?") for i in range(5)]
    readings = {Q["Q2.1"]: {"label": "plan", "question_id": "Q2.1"},
                **{f"And then what happened, part {i}?": {"label": "follow_up", "question_id": "Q2.1"} for i in range(5)}}
    a, live, sent = await audit_run(script, readings)
    assert [s.follow_up_number for s in sent if s.label.value == "follow_up"] == [1, 2, 3, 4, 5]
    assert live.invalid == 0


# -- 5. a late answer files under its question's area (A-116) -------------------------------------

class _Emitter:
    def __init__(self):
        self.events = []

    async def start(self):
        return None

    def emit(self, event):
        self.events.append(event)
        return True

    async def close(self):
        return {}


async def test_an_answer_that_arrives_after_the_move_is_filed_under_the_area_it_answers():
    from bot.live_events import LiveEvents

    live = LiveEvents("int_0123456789ab", clock_zero=time.time() - 300, blueprint=BP, emitter=_Emitter())
    await live.start()
    area = {"n": 2}
    live.area_now = lambda: area["n"]
    t0 = time.time()
    live.turn(speaker="interviewer", text=Q["Q2.1"], start_wall=t0 - 12, end_wall=t0 - 8)
    started = t0 - 6                       # he starts answering in area 2
    live.area_changed(3)                   # she moves on before his transcript arrives
    area["n"] = 3
    live.turn(speaker="candidate", text="I don't know about this. Sorry.", start_wall=started, end_wall=t0 - 1)
    live.turn(speaker="interviewer", text="How did you assess live performance?", start_wall=time.time(),
              end_wall=time.time() + 3)
    live.turn(speaker="candidate", text="We watched error rates.", start_wall=time.time() + 4, end_wall=time.time() + 6)
    turns = [e.payload for e in live._emitter.events if e.payload.kind == "transcript_turn"]
    names = {1: "opening", 2: BP.competency_plans[0].competency_id, 3: BP.competency_plans[1].competency_id}
    assert [t.area_id for t in turns] == [names[2], names[2], names[3], names[3]]
