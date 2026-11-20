"""Track H, 2026-11-20: a more natural MITRA, on the same goals.

Live tests found every interview opening the same way ("how are you", then "what
are you working on now") and questions that followed the plan rather than the
answers. The opening is now chosen per interview, in code; questions build on what
was said; the goals, and their coverage, are unchanged."""

import random

from bot import realtime_coordinator as rc
from bot.blueprint_source import load_blueprint
from bot.realtime_prompt import (
    INVITES,
    OPENING_ANGLES,
    Opening,
    area_count,
    build_area_block,
    build_area_instruction,
    build_base_instruction,
    choose_opening,
)

BP = load_blueprint()


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_opening_is_chosen_per_interview_and_every_way_in_is_used():
    rng = random.Random(7)
    seen = {choose_opening(rng) for _ in range(200)}
    assert {o.angle for o in seen} == set(OPENING_ANGLES)
    assert {o.invite for o in seen} == set(INVITES)


def test_two_openings_give_two_different_first_turns():
    a = build_base_instruction(BP, first_name="Asha", time_of_day="morning", opening=Opening(OPENING_ANGLES[0], INVITES[0]))
    b = build_base_instruction(BP, first_name="Asha", time_of_day="morning", opening=Opening(OPENING_ANGLES[1], INVITES[1]))
    assert a != b
    assert "ask briefly how they are doing" in _flat(a)
    assert "check that they can hear you clearly" in _flat(b) and "their most recent move" in _flat(b)


def test_every_first_turn_waits_for_their_reply_before_any_question():
    """2026-11-20: half the first turns greeted and asked an interview question in the
    same breath, so the candidate never got to say hello."""
    for invite in INVITES:
        text = _flat(build_base_instruction(BP, first_name="Asha", time_of_day="morning",
                                            opening=Opening(OPENING_ANGLES[0], invite)))
        assert f"Then {invite}, in a few words of your own, and stop there" in text
        assert "your first turn has no interview question in it. Wait for their reply." in text
        assert "in the same turn" not in text


def test_the_opening_names_a_real_cv_detail_and_never_invents_one():
    text = _flat(build_base_instruction(BP, first_name="Asha", time_of_day=None, opening=Opening(OPENING_ANGLES[2])))
    assert "name one real detail from it" in text and "Only mention what is written there" in text
    assert 'Do not ask "what are you working on now" unless that is the angle' in text


def test_an_interviews_opening_never_changes_between_areas():
    """The base is cached by the model: it must stay byte-identical when the area changes."""
    opening = Opening(OPENING_ANGLES[3], INVITES[2])
    base = build_base_instruction(BP, first_name="Asha", time_of_day="evening", opening=opening)
    for area in range(1, area_count(BP) + 1):
        full = build_area_instruction(BP, first_name="Asha", time_of_day="evening", area=area, opening=opening)
        assert full == base + build_area_block(BP, area)


def test_questions_build_on_the_answer_and_cover_the_same_goals():
    text = _flat(build_base_instruction(BP, first_name="Asha", time_of_day=None))
    # Follow-ups come from what an answer opens up, not only from thin answers.
    assert "follow it up before you move on, even when the answer was good" in text
    assert "Use their own words in the follow-up" in text
    # The next goal is the one that connects, not the next on the list; covered goals are skipped.
    assert ("Choose, among this area's goals, the one that connects best to what they have said in their "
            "answers, not the next one on the list") in text
    assert "Skip a goal their answers have already covered" in text
    # Still plan-bound: three kinds of question and no fourth, at most 3 follow-ups.
    assert "there is no fourth kind" in text
    # Follow-ups: usually one or two, never more than three, then back to the plan.
    assert "Usually one or two follow-ups on a plan goal are enough, never more than 3" in text
    assert "carry on with the plan's next goal" in text
    block = _flat(build_area_block(BP, 2))
    assert "Cover them all, in whatever order the conversation makes natural" in block


def test_the_time_note_asks_her_to_finish_the_thread():
    note = rc.NOTE_AREA_SOON.format(area="Prioritisation", remaining="")
    assert "finish the thread you are on" in note and "at most one more question" in note


def test_the_greeting_varies_but_always_says_she_is_mitra_an_ai_interviewer():
    from bot.realtime_prompt import GREETING_STYLES

    rng = random.Random(1)
    assert {choose_opening(rng).greeting for _ in range(200)} == set(GREETING_STYLES)
    for style in GREETING_STYLES:
        text = _flat(build_base_instruction(BP, first_name="Asha", time_of_day="morning",
                                            opening=Opening(OPENING_ANGLES[0], INVITES[0], style)))
        assert f"Make the greeting {style}" in text
        assert "All three are required in every first turn" in text and "say that you are Mitra, an AI interviewer" in text


