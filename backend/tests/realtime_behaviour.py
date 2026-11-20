"""Behaviour harness for the realtime interviewer (DEV_PLAN.md section 8).

Real model, real money: every test here is marked `behavior` and skipped unless
RUN_BEHAVIOR_TESTS is set, so it never runs in the normal suite or in CI.

    RUN_BEHAVIOR_TESTS=1 uv run pytest tests/realtime_behaviour.py -m behavior -v

What is real:
  - the realtime model (gpt-realtime-2.1 by default), in TEXT mode, with the
    bot's own session settings (bot/services/realtime.realtime_session_dict)
    and tools (REALTIME_TOOLS);
  - the instruction: the plan-bound one (build_area_instruction with the area
    swaps). Task 18's "before", the legacy whole-plan prompt, was removed with
    its builder; it is reproducible from commit 0909232;
  - the coordinator: the real RealtimeCoordinator. Its clock is simulated
    (MINUTES_PER_TURN of interview time per candidate turn); when a boundary
    is crossed the harness calls the coordinator's own `_on_mark` (swap + time
    note), or only `enter_area` for the "swap, no note" variant. Its tool
    handlers, notes and swap deferral run unchanged against a bridge to the
    live session.
What is a stand-in:
  - the candidate: a text model (CANDIDATE_MODEL) playing a persona against
    the CV, with a fixed seed;
  - the labeller: a text model (LABELLER_MODEL, never a reasoning model) that
    labels each Mitra question after the run. It is a measurement tool, not
    the live auditor.

Usage records for every call (realtime and text) are kept, priced from the
API's own usage.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

BACKEND = Path(__file__).resolve().parents[1]
CANDIDATE_MODEL = "gpt-4.1-mini"
LABELLER_MODEL = "gpt-4.1-mini"
#: Simulated interview minutes per exchange (question plus a 3-5 sentence
#: spoken answer is about a minute). 14 x 1.25 = 17.5 minutes: on pm_senior the
#: opening plus areas 2-4 (boundaries at 2, 9.5 and 16.5 minutes). The closing
#: is not reached; that keeps a run to about $0.10 (task 18's $4 cap).
MINUTES_PER_TURN = 1.25
CANDIDATE_TURNS = 14
MAX_OUTPUT_TOKENS = 300

PERSONAS = {
    "cooperative": "You answer every question fully and honestly, with one concrete example, in 3-5 sentences.",
    "verbose": "You talk a lot: long answers of 7-10 sentences that wander into side stories before the point.",
    "brief_vague": "You give short, vague answers of one or two sentences, with no numbers and no specific examples.",
    "deflecting": "You often avoid the question: you talk about the team instead of yourself, or answer a different, easier question.",
    "off_topic": "Twice during the interview you try to steer the conversation elsewhere: ask about salary, or start talking about a hobby or the company's stock price. Otherwise answer normally.",
    "skip_request": "Once or twice you say you would rather skip the question (for example 'Can we skip this one?'), then answer the next question normally.",
    # Added after task 18. The cap under pressure (A-04: never approached).
    "intriguing": (
        "Your answers are intriguing but incomplete. Each answer mentions a striking result, a surprising "
        "twist or a conflict, but leaves out how it happened, the numbers or who was involved, as if inviting "
        "the interviewer to ask more. When asked a follow-up, reveal one more piece but open a new loose end."),
}
#: The candidate's seed depends on a persona's place in THIS order, fixed at task 18's six,
#: so adding a persona never changes an existing persona's seed (task 18 used sorted(PERSONAS)).
SEED_ORDER = ["brief_vague", "cooperative", "deflecting", "off_topic", "skip_request", "verbose", "intriguing"]
#: A skip that really declines (A-10: the skip_request persona answered anyway). Said by the
#: harness, word for word, instead of the candidate model's reply.
SKIP_LINE = "I'd rather skip this one, if that's okay."
#: Added to the candidate's instructions in closing runs, so the closing is tested with real questions.
CLOSING_QUESTIONS = (
    "If the interviewer asks whether you have any questions for them, ask two: one about the next steps or "
    "when you will hear back, and one about the team or the company.")


# -- the realtime session ------------------------------------------------------------------


@dataclass
class Ledger:
    rows: list = field(default_factory=list)

    def add(self, kind, model, usd, **extra):
        self.rows.append({"kind": kind, "model": model, "usd": usd, **extra})

    @property
    def usd(self) -> float:
        return sum(r["usd"] or 0 for r in self.rows)


def realtime_usd(usage: dict) -> float:
    from bot.run_realtime import PRICE_PER_MILLION as p

    i = usage.get("input_token_details") or {}
    o = usage.get("output_token_details") or {}
    cached = (i.get("cached_tokens_details") or {}).get("text_tokens", 0) or 0
    text_in = max((i.get("text_tokens", 0) or 0) - cached, 0)
    return (text_in * p["text_in"] + cached * p["text_in_cached"]
            + (o.get("text_tokens", 0) or 0) * p["text_out"]) / 1_000_000


class Bridge:
    """What the coordinator calls on MitraRealtimeService, sent to the live session."""

    def __init__(self, session):
        self.s = session
        # The service's record of each reply (bot/services/realtime.py), which the
        # coordinator's ending decision reads.
        self.reply_of_call, self.reply_spoke, self.reply_text = {}, {}, {}

    def set_audio_input_paused(self, paused):
        pass

    async def update_instructions(self, text):
        # The same minimal update MitraRealtimeService.update_instructions sends.
        await self.s.send({"type": "session.update", "session": {"type": "realtime", "instructions": text}})
        await self.s.wait("session.updated")
        self.s.log.append({"event": "instructions_swapped"})

    async def insert_note(self, text):
        ev = await self.s.add("system", text)
        self.s.log.append({"event": "note", "text": text})
        return ev["item"]["id"]

    async def delete_note(self, item_id):
        await self.s.send({"type": "conversation.item.delete", "item_id": item_id})

    async def say(self, instructions):
        await self.s.respond(instructions=instructions, label="say")


class Session:
    def __init__(self, ledger: Ledger, label: str):
        self.ledger, self.label = ledger, label
        self.log: list[dict] = []
        self.coordinator = None
        self.responses = 0
        self.sim_minutes = 0.0

    async def __aenter__(self):
        import websockets

        from bot.services.realtime import realtime_model

        self.ws = await websockets.connect(
            f"wss://api.openai.com/v1/realtime?model={realtime_model()}",
            additional_headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, max_size=None)
        await self.wait("session.created")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def send(self, event):
        await self.ws.send(json.dumps(event))

    async def wait(self, *types, timeout=60):
        while True:
            ev = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if ev["type"] == "error":
                raise RuntimeError(ev["error"])
            if ev["type"] in types:
                return ev

    async def add(self, role, text):
        kind = "output_text" if role == "assistant" else "input_text"
        await self.send({"type": "conversation.item.create",
                         "item": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}})
        return await self.wait("conversation.item.added", "conversation.item.created")

    async def respond(self, instructions=None, label="reply"):
        from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame

        response = {"output_modalities": ["text"], "max_output_tokens": MAX_OUTPUT_TOKENS}
        if instructions:
            response["instructions"] = instructions
        await self._frame(LLMFullResponseStartFrame())
        await self.send({"type": "response.create", "response": response})
        started = time.monotonic()
        self.responses += 1
        response_no = self.responses
        done = await self.wait("response.done")
        await self._frame(LLMFullResponseEndFrame())
        resp = done["response"]
        self.ledger.add("realtime", resp.get("model") or "gpt-realtime", realtime_usd(resp.get("usage") or {}),
                        label=self.label)
        text, calls = [], []
        for out in resp.get("output") or []:
            if out.get("type") == "function_call":
                calls.append(out)
            for c in out.get("content") or []:
                if c.get("text"):
                    text.append(c["text"])
        said = " ".join(text).strip()
        bridge = getattr(self.coordinator, "_llm", None)
        if bridge is not None and hasattr(bridge, "reply_spoke"):
            for call in calls:
                bridge.reply_of_call[call["call_id"]] = resp.get("id")
            bridge.reply_spoke[resp.get("id")] = any(o.get("type") == "message" for o in resp.get("output") or [])
            bridge.reply_text[resp.get("id")] = said
        if said:
            self.log.append({"speaker": "interviewer", "text": said, "label": label,
                             "latency_s": round(time.monotonic() - started, 2),
                             "response": response_no, "sim_minutes": self.sim_minutes})
        for call in calls:
            await self._tool(call, response_no)
        return said

    async def _frame(self, frame):
        from pipecat.observers.base_observer import FramePushed

        if self.coordinator is not None:
            await self.coordinator.on_push_frame(
                FramePushed(source=None, destination=None, frame=frame, direction=None, timestamp=0))

    async def _tool(self, call, response_no=None):
        box = {}

        async def cb(result, properties=None):
            # As in Pipecat: the result is added to the conversation first, and
            # only then may anything else (a say(), a follow-up reply) happen.
            box["properties"] = properties
            await self.send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}})
            await self.wait("conversation.item.added", "conversation.item.created")

        args = SimpleNamespace(arguments=json.loads(call.get("arguments") or "{}"), result_callback=cb,
                               tool_call_id=call.get("call_id"))  # as Pipecat's FunctionCallParams
        handler = {"end_interview": self.coordinator._handle_end_interview,
                   "finish_interview": self.coordinator._handle_finish_interview}.get(call["name"])
        self.log.append({"event": "tool", "name": call["name"], "arguments": call.get("arguments"),
                         "response": response_no, "sim_minutes": self.sim_minutes})
        if handler is None:
            return
        await handler(args)
        props = box.get("properties")
        if props is None or getattr(props, "run_llm", True) is not False:
            await self.respond(label="after_tool")


# -- one interview -------------------------------------------------------------------------


async def candidate_reply(client, ledger, persona, cv, transcript, seed, extra: str = "") -> str:
    from shared.text_models import price_usd

    system = (
        "You are a job candidate in a live voice interview for a Senior Product Manager role. "
        f"Your background (stay consistent with it):\n{cv}\n\nHow you behave: {PERSONAS[persona]}\n"
        "Reply only with what you say out loud, in plain spoken English, as one turn."
    )
    if extra:
        system += "\n" + extra
    messages = [{"role": "system", "content": system}]
    for t in transcript:
        if t.get("speaker") == "interviewer":
            messages.append({"role": "user", "content": t["text"]})
        elif t.get("speaker") == "candidate":
            messages.append({"role": "assistant", "content": t["text"]})
    r = await client.chat.completions.create(model=CANDIDATE_MODEL, messages=messages, temperature=0.7,
                                             seed=seed, max_completion_tokens=350)
    u = r.usage
    ledger.add("candidate", CANDIDATE_MODEL, price_usd(CANDIDATE_MODEL, input_tokens=u.prompt_tokens,
               cached_input_tokens=getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0,
               output_tokens=u.completion_tokens))
    return (r.choices[0].message.content or "").strip()


_STOP = {"what", "about", "that", "your", "with", "this", "have", "were", "when", "tell", "from", "they",
         "there", "which", "would", "been", "into", "than", "then", "them", "time", "make", "made"}


def _content(text: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4 and w not in _STOP}


def matches_bank(turn: str, bp, area: int) -> str | None:
    """The current area's plan question this interviewer turn most resembles (half or more of
    the plan question's content words), or None. Used only to choose WHEN to inject a skip;
    whether the skipped turn really was a plan question is checked afterwards by the labeller."""
    from shared.contracts import FIRST_COMPETENCY_AREA

    index = area - FIRST_COMPETENCY_AREA
    if not 0 <= index < len(bp.competency_plans):
        return None
    words, best_share, best_qid = _content(turn), 0.0, None
    for qid, q in bp.competency_plans[index].numbered_questions():
        bank = _content(q)
        share = len(bank & words) / len(bank) if bank else 0
        if share > best_share:  # ties keep the earlier question
            best_share, best_qid = share, qid
    return best_qid if best_share >= 0.5 else None


def without_one_question_rule(text: str) -> str:
    """The instructions as they were before the one-question rule (prompt "p1"): the rule
    block cut out of the built text. Checked byte-for-byte against the hashes recorded
    before the rule was added (track_i_notes/A_checks/area_prompt_hashes_pre_rule.txt)."""
    from bot.realtime_prompt import ONE_QUESTION_RULE_HEADER

    start = text.index(f"\n\n{ONE_QUESTION_RULE_HEADER}\n")
    end = text.index('cost you?"', start) + len('cost you?"')
    return text[:start] + text[end:]


async def run_interview(*, condition: str, persona: str, run: int, ledger: Ledger,
                        blueprint_name: str = "pm_senior", cv_name: str = "cv_strong_pm.txt",
                        minutes_per_turn: float | None = None, candidate_turns: int | None = None,
                        skip: bool = False, closing_questions: bool = False,
                        one_question_rule: bool = True) -> dict:
    """condition: "after" (area swaps + notes) or "after_no_note" (area swaps, boundary
    notes not sent). Task 18's "before" (the legacy whole-plan prompt) was removed with
    the legacy builder; it is reproducible from commit 0909232.

    Defaults reproduce task 18's runs. minutes_per_turn / candidate_turns change the
    simulated pace and length (the closing runs). skip: from candidate turn 4, the
    first reply to a current-area plan question is SKIP_LINE instead of the model's.
    closing_questions: the candidate asks questions when invited in the closing."""
    from openai import AsyncOpenAI

    from bot.blueprint_source import load_blueprint
    from bot.brain.brain import InterviewBrain
    from bot.realtime_coordinator import REALTIME_TOOLS, RealtimeCoordinator
    from bot.realtime_prompt import build_area_instruction
    from bot.services.realtime import realtime_session_dict
    from pipecat.adapters.services.open_ai_realtime_adapter import OpenAIRealtimeLLMAdapter

    bp = load_blueprint(blueprint_id=blueprint_name)
    opening = InterviewBrain(bp, time_of_day="afternoon").opening_line()
    cv = (BACKEND / "tests" / "fixtures" / "documents" / cv_name).read_text()
    if condition not in ("after", "after_no_note"):
        raise ValueError(f"unknown condition {condition!r}")
    swaps = True

    def area_text(area: int) -> str:
        text = build_area_instruction(bp, opening_line=opening, area=area)
        return text if one_question_rule else without_one_question_rule(text)

    instructions = area_text(1)
    tools = OpenAIRealtimeLLMAdapter().from_standard_tools(REALTIME_TOOLS)
    client = AsyncOpenAI()
    seed = 1000 + run * 17 + SEED_ORDER.index(persona)
    pace = minutes_per_turn or MINUTES_PER_TURN
    import hashlib

    prompt_sha = hashlib.sha256(instructions.encode()).hexdigest()[:16]

    async with Session(ledger, f"{condition}/{persona}/{run}") as s:
        await s.send({"type": "session.update", "session": realtime_session_dict(instructions=instructions, tools=tools)})
        await s.wait("session.updated")
        coord = RealtimeCoordinator(
            blueprint=bp, llm=Bridge(s), session_id=s.label,
            area_instruction=area_text if swaps else None)
        coord.register_tools = lambda: None
        s.coordinator = coord
        schedule = coord._schedule()
        next_mark = 0
        # The coordinator's own clock, simulated: _started_at is set so that
        # elapsed_seconds reads the simulated interview time (start() is not
        # called: it would run the real-time clock and pause audio input).
        coord._started_at = time.monotonic()
        await s.respond(label="greeting")
        skipped = False
        for turn in range(candidate_turns or CANDIDATE_TURNS):
            if coord.is_over:
                break
            asked = next((t["text"] for t in reversed(s.log) if t.get("speaker") == "interviewer"), "")
            qid = matches_bank(asked, bp, coord.current_area if swaps else 0) if skip and not skipped and turn >= 4 else None
            if qid:
                said, skipped = SKIP_LINE, True
                s.log.append({"event": "skip_injected", "matched_qid": qid, "turn": turn})
            else:
                said = await candidate_reply(client, ledger, persona, cv, s.log, seed + turn,
                                             CLOSING_QUESTIONS if closing_questions else "")
            s.log.append({"speaker": "candidate", "text": said, "sim_minutes": turn * pace})
            await s.add("user", said)
            await coord._on_candidate_turn(said)
            sim_s = (turn + 1) * pace * 60
            while next_mark < len(schedule) and schedule[next_mark][0] <= sim_s:
                coord._started_at = time.monotonic() - sim_s
                area = coord._area_for_mark(next_mark)
                if condition == "after_no_note":
                    await coord.enter_area(area)
                else:
                    await coord._on_mark(next_mark, schedule[next_mark][1])
                s.log.append({"event": "boundary", "mark": next_mark, "area": area, "sim_minutes": sim_s / 60})
                next_mark += 1
            s.sim_minutes = sim_s / 60
            await s.respond()
        await coord.stop()
    return {"condition": condition, "persona": persona, "run": run, "blueprint": blueprint_name,
            "prompt_sha": prompt_sha, "one_question_rule": one_question_rule,
            "minutes_per_turn": pace, "skip": skip,
            "closing_questions": closing_questions, "schedule_minutes": [m / 60 for m, _ in schedule],
            "log": s.log, "coordinator_events": coord.events}


# -- the labeller ------------------------------------------------------------------------


class TurnLabel(BaseModel):
    turn_index: int
    kind: str = Field(description="One of: opening, plan, follow_up, off_plan, closing, not_a_question.")
    plan_id: str | None = Field(description="For kind plan: the numbered question's id as written (\"Q2.1\"). Else null.")
    asks: list[str] = Field(description=(
        "Each separate thing the candidate must answer, as a short phrase. A reason (\"and why\") or framing "
        "(\"starting from where the idea came from\") belongs to its ask; it is not a separate one."))
    invented_attribution: bool = Field(description=(
        "True only if the turn claims the candidate said something (\"you mentioned X\", \"as you said\") that "
        "they did not say anywhere earlier in the transcript."))
    note: str = Field(description="One short line: why this kind, quoting the words that decided it.")


class Labels(BaseModel):
    labels: list[TurnLabel]


LABELLER_SYSTEM = """You label each interviewer turn in an interview transcript against the interview plan.
For EVERY interviewer turn listed, choose kind with these rules, in this order:
1. closing: asks whether they have questions for the interviewer, thanks them, or says goodbye.
   opening: the greeting, small talk, or the opening question ("what are you working on now").
   not_a_question: asks nothing.
