"""The auditor: records only (decision 21), off the reply path (decision 18). Task 23.

After each of Mitra's turns a small text model (job "auditor": OPENAI_AUDITOR_MODEL,
default gpt-4.1-mini, never a reasoning model, bot/config.py) reads:
  - the turn;
  - the current area's numbered plan questions;
  - what has been asked so far in that area;
  - the candidate's last answer (and whether the coordinator recorded it as a
    skip or a decline);
and returns a label (plan question with its id, follow-up on which question,
easier variant after a decline, or off-plan), plus that area's coverage, any new
claims and contradictions. Claims are kept only when their quote is really in
the candidate's answer (normalised exact match, as report quotes are).

**It never writes into the realtime session.** It holds no reference to the
realtime service, the coordinator or the pipeline: it gets the current area from
a callable and reports through LiveEvents (Session C's emit) and its own results.

**Bounded.** One worker labels turns in order from a queue of at most
MAX_QUEUE turns. A turn that arrives when the queue is full is dropped and
counted (never waited for). Each model call has CALL_TIMEOUT_SECONDS; a failure
is counted and the turn left unlabelled. Nothing raises into the call. At the
end, `finish()` gives pending turns FINISH_SECONDS, then stops.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable

from loguru import logger
from pydantic import BaseModel, Field

#: Turns waiting to be labelled. Labels take ~1-3 s and Mitra speaks at most every
#: few seconds, so a backlog of 20 means the model has been failing or slow for a
#: minute or more; dropping then keeps memory and catch-up time bounded.
MAX_QUEUE = 20
#: One model call. A label that takes longer is no longer worth waiting for live.
CALL_TIMEOUT_SECONDS = 20.0
#: At the end of the call: time for pending labels before results are saved.
#: The backlog at the end is normally the last one or two turns (1-3 s each);
#: 30 s covers a slow call plus a retry, and only delays saving, never the call.
FINISH_SECONDS = 30.0

#: A turn is another area's numbered question when it holds at least this share of
#: that question's content words (A-63, carried-over questions).
CARRIED_OVER_SHARE = 0.6
#: Round 18 (A-115): a turn in a competency area is never "opening". When the model says
#: so, the turn is the area's numbered question it shares at least this much with (its
#: wording or its goal), else off-plan.
IN_AREA_SHARE = 0.4
#: Words every goal starts with or leans on ("Learn how they ..."): not content.
_GOAL_FILLER = {"learn", "about", "they", "their", "them", "what", "when", "with", "that", "this", "were",
                "have", "from", "into", "which", "whether", "while"}

LABELS = ("opening", "plan", "follow_up", "easier_variant", "clarification", "off_plan", "closing",
          "not_a_question")


def _contract_has(label: str) -> bool:
    """Whether Session C's QuestionLabel holds this value (easier_variant: C-A4)."""
    from shared.interview_contracts import QuestionLabel

    return label in {v.value for v in QuestionLabel}


class AuditorClaim(BaseModel):
    claim: str = Field(description="What the candidate asserted, in a few words.")
    quote: str = Field(description="The candidate's own words from their last answer, copied exactly.")


class AuditorContradiction(BaseModel):
    earlier_claim: str = Field(description="An earlier claim from the list given.")
    later_statement: str = Field(description="The candidate's words in the last answer that sit against it.")


class AuditorReading(BaseModel):
    label: str = Field(description="One of: opening, plan, follow_up, easier_variant, clarification, off_plan, closing, "
                                   "not_a_question.")
    question_id: str | None = Field(description=(
        "plan: the numbered question's id. follow_up / easier_variant: the id of the plan question it follows "
        "or replaces. clarification: the id of the question asked again. Otherwise null."))
    coverage: str = Field(description="This area's coverage so far: not_started, insufficient, partial or sufficient.")
    new_claims: list[AuditorClaim]
    contradictions: list[AuditorContradiction]