def test_the_greeting_never_says_there_is_no_rush():
    from bot.realtime_prompt import GREETING_STYLES

    assert not any("rush" in style for style in GREETING_STYLES)


def test_turn_detection_waits_less_for_the_end_of_an_answer():
    from bot.services.realtime import DEFAULT_EAGERNESS

    assert DEFAULT_EAGERNESS == "medium"


# -- the candidate does not lead (Track H, 2026-11-20) -------------------------------------------

def test_she_leads_and_a_topic_request_is_thanked_not_followed():
    text = _flat(build_base_instruction(BP, first_name="Asha", time_of_day=None))
    assert "YOU LEAD THE INTERVIEW" in text
    # The first offer: acknowledged (never a bare "okay"), not followed.
    assert "The first time a topic is offered, do not follow it" in text
    assert 'a bare "okay" is not enough' in text
    # Offered again and relevant to the role: one or two questions, then the plan.
    assert "The second time or more that the same topic is offered, and it is relevant to this role" in text
    assert "A topic that is not relevant to this role is never followed" in text
    assert "Never go back to a question or an area that is finished because they asked" in text
    # An experience mentioned inside an answer is still an answer.
    assert "This is about requests, not answers" in text
    assert "never a topic they asked you for" in text


def test_an_answer_saying_they_do_not_know_is_recognised():
    from bot.realtime_coordinator import said_dont_know

    for said in ("I don't know.", "I dont know about it", "I don't know about AWS, ask me questions about Google Cloud.",
                 "No idea, sorry.", "Honestly I'm not sure about that one."):
        assert said_dont_know(said), said
    long_answer = ("I don't know if this is what you mean, but we measured retrieval with a held-out set of two "
                   "hundred questions, compared recall at five before and after the change, and saw it rise.")
    for said in ("We used Kafka for that.", long_answer):
        assert not said_dont_know(said), said


async def _area_two():
    from tests.test_one_voice import call, make

    c, service, _ = make(area_instruction=lambda a: f"area {a}")
    await call(c, service, "move_on", reply="r0", spoke=False)
    assert c.current_area == 2
    return c, service


async def test_two_dont_knows_in_a_row_move_the_interview_on_before_she_replies():
    import asyncio

    from tests.test_one_voice import candidate, mitra

    c, service = await _area_two()
    await candidate(c, "I don't know about it.")
    await mitra(c, service, "r1", "That's completely fine. How did you measure it?")
    await candidate(c, "I don't know. Ask me questions about AWS.")
    for _ in range(40):
        if c.current_area == 3:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)
    assert c.current_area == 3
    note = next(n for n in service.notes if "don't know twice in a row" in n)
    assert "Say kindly that it is completely fine" in note and "do not call move_on for it" in note


async def test_an_answer_in_between_starts_the_count_again():
    import asyncio

    from tests.test_one_voice import candidate, mitra

    c, service = await _area_two()
    await candidate(c, "I don't know.")
    await mitra(c, service, "r1", "That's fine. What did you build?")
    await candidate(c, "The chunking and the retrieval for a logistics assistant.")
    await mitra(c, service, "r2", "How did you measure it?")
    await candidate(c, "I don't know.")
    await asyncio.sleep(0.2)
    assert c.current_area == 2 and not any("don't know twice" in n for n in service.notes)


# -- a topic offered again (Track H, 2026-11-20) -------------------------------------------------

def test_a_topic_the_candidate_offers_is_recognised():
    from bot.realtime_coordinator import offered_topic

    assert offered_topic("I don't know about it. I know about LLMs.") == "llms"
    assert offered_topic("I don't know about AWS, ask me questions about Google Cloud.") == "google cloud"
    assert offered_topic("I have worked a lot with Kubernetes, so") == "kubernetes"
    for not_an_offer in ("We used Kafka for that.", "I know.", "I know it well", "I don't know."):
        assert offered_topic(not_an_offer) is None, not_an_offer


async def test_a_topic_offered_the_second_time_is_followed_once_and_the_first_time_is_not():
    import asyncio

    from tests.test_one_voice import candidate, mitra

    c, service = await _area_two()
    await candidate(c, "I don't know about it. I know about LLMs.")
    await asyncio.sleep(0.1)
    assert not any("offered" in n for n in service.notes)  # once: acknowledged by her, not followed
    await mitra(c, service, "r1", "Thanks for letting me know. How did you measure it?")
    await candidate(c, "I'm not sure. But I really know LLMs well.")
    for _ in range(40):
        if any("offered llms more than once" in n for n in service.notes):
            break
        await asyncio.sleep(0.05)
    note = next(n for n in service.notes if "offered llms more than once" in n)
    assert "make your very next question about llms itself" in note and "If it is not relevant to this role" in note
    # Following the topic is not two "I don't know"s in a row: the area does not move.
    assert c.current_area == 2
    # A third offer of the same topic is not followed again.
    await mitra(c, service, "r2", "With LLMs, what did you build?")
    await candidate(c, "I know about LLMs, ask me more about LLMs.")
    await asyncio.sleep(0.2)
    assert sum("offered llms more than once" in n for n in service.notes) == 1