2. follow_up: it digs further into the most recent plan question, using what the candidate just said
   (asks for a detail, an example, a number, who, why, what they personally did).
3. plan: it asks the substance of a numbered plan question other than the most recent one, even when
   it is phrased around the candidate's last answer (questions are meant to be adapted). Give plan_id.
4. follow_up: it builds on the candidate's last answer but asks no numbered question.
5. off_plan: none of the above.
asks: list what the candidate is asked to answer. "Tell me about a strategy you set. What was the bet
behind it?" is two asks. "Who disagreed, and what did it cost?" is two asks. "What was the metric, and
why?" is one ask.
Judge from the words only. Be strict and consistent."""


def labeller_input(run: dict) -> str:
    from bot.blueprint_source import load_blueprint

    bp = load_blueprint(blueprint_id=run["blueprint"])
    plan = ["Area 1: Opening. Greet, settle them in, ask what they are working on now."]
    for index, p in enumerate(bp.competency_plans):
        plan.append(f"Area {index + 2}: {p.name}")
        plan += [f"  {qid}: {q}" for qid, q in p.numbered_questions()]
    plan.append(f"Area {len(bp.competency_plans) + 2}: Closing. Ask whether they have questions, thank them, goodbye.")
    lines, n = [], 0
    for t in run["log"]:
        if t.get("speaker"):
            lines.append(f"[{n}] {t['speaker'].upper()}: {t['text']}")
            t["turn_index"] = n
            n += 1
    return "PLAN\n" + "\n".join(plan) + "\n\nTRANSCRIPT\n" + "\n".join(lines)


def derive_labels(run: dict, raw: list[dict]) -> list[dict]:
    """The labeller's kinds, with three things decided in code: area-1 turns are the opening,
    plan ids are normalised ("2.1" -> "Q2.1"), the current plan question asked again is a
    follow-up on it, and a follow-up belongs to the most recent plan question (None after an
    off-plan question). stacked = two or more asks."""
    areas = area_of_turns(run)
    by_turn = {r["turn_index"]: r for r in raw}
    current, out = None, []
    for index in sorted(areas):
        r = by_turn.get(index)
        if r is None:
            continue
        kind, qid = r["kind"], r.get("plan_id")
        if qid and not qid.upper().startswith("Q"):
            qid = f"Q{qid}"
        if areas[index] == 1 and kind in ("plan", "off_plan", "follow_up"):
            kind = "opening"
        if kind == "plan" and qid and qid == current:
            kind = "follow_up"  # the current plan question again: digging further into it
        elif kind == "plan" and qid:
            current = qid
        elif kind == "plan":
            kind = "off_plan"  # a plan question without an id cannot be checked
        if kind == "off_plan":
            current = None
        if kind == "follow_up":
            qid = current
        elif kind != "plan":
            qid = None
        out.append({"turn_index": index, "kind": kind, "question_id": qid,
                    "stacked": len(r.get("asks") or []) >= 2,
                    "invented_attribution": bool(r["invented_attribution"]),
                    "asks": r.get("asks"), "note": r.get("note"),
                    **({"off_area_check": r["off_area_check"]} if "off_area_check" in r else {})})
    return out


class OffAreaCheck(BaseModel):
    refers_back: str | None = Field(description=(
        "Words in the INTERVIEWER TURN that point at something specific the candidate just said "
        "(\"that postponement\", \"in that compromise\"), copied exactly. Null if none."))
    main_ask: str = Field(description="The main thing the turn asks, in a few words (ignore 'including ...' details).")
    verdict: str = Field(description="new_plan_question, continues_current_question, or digs_into_last_answer")
    reason: str = Field(description="One short line.")


OFF_AREA_SYSTEM = """An interviewer turn was matched to plan question X, from a different area of the interview.
Decide what the turn's MAIN ask is (ignore added details such as "including who disagreed"):
- new_plan_question: the main ask is X's topic, a new topic (it may be adapted to the candidate).
- continues_current_question: the main ask goes further into CURRENT, the plan question being discussed.
- digs_into_last_answer: the main ask is more about something specific the candidate just said, and
  it only shares a word or theme with X.