SYSTEM = """You record how an interview is going. You never talk to anyone; you only label.
Label the INTERVIEWER TURN with these rules, in this order:
1. closing: thanks or goodbye. opening: ONLY in Area 1 (the opening): the greeting or the opening
   question ("what are you working on now"); never in any other area, whatever the turn starts with.
   not_a_question: asks nothing.
2. easier_variant: the candidate just DECLINED to answer, and this is a different, easier question on the
   same topic. Give the id of the declined question.
   clarification: the candidate did not understand the question, or asked to hear it again, and this asks
   the SAME question again, in the same or simpler words. Give the id of that question. It is not a
   follow-up and not an easier variant.
3. follow_up: it digs further into the PLAN QUESTION BEING DISCUSSED, using what the candidate just said
   (a detail, an example, a number, who, why, what they personally did). Give that question's id.
4. plan: it asks the substance of one of the area's numbered questions other than the one being
   discussed, even when phrased around the last answer (questions are meant to be adapted). Each
   question is listed with its goal (what it is meant to find out): the interviewer is given only the
   goals, so match the turn against the wording AND the goal. Give its id.
5. follow_up: it builds on the last answer but asks no numbered question.
6. off_plan: none of the above.
"I don't know" is an answer, not a decline. A skip ("can we skip this one") is not a decline: the next
question after a skip is labelled on its own terms.
Coverage: how much of THIS area's target the candidate's answers so far have shown.
new_claims: concrete things the candidate asserted in their LAST answer; quote their exact words.
contradictions: only a clear conflict between the last answer and an earlier claim listed. Hedges and
revised opinions are not contradictions."""


def make_turn_listener(auditor: "RealtimeAuditor"):
    """The callback run_realtime gives TurnEventObserver: candidate turns become the
    auditor's "last answer"; Mitra turns are queued (put_nowait). Synchronous: the
    observer never waits for a label."""

    def on_turn(speaker, index, text, end_seconds):
        if speaker == "candidate":
            auditor.candidate_turn(index, text)
        else:
            auditor.mitra_turn(index, text, end_seconds)

    return on_turn


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


