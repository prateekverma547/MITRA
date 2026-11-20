"""Refining a generated blueprint by talking to it.

Generation produces a plan from the CV and the spec. This lets the employer say
"stop asking about mentoring, push harder on the pricing claim" and have the
plan actually change.

**The revision cannot break the plan.** Whatever the model returns goes back
through `build_blueprint`, which enforces every rule generation is held to: one
plan per competency, seed questions present, and time budgets recomputed in code
against the configured duration. A refinement that would produce an
uninterviewable plan is rejected and the existing blueprint is kept.

Reasoning tier, off the critical path — same model as generation.
"""

import json
from dataclasses import dataclass

from openai import AsyncOpenAI
from pydantic import Field

from shared.branding import PROSE_STYLE
from shared.contracts import MAX_INTERVIEW_MINUTES, EvaluationSpec, InterviewBlueprint

from blueprint.generate import BlueprintGenerationError, GenerationResponse, PlanDraft, build_blueprint
from shared.text_models import TextModelError, pick, request_structured

#: The reply's shape for Structured Outputs: the plan in exactly the shape the
#: model was given (no domain_language: the vocabulary is carried across below),
#: plus a short reply to the employer. Picked from generate.py's reply model,
#: which is itself picked from the contract types.
RefinementResponse = pick(
    GenerationResponse, "candidate_name", "candidate_summary", "claims_to_verify",
    "suggested_opening", "interviewing_guidance",
    model_name="RefinementResponse",
    # The refinement shape, without the interviewer's briefing: a refinement changes
    # questions and emphasis, and the briefing and opening goals are carried across.
    competency_plans=(list[PlanDraft], ...),
    reply=(str, Field(description="One or two sentences telling the employer what you changed.")),
)
assert PlanDraft  # re-exported for readers of this module

SYSTEM = """\
You are revising an interview plan, working with the employer who will use it.

They will tell you what to change. Apply exactly what they ask and nothing more.
This is their plan, not yours — do not improve things they did not mention, do
not reword questions you were not asked about, and do not rebalance emphasis
they were happy with.

Common requests and what they mean:
- "spend less time on X"        -> lower X's `emphasis`
- "push harder on Y"           -> raise Y's `emphasis`, sharpen its questions
- "ask about Z"                -> add a seed question to the right competency
- "that question is unfair"    -> replace it, keep the competency
- "they have not done X"       -> soften questions that assume they have

Return the COMPLETE plan as JSON, in exactly the shape you were given — not a
diff, not only the parts you changed. Include every competency, even untouched
ones.

{
  "candidate_name": "...",
  "candidate_summary": "...",
  "claims_to_verify": [{"claim": "...", "source": "cv"}],
  "suggested_opening": "...",
  "interviewing_guidance": ["..."],
  "competency_plans": [
    {
      "competency_id": "must match the spec",
      "name": "...",
      "target_depth": "...",
      "emphasis": 1.0,
      "seed_questions": ["..."]
    }
  ],
  "reply": "one or two sentences telling the employer what you changed"
}

YOU CANNOT CHANGE HOW LONG THE INTERVIEW RUNS

The total length is fixed on the profile, not on this plan, and nothing you
return here can alter it. `emphasis` moves time BETWEEN competencies inside that
fixed total. It cannot shorten the interview. Lowering every competency's
emphasis to the same number changes nothing at all, because the split is
proportional: all it does is divide the same minutes the same way.

So if they ask for a shorter or longer interview, say plainly that you cannot do
it here and where it is done:

  "I cannot change the length from here. That is set on the profile: open the
   profile and use Change this, next to what the interview tests."

Then ask what they would like changed about the plan itself, or make only the
part of their request you can actually make.

DESCRIBE WHAT YOU CHANGED, NOT WHAT YOU HOPE IT ACHIEVES

Your `reply` says what you did. It does not say what that accomplishes unless it
does accomplish it. Cutting seed questions is a real change, and "I cut each
competency to one seed question" is a true sentence. "so the interview fits into
five minutes" is not, and attaching it to a true sentence is worse than saying
nothing, because the employer sees one real change and assumes the rest happened
too.

Say this:
  "I cut each competency down to one seed question."

Not this:
  "I reduced the emphasis for every competency and limited each to one seed
   question so the interview can fit into five minutes."

Rules that still apply, whatever they ask:
- Exactly one plan per competency in the spec. You may not drop a competency
  the employer's spec requires; if they want it gone, tell them it has to be
  removed from the spec itself.
- Every seed question asks ONE thing. One question mark is not enough: "Who
  disagreed, and what did it cost?" and "Tell me about X. What was the bet?" are
  two asks each. A reason on the same ask ("..., and why that one?") is one.
- Questions are ordered easiest to hardest.
- `emphasis` runs 0.5 to 2.0. Time is computed from it, so do not state minutes.
- Never invent facts about the candidate that are not in the CV.
"""