A question that opens a new topic and also asks for details is still new_plan_question."""


async def _check_off_area(run, raw_label, current_qid, ledger) -> bool:
    """True if the turn really asks the other area's plan question."""
    from bot.blueprint_source import load_blueprint
    from shared.text_models import request_structured

    bp = load_blueprint(blueprint_id=run["blueprint"])
    questions = dict(q for p in bp.competency_plans for q in p.numbered_questions())
    index, last_answer, turn = raw_label["turn_index"], "", ""
    for t in run["log"]:
        if t.get("speaker") == "candidate" and t["turn_index"] < index:
            last_answer = t["text"]
        if t.get("turn_index") == index:
            turn = t["text"]
    current = f"{current_qid}: {questions[current_qid]}" if current_qid in questions else "(none yet)"
    result = await request_structured(
        job="labeller", model=LABELLER_MODEL, temperature=0, response_model=OffAreaCheck,
        messages=[{"role": "system", "content": OFF_AREA_SYSTEM}, {"role": "user", "content": (
            f"X = {raw_label['plan_id']}: {questions.get(raw_label['plan_id'], '(unknown id)')}\n\n"
            f"CURRENT = {current}\n\n"
            f"CANDIDATE'S LAST ANSWER: {last_answer}\n\nINTERVIEWER TURN: {turn}")}])
    ledger.add("labeller", LABELLER_MODEL, result.record.cost_usd)
    raw_label["off_area_check"] = result.payload
    return result.payload["verdict"] == "new_plan_question"


