"""A-63 (round 10): a skip, a decline or an easier variant of a question carried over from
another area is filed under THAT question's own area; when the question is not known, under
the current area, marked as such. Deterministic: a stand-in auditor model, no keys."""

import json
from pathlib import Path
from types import SimpleNamespace

from bot.auditor import RealtimeAuditor
from bot.live_events import LiveEvents
from bot.realtime_coordinator import RealtimeCoordinator
from feedback.score import build_report
from shared.contracts import InterviewBlueprint, Speaker, Transcript, TranscriptTurn
from shared.interview_contracts import InterviewEvent

BP = InterviewBlueprint.model_validate(json.loads(
    (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text()))
Q = {qid: text for p in BP.competency_plans for qid, text in p.numbered_questions()}


def model(readings_by_text):
    """The stand-in auditor model: a reading chosen by the interviewer turn's words."""
    async def request(**kw):
        text = kw["messages"][1]["content"].split("INTERVIEWER TURN\n  ", 1)[1]
        reading = readings_by_text.get(text, {"label": "off_plan", "question_id": None})
        return SimpleNamespace(payload={"coverage": "partial", "new_claims": [], "contradictions": [], **reading},
                               record=SimpleNamespace(cost_usd=0.0))
    return request


async def run(script, readings, area=3):
    """script: (speaker, text, event) tuples; event "skip" / "decline" after a candidate turn."""
    where = {"n": area}
    a = RealtimeAuditor(blueprint=BP, area_now=lambda: where["n"], request=model(readings))
    a.start()
    for i, (speaker, text, event) in enumerate(script):
        if speaker == "mitra":
            await a.label_now(i, text)
        else:
            a.candidate_turn(i, text)
            if event == "skip":
                a.note_skip(where["n"])
            elif event == "decline":
                a.note_decline(where["n"])
    summary = await a.finish(timeout=5)
    return a, summary, {o["section_id"]: o for o in a.section_outcomes()}


def gaps(outcomes):
    t = Transcript(interview_id="int_1", duration_seconds=1.0, turns=[
        TranscriptTurn(index=0, speaker=Speaker.CANDIDATE, text="x", at_seconds=0.0)])
    report = build_report(interview_id="int_1", blueprint_id="bp", spec=BP.evaluation_spec, transcript=t,
                          payload={"competency_scores": [], "summary": "s", "recommendation": "limited_evidence"},
                          section_outcomes=list(outcomes.values()))
    return [g for g in report.coverage_gaps if "skipped" in g or "declined" in g]


async def test_a_skip_of_a_carried_over_question_is_filed_under_its_own_area():
    a, summary, out = await run([("mitra", Q["Q2.2"], None), ("cand", "Can we skip this one?", "skip")],
                                {Q["Q2.2"]: {"label": "plan", "question_id": "Q2.2"}})
    assert summary["skips"][0] | {} == {"area": 2, "after_turn": 0, "question_id": "Q2.2", "spoken_in_area": 3,
                                         "area_source": "question"}
    assert out["product_strategy"]["skipped_question_ids"] == ["Q2.2"]
    assert not out.get("prioritization", {}).get("skipped_question_ids")
    assert gaps(out) == ["Product strategy and vision: the candidate skipped Q2.2."]


async def test_a_carried_over_question_the_model_mislabels_still_gets_its_own_id():
    # The model sees only the current area's questions: off-plan, or a current-area id.
    for wrong in ({"label": "off_plan", "question_id": None}, {"label": "plan", "question_id": "Q3.2"},
                  {"label": "follow_up", "question_id": "Q3.2"}, {"label": "opening", "question_id": None}):
        a, summary, out = await run([("mitra", Q["Q2.2"], None), ("cand", "Can we skip this one?", "skip")],
                                    {Q["Q2.2"]: wrong})
        assert a.labels[0]["question_id"] == "Q2.2" and a.labels[0]["label"] == "plan"
        assert out["product_strategy"]["skipped_question_ids"] == ["Q2.2"]


async def test_a_decline_of_a_carried_over_question_and_its_easier_variant_go_to_its_own_area():
    easier = "Okay. What is one small assumption that changed after you shipped?"
    a, summary, out = await run([("mitra", Q["Q2.3"], None), ("cand", "I'd rather not answer that.", "decline"),
                                 ("mitra", easier, None)],
                                {Q["Q2.3"]: {"label": "plan", "question_id": "Q3.2"},   # mislabelled
                                 easier: {"label": "easier_variant", "question_id": None}})
    assert summary["declines"][0]["area"] == 2 and summary["declines"][0]["question_id"] == "Q2.3"
    ps = out["product_strategy"]
    assert ps["declined_question_ids"] == ["Q2.3"] and ps["easier_variants"] == 1 and ps["declined_turns"] == 1
    assert not out.get("prioritization", {}).get("declined_question_ids")
    assert not out.get("prioritization", {}).get("easier_variants")


async def test_an_unknown_question_falls_back_to_the_current_area_and_is_marked():
    a, summary, out = await run([("mitra", "Tell me something about that.", None),
                                 ("cand", "Can we skip this one?", "skip")], {})
    assert summary["skips"][0]["area"] == 3 and summary["skips"][0]["area_source"] == "current_area"
    assert out["prioritization"]["skipped_question_ids"] == [None]
    assert out["prioritization"]["auditor"]["skips"] == [
        {"question_id": None, "area_source": "current_area", "spoken_in_area": 3}]
    assert gaps(out) == ["Prioritization and trade-offs: the candidate skipped a question (not identified; "
                         "filed under the area it happened in)."]


async def test_an_ordinary_skip_is_unchanged():
    a, summary, out = await run([("mitra", Q["Q3.1"], None), ("cand", "Next question, please.", "skip")],
                                {Q["Q3.1"]: {"label": "plan", "question_id": "Q3.1"}})
    assert summary["skips"][0]["area"] == 3 and summary["skips"][0]["area_source"] == "question"
    assert out["prioritization"]["skipped_question_ids"] == ["Q3.1"]
    assert gaps(out) == ["Prioritization and trade-offs: the candidate skipped Q3.1."]


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


async def test_the_live_skip_carries_the_question_area_when_known_else_the_current_one():
    live = LiveEvents("int_0123456789ab", clock_zero=0.0, blueprint=BP, emitter=Emitter())
    await live.start()
    coord = RealtimeCoordinator(blueprint=BP, llm=Spy())
    coord.attach_events(live)
    coord._area = 3
    a = RealtimeAuditor(blueprint=BP, area_now=lambda: coord.current_area, request=model({}))
    coord.attach_auditor(a)
    a.mitra_turn(0, Q["Q2.2"])                        # not labelled yet: the whole plan's bank finds it
    await coord._on_candidate_turn("Can we skip this one?")
    a.mitra_turn(2, "Tell me something about that.")  # nothing to find
    await coord._on_candidate_turn("Can we skip this one?")
    skips = [e.payload for e in live._emitter.events if e.payload.kind == "skip"]
    assert [(s.area_id, s.question_id) for s in skips] == [("product_strategy", "Q2.2"), ("prioritization", None)]


async def test_questions_asked_count_under_their_own_area():
    """Round 12: a carried-over Q2.2 asked in area 3 counts under product strategy; an
    ordinary Q3.1 stays under prioritization; an unknown question is filed where it was
    spoken, marked."""
    a, summary, out = await run([("mitra", Q["Q2.2"], None), ("cand", "We bet on routing.", None),
                                 ("mitra", Q["Q3.1"], None), ("cand", "Retries or a second acquirer.", None),
                                 ("mitra", "Tell me something about that.", None)],
                                {Q["Q2.2"]: {"label": "plan", "question_id": "Q2.2"},
                                 Q["Q3.1"]: {"label": "plan", "question_id": "Q3.1"}})
    assert out["product_strategy"]["questions_asked"] == ["Q2.2"]
    assert out["prioritization"]["questions_asked"] == ["Q3.1"]
    ps_labels = out["product_strategy"]["auditor"]["labels"]
    assert ps_labels == [{"label": "plan", "question_id": "Q2.2", "spoken_in_area": 3, "area_source": "question"}]
    assert {"label": "off_plan", "question_id": None, "spoken_in_area": 3, "area_source": "spoken"} in \
        out["prioritization"]["auditor"]["labels"]
    # What the scorer is told per section (the report's coverage input).
    from feedback.score import _render_sections

    lines = {line.split(" · ")[0]: line for line in _render_sections(list(out.values())).splitlines()}
    assert "asked=Q2.2" in next(v for k, v in lines.items() if "product_strategy" in k)
    assert "asked=Q3.1" in next(v for k, v in lines.items() if "prioritization" in k)