SYSTEM = f"{SYSTEM}\n\n{PROSE_STYLE}\n"


class RefinementError(RuntimeError):
    pass


@dataclass
class RefinedBlueprint:
    blueprint: InterviewBlueprint
    reply: str


@dataclass
class BlueprintRefiner:
    api_key: str
    model: str

    def __post_init__(self):
        # Retries are the helper's (exactly one), not the SDK's.
        self._client = AsyncOpenAI(api_key=self.api_key, max_retries=0)
        self.last_usage = None

    async def refine(
        self,
        *,
        blueprint: InterviewBlueprint,
        cv_text: str,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> RefinedBlueprint:
        spec: EvaluationSpec = blueprint.evaluation_spec
        if spec.duration_minutes > MAX_INTERVIEW_MINUTES:
            # A refined plan is a new plan: held to the 60-minute maximum, before
            # any model call. The stored longer plan still loads untouched.
            raise RefinementError(
                f"This plan is for {spec.duration_minutes} minutes; an interview can be at "
                f"most {MAX_INTERVIEW_MINUTES}. Shorten it on the profile and regenerate the plan."
            )

        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"EVALUATION SPEC:\n{spec.model_dump_json(indent=2)}\n\n"
                    # Stated as a fact in the context, not only as a rule in the
                    # prompt. The model twice told an employer it had shortened
                    # an interview it had not touched, so it is handed the number
                    # and told it is fixed, leaving nothing to reason around.
                    f"INTERVIEW LENGTH: fixed at {spec.duration_minutes} minutes. "
                    f"You cannot change this, and nothing you return will. It is "
                    f"changed on the profile, not here.\n\n"
                    f"CANDIDATE CV:\n{cv_text}\n\n"
                    f"CURRENT PLAN:\n{_plan_json(blueprint)}"
                ),
            },
        ]
        for turn in history or []:
            messages.append(
                {
                    "role": "assistant" if turn["role"] == "assistant" else "user",
                    "content": turn["content"],
                }
            )
        messages.append({"role": "user", "content": message})

        try:
            result = await request_structured(
                job="blueprint",
                model=self.model,
                client=self._client,
                messages=messages,
                response_model=RefinementResponse,
                temperature=0.3,  # used only by non-reasoning models
            )
        except TextModelError as exc:
            self.last_usage = exc.record
            raise RefinementError(f"Refinement model call failed: {exc}") from exc
        self.last_usage = result.record
        payload = result.payload

        reply = str(payload.get("reply", "")).strip() or "Updated the plan."

        # The gate: a revision that cannot be interviewed is not applied.
        try:
            revised = build_blueprint(
                blueprint_id=blueprint.blueprint_id,
                spec=spec,
                payload=payload,
                source_text=cv_text,
                opening_minutes=blueprint.opening_minutes,
            )
        except BlueprintGenerationError as exc:
            raise RefinementError(
                f"That change would leave the plan unusable, so it was not applied: {exc}"
            ) from exc

        # Refinement changes the plan, never the field. The revision payload has
        # no vocabulary in it, so without carrying it across, one refinement
        # would quietly strip the interview of the language it was taught, and
        # nothing would say so.
        if revised.domain_language is None:
            revised.domain_language = blueprint.domain_language
        # The safety net's record is history: a refinement keeps the cuts made
        # when the plan was generated, and adds any it made itself.
        revised.question_cuts = [*blueprint.question_cuts, *revised.question_cuts]
        # Nor the interviewer's preparation (2026-11-20): the opening goals and each
        # area's briefing are not in the revision payload, so they are carried across.
        _carry_preparation(blueprint, revised)

        return RefinedBlueprint(blueprint=revised, reply=reply)