async def label(run: dict, ledger: Ledger) -> list[dict]:
    """Whole-transcript labels, then a focused second check on every turn labelled
    as another area's plan question (the labeller matches on shared words:
    "who disagreed with that postponement" -> Q4.1 "a serious disagreement").
    A turn the check finds is digging into the last answer becomes a follow-up."""
    from shared.text_models import request_structured

    labeller_input(run)  # sets turn_index on the log
    result = await request_structured(
        job="labeller", model=LABELLER_MODEL, temperature=0,
        messages=[{"role": "system", "content": LABELLER_SYSTEM}, {"role": "user", "content": labeller_input(run)}],
        response_model=Labels)
    ledger.add("labeller", LABELLER_MODEL, result.record.cost_usd)
    raw = sorted(result.payload["labels"], key=lambda r: r["turn_index"])
    areas = area_of_turns(run)
    current = None
    for r in raw:
        qid = r.get("plan_id")
        if qid and not qid.upper().startswith("Q"):
            r["plan_id"] = qid = f"Q{qid}"
        area = areas.get(r["turn_index"])
        if r["kind"] == "plan" and qid and area and area >= 2 and qid_area(qid) != area:
            if not await _check_off_area(run, r, current, ledger):
                r["kind"], r["plan_id"] = "follow_up", None
        if r["kind"] == "plan" and r.get("plan_id"):
            current = r["plan_id"]
        elif r["kind"] == "off_plan":
            current = None
    return derive_labels(run, raw)