# -- the plan as MITRA's preparation (Track H, 2026-11-20) ----------------------------------


def _prepared(bp=BP):
    plans = [p.model_copy(update={"key_ideas": ["Retrieval finds text; the model answers from it"],
                                  "level_signals": "Newer: names the tool. Experienced: says why it was chosen.",
                                  "analytical_angles": ["How they would tell answers had got worse"]})
             for p in bp.competency_plans]
    return bp.model_copy(update={"competency_plans": plans,
                                 "opening_goals": ["Learn what kinds of AI work they have done so far",
                                                   "Learn which tools they use day to day"]})


def test_a_prepared_plan_opens_with_its_own_goals_and_is_not_scored():
    text = _flat(build_base_instruction(_prepared(), first_name="Asha", time_of_day=None))
    assert "YOUR OPENING: GETTING TO KNOW THEIR WORK" in text
    assert "- Learn what kinds of AI work they have done so far - Learn which tools they use day to day" in text
    assert "It is not scored" in text and "the plan's areas come later" in text


def test_an_older_plan_without_goals_opens_as_before():
    text = _flat(build_base_instruction(BP, first_name="Asha", time_of_day=None))
    assert "GETTING TO KNOW THEIR WORK" not in text and "Keep the opening to about two short exchanges" in text


def test_each_area_carries_its_briefing_for_her_thinking_never_to_recite():
    block = _flat(build_area_block(_prepared(), 2))
    assert "How answers sound at different levels: Newer: names the tool." in block
    assert "never recite, teach or correct them aloud" in block
    assert "- Retrieval finds text; the model answers from it" in block
    assert "Light analytical questions you may ask" in block and "How they would tell answers had got worse" in block
    assert "Pitch it to what you learned in the opening" in block
    # Before the numbered goals, which she still covers.
    assert block.index("How answers sound") < block.index("Numbered goals")
    assert "Cover them all" in block


def test_an_older_plan_has_no_briefing_in_its_area_block():
    block = _flat(build_area_block(BP, 2))
    assert "How answers sound" not in block and "Ideas a good answer" not in block


def test_generation_reads_the_briefing_and_the_opening_goals():
    from blueprint.generate import build_blueprint

    spec = BP.evaluation_spec
    payload = {"suggested_opening": "Hello.", "opening_goals": ["Learn what they build", " "],
               "competency_plans": [
                   {"competency_id": c.id, "name": c.name, "target_depth": "basic", "emphasis": 1.0,
                    "seed_questions": ["What kind of work have you done here?"],
                    "key_ideas": ["An idea"], "level_signals": "Newer vs experienced.",
                    "analytical_angles": ["An angle"]} for c in spec.competencies]}
    built = build_blueprint(blueprint_id="b", spec=spec, payload=payload)
    assert built.opening_goals == ["Learn what they build"]
    plan = built.competency_plans[0]
    assert (plan.key_ideas, plan.level_signals, plan.analytical_angles) == (["An idea"], "Newer vs experienced.", ["An angle"])
    from blueprint.generate import opening_minutes_for

    assert built.opening_minutes == opening_minutes_for(spec.duration_minutes)


def test_a_refinement_keeps_the_preparation_and_the_opening_length():
    from blueprint.refine import _carry_preparation

    old = _prepared()
    new = BP.model_copy(deep=True)
    _carry_preparation(old, new)
    assert new.opening_goals == old.opening_goals
    assert all(p.key_ideas and p.level_signals and p.analytical_angles for p in new.competency_plans)


def test_every_first_turn_says_the_interview_is_live_proctored_calmly():
    """2026-11-20 (product owner): she tells them at the start, in her own words; never a
    warning and never the list of what is checked."""
    for invite in INVITES:
        text = _flat(build_base_instruction(BP, first_name="Asha", time_of_day="morning",
                                            opening=Opening(OPENING_ANGLES[0], invite)))
        assert "tell them that this interview is live proctored" in text
        assert "never as a warning, and never list what is checked" in text
        # Said before the hand-over line, which ends the turn.
        assert text.index("live proctored") < text.index(f"Then {invite}")