def _carry_preparation(old: InterviewBlueprint, new: InterviewBlueprint) -> None:
    """Keep the opening goals and every area's briefing across a refinement."""
    if not new.opening_goals:
        new.opening_goals = list(old.opening_goals)
    before = {p.competency_id: p for p in old.competency_plans}
    for plan in new.competency_plans:
        was = before.get(plan.competency_id)
        if was is None:
            continue
        plan.key_ideas = plan.key_ideas or list(was.key_ideas)
        plan.level_signals = plan.level_signals or was.level_signals
        plan.analytical_angles = plan.analytical_angles or list(was.analytical_angles)


def _plan_json(blueprint: InterviewBlueprint) -> str:
    """The plan as the model should see it — no computed minutes.

    Budgets are derived from emphasis in code. Showing minutes would invite the
    model to edit them, and they would be silently recomputed anyway.
    """
    return json.dumps(
        {
            "candidate_name": blueprint.candidate_name,
            "candidate_summary": blueprint.candidate_summary,
            "claims_to_verify": [c.model_dump() for c in blueprint.claims_to_verify],
            "suggested_opening": blueprint.suggested_opening,
            "interviewing_guidance": blueprint.interviewing_guidance,
            "competency_plans": [
                {
                    "competency_id": p.competency_id,
                    "name": p.name,
                    "target_depth": p.target_depth,
                    "seed_questions": p.seed_questions,
                }
                for p in blueprint.competency_plans
            ],
        },
        indent=2,
    )


# -- HR findings into the technical plan (phase 3 wave 1; C-114, C-124) -------------------------
#
# Decided (Karandeep): at booking, the plan is refined from Track H's findings for the
# technical round (plain sentences). Each finding maps to an area with a NEUTRAL probe
# question, or is "not technical, left out". The raw findings text never reaches anything
# Mitra reads, and Mitra never mentions HR: the probe is an ordinary numbered question in
# its area; where it came from is recorded only in `hr_findings`, which the prompt builder
# (bot/realtime_prompt.py) never reads. No findings: the plan is returned unchanged.

import re as _re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

#: A probe is one short question.
PROBE_MAX_WORDS = 30
#: Words a neutral probe never uses: they point at HR, an earlier conversation, or a
#: judgement someone else made.
_NOT_NEUTRAL = _re.compile(
    r"\b(hr|human resources|recruit\w*|screening|interviewer|earlier (round|interview|conversation|call|chat)|"
    r"previous (round|interview|conversation|call)|first round|last round|you (mentioned|told (us|me)|noted)|(as|what|earlier|before) you (said|shared)|you (said|shared) (you|that|earlier|before|in)|"
    r"(we|i) (hear|heard|were told|was told|understand|noticed)|according to|feedback|flagged|concern\w*|reported|"
    r"a colleague|your colleague|someone)\b", _re.IGNORECASE)
#: A run of this many words copied from the finding counts as the raw text.
_COPIED_WORDS = 5


class _FindingMapping(BaseModel):
    index: int = Field(description="The finding's number, as given (0-based).")
    outcome: Literal["probed", "not_technical"]
    area_id: str | None = Field(default=None, description="The competency_id it maps to (probed only).")
    probe: str | None = Field(default=None, description="One neutral question (probed only).")


class _FindingsResponse(BaseModel):
    findings: list[_FindingMapping]


HR_SYSTEM = """You prepare a technical interview plan. You are given the plan's areas (competencies) and
some notes about the candidate written before this interview. For EACH note, decide:
- "probed": the note is about something this technical interview can test. Choose the ONE area it
  belongs to (its competency_id) and write ONE short, neutral question that lets the candidate show it.
- "not_technical": the note is not about the technical skills in these areas (salary, notice period,
  location, manners, availability, anything personal), or it is too vague to test. Leave it out.
Rules for a probe question:
- One question, under 30 words, asked as if it were the interviewer's own idea.
- Never mention, hint at or imply where the note came from: no "HR", no "earlier", no "previous
  conversation", no "you mentioned", no "we heard", no "feedback", no "concern".
- Never repeat the note's wording, never accuse, never state a judgement. Ask for a concrete example
  or a decision they made, the way a curious colleague would.
Return one entry per note, with its index."""