# -- metrics -----------------------------------------------------------------------------

ASKING = ("plan", "follow_up", "off_plan")


def area_of_turns(run: dict) -> dict[int, int]:
    """Which area the coordinator was in when each interviewer turn was produced."""
    area, out = 1, {}
    for t in run["log"]:
        if t.get("event") == "boundary":
            area = t["area"]
        elif t.get("speaker") == "interviewer":
            out[t["turn_index"]] = area
    return out


def qid_area(qid: str | None) -> int | None:
    try:
        return int(qid[1:].split(".")[0]) if qid else None
    except ValueError:
        return None


def metrics(run: dict, labels: list[dict]) -> dict:
    """Counts from the labels and the run's own boundary events.

    off_plan:  the labeller found neither a plan question nor a follow-up.
    off_area:  a plan question from an area other than the current one (the
               rule is "a plan question of this area, or a follow-up").
    max_follow_ups: the longest run of follow-ups on one plan question.
    plan_ids_used / bank: distinct plan questions asked in the areas the run
               finished (entered and left), against those areas' question count.
    switches_landed: the first or second question after a boundary into a
               competency area is one of that area's plan questions.
    """
    from bot.blueprint_source import load_blueprint

    bp = load_blueprint(blueprint_id=run["blueprint"])
    closing = len(bp.competency_plans) + 2
    areas = area_of_turns(run)
    by_turn = {lbl["turn_index"]: lbl for lbl in labels if lbl["turn_index"] in areas}
    asked = [by_turn[i] for i in sorted(by_turn) if by_turn[i]["kind"] in ASKING]

    follow, best, current = {}, 0, None
    for lbl in asked:
        if lbl["kind"] == "plan":
            current = lbl["question_id"]
            follow.setdefault(current, 0)
        elif lbl["kind"] == "follow_up":
            qid = current or lbl["question_id"]
            follow[qid] = follow.get(qid, 0) + 1
            best = max(best, follow[qid])
        else:
            current = None  # an off-plan question breaks the thread

    off_area = [lbl for lbl in asked if lbl["kind"] == "plan"
                and qid_area(lbl["question_id"]) not in (None, areas[lbl["turn_index"]])]

    entered = [t["area"] for t in run["log"] if t.get("event") == "boundary"]
    finished = [a for a in entered if 2 <= a < closing and any(b > a for b in entered)]
    bank = sum(len(bp.competency_plans[a - 2].seed_questions) for a in finished)
    used = {lbl["question_id"] for lbl in asked if lbl["kind"] == "plan"
            and qid_area(lbl["question_id"]) in finished}

    switches = []
    log = run["log"]
    for i, e in enumerate(log):
        if e.get("event") == "boundary" and 2 <= e["area"] < closing:
            nxt = [by_turn.get(t["turn_index"]) for t in log[i + 1:] if t.get("speaker") == "interviewer"][:2]
            ids = [(n or {}).get("question_id") if (n or {}).get("kind") == "plan" else None for n in nxt]
            switches.append({"area": e["area"], "landed": any(qid_area(q) == e["area"] for q in ids),
                             "next_ids": ids, "available": len(nxt)})
    # What happens right after the third follow-up on a plan question (the cap).
    at_cap, counts, current = [], {}, None
    texts = {t["turn_index"]: t["text"] for t in run["log"] if t.get("speaker") == "interviewer"}
    for n, lbl in enumerate(asked):
        if lbl["kind"] == "plan":
            current = lbl["question_id"]
        elif lbl["kind"] == "follow_up":
            qid = current or lbl["question_id"]
            counts[qid] = counts.get(qid, 0) + 1
            if counts[qid] == 3 and n + 1 < len(asked):
                nxt = asked[n + 1]
                at_cap.append({"question": qid, "third_follow_up": texts.get(lbl["turn_index"]),
                               "next_kind": nxt["kind"], "next_id": nxt["question_id"],
                               "next": texts.get(nxt["turn_index"])})
        else:
            current = None

    return {
        "at_cap": at_cap,
        "questions": len(asked),
        "off_plan": sum(1 for lbl in asked if lbl["kind"] == "off_plan"),
        "off_area": len(off_area),
        "off_area_ids": [(lbl["turn_index"], lbl["question_id"]) for lbl in off_area],
        "follow_ups": sum(1 for lbl in asked if lbl["kind"] == "follow_up"),
        "max_follow_ups": best,
        "over_cap": best > 3,
        "stacked": sum(1 for lbl in asked if lbl["stacked"]),
        "invented": sum(1 for lbl in by_turn.values() if lbl["invented_attribution"]),
        "plan_ids_used": len(used), "bank_in_finished_areas": bank,
        "switches": sum(1 for x in switches if x["available"]),
        "switches_landed": sum(1 for x in switches if x["landed"]),
        "switch_detail": switches,
        "tool_calls": [e["name"] for e in log if e.get("event") == "tool"],
    }


