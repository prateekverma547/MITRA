"""Round 13 (B-90 A1, A2, and the per-area claims and contradictions). Deterministic: a
stand-in auditor model; every label goes through LiveEvents, which validates against the
contract, so a refused event shows as `invalid`."""

import json
from pathlib import Path
from types import SimpleNamespace

from bot.auditor import RealtimeAuditor
from bot.live_events import LiveEvents
from feedback.run import _contradictions
from feedback.score import build_report
from shared.contracts import InterviewBlueprint, SectionOutcome, Speaker, Transcript, TranscriptTurn
from shared.interview_contracts import InterviewEvent

BP = InterviewBlueprint.model_validate(json.loads(
    (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text()))
Q = {qid: text for p in BP.competency_plans for qid, text in p.numbered_questions()}


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


def model(readings_by_text):
    async def request(**kw):
        text = kw["messages"][1]["content"].split("INTERVIEWER TURN\n  ", 1)[1]
        reading = {"label": "off_plan", "question_id": None, "new_claims": [], "contradictions": [],
                   **readings_by_text.get(text, {})}
        return SimpleNamespace(payload={"coverage": "partial", **reading}, record=SimpleNamespace(cost_usd=0.0))
    return request


async def run(script, readings):
    live = LiveEvents("int_0123456789ab", clock_zero=0.0, blueprint=BP, emitter=Emitter())
    await live.start()
    where = {"n": 2}
    a = RealtimeAuditor(blueprint=BP, area_now=lambda: where["n"], live=live, request=model(readings))
    a.start()
    for i, (area, who, text) in enumerate(script):
        where["n"] = area
        if who == "decline":
            a.note_decline(area)
        elif who == "mitra":
            await a.label_now(i, text)
        else:
            a.candidate_turn(i, text)
    await a.finish(timeout=5)
    sent = [e.payload for e in live._emitter.events if e.payload.kind == "question_labelled"]
    return a, live, sent


async def test_a_follow_up_with_no_thread_and_no_id_gets_its_id_from_its_own_words():
    """No question is being discussed yet (no thread), the model gives no id: the
    whole plan's bank on the turn's own words supplies it, and the event is valid."""
    fu = Q["Q2.2"].replace("What did", "So, what did")
    a, live, sent = await run([(2, "mitra", fu)], {fu: {"label": "follow_up", "question_id": None}})
    assert a._thread is None
    assert a.labels[0]["question_id"] == "Q2.2" and a.labels[0]["follow_up_number"] == 1
    assert live.invalid == 0 and [(s.label.value, s.question_id) for s in sent] == [("follow_up", "Q2.2")]


async def test_a_follow_up_on_an_off_plan_question_is_unknown_kept_and_not_sent():
    off, fu = "How do you spend a quiet Friday?", "What did that cost you?"
    a, live, sent = await run([(2, "mitra", Q["Q2.1"]), (2, "cand", "x"), (2, "mitra", off), (2, "cand", "y"),
                               (2, "mitra", fu)],
                              {Q["Q2.1"]: {"label": "plan", "question_id": "Q2.1"},
                               fu: {"label": "follow_up", "question_id": None}})
    assert a.labels[4]["label"] == "follow_up" and a.labels[4]["question_id"] is None
    assert a.labels[4]["question_known"] is False
    assert live.invalid == 0, "nothing the contract refuses is ever sent (B-90 A1)"
    assert [s.label.value for s in sent] == ["plan", "off_plan"]
    assert a.stats["not_sent_unknown_id"] == 1


async def test_an_invented_id_is_dropped_and_an_easier_variant_points_at_the_declined_question():
    easier = "Okay. What is one smaller strategy call you made?"
    a, live, sent = await run([(2, "mitra", Q["Q2.1"]), (2, "cand", "I'd rather not answer that."),
                               (2, "decline", None), (2, "mitra", easier)],
                              {Q["Q2.1"]: {"label": "plan", "question_id": "Q2.1"},
                               easier: {"label": "easier_variant", "question_id": "Q0"}})
    assert a.labels[3]["question_id"] == "Q2.1", "the declined question's real id, not Q0"
    assert sent[-1].label.value == "easier_variant" and sent[-1].question_id == "Q2.1"
    assert a.stats["invented_ids_dropped"] == 1 and live.invalid == 0


async def test_an_easier_variant_of_an_opening_question_is_unknown_and_not_sent():
    first, easier = "What are you working on these days?", "Okay. Broadly, what kind of team are you on?"
    a, live, sent = await run([(1, "mitra", first), (1, "cand", "I'd rather not answer that."),
                               (1, "decline", None), (1, "mitra", easier)],
                              {first: {"label": "opening"}, easier: {"label": "easier_variant", "question_id": "opening"}})
    assert a.labels[3]["question_id"] is None and a.labels[3]["question_known"] is False
    assert all(s.question_id != "Qopening" for s in sent) and live.invalid == 0


async def test_an_invented_plan_id_becomes_off_plan():
    q = "What is your favourite framework?"
    a, live, sent = await run([(2, "mitra", q)], {q: {"label": "plan", "question_id": "Q9.9"}})
    assert a.labels[0]["label"] == "off_plan" and a.labels[0]["question_id"] is None


async def test_key_claims_and_contradictions_reach_the_section_outcome_and_the_report():
    q2 = "When did it go live?"
    a, live, sent = await run([
        (2, "mitra", Q["Q2.1"]), (2, "cand", "We shipped the routing layer in March."),
        (2, "mitra", q2), (2, "cand", "It only went live in June, after the audit."),
        (2, "mitra", Q["Q2.2"])],
        {Q["Q2.1"]: {"label": "plan", "question_id": "Q2.1"},
         q2: {"label": "follow_up", "question_id": "Q2.1",
              "new_claims": [{"claim": "shipped the routing layer in March",
                              "quote": "We shipped the routing layer in March."}]},
         Q["Q2.2"]: {"label": "plan", "question_id": "Q2.2",
                     "contradictions": [{"earlier_claim": "shipped the routing layer in March",
                                         "later_statement": "It only went live in June"}]}})
    [ps] = [o for o in a.section_outcomes() if o["section_id"] == "product_strategy"]
    outcome = SectionOutcome.model_validate({k: v for k, v in ps.items() if k != "auditor"})
    assert [(c.text, c.turn_index) for c in outcome.key_claims] == [("shipped the routing layer in March", 1)]
    assert [(c.earlier_claim, c.earlier_section_id, c.later_statement, c.turn_index) for c in outcome.contradictions] == [
        ("shipped the routing layer in March", "product_strategy", "It only went live in June", 3)]
    t = Transcript(interview_id="int_1", duration_seconds=1.0, turns=[
        TranscriptTurn(index=0, speaker=Speaker.CANDIDATE, text="x", at_seconds=0.0)])
    report = build_report(interview_id="int_1", blueprint_id="bp", spec=BP.evaluation_spec, transcript=t,
                          payload={"competency_scores": [], "summary": "s", "recommendation": "limited_evidence"},
                          contradictions=_contradictions([ps]), section_outcomes=[ps])
    assert [c.later_statement for c in report.contradictions] == ["It only went live in June"]


async def test_a_contradiction_whose_words_were_not_said_is_never_recorded():
    a, live, sent = await run([(2, "mitra", Q["Q2.1"]), (2, "cand", "We shipped in March."), (2, "mitra", Q["Q2.2"])],
                              {Q["Q2.1"]: {"label": "plan", "question_id": "Q2.1"},
                               Q["Q2.2"]: {"label": "plan", "question_id": "Q2.2",
                                           "contradictions": [{"earlier_claim": "x", "later_statement": "we never shipped"}]}})
    assert a.contradictions == [] and all(not o["contradictions"] for o in a.section_outcomes())