def _probe_problems(probe: str, finding: str) -> list[str]:
    problems = []
    words = _re.findall(r"[a-z0-9']+", probe.lower())
    if probe.count("?") != 1 or not probe.strip().endswith("?"):
        problems.append("not exactly one question")
    if len(words) > PROBE_MAX_WORDS:
        problems.append(f"{len(words)} words (at most {PROBE_MAX_WORDS})")
    hit = _NOT_NEUTRAL.search(probe)
    if hit:
        problems.append(f"not neutral: {hit.group(0)!r}")
    raw = _re.findall(r"[a-z0-9']+", finding.lower())
    spans = {" ".join(raw[i:i + _COPIED_WORDS]) for i in range(len(raw) - _COPIED_WORDS + 1)}
    text = " ".join(words)
    if any(span in text for span in spans):
        problems.append("copies the note's wording")
    return problems


async def refine_with_hr_findings(plan: InterviewBlueprint, findings, *, request=None,
                                  usage: list | None = None) -> InterviewBlueprint:
    """The plan refined with HR findings (C-124's call): each finding probed in one area
    with a neutral question, or left out as not technical; `hr_findings` records which.

    `findings` is an HRFindingsForTechnical. `request` stands in for the text-model call
    (shared.text_models.request_structured), so the smoke test runs it free (C-132).
    `usage`, when given, receives each call's cost record (job, model, tokens, usd) for
    the caller to store with the candidate (C-132). Raises RefinementError on failure:
    the booking then keeps the plan unchanged (C-124).
    """
    from shared.contracts import MAX_HR_FINDINGS, HRFindingsRefinement, HRFindingUse, question_ids_for

    # An HRFindingsForTechnical, or a plain list of sentences. No cap of its own: Track H
    # does not cap them (Karandeep); the only cap is the contract's MAX_HR_FINDINGS.
    raw_list = findings if isinstance(findings, (list, tuple)) else getattr(findings, "findings_for_technical", None)
    texts = [str(t).strip() for t in (raw_list or []) if t and str(t).strip()]
    if not texts:
        return plan
    request = request or request_structured
    areas = {p.competency_id: i for i, p in enumerate(plan.competency_plans)}
    plan_lines = "\n".join(f"- {p.competency_id}: {p.name}. Questions: " + " | ".join(p.seed_questions)
                           for p in plan.competency_plans)
    notes = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
    messages = [{"role": "system", "content": HR_SYSTEM},
                {"role": "user", "content": f"ROLE: {plan.evaluation_spec.role_title}\n\nAREAS:\n{plan_lines}\n\n"
                                            f"NOTES (index: text):\n{notes}"}]
    mapped: dict[int, _FindingMapping] = {}
    for attempt in (1, 2):
        try:
            result = await request(job="blueprint", messages=messages, response_model=_FindingsResponse,
                                   temperature=0.2)
        except TextModelError as exc:
            if usage is not None and getattr(exc, "record", None) is not None:
                usage.append(exc.record.to_dict())
            raise RefinementError(f"HR findings refinement failed: {exc}") from exc
        if usage is not None and getattr(result, "record", None) is not None and hasattr(result.record, "to_dict"):
            usage.append(result.record.to_dict())
        failed = []
        for raw in result.payload.get("findings") or []:
            m = _FindingMapping.model_validate(raw)
            if not 0 <= m.index < len(texts) or m.index in mapped:
                continue
            if m.outcome == "probed":
                problems = ([] if m.area_id in areas else [f"unknown area {m.area_id!r}"]) + \
                    _probe_problems(m.probe or "", texts[m.index])
                if problems:
                    failed.append((m.index, problems))
                    continue
            mapped[m.index] = m
        missing = [i for i in range(len(texts)) if i not in mapped and i not in {f[0] for f in failed}]
        if not failed and not missing:
            break
        if attempt == 1:
            fix = "\n".join(f"{i}: {', '.join(p)}" for i, p in failed) + \
                  "".join(f"\n{i}: no entry returned" for i in missing)
            messages = [*messages, {"role": "user", "content": "Redo only these notes; the rules were broken:\n" + fix}]
    # Still failing after the retry: left out (never weakened into a probe that is not neutral).
    for i in range(len(texts)):
        mapped.setdefault(i, _FindingMapping(index=i, outcome="not_technical"))

    plans = [p.model_copy(deep=True) for p in plan.competency_plans]
    uses = []
    inserted: dict[str, int] = {}
    for i, text in enumerate(texts):
        m = mapped[i]
        if m.outcome != "probed":
            uses.append({"finding": text, "outcome": "not_technical"})
            continue
        index = areas[m.area_id]
        target = plans[index]
        position = 1 + inserted.get(m.area_id, 0)      # after the area's first question
        target.seed_questions.insert(min(position, len(target.seed_questions)), m.probe.strip())
        inserted[m.area_id] = inserted.get(m.area_id, 0) + 1
        uses.append({"finding": text, "outcome": "probed", "area_id": m.area_id, "_index": index,
                     "_position": min(position, len(target.seed_questions) - 1), "_probe": m.probe.strip()})
    for i, p in enumerate(plans):
        p.question_ids = question_ids_for(i, len(p.seed_questions))
    # Ids follow position, so a probe's id is read after every insertion is done.
    for u in uses:
        if u["outcome"] == "probed":
            p = plans[u.pop("_index")]
            u["question_id"] = p.question_ids[p.seed_questions.index(u.pop("_probe"))]
            u.pop("_position")
    try:
        refinement = HRFindingsRefinement(findings=[HRFindingUse(**u) for u in uses], refined_at=datetime.now(UTC))
        data = plan.model_dump(mode="json")
        data["competency_plans"] = [p.model_dump(mode="json") for p in plans]
        data["hr_findings"] = refinement.model_dump(mode="json")
        return InterviewBlueprint.model_validate(data)
    except ValueError as exc:
        # Today: more findings than the contract's MAX_HR_FINDINGS (request to Session C
        # to lift it). The booking then keeps the plan unchanged (C-124).
        raise RefinementError(f"the refined plan does not fit the contract ({len(texts)} findings; "
                              f"contract cap {MAX_HR_FINDINGS}): {str(exc)[:300]}") from exc