# -- added after task 18: stacking by cause, the closing, skips -------------------------

BANK_ASKS_SYSTEM = """For each numbered interview question, list the separate things a candidate must answer.
"Tell me about a strategy you set. What was the bet behind it?" is two asks. "Who disagreed, and what did
it cost?" is two asks. A reason ("and why that one?") or framing ("Start from where the idea came from")
belongs to its ask; it is not a separate one."""


class BankAsks(BaseModel):
    id: str
    asks: list[str]


class BankAsksList(BaseModel):
    questions: list[BankAsks]


async def bank_asks(blueprint_name: str, ledger: Ledger) -> dict[str, list[str]]:
    """The labeller's split of every bank question into asks, by the same definition it uses
    for interviewer turns. One call per blueprint; the result is checked by hand."""
    from bot.blueprint_source import load_blueprint
    from shared.text_models import request_structured

    bp = load_blueprint(blueprint_id=blueprint_name)
    lines = [f"{qid}: {q}" for p in bp.competency_plans for qid, q in p.numbered_questions()]
    result = await request_structured(
        job="labeller", model=LABELLER_MODEL, temperature=0, response_model=BankAsksList,
        messages=[{"role": "system", "content": BANK_ASKS_SYSTEM}, {"role": "user", "content": "\n".join(lines)}])
    ledger.add("labeller", LABELLER_MODEL, result.record.cost_usd)
    return {q["id"] if q["id"].startswith("Q") else f"Q{q['id']}": q["asks"] for q in result.payload["questions"]}