class RealtimeAuditor:
    def __init__(self, *, blueprint, area_now: Callable[[], int], live=None, request=None,
                 max_queue: int = MAX_QUEUE, call_timeout: float = CALL_TIMEOUT_SECONDS):
        self._bp = blueprint
        self._area_now = area_now
        self._live = live
        self._request = request  # injectable for tests; default shared.text_models.request_structured
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._call_timeout = call_timeout
        self._worker: asyncio.Task | None = None
        self.labels: dict[int, dict] = {}          # mitra turn index -> label record
        self.turn_area: dict[int, int] = {}         # mitra turn index -> area number when spoken
        self.turn_text: dict[int, str] = {}         # mitra turn index -> its words (A-63: bank matching)
        self.coverage: dict[int, str] = {}          # area number -> coverage
        self.claims: list[dict] = []
        self.contradictions: list[dict] = []
        self.skips: list[dict] = []                 # {"area", "after_turn"}
        self.declines: list[dict] = []
        self._last_answer: dict | None = None       # {"index", "text", "kind"}
        self._last_mitra: int | None = None
        #: The plan question being discussed (the last plan label, any area).
        self._thread: str | None = None
        self.stats = {"submitted": 0, "labelled": 0, "dropped_queue_full": 0, "failed": 0, "timed_out": 0,
                      "unlabelled_at_end": 0, "usd": 0.0, "latency_s": []}
        self.status = "not_started"

    # -- the call's side: never blocks ----------------------------------------------------------

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())
            self.status = "running"

    def candidate_turn(self, index: int, text: str) -> None:
        self._last_answer = {"index": index, "text": text, "kind": None}

    def note_skip(self, area: int) -> None:
        """From the coordinator: the candidate's last turn was a skip."""
        self.skips.append({"area": area, "after_turn": self._last_mitra})
        if self._last_answer is not None:
            self._last_answer["kind"] = "skip"

    def note_decline(self, area: int) -> None:
        self.declines.append({"area": area, "after_turn": self._last_mitra})
        if self._last_answer is not None:
            self._last_answer["kind"] = "decline"

    def mitra_turn(self, index: int, text: str, at_seconds: float | None = None) -> None:
        """Queue one Mitra turn for labelling. put_nowait: never waits."""
        area = self._area_now()
        self.turn_area[index] = area
        self.turn_text[index] = text
        self._last_mitra = index
        item = {"index": index, "text": text, "area": area, "at": at_seconds,
                "answer": dict(self._last_answer) if self._last_answer else None}
        try:
            self._queue.put_nowait(item)
            self.stats["submitted"] += 1
        except asyncio.QueueFull:
            self.stats["dropped_queue_full"] += 1

    # -- the worker ---------------------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._label(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  (never into the call)
                self.stats["failed"] += 1
                logger.warning(f"auditor: turn {item['index']} not labelled: {exc!r}")
            finally:
                self._queue.task_done()

    def _plan_lines(self, area: int) -> list[str]:
        plans = self._bp.competency_plans
        if 2 <= area < len(plans) + 2:
            plan = plans[area - 2]
            return [f"Area {area}: {plan.name}. Target: {plan.target_depth}"] + [
                f"  {qid}: {q}" + (f"\n      goal: {plan.goal_of(i)}" if plan.goal_of(i) else "")
                for i, (qid, q) in enumerate(plan.numbered_questions())]
        if area <= 1:
            return ["Area 1: Opening. Greet, settle them in, ask what they are working on now."]
        return [f"Area {area}: Closing. Thank them and say goodbye (no numbered questions)."]

    async def _label(self, item: dict) -> None:
        from shared.text_models import request_structured

        area = item["area"]
        asked = [f"  turn {i}: {r['label']} {r.get('question_id') or ''}".rstrip()
                 for i, r in sorted(self.labels.items()) if self.turn_area.get(i) == area]
        answer = item["answer"]
        earlier = [c["claim"] for c in self.claims][-15:]
        questions = {qid: q for p in self._bp.competency_plans for qid, q in p.numbered_questions()}
        thread = f"  {self._thread}: {questions.get(self._thread, '')}" if self._thread else "  (none yet)"
        user = "\n".join([
            "PLAN FOR THE CURRENT AREA", *self._plan_lines(area),
            "THE PLAN QUESTION BEING DISCUSSED", thread,
            "ASKED SO FAR IN THIS AREA", *(asked or ["  (nothing yet)"]),
            "EARLIER CLAIMS", *([f"  - {c}" for c in earlier] or ["  (none)"]),
            "CANDIDATE'S LAST ANSWER" + (f" (the coordinator recorded it as a {answer['kind']})"
                                         if answer and answer.get("kind") else ""),
            f"  {answer['text'] if answer else '(none)'}",
            "INTERVIEWER TURN", f"  {item['text']}",
        ])
        request = self._request or request_structured
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(request(
                job="auditor", temperature=0, response_model=AuditorReading,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]),
                timeout=self._call_timeout)
        except asyncio.TimeoutError:
            self.stats["timed_out"] += 1
            return
        self.stats["latency_s"].append(round(time.monotonic() - started, 2))
        cost = getattr(getattr(result, "record", None), "cost_usd", None)
        if cost:
            self.stats["usd"] += cost
        reading = result.payload
        label = reading.get("label") if reading.get("label") in LABELS else "off_plan"
        qid = reading.get("question_id")
        if qid and not str(qid).upper().startswith("Q"):
            qid = f"Q{qid}"
        # B-90 A2 (round 13): an id that is not a numbered question in the plan
        # ("Qopening", "Q0") is not an id. Dropped here, then resolved below or
        # left unknown.
        if qid and qid not in self._questions():
            self.stats["invented_ids_dropped"] = self.stats.get("invented_ids_dropped", 0) + 1
            qid = None
        # Decided in code: the opening area's questions are the opening; the current
        # question asked again is a CLARIFICATION of it (round 14, C-146 option 1: it
        # was counted as a follow-up, against the cap of 3); a follow-up or a
        # clarification belongs to the question being discussed.
        if area <= 1 and label in ("plan", "off_plan", "follow_up", "clarification"):
            label, qid = "opening", None
        if area > 1 and label == "opening":
            # A-115: "Let's begin. <the area's first question>" was labelled opening, and
            # the area recorded no plan question. In a competency area it is the area's
            # numbered question it most resembles (wording or goal), else off-plan.
            best, share = self._best_bank_match(item["text"], prefer_area=area, only_area=area)
            label, qid = ("plan", best) if best and share >= IN_AREA_SHARE else ("off_plan", None)
            self.stats["opening_in_area_relabelled"] = self.stats.get("opening_in_area_relabelled", 0) + 1
        if label == "plan" and qid and qid == self._thread:
            label = "clarification"
        if label in ("follow_up", "clarification") and self._thread:
            qid = self._thread
        # A-63 (round 10): the model is shown only the current area's questions, so a
        # question carried over from another area (A-04 / A-08) comes back off-plan or
        # with a current-area id. When the turn plainly IS another area's numbered
        # question (most of its content words), it is labelled with that question's own
        # id. Deterministic, after the model; the model's input is unchanged.
        if label in ("plan", "off_plan", "follow_up"):
            best, best_share = self._best_bank_match(item["text"], prefer_area=area)
            # For a follow-up, "given" is the thread it would be filed under.
            given_id = (self._thread or qid) if label == "follow_up" else qid
            given = self._bank_share(item["text"], given_id) if given_id else 0.0
            if (best and self.plan_area_of(best) != area and best_share >= CARRIED_OVER_SHARE
                    and best_share > given + 0.2):
                label, qid = "plan", best
        if label == "easier_variant" and not qid:
            # C-A4: an easier variant carries the DECLINED question's id. The model
            # did not give one: take the question of the turn the latest decline answered.
            declined_turn = next((d["after_turn"] for d in reversed(self.declines) if d["area"] == area), None)
            qid = self.question_of_turn(declined_turn)
        if label == "follow_up" and not qid:
            # B-90 A1 (round 13): a follow-up digs into a question already being
            # discussed in this area: the latest labelled question here, else the
            # whole plan's bank on the turn's own words.
            qid = self._latest_question_in_area(area) or self.question_of_turn(item["index"])
        if label in ("plan", "follow_up", "easier_variant", "clarification") and not qid:
            label = "off_plan" if label == "plan" else label
        if label == "plan":
            self._thread = qid
        elif label == "off_plan":
            self._thread = None
        record = {"label": label,
                  "question_id": qid if label in ("plan", "follow_up", "easier_variant", "clarification") else None,
                  "area": area, "text": item["text"], "at": item["at"]}
        if label in ("follow_up", "easier_variant", "clarification") and not qid:
            # Genuinely unknown: kept in the record, never sent live (the contract has
            # no "unknown" for these labels; request C-A12).
            record["question_known"] = False
        if label == "follow_up" and qid:
            n = sum(1 for r in self.labels.values() if r["label"] == "follow_up" and r["question_id"] == qid)
            record["follow_up_number"] = n + 1
        self.labels[item["index"]] = record
        self.stats["labelled"] += 1
        if reading.get("coverage") in ("not_started", "insufficient", "partial", "sufficient"):
            self.coverage[area] = reading["coverage"]
        if answer:
            said = _norm(answer["text"])
            for claim in reading.get("new_claims") or []:
                if claim.get("quote") and _norm(claim["quote"]) and _norm(claim["quote"]) in said:
                    self.claims.append({"claim": claim["claim"], "quote": claim["quote"], "area": area,
                                        "turn_index": answer["index"]})
            for c in reading.get("contradictions") or []:
                if _norm(c.get("later_statement", "")) in said:
                    self.contradictions.append({**c, "area": area, "turn_index": answer["index"]})
        self._send(item["index"], record)

    def _send(self, index: int, record: dict) -> None:
        """A label as a live question_labelled event (existing kinds only).
        easier_variant is sent with C-A4's QuestionLabel.EASIER_VARIANT and the
        declined question's id (round 4); without an id it is kept, not sent.
        Round 18 (A-115): follow-ups past the cap of 3 are sent too; the cap limits what
        Mitra does, not what is recorded (the contract allows any number from 1)."""
        if self._live is None:
            return
        label = record["label"]
        if label == "not_a_question":
            return
        if label in ("follow_up", "easier_variant", "clarification") and not record.get("question_id"):
            # The contract requires the id (QuestionLabelled._shape); sending would be
            # refused and the label lost in the store (B-90 A1). Kept, counted, not sent.
            self.stats["not_sent_unknown_id"] = self.stats.get("not_sent_unknown_id", 0) + 1
            return
        if label == "easier_variant" and not _contract_has("easier_variant"):
            return
        area_id = self._live.area(record["area"])[0] if hasattr(self._live, "area") else None
        self._live.send("QuestionLabelled", {
            "turn_index": index, "label": label, "question_id": record["question_id"],
            "follow_up_number": record.get("follow_up_number") if label == "follow_up" else None,
            "area_id": area_id}, source="auditor")

    async def label_now(self, index: int, text: str, at_seconds: float | None = None) -> dict | None:
        """Label one Mitra turn inline (offline replays and checks only; the call
        uses `mitra_turn`, which never waits)."""
        area = self._area_now()
        self.turn_area[index] = area
        self.turn_text[index] = text
        self._last_mitra = index
        await self._label({"index": index, "text": text, "area": area, "at": at_seconds,
                           "answer": dict(self._last_answer) if self._last_answer else None})
        return self.labels.get(index)

    # -- the end of the call ------------------------------------------------------------------------

    async def finish(self, timeout: float = FINISH_SECONDS) -> dict:
        """Let pending labels finish (at most `timeout`), then stop. Never raises."""
        try:
            if self._worker is not None:
                try:
                    await asyncio.wait_for(self._queue.join(), timeout=timeout)
                    self.status = "complete"
                except asyncio.TimeoutError:
                    self.status = "partial"
                self._worker.cancel()
            else:
                self.status = "not_started"
        except Exception as exc:  # noqa: BLE001
            self.status = f"failed: {exc!r}"
        self.stats["unlabelled_at_end"] = self._queue.qsize()
        if self.stats["failed"] and self.status == "complete":
            self.status = "complete_with_failures"
        return self.summary()

    # -- results ------------------------------------------------------------------------------------

    def question_of_turn(self, index: int | None) -> str | None:
        """The plan question a Mitra turn asked or followed up (label first, then the bank)."""
        if index is None:
            return None
        record = self.labels.get(index)
        if record and record.get("question_id"):
            return record["question_id"]
        text = (record or {}).get("text") or self.turn_text.get(index)
        if not text:
            return None
        # The whole plan's bank, the current area first on a tie (A-63: a question
        # carried over from another area is found under its own id).
        best, best_share = self._best_bank_match(text, prefer_area=self.turn_area.get(index))
        return best if best_share >= 0.5 else None

    def _latest_question_in_area(self, area: int) -> str | None:
        """The question a follow-up digs into: the LATEST labelled Mitra turn in this
        area, if it is a plan question or a follow-up with an id. If the latest one is
        off-plan (or anything else), the follow-up is on that, which has no plan id:
        None (unknown), never an older plan question."""
        for index in sorted(self.labels, reverse=True):
            r = self.labels[index]
            if r["area"] != area:
                continue
            if r["label"] in ("plan", "follow_up") and r.get("question_id"):
                return r["question_id"]
            return None
        return None

    def _bank_share(self, text: str, qid: str | None) -> float:
        """The share of a numbered question's content words (4+ letters) found in `text`:
        the larger of its wording's and its goal's (round 18: Mitra is shown only goals)."""
        question = self._questions().get(qid or "")
        if not question:
            return 0.0
        words = set(_norm(text).split())
        shares = []
        for source in (question, self._goals().get(qid or "")):
            bank = {w for w in _norm(source or "").split() if len(w) >= 4} - _GOAL_FILLER
            if bank:
                shares.append(len(bank & words) / len(bank))
        return max(shares, default=0.0)

    def _goals(self) -> dict[str, str | None]:
        return {qid: p.goal_of(i) for p in self._bp.competency_plans for i, (qid, _) in enumerate(p.numbered_questions())}

    def _best_bank_match(self, text: str, prefer_area: int | None = None,
                         only_area: int | None = None) -> tuple[str | None, float]:
        best, best_key = None, (0.0, False)
        for qid in self._questions():
            if only_area is not None and self.plan_area_of(qid) != only_area:
                continue
            key = (self._bank_share(text, qid), self.plan_area_of(qid) == prefer_area)
            if key > best_key:
                best, best_key = qid, key
        return best, best_key[0]

    def _questions(self) -> dict[str, str]:
        return {qid: q for p in self._bp.competency_plans for qid, q in p.numbered_questions()}

    def plan_area_of(self, qid: str | None) -> int | None:
        """The area number a numbered question belongs to in the plan ("Q2.2" -> 2), or None."""
        if not qid:
            return None
        for i, plan in enumerate(self._bp.competency_plans):
            if any(q == qid for q, _ in plan.numbered_questions()):
                return i + 2
        return None

    def _placed(self, entry: dict) -> dict:
        """A skip or decline filed under the area its question belongs to (A-63). When the
        question is not known, the area current when it happened, marked as such."""
        qid = self.question_of_turn(entry["after_turn"])
        own = self.plan_area_of(qid)
        return {**entry, "question_id": qid, "spoken_in_area": entry["area"],
                "area": own if own is not None else entry["area"],
                "area_source": "question" if own is not None else "current_area"}

    def placement_now(self) -> tuple[int, str | None]:
        """For a live Skip event at the moment of the skip: (area, question id) of the last
        Mitra turn, from its label if it has one yet, else the whole-plan bank; the
        current area and None when the question is not known."""
        qid = self.question_of_turn(self._last_mitra)
        own = self.plan_area_of(qid)
        return (own, qid) if own is not None else (self._area_now(), None)

    def _section_of_claim(self, claim_text: str, default: str) -> str:
        """The section id of the area where an earlier claim was recorded."""
        for c in self.claims:
            if c["claim"] == claim_text:
                area = c["area"]
                plans = self._bp.competency_plans
                if area <= 1:
                    return "opening"
                if area >= len(plans) + 2:
                    return "closing"
                return plans[area - 2].competency_id
        return default

    def _label_area(self, record: dict) -> int:
        """The area a label counts under (A-63, round 12): its question's own area when
        the question is known (plan, follow-up, easier variant), otherwise the area it
        was spoken in. A carried-over Q2.2 asked in area 3 counts under area 2."""
        own = self.plan_area_of(record.get("question_id"))
        return own if own is not None else record["area"]

    def summary(self) -> dict:
        lat = sorted(self.stats["latency_s"])
        return {
            "status": self.status,
            "model_job": "auditor",
            "labels": {str(i): r for i, r in sorted(self.labels.items())},
            "skips": [self._placed(s) for s in self.skips],
            "declines": [self._placed(d) for d in self.declines],
            "claims": self.claims, "contradictions": self.contradictions,
            "coverage": {str(a): c for a, c in self.coverage.items()},
            "stats": {**{k: v for k, v in self.stats.items() if k != "latency_s"},
                      "usd": round(self.stats["usd"], 6),
                      "latency_median_s": lat[len(lat) // 2] if lat else None,
                      "latency_max_s": lat[-1] if lat else None},
        }

    def section_outcomes(self) -> list[dict]:
        """SectionOutcome-shaped dicts for the report (feedback reads section_outcomes),
        one per area that was reached, plus an `auditor` block the contract has no
        field for yet (request C-A5): questions asked, skips and declines with ids."""
        from shared.contracts import Contradiction, CoverageLevel, KeyClaim, SectionKind, SectionOutcome

        summary = self.summary()
        plans = self._bp.competency_plans
        reached = sorted(set(self.turn_area.values()) | {e["area"] for e in summary["skips"] + summary["declines"]}
                         | {self._label_area(r) for r in self.labels.values()})
        out = []
        for area in reached:
            if area <= 1:
                kind, sid, cid, name = SectionKind.OPENING, "opening", None, "Opening"
            elif area >= len(plans) + 2:
                kind, sid, cid, name = SectionKind.CLOSING, "closing", None, "Closing"
            else:
                plan = plans[area - 2]
                kind, sid, cid, name = SectionKind.COMPETENCY, plan.competency_id, plan.competency_id, plan.name
            # Filed by the question's own area (round 12): questions asked, follow-ups and
            # easier variants count where their question belongs, not where they were spoken.
            area_labels = [r for i, r in sorted(self.labels.items()) if self._label_area(r) == area]
            asked = sorted({r["question_id"] for r in area_labels if r["label"] == "plan" and r["question_id"]})
            unknown = "a question (not identified; filed under the area it happened in)"
            skips = [s["question_id"] or unknown for s in summary["skips"] if s["area"] == area]
            declines = [d for d in summary["declines"] if d["area"] == area]
            easier = [r for r in area_labels if r["label"] == "easier_variant"]
            reasons = []
            if skips:
                reasons.append(f"The candidate skipped {', '.join(skips)}.")
            if declines:
                ids = ", ".join(d["question_id"] or unknown for d in declines)
                reasons.append(f"The candidate declined {ids}; an easier variant was asked {len(easier)} time(s).")
            outcome = SectionOutcome(
                section_id=sid, kind=kind, competency_id=cid,
                coverage=CoverageLevel(self.coverage.get(area, "not_started")),
                depth_rationale=f"Auditor: asked {', '.join(asked) or 'no plan question'}; "
                                f"{sum(1 for r in area_labels if r['label'] == 'follow_up')} follow-up(s).",
                turns_spent=sum(1 for i, a in self.turn_area.items() if a == area),
                declined_turns=len(declines),
                shortfall_reason=" ".join(reasons) or None,
                # C-A5: the contract's own fields (they were in the "auditor" block).
                questions_asked=asked,
                skipped_question_ids=[s["question_id"] for s in summary["skips"] if s["area"] == area],
                declined_question_ids=[d["question_id"] for d in declines],
                easier_variants=len(easier),
                # Round 13: the per-area claims and contradictions the auditor verified
                # (each claim's quote, and each contradiction's later words, were found
                # in the candidate's answer), as the contract's own fields.
                key_claims=[KeyClaim(text=c["claim"], section_id=sid, turn_index=c["turn_index"])
                            for c in self.claims if c["area"] == area],
                contradictions=[Contradiction(
                    earlier_claim=c.get("earlier_claim", ""), earlier_section_id=self._section_of_claim(
                        c.get("earlier_claim", ""), default=sid),
                    later_statement=c.get("later_statement", ""), section_id=sid, turn_index=c["turn_index"])
                    for c in self.contradictions if c["area"] == area],
            ).model_dump(mode="json")
            # What the contract has no field for stays in the block.
            outcome["auditor"] = {
                "area_name": name,
                # area_source: "question" = filed by its question's own area; "spoken" =
                # no known question, filed where it was spoken (marked, A-63 rule).
                "labels": [{"label": r["label"], "question_id": r["question_id"], "spoken_in_area": r["area"],
                            "area_source": "question" if self.plan_area_of(r["question_id"]) is not None else "spoken"}
                           for r in area_labels],
                # Where each skip and decline came from (A-63): "question" = its plan
                # question's own area; "current_area" = not identified, filed where it happened.
                "skips": [{k: s[k] for k in ("question_id", "area_source", "spoken_in_area")}
                          for s in summary["skips"] if s["area"] == area],
                "declines": [{k: d[k] for k in ("question_id", "area_source", "spoken_in_area")}
                             for d in declines],
                "claims": [c for c in self.claims if c["area"] == area],
                "contradictions": [c for c in self.contradictions if c["area"] == area],
            }
            out.append(outcome)
        return out