# -- round 16: a GOAL per plan question (Karandeep, option b; C-168's question_goals) --------

#: The cost record's job label (Session D tells plan costs apart by it, C-158).
GOALS_JOB = "blueprint_goals"
GOAL_MAX_WORDS = 25
#: A goal this close to its question (Session D's d21 word ratio) would be read out.
GOAL_MAX_RATIO = 0.6


class _GoalItem(BaseModel):
    id: str = Field(description="The question's id, as given.")
    goal: str = Field(description="What to learn, in plain words; never a question.")


class _GoalsResponse(BaseModel):
    goals: list[_GoalItem]


GOALS_SYSTEM = """You prepare an interview plan for a live voice interviewer. For EACH plan question,
write a GOAL: what the interviewer should learn from the candidate, in plain everyday words.
Rules for every goal:
- Describe what to learn, starting with "Learn" (for example "Learn how they set a product strategy
  and what main bet it rested on"). It is never a question and never ends with "?".
- Keep exactly the question's meaning: the same thing to find out, nothing added, nothing dropped.
- Do not copy the question's wording: no run of five of its words, and no stiff or written phrasing.
  Keep a job-specific term only where an interviewer would naturally say it.
- One short sentence, at most 25 words.
- Never mention where a question came from (no HR, no earlier round, no feedback).
Return one goal per question id given."""


