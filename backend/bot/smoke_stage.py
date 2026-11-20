"""Session A's smoke stages (found by name by scripts/smoke_interview.py, C-110).

Free and deterministic: the real bot code, with stand-ins only at its injection points
(the auditor's text-model request). No paid call, no Daily room.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts.smoke_interview import stage

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pm_senior.json"


class _ListEmitter:
    def __init__(self):
        self.events = []

    async def start(self):
        pass

    def emit(self, event):
        self.events.append(event)
        return True

    async def close(self):
        return {}


def _stand_in_model(readings_by_text: dict):
    async def request(**kw):
        text = kw["messages"][1]["content"].split("INTERVIEWER TURN\n  ", 1)[1]
        reading = {"label": "off_plan", "question_id": None, "new_claims": [], "contradictions": [],
                   **readings_by_text.get(text, {})}
        return SimpleNamespace(payload={"coverage": "partial", **reading}, record=SimpleNamespace(cost_usd=0.0))
    return request


@stage("a_auditor_labels", owner="A")
def auditor_labels(ctx):
    """Round 13 (B-90 A1, A2): no label the contract refuses is ever sent; an invented id
    is dropped; an easier variant points at the declined question; key claims and
    contradictions reach the section outcome. Also B-70: an unchanged "14B" turn passes."""
    from bot.auditor import RealtimeAuditor
    from bot.live_events import LiveEvents
    from feedback.clean import check_turn
    from shared.contracts import InterviewBlueprint, SectionOutcome

    bp = InterviewBlueprint.model_validate(json.loads(_FIXTURE.read_text()))
    q = {qid: text for p in bp.competency_plans for qid, text in p.numbered_questions()}
    easier, off, fu = "Okay. What is one smaller strategy call you made?", "How do you spend a Friday?", "Why?"
    readings = {
        q["Q2.1"]: {"label": "plan", "question_id": "Q2.1",
                    "new_claims": []},
        easier: {"label": "easier_variant", "question_id": "Q0"},
        # A reading's claims come from the candidate's answer BEFORE that Mitra turn.
        off: {"label": "off_plan",
              "new_claims": [{"claim": "shipped routing in March", "quote": "we shipped routing in March"}]},
        fu: {"label": "follow_up", "question_id": None,
             "contradictions": [{"earlier_claim": "shipped routing in March", "later_statement": "it went live in June"}]},
    }

    async def run():
        emitter = _ListEmitter()
        live = LiveEvents(ctx.iid, clock_zero=0.0, blueprint=bp, emitter=emitter)
        await live.start()
        a = RealtimeAuditor(blueprint=bp, area_now=lambda: 2, live=live, request=_stand_in_model(readings))
        a.start()
        script = [("m", q["Q2.1"]), ("c", "I'd rather not answer that."), ("d", None), ("m", easier),
                  ("c", "Fine: we shipped routing in March."), ("m", off), ("c", "Honestly it went live in June."),
                  ("m", fu)]
        for i, (who, text) in enumerate(script):
            if who == "m":
                await a.label_now(i, text)
            elif who == "c":
                a.candidate_turn(i, text)
            else:
                a.note_decline(2)
        await a.finish(timeout=5)
        return a, live, emitter

    a, live, emitter = ctx.call(run)
    sent = [e.payload for e in emitter.events if e.payload.kind == "question_labelled"]
    ctx.expect(live.invalid == 0, f"an event the contract refuses was sent ({live.invalid})")
    ctx.expect(all(s.question_id != "Q0" for s in sent), "an invented id (Q0) went out")
    ctx.expect(a.labels[3]["question_id"] == "Q2.1", "the easier variant does not point at the declined Q2.1")
    ctx.expect(a.labels[7].get("question_known") is False and a.stats.get("not_sent_unknown_id") == 1,
               "a follow-up on an off-plan question was not kept as unknown and unsent")
    [ps] = [o for o in a.section_outcomes() if o["section_id"] == "product_strategy"]
    outcome = ctx.validate(SectionOutcome, {k: v for k, v in ps.items() if k != "auditor"}, "the section outcome")
    ctx.expect(len(outcome.key_claims) == 1 and len(outcome.contradictions) == 1,
               "key claims and contradictions did not reach the section outcome")
    raw = "We used the 14B model."
    ctx.expect(check_turn(raw, {"index": 1, "text": raw, "corrections": [], "uncertain": []}) == [],
               'an unchanged "14B" turn fails the cleaner\'s guard (B-70)')
    ctx.out["a_auditor_labels"] = {"sent": [s.model_dump(mode="json") for s in sent],
                                   "outcome": outcome.model_dump(mode="json")}


@stage("a_rejoin", owner="A")
def rejoin_sequence(ctx):
    """Phase 3 wave 1 rejoin, on a replayed event sequence: a silent cut seen only by the
    status readings (C-121), a reload (a new participant id), two more counted drops, and
    the return from a 4th counted drop ending the interview with the kind goodbye. The real
    coordinator, presence, controller and contract; a stand-in realtime service."""
    import asyncio

    from bot.live_events import LiveEvents
    from bot.presence import RoomPresence
    from bot.realtime_coordinator import RealtimeCoordinator

    # Round 14: code speaks no line; a private note tells Mitra, and she speaks.
    WELCOME_BACK, REJOIN_LIMIT_GOODBYE = "has just reconnected after a short drop", "has dropped several times"
    from bot.rejoin import RejoinController
    from shared.contracts import ConnectionRecord, InterviewBlueprint

    bp = InterviewBlueprint.model_validate(json.loads(_FIXTURE.read_text()))
    question = "What options did you weigh in the hardest prioritization call you have made?"

    class Service:
        def __init__(self):
            self.said, self.notes, self.paused, self.reply_text = [], [], [], {"r1": f"Thanks. {question}"}

        async def insert_note(self, text):
            self.notes.append(text)
            return f"n{len(self.notes)}"

        async def delete_note(self, item_id):
            pass

        async def request_reply(self):
            self.said.append(self.notes[-1])

        async def cancel_reply(self):
            pass

        def set_audio_input_paused(self, paused):
            self.paused.append(paused)

    class Worker:
        async def queue_frames(self, frames):
            pass

    mark = f"candidate:{ctx.iid}"

    async def run():
        service = Service()
        coord = RealtimeCoordinator(blueprint=bp, llm=service)
        coord.attach(Worker())
        emitter = _ListEmitter()
        live = LiveEvents(ctx.iid, clock_zero=0.0, blueprint=bp, emitter=emitter)
        await live.start()
        coord.attach_events(live)
        presence = RoomPresence(interview_over=lambda: coord.is_over, candidate_mark=mark)
        presence.observe_join({"id": "p-1", "info": {"userId": mark}})
        t = {"now": 60.0}
        rejoin = RejoinController(coordinator=coord, presence=presence, live=live, interview_id=ctx.iid,
                                  clock=lambda: t["now"], grace_seconds=2.0, settle_seconds=0.02)
        sig = SimpleNamespace
        # 1. A silent cut: Daily never notices; the readings do (dropped, then resumed).
        await rejoin.on_signal(sig(kind="dropped", at_seconds=t["now"], gap_seconds=None, seq=1))
        # Silenced at the drop: her input paused (the stand-in never starts a reply by
        # itself, so "nothing said" alone could not show it).
        silent_during = list(service.said) + ([] if service.paused[-1:] == [True] else ["(input not paused)"])
        t["now"] += 20
        await rejoin.on_signal(sig(kind="resumed", at_seconds=t["now"], gap_seconds=20.0, seq=2))
        await asyncio.sleep(0.08)
        # 2-4. A reload, then two more counted drops; the 4th counted drop's return ends it.
        current = "p-1"
        for n in range(2, 5):
            presence.observe_leave({"id": current})
            await rejoin.on_candidate_left(current)
            t["now"] += 15
            current = f"p-{n}"
            presence.observe_join({"id": current, "info": {"userId": mark}})
            await rejoin.on_candidate_joined(current)
            await asyncio.sleep(0.08)
        await rejoin.close()
        return coord, live, emitter, service, rejoin, silent_during

    coord, live, emitter, service, rejoin, silent_during = ctx.call(run)
    kinds = [e.payload.kind for e in emitter.events]
    ctx.expect(live.invalid == 0, f"a rejoin event the contract refuses ({live.invalid})")
    ctx.expect(silent_during == [], "Mitra spoke while the candidate was away")
    ctx.expect(sum(1 for s in service.said if WELCOME_BACK in s and question in s) == 3,
               "not three welcome-back notes carrying the last question")
    ctx.expect(REJOIN_LIMIT_GOODBYE in service.said[-1], "the 4th counted drop did not end with the kind goodbye")
    ctx.expect(coord.reason == "rejoin_limit_reached", f"ended as {coord.reason}")
    ctx.expect(kinds.count("candidate_returned") == 4 and kinds.count("rejoin_limit_reached") == 1,
               f"events: {kinds}")
    ctx.expect(kinds.count("candidate_dropped") == 3, "the bot must send Daily's drops only (the server sends readings')")
    record = ctx.validate(ConnectionRecord, rejoin.record(), "the connection record")
    ctx.expect(record.rejoin_count == 4 and record.limit_reached, "the record does not show the limit")
    ctx.out["a_rejoin"] = {"events": [e.payload.model_dump(mode="json") for e in emitter.events],
                           "connection": record.model_dump(mode="json")}


@stage("a_hr_refinement", owner="A")
def hr_refinement(ctx):
    """Phase 3 wave 1 HR findings (C-124), through the real refine_with_hr_findings with a
    stand-in for the model call (request=, C-132): 2 technical findings probed neutrally,
    1 not technical and 1 vague left out; the raw text and the word HR reach nothing
    Mitra reads; no findings leaves the plan unchanged byte for byte."""
    import re

    from blueprint.refine import refine_with_hr_findings
    from bot.realtime_prompt import area_count, build_area_block, build_area_instruction, build_base_instruction
    from shared.contracts import HRFindingsForTechnical, InterviewBlueprint

    bp = InterviewBlueprint.model_validate(json.loads(_FIXTURE.read_text()))
    texts = ["Unclear on how to size an A/B test or decide when it had run long enough.",
             "Described prioritisation only as stakeholder pressure, with no trade-off reasoning.",
             "Notice period is 90 days and expects a 30 percent raise.",
             "Seemed a bit off in places."]
    probes = {0: ("metrics", "How do you decide how many users an experiment needs, and when to stop it?"),
              1: ("prioritization", "Can you walk me through a trade-off where you said no to something valuable?")}

    async def request(**kw):
        out = [{"index": i, "outcome": "probed", "area_id": probes[i][0], "probe": probes[i][1]} if i in probes
               else {"index": i, "outcome": "not_technical"} for i in range(len(texts))]
        return SimpleNamespace(payload={"findings": out}, record=SimpleNamespace(to_dict=lambda: {"cost_usd": 0.0}))

    findings = HRFindingsForTechnical(candidate_id="cand_smoke", findings_for_technical=texts)
    refined = ctx.call(lambda: refine_with_hr_findings(bp, findings, request=request))
    unchanged = ctx.call(lambda: refine_with_hr_findings(
        bp, HRFindingsForTechnical(candidate_id="cand_smoke", findings_for_technical=[]), request=request))
    ctx.validate(InterviewBlueprint, refined.model_dump(mode="json"), "the refined plan")
    uses = refined.hr_findings.findings
    ctx.expect([u.outcome for u in uses] == ["probed", "probed", "not_technical", "not_technical"],
               f"outcomes: {[u.outcome for u in uses]}")
    who = {"first_name": "Asha", "time_of_day": "afternoon"}
    seen = "\n".join([build_base_instruction(refined, **who)] +
                     [build_area_instruction(refined, **who, area=a) for a in
                      range(1, area_count(refined) + 1)] +
                     [build_area_block(refined, a) for a in range(1, area_count(refined) + 1)])
    from bot.realtime_prompt import as_goal

    ctx.expect(all(as_goal(p) in seen for _, p in probes.values()),
               "the probes do not reach Mitra (the search's control)")
    ctx.expect(not any(t in seen for t in texts), "a finding's raw text reaches Mitra's prompt")
    ctx.expect(not re.search(r"\bHR\b|human resources", seen, re.IGNORECASE), "Mitra's prompt says HR")
    ctx.expect(unchanged.model_dump_json() == bp.model_dump_json(), "no findings changed the plan")
    ctx.out["a_hr_refinement"] = refined.hr_findings.model_dump(mode="json")


@stage("a_one_voice", owner="A")
def one_voice(ctx):
    """Round 14: one voice. The real coordinator over a stand-in service: a note never
    lands while the candidate speaks; withdrew needs she-asked-they-answered; completed
    needs the plan covered or time nearly up; code's endings ask for her goodbye by note
    and hang up after it; move_on and the backstop move one area at a time."""
    import asyncio
    import time

    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.observers.base_observer import FramePushed

    import bot.realtime_coordinator as rc
    from shared.contracts import InterviewBlueprint

    bp = InterviewBlueprint.model_validate(json.loads(_FIXTURE.read_text()))

    class Service:
        def __init__(self):
            self.notes, self.events, self.swaps = [], [], []
            self.reply_of_call, self.reply_spoke, self.reply_text = {}, {}, {}

        async def insert_note(self, text):
            self.notes.append(text)
            self.events.append("note")
            return f"n{len(self.notes)}"

        async def delete_note(self, item_id):
            pass

        async def request_reply(self):
            self.events.append("reply")

        async def cancel_reply(self):
            self.events.append("cancel")

        def set_audio_input_paused(self, paused):
            pass

        async def update_instructions(self, text):
            self.swaps.append(text)

    class Worker:
        def __init__(self):
            self.ended = 0

        async def queue_frames(self, frames):
            self.ended += 1

    async def push(c, frame):
        await c.on_push_frame(FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))

    async def call(c, s, tool, reply, spoke, **arguments):
        box = {}

        async def cb(result, properties=None):
            box["result"] = result

        s.reply_of_call[f"c_{reply}"], s.reply_spoke[reply] = reply, spoke
        await c.tool_handlers()[tool](SimpleNamespace(arguments=arguments, result_callback=cb,
                                                       tool_call_id=f"c_{reply}"))
        return box["result"]

    def make():
        s, w = Service(), Worker()
        c = rc.RealtimeCoordinator(blueprint=bp, llm=s, area_instruction=lambda a: f"area {a}")
        c.attach(w)
        c._started_at = time.monotonic()
        return c, s, w

    async def run():
        out = {}
        rc._REPLY_RECORD_WAIT_SECONDS, rc._ENDED_WHILE_SILENT_SECONDS = 0.1, 0.05
        # 1. a note waits for the candidate to stop speaking
        c, s, _ = make()
        await push(c, UserStartedSpeakingFrame())
        task = asyncio.create_task(c.tell("x", kind="welcome_back"))
        await asyncio.sleep(0.1)
        out["note_while_speaking"] = list(s.notes)
        await push(c, UserStoppedSpeakingFrame())
        await asyncio.wait_for(task, 1)
        out["note_after"] = list(s.events)
        # 2. withdrew: refused with no answer since her question; accepted after it
        c, s, w = make()
        await push(c, UserStartedSpeakingFrame())
        await push(c, LLMFullResponseStartFrame())
        s.reply_spoke["r1"] = True
        await push(c, LLMFullResponseEndFrame())
        out["withdrew_unanswered"] = await call(c, s, "end_interview", "r2", True, reason="withdrew")
        await push(c, UserStartedSpeakingFrame())
        await push(c, UserStoppedSpeakingFrame())
        # As on the real route: the reply carrying the call is still playing when the tool
        # is accepted, and its audio stops before the goodbye starts.
        await push(c, LLMFullResponseStartFrame())
        await push(c, BotStartedSpeakingFrame())
        out["withdrew_answered"] = await call(c, s, "end_interview", "r3", True, reason="withdrew")
        await push(c, LLMFullResponseEndFrame())
        await push(c, BotStoppedSpeakingFrame())
        out["withdrew_hung_up_on_the_call"] = w.ended
        # Round 16: the call ends only after the FOLLOWING reply, her goodbye, has played.
        await push(c, LLMFullResponseStartFrame())
        s.reply_spoke["r4"] = True
        await push(c, BotStartedSpeakingFrame())
        await push(c, LLMFullResponseEndFrame())
        await push(c, BotStoppedSpeakingFrame())
        out["withdrew_hung_up"] = w.ended
        # 3. completed: refused early, accepted in the closing
        c, s, _ = make()
        out["completed_early"] = await call(c, s, "end_interview", "r1", True, reason="completed")
        for i in range(len(bp.competency_plans) + 1):
            await call(c, s, "move_on", f"m{i}", False)
        out["completed_closing"] = await call(c, s, "end_interview", "r2", True, reason="completed")
        # 4. a code ending: the End button cancels, notes, asks for her reply; hangs up after
        c, s, w = make()
        await push(c, LLMFullResponseStartFrame())
        await c.end_by_candidate_button()
        out["end_button"] = list(s.events)
        await push(c, LLMFullResponseStartFrame())
        s.reply_spoke["eb"] = True
        await push(c, BotStartedSpeakingFrame())
        await push(c, LLMFullResponseEndFrame())
        await push(c, BotStoppedSpeakingFrame())
        out["end_button_hung_up"] = w.ended
        # 5. move_on and the backstop together: one move
        c, s, _ = make()
        await call(c, s, "move_on", "m0", False)
        c._area_log[-1]["entered_seconds"] = 0.0
        c._started_at = time.monotonic() - bp.competency_plans[0].time_budget_minutes * 2 * 60 - 1
        await c.check_time_now()
        c._reply_area = 2
        await asyncio.gather(call(c, s, "move_on", "m1", False), c._backstop_task)
        out["areas_after_race"] = [a["area"] for a in c.area_log()]
        return out

    out = ctx.call(run)
    ctx.expect(out["note_while_speaking"] == [], "a note was sent while the candidate spoke")
    ctx.expect(out["note_after"] == ["note", "reply"], f"note then reply expected: {out['note_after']}")
    ctx.expect(out["withdrew_unanswered"]["accepted"] is False, "withdrew accepted with no answer")
    ctx.expect(out["withdrew_answered"]["accepted"] is True and out["withdrew_hung_up"] == 1,
               "withdrew after asked-and-answered not accepted or not hung up after her goodbye")
    ctx.expect(out["withdrew_hung_up_on_the_call"] == 0, "hung up on the tool call itself (no goodbye)")
    ctx.expect("goodbye" in str(out["withdrew_answered"].get("next", "")).lower(),
               f"the accepted tool result does not ask for her goodbye: {out['withdrew_answered']}")
    ctx.expect(out["completed_early"]["accepted"] is False, "completed accepted before the plan was covered")
    ctx.expect(out["completed_closing"]["accepted"] is True, "completed refused in the closing")
    ctx.expect(out["end_button"] == ["cancel", "note", "reply"] and out["end_button_hung_up"] == 1,
               f"End button path: {out['end_button']}")
    ctx.expect(out["areas_after_race"] == [1, 2, 3], f"double area move: {out['areas_after_race']}")
    ctx.out["a_one_voice"] = {k: v for k, v in out.items() if k != "note_after"}