TEXT_ONLY_SYSTEM = """For each numbered line, list the separate things it asks the listener to answer,
judging ONLY the words on that line. Do not add anything the line does not say.
"Tell me about a strategy you set. What was the bet behind it?" is two asks. "Who disagreed, and what did
it cost?" is two asks. "Tell me about a strategy you set." is one ask. A reason ("and why?") or framing
("starting from where the idea came from") belongs to its ask. A line that asks nothing has no asks."""


class LineAsks(BaseModel):
    line: int
    asks: list[str]


class LineAsksList(BaseModel):
    lines: list[LineAsks]


async def stacked_text_only(run: dict, ledger: Ledger) -> dict[int, list[str]]:
    """Asks per interviewer turn, from the turn's own words only: no plan, no candidate.
    Added after task 18 because the whole-transcript labeller copies a plan question's
    second part into a turn that asked only the first ("Tell me about a product strategy
    you personally set." -> ['... strategy ...', 'what was the bet'])."""
    from shared.text_models import request_structured

    turns = [t for t in run["log"] if t.get("speaker") == "interviewer"]
    result = await request_structured(
        job="labeller", model=LABELLER_MODEL, temperature=0, response_model=LineAsksList,
        messages=[{"role": "system", "content": TEXT_ONLY_SYSTEM},
                  {"role": "user", "content": "\n".join(f"{t['turn_index']}: {t['text']}" for t in turns)}])
    ledger.add("labeller", LABELLER_MODEL, result.record.cost_usd)
    return {line["line"]: line["asks"] for line in result.payload["lines"]}


def question_sentences(text: str) -> int:
    """A floor computed in code: sentences that end in "?" or open with a request to tell
    ("Tell me", "Walk me", "Take me", "Describe", "Talk me"). Misses "X, and Y?" joins."""
    import re

    sentences = [x.strip() for x in re.split(r"(?<=[.?!])\s+", text) if x.strip()]
    asks = re.compile(r"^(tell me|walk me|take me|talk me|describe|give me|share)\b", re.I)
    return sum(1 for x in sentences if x.endswith("?") or asks.match(x))


def stacking_causes(labels: list[dict], asks_by_qid: dict[str, list[str]]) -> dict:
    """A stacked question is either a two-part bank question asked whole (kind plan, and the
    bank question itself has two or more asks) or Mitra adding a question of its own."""
    out = {"bank_two_part_whole": 0, "own_addition_on_plan": 0, "own_addition_on_follow_up": 0,
           "own_addition_off_plan": 0}
    for lbl in labels:
        if not lbl["stacked"] or lbl["kind"] not in ASKING:
            continue
        if lbl["kind"] == "plan" and len(asks_by_qid.get(lbl["question_id"], [])) >= 2:
            out["bank_two_part_whole"] += 1
        else:
            out[f"own_addition_{'off_plan' if lbl['kind'] == 'off_plan' else 'on_' + lbl['kind']}"] += 1
    return out


def closing_metrics(run: dict, labels: list[dict]) -> dict:
    """Against the coordinator's own schedule: when the closing began, whether the interview
    ended early or ran past the hard stop, and finish_interview against the goodbye."""
    from bot.blueprint_source import load_blueprint

    bp = load_blueprint(blueprint_id=run["blueprint"])
    n = len(bp.competency_plans)
    sched = run["schedule_minutes"]
    closing_start, booked_end, hard_stop = sched[n], sched[n + 1], sched[n + 2]
    log = run["log"]
    by_turn = {lbl["turn_index"]: lbl for lbl in labels}
    turns = [t for t in log if t.get("speaker") == "interviewer"]
    closing_turns = [t for t in turns if by_turn.get(t.get("turn_index"), {}).get("kind") == "closing"]
    finishes = [e for e in log if e.get("event") == "tool" and e["name"] == "finish_interview"]
    ends = [e for e in log if e.get("event") == "tool" and e["name"] == "end_interview"]
    finish = finishes[0] if finishes else None
    same_reply = [t["text"] for t in turns if finish and t.get("response") == finish["response"]]
    after_hard_stop = [t for t in turns if t.get("sim_minutes", 0) >= hard_stop]
    return {
        "closing_start": closing_start, "booked_end": booked_end, "hard_stop": hard_stop,
        "first_closing_turn_minute": closing_turns[0]["sim_minutes"] if closing_turns else None,
        "closing_began_early": bool(closing_turns) and closing_turns[0]["sim_minutes"] < closing_start,
        "finish_calls": len(finishes), "end_interview_calls": len(ends),
        "finish_minute": finish["sim_minutes"] if finish else None,
        "ended_early": bool(finish) and finish["sim_minutes"] < closing_start,
        "goodbye_in_finishing_reply": same_reply[0] if same_reply else None,
        "replies_after_hard_stop": len(after_hard_stop),
        "overran_hard_stop": (len(after_hard_stop) > 1) or (not finish and bool(after_hard_stop)),
        "finished": bool(finish),
        "closing_area_turns": [{"turn": t["turn_index"], "minute": t.get("sim_minutes"), "text": t["text"]}
                               for t in turns if t.get("sim_minutes", 0) >= closing_start],
    }