def goal_problems(goal: str, question: str) -> list[str]:
    """Checked in code: a goal the interviewer cannot read out as the question."""
    from difflib import SequenceMatcher

    problems = []
    words = _re.findall(r"[a-z0-9']+", goal.lower())
    q_words = _re.findall(r"[a-z0-9']+", question.lower())
    if not words:
        return ["empty"]
    if "?" in goal:
        problems.append("is a question")
    if len(words) > GOAL_MAX_WORDS:
        problems.append(f"{len(words)} words (at most {GOAL_MAX_WORDS})")
    runs = {" ".join(q_words[i:i + _COPIED_WORDS]) for i in range(len(q_words) - _COPIED_WORDS + 1)}
    if any(run in " ".join(words) for run in runs):
        problems.append("copies five or more of the question's words in a row")
    if SequenceMatcher(None, words, q_words).ratio() >= GOAL_MAX_RATIO:
        problems.append("too close to the question's wording")
    hit = _NOT_NEUTRAL.search(goal)
    if hit:
        problems.append(f"not neutral: {hit.group(0)!r}")
    return problems


async def write_goals(plan: InterviewBlueprint, *, request=None, usage: list | None = None,
                      report: dict | None = None) -> InterviewBlueprint:
    """The plan with a goal for every question (CompetencyPlan.question_goals), written by
    one model call per plan and checked in code; a failing goal is retried once, then
    left None (the bot then shows that question in round 15's form) and recorded in
    `report["fallbacks"]`. The questions, ids and areas are unchanged: the auditor and the
    report keep the original wording. Never raises: a failed call leaves every goal None."""
    request = request or request_structured
    report = report if report is not None else {}
    questions = {qid: q for p in plan.competency_plans for qid, q in p.numbered_questions()}
    if not questions:
        return plan
    lines = "\n".join(f"{qid}: {q}" for qid, q in questions.items())
    messages = [{"role": "system", "content": GOALS_SYSTEM},
                {"role": "user", "content": f"ROLE: {plan.evaluation_spec.role_title}\n\nQUESTIONS (id: question):\n{lines}"}]
    accepted: dict[str, str] = {}
    problems_seen: dict[str, list[str]] = {}
    for attempt in (1, 2):
        try:
            result = await request(job="blueprint", messages=messages, response_model=_GoalsResponse, temperature=0.3)
        except TextModelError as exc:
            if usage is not None and getattr(exc, "record", None) is not None:
                usage.append({**exc.record.to_dict(), "job": GOALS_JOB})
            report["error"] = f"goal writing failed: {str(exc)[:200]}"
            break
        if usage is not None and getattr(result, "record", None) is not None and hasattr(result.record, "to_dict"):
            usage.append({**result.record.to_dict(), "job": GOALS_JOB})
        failed = {}
        for raw in result.payload.get("goals") or []:
            item = _GoalItem.model_validate(raw)
            qid = item.id.strip()
            if qid not in questions or qid in accepted:
                continue
            problems = goal_problems(item.goal.strip(), questions[qid])
            if problems:
                failed[qid] = problems
            else:
                accepted[qid] = item.goal.strip()
        missing = [q for q in questions if q not in accepted and q not in failed]
        problems_seen.update(failed)
        if not failed and not missing:
            break
        if attempt == 1:
            fix = "\n".join(f"{q}: {', '.join(p)}" for q, p in failed.items())
            fix += "".join(f"\n{q}: no goal returned" for q in missing)
            messages = [*messages, {"role": "user", "content": "Rewrite only these goals; the rules were broken:\n" + fix}]
    report["fallbacks"] = {q: problems_seen.get(q, ["not written"]) for q in questions if q not in accepted}
    report["written"] = len(accepted)
    data = plan.model_dump(mode="json")
    for p in data["competency_plans"]:
        p["question_goals"] = [accepted.get(qid) for qid in p["question_ids"]]
    return InterviewBlueprint.model_validate(data)


# -- round 16: the plan sized to the booked length (A-98; C-172's not_planned) ---------------

#: About two exchanges per area at round 15's measured acceptance pace of 2.0 minutes an
#: exchange (A-96, A-98): a competency area needs about this many minutes.
MINUTES_PER_AREA = 4.0