_OFFER_TO_END = r"\b(stop here|end (the|this|our) (interview|conversation|call)|wrap (this|it) up|" \
                r"would you (like|prefer) to (continue|stop|end)|carry on with the interview|continue the interview)\b"


def skip_metrics(run: dict, labels: list[dict]) -> dict | None:
    """What Mitra does after SKIP_LINE: another plan question, no offer to end, no
    end_interview, the skipped question never asked again, the skip not followed up."""
    import re

    log = run["log"]
    at = next((i for i, e in enumerate(log) if e.get("event") == "skip_injected"), None)
    if at is None:
        return None
    by_turn = {lbl["turn_index"]: lbl for lbl in labels}
    before = [t for t in log[:at] if t.get("speaker") == "interviewer"][-1]
    after = [t for t in log[at:] if t.get("speaker") == "interviewer"]
    skipped = by_turn.get(before["turn_index"], {})
    skipped_id = skipped.get("question_id")
    nxt = by_turn.get(after[0]["turn_index"], {}) if after else {}
    later = [by_turn.get(t["turn_index"], {}) for t in after[1:]]
    tools_after = [e["name"] for e in log[at:] if e.get("event") == "tool"]
    events = [e["kind"] for e in run["coordinator_events"]]
    return {
        "skipped_turn": before["text"], "skipped_kind": skipped.get("kind"), "skipped_id": skipped_id,
        "matched_id": log[at]["matched_qid"],
        "next_turn": after[0]["text"] if after else None, "next_kind": nxt.get("kind"), "next_id": nxt.get("question_id"),
        "moved_to_another_plan_question": nxt.get("kind") == "plan" and nxt.get("question_id") != skipped_id,
        "offered_to_end": bool(after and re.search(_OFFER_TO_END, after[0]["text"], re.I))
                          or "withdrawal_confirmation_spoken" in events,
        "end_interview_called": "end_interview" in tools_after,
        "asked_again_later": [lbl.get("turn_index") for lbl in later
                              if skipped_id and lbl.get("question_id") == skipped_id and lbl.get("kind") == "plan"],
        "followed_up": nxt.get("kind") == "follow_up" and nxt.get("question_id") == skipped_id,
    }


def summarise(results: list[dict]) -> dict:
    out = {}
    for cond in sorted({r["condition"] for r in results}):
        m = [r["metrics"] for r in results if r["condition"] == cond]
        q = sum(x["questions"] for x in m)
        out[cond] = {
            "runs": len(m),
            "questions": q,
            "off_plan": f"{sum(x['off_plan'] for x in m)} of {q}",
            "off_area": f"{sum(x['off_area'] for x in m)} of {q}",
            "runs_with_off_plan_or_off_area": f"{sum(1 for x in m if x['off_plan'] or x['off_area'])} of {len(m)}",
            "follow_ups": f"{sum(x['follow_ups'] for x in m)} of {q}",
            "max_follow_ups_per_run": sorted(x["max_follow_ups"] for x in m),
            "runs_over_cap_3": f"{sum(1 for x in m if x['over_cap'])} of {len(m)}",
            "stacked_turns": f"{sum(x['stacked'] for x in m)} of {q}",
            "runs_with_stacked": f"{sum(1 for x in m if x['stacked'])} of {len(m)}",
            "invented": f"{sum(x['invented'] for x in m)} (in {sum(1 for x in m if x['invented'])} of {len(m)} runs)",
            "plan_ids_used": f"{sum(x['plan_ids_used'] for x in m)} of {sum(x['bank_in_finished_areas'] for x in m)}",
            "switches_landed": f"{sum(x['switches_landed'] for x in m)} of {sum(x['switches'] for x in m)}",
        }
    return out


# -- tests (behaviour suite) --------------------------------------------------------------

pytestmark = [
    pytest.mark.behavior,
    pytest.mark.skipif(not os.environ.get("RUN_BEHAVIOR_TESTS"), reason="Real model calls. Set RUN_BEHAVIOR_TESTS=1."),
]


async def test_plan_bound_interview_stays_on_the_plan():
    """One cooperative run on the plan-bound prompt: no off-plan question, the
    follow-up cap held, no stacked or invented-quote turns, and every area
    switch landed. Tolerant: a behaviour check, not a unit test."""
    import bot.config  # noqa: F401  (loads .env)

    ledger = Ledger()
    run = await run_interview(condition="after", persona="cooperative", run=1, ledger=ledger)
    run["metrics"] = metrics(run, await label(run, ledger))
    m = run["metrics"]
    assert m["off_plan"] <= 1
    assert not m["over_cap"]
    assert m["invented"] == 0
    assert m["switches_landed"] >= m["switches"] - 1