def areas_that_fit(plan: InterviewBlueprint, booked_minutes: float) -> int:
    """Competency areas a booking of `booked_minutes` can hold: what is left after the
    opening and the closing (always kept), at MINUTES_PER_AREA each, at least one."""
    available = float(booked_minutes) - float(plan.opening_minutes) - float(plan.closing_minutes)
    return max(1, int(available // MINUTES_PER_AREA))


def size_to_booking(plan: InterviewBlueprint, booked_minutes: float, *, report: dict | None = None) -> InterviewBlueprint:
    """The plan with only the competency areas the booking can hold. Kept: the ones most
    central to the job (the employer's weight in the spec, set from the job description),
    an area holding an HR probe ahead of others, then plan order. Dropped areas are listed
    in `not_planned` (reason booked_length), so the report says "not planned for this
    length", not "not covered". Question ids follow position and are recomputed, with the
    HR record's ids. The time budgets of the kept areas are rescaled to the plan's length.
    A plan that fits is returned unchanged."""
    from shared.contracts import question_ids_for

    report = report if report is not None else {}
    fit = areas_that_fit(plan, booked_minutes)
    report.update(booked_minutes=booked_minutes, fits=fit, planned=len(plan.competency_plans))
    if len(plan.competency_plans) <= fit:
        report["dropped"] = []
        return plan
    weight = {c.id: c.weight for c in plan.evaluation_spec.competencies}
    probed = {u.area_id for u in (plan.hr_findings.findings if plan.hr_findings else []) if u.outcome == "probed"}
    order = sorted(range(len(plan.competency_plans)),
                   key=lambda i: (-(plan.competency_plans[i].competency_id in probed),
                                  -weight.get(plan.competency_plans[i].competency_id, 0.0), i))
    keep = sorted(order[:fit])
    dropped = [plan.competency_plans[i] for i in range(len(plan.competency_plans)) if i not in keep]
    data = plan.model_dump(mode="json")
    old_plans = data["competency_plans"]
    kept_plans = [old_plans[i] for i in keep]
    # Ids follow position: renumber, remembering old -> new for the HR record.
    renumber = {}
    for new_index, p in enumerate(kept_plans):
        new_ids = question_ids_for(new_index, len(p["seed_questions"]))
        renumber.update(dict(zip(p["question_ids"], new_ids)))
        p["question_ids"] = new_ids
    # Budgets: the kept areas share the competency time in their old proportions.
    available = float(data["evaluation_spec"]["duration_minutes"]) - data["opening_minutes"] - data["closing_minutes"]
    total = sum(p["time_budget_minutes"] for p in kept_plans) or 1.0
    for p in kept_plans:
        p["time_budget_minutes"] = round(available * p["time_budget_minutes"] / total, 2)
    data["competency_plans"] = kept_plans
    data["not_planned"] = [*(data.get("not_planned") or []),
                           *[{"competency_id": p.competency_id, "name": p.name, "reason": "booked_length"} for p in dropped]]
    if data.get("hr_findings"):
        for u in data["hr_findings"]["findings"]:
            if u.get("outcome") == "probed":
                if u["area_id"] in {p.competency_id for p in dropped}:
                    u.update(outcome="not_technical", area_id=None, question_id=None)
                else:
                    u["question_id"] = renumber.get(u["question_id"], u["question_id"])
    report["dropped"] = [p.competency_id for p in dropped]
    return InterviewBlueprint.model_validate(data)


async def refine_for_booking(plan: InterviewBlueprint, findings=None, *, booked_minutes: float | None = None,
                             request=None, usage: list | None = None, report: dict | None = None) -> InterviewBlueprint:
    """Everything done to a plan at booking, for EVERY plan (round 16): HR findings when
    there are any (refine_with_hr_findings), the plan sized to the booked length, then a
    goal for each question (write_goals). Never raises for goals (fallbacks are recorded);
    raises RefinementError only if the HR refinement fails, as before."""
    report = report if report is not None else {}
    if findings is not None and (getattr(findings, "findings_for_technical", None) or isinstance(findings, (list, tuple))):
        plan = await refine_with_hr_findings(plan, findings, request=request, usage=usage)
    if booked_minutes is not None:
        plan = size_to_booking(plan, booked_minutes, report=report.setdefault("sizing", {}))
    return await write_goals(plan, request=request, usage=usage, report=report.setdefault("goals", {}))
