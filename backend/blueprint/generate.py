"""Generate an InterviewBlueprint from a CV plus an EvaluationSpec.

**This is the product's IP.** A blueprint that reformats the JD into sections is
worthless — the employer could have written that. What makes a candidate-specific
interview worth conducting is that it targets *this* CV: the claims worth
testing, the gaps worth probing, the suspiciously round numbers, the two-year
jump with no explanation.

Reasoning tier (`OPENAI_BLUEPRINT_MODEL`), off the critical path, run once at CV
upload time and stored. Latency is irrelevant here.

**MITRA is a pre-screener** (product owner, 2026-11-20): the plan checks that the
CV holds up and that the candidate knows the basics of their field, and leaves
design, trade-offs and scenarios to later rounds with an expert. Before this
the prompt asked for "the trade-off, the failure, the number and how it was
measured", and live interviews went too deep for a first round.

The time budgeting is done in code rather than by the model. Models are poor at
arithmetic that has to sum exactly, and the contract validator rejects a
blueprint whose sections overrun the configured duration — so we let the model
decide *relative emphasis* and compute the minutes ourselves.
"""

import re
from dataclasses import dataclass

from loguru import logger
from openai import AsyncOpenAI
from pydantic import Field

from shared.branding import PROSE_STYLE

from shared.contracts import (
    ClaimToVerify,
    QuestionCut,
    CompetencyPlan,
    question_ids_for,
    EvaluationSpec,
    InterviewBlueprint,
    InterviewRegister,
    MAX_INTERVIEW_MINUTES,
)
from shared.text_models import TextModelError, pick, request_structured

#: Reserved so the interview can open and close like a conversation rather than
#: starting mid-interrogation. The opening is an exploratory conversation about their
#: work (Track H, 2026-11-20: a fixed 2 minutes made the interview feel compact), so it
#: grows with the interview: about 15% of it, at least 3 and at most 6 minutes.
OPENING_SHARE = 0.15
OPENING_MINUTES = 3.0
OPENING_MAX_MINUTES = 6.0
CLOSING_MINUTES = 2.0


def opening_minutes_for(duration_minutes: float) -> float:
    """The opening's minutes for an interview of this length, to the half minute."""
    minutes = round(float(duration_minutes) * OPENING_SHARE * 2) / 2
    return min(max(minutes, OPENING_MINUTES), OPENING_MAX_MINUTES)

#: Below this a section cannot reach any useful depth, so we would rather cover
#: fewer competencies properly than all of them badly.
MIN_SECTION_MINUTES = 3.0

SYSTEM = """\
You prepare a PRE-SCREEN interview for MITRA, a live voice interviewer. You are
given an evaluation spec (what the employer wants tested), the job description and
a candidate's CV.

What you write is two things at once: the interview plan, and MITRA's preparation.
MITRA cannot look anything up during the conversation. The ideas of the field, what
a good answer sounds like, how to pitch a question to this person: everything it
thinks with live comes from what you write here. Write it to help a good
interviewer think, not as a script: MITRA words every question itself and follows
the conversation.

WHAT A PRE-SCREEN IS
The first round, short, before any expert interview. It finds out:
1. What they have actually worked on in this field, and at what level.
2. Whether they know the basics of their field: the core ideas anyone who has
   really done this job knows, and can reason with them a little.
3. Whether the CV holds up: they can explain their own projects plainly (what it
   was, what THEY did, the tools or methods, roughly how it turned out).
4. Whether they meet what the employer said matters most (the spec's dealbreakers
   and must-haves), asked plainly.
It is NOT the place for design or architecture questions, deep trade-offs,
scaling, failure post-mortems, puzzles, edge cases, or several layers of "and
why". Those belong to later rounds with an expert. A clear, concrete answer at a
basic level passes.

THE SHAPE OF THE INTERVIEW
- The opening (`opening_goals`) is a conversation about their work, before any
  area. Broad and exploratory: what kind of work they have done in this field, what
  they know best, the tools and methods they use day to day, what they have been
  learning recently. It is how MITRA learns their level, so it can pitch every
  later question. Ask about their work and knowledge only: never their motivation,
  feelings or personality, and never anything personal.
- Each area then moves from broad to specific: first what kind of experience they
  have in it, then a basic idea of the field, then one light analytical question,
  then their CV. At most half of an area's questions name a specific CV item; the
  rest are about the area and the field.

ANALYTICAL QUESTIONS, THE MODERN WAY
A light analytical question asks them to think with what they know, in one step,
about a situation close to their own work: "how would you tell", "what would you
look at first", "how did you know". Answerable in about a minute by someone who has
done the job, with no whiteboard and no preparation. It shows how they think, which
a definition cannot. Examples of the LEVEL only, by field: never copy or adapt their
wording; write each question fresh from this candidate's work and this role:
- AI engineer (LLM applications): "How did you know the chatbot's answers were good
  enough to ship?", "If users said the answers had got worse, what would you check
  first?", "When would you use retrieval instead of putting everything in the prompt?"
- ML engineer: "How would you tell whether a new model is really better than the
  old one?", "What would you look at if a model did well in testing and poorly after
  release?", "Which metric did you pick for that classifier, and why that one?"
- Sales executive: "How do you decide which leads to call first?", "If your
  meetings were not turning into deals, what would you look at first?"
- Sales or business development manager: "How do you tell a real opportunity from a
  polite conversation?", "Which numbers in your pipeline do you watch every week?"
- Business analyst: "If a feature shipped and nobody used it, how would you find out
  why?", "How do you check that a requirement is clear enough before it goes to the
  developers?"
- Software engineer: "If an API you own got slow, where would you look first?",
  "How do you decide what to test in a change?"
- Product manager: "How would you know whether a feature you shipped worked?"
Basic-knowledge and CV questions sound like: "What did the chatbot you built at
Acme do?", "What was your own part in it?", "What is RAG, in a sentence or two?",
"What was your quota last year, and how did you do against it?", "What documents
did you write on that project?".

READ THE CV FOR
- What they have worked on, and the field's ideas their work relies on.
- Projects and claims worth hearing about: quantified results, scope, ownership.
  "Cut costs 40%" invites "what was that project, and what was your part in it?"
  and at most "roughly how was it measured?", never a cross-examination.
- Thin spots: a competency the spec cares about that the CV barely evidences.
  These need MORE time, not less: a broad question and a basic one show whether
  the knowledge is there.
- Ambiguities worth one plain question: unexplained gaps, very short tenures,
  titles that do not match the described scope, "we" doing all the work (ask what
  they did themselves).

Return JSON only:

{
  "candidate_name": "name from the CV, or null",
  "candidate_summary": "3-4 factual sentences. What they have done, scope, and
                        domain. No evaluation, no adjectives like 'strong'.",
  "claims_to_verify": [
    {"claim": "a specific, checkable assertion from the CV", "source": "cv"}
  ],
  "suggested_opening": "How to open. Two or three spoken sentences that greet
                        them, name the role, and END WITH ONE BROAD, EASY
                        QUESTION: how long they have worked in this area, or
                        what they are working on now. It must contain an actual
                        question. Do NOT reference their CV, their employers or
                        any specific project here.",
  "opening_goals": [
    "3 or 4 goals for the opening conversation, each starting with 'Learn', about
     THIS candidate's work and field: broad and exploratory, easiest first. For
     example 'Learn what kinds of AI applications they have built so far, and which
     they know best' or 'Learn which tools and methods they use day to day'. Never
     about motivation, feelings or personality. Never a question."
  ],
  "interviewing_guidance": [
    "Instructions to the interviewer for THIS pre-screen: what to listen for in
     this candidate's answers, which CV points need a plain check, how to pitch the
     areas to the level the opening shows. Include that a clear, concrete answer at
     a basic level is enough and the interviewer moves on; it does not drill into
     design, trade-offs or edge cases."
  ],
  "domain_language": {
    "domain": "one short phrase for the field, e.g. 'business analysis in
               retail banking' or 'backend engineering in fintech'",
    "vocabulary": [
      "Up to twelve terms this field actually uses, COPIED FROM the job
       description or the CV. Copy them exactly as they are written there.
       Do NOT invent plausible-sounding jargon: every term is checked against
       those documents and anything not found is discarded. An empty list is a
       perfectly good answer."
    ]
  },
  "competency_plans": [
    {
      "competency_id": "must match a competency id from the spec",
      "name": "...",
      "target_depth": "what a sufficient PRE-SCREEN answer looks like for this
                       competency: concrete, their own part, the basic idea
                       explained correctly. Say plainly that deep design or
                       trade-off detail is not expected.",
      "key_ideas": ["3 to 5 ideas of the field a good answer in this area rests
                     on, each one short line in plain words, so MITRA can tell a
                     correct basic answer from a vague one. Facts of the field,
                     never facts about the candidate."],
      "level_signals": "One or two sentences: how an answer sounds from someone
                        newer to this, and how it sounds from someone experienced.",
      "analytical_angles": ["1 or 2 light 'how would you think about it'
                             directions grounded in this candidate's own work,
                             each answerable in about a minute."],
      "emphasis": 1.0,
      "seed_questions": ["3 or 4 pre-screen questions, broad to specific"]
    }
  ]
}

Rules:
- Exactly one plan per competency in the spec. Same ids. No extras, none missing.
- `emphasis` is a relative weight from 0.5 to 2.0 for how much interview time
  this competency needs FOR THIS CANDIDATE. Use it: raise it where the CV is
  thin or the claims are big and unverified, lower it where the CV already
  evidences the competency heavily. Do not return 1.0 for everything.
- `seed_questions` are written for this candidate and this role. The first is
  broad, about the area: what kind of experience they have in it, so they choose
  their own example. Then a basic idea of the field, one light analytical
  question, and questions on their CV that name their projects, employers, tools
  and numbers. A question that would work unchanged for any candidate in any
  field is a wasted question.
- **Every seed question is answerable in about a minute by someone who really
  did the work**, without preparation. If answering well needs a whiteboard, a
  long story about a failure, or a design, it is too deep for a pre-screen: ask
  the basic version instead.
- **Each seed question must ask ONE thing.** One question mark is not enough:
  "Who disagreed, and what did it cost?" has one question mark and two asks, and
  so does "Tell me about a launch that slipped. What was the real cause?". The
  candidate answers the easier half and the harder one is lost. Split it and
  keep the better half. A reason attached to the same ask is still one ask:
  "What was the metric you held yourself to, and why that one?"
- **Order seed_questions from easiest to hardest**, in the shape above: broad,
  basic, analytical, CV detail. Never a trade-off analysis, a design or a failure
  post-mortem.
- `target_depth` and `level_signals` are calibrated to the seniority in the
  spec, at pre-screen level: a senior is expected to explain their work and the
  basics more fluently, not to be examined more deeply.
- Never invent facts about the candidate. If the CV does not say it, do not
  assert it.
- Do not include time budgets. Those are computed separately.
"""

SYSTEM = f"{SYSTEM}\n\n{PROSE_STYLE}\n"


class BlueprintGenerationError(RuntimeError):
    pass


# -- the reply's shape, for Structured Outputs ------------------------------------
# Exactly the JSON SYSTEM asks for. Fields are picked from the contract types, so
# the schema cannot drift from what build_blueprint() turns it into. The
# contract (InterviewBlueprint) is not changed: build_blueprint() still
# assembles it, adding the time budgets and the verified vocabulary.

PlanDraft = pick(
    CompetencyPlan, "competency_id", "name", "target_depth", "seed_questions",
    model_name="PlanDraft",
    emphasis=(float, Field(description="How heavily the CV evidences this competency.")),
)
#: What generation returns per area: the refinement shape plus the interviewer's
#: briefing (key ideas, level signals, analytical angles). Refinement keeps PlanDraft
#: and carries the briefing across, so a refinement never has to rewrite it.
GenerationPlanDraft = pick(
    CompetencyPlan, "competency_id", "name", "target_depth", "seed_questions",
    "key_ideas", "level_signals", "analytical_angles",
    model_name="GenerationPlanDraft",
    emphasis=(float, Field(description="How heavily the CV evidences this competency.")),
)
GenerationResponse = pick(
    InterviewBlueprint, "candidate_name", "candidate_summary", "claims_to_verify",
    "suggested_opening", "interviewing_guidance", "opening_goals",
    model_name="GenerationResponse",
    domain_language=(InterviewRegister, ...),
    competency_plans=(list[GenerationPlanDraft], ...),
)


@dataclass
class BlueprintGenerator:
    """Builds a blueprint from a CV and an evaluation spec."""

    api_key: str
    model: str

    def __post_init__(self):
        # Retries are the helper's (exactly one), not the SDK's.
        self._client = AsyncOpenAI(api_key=self.api_key, max_retries=0)
        self.last_usage = None
        #: The model's questions before the one-ask backstop, and how many it split.
        self.last_raw_questions: list[str] | None = None
        self.last_split_count = 0

    async def generate(
        self,
        *,
        blueprint_id: str,
        spec: EvaluationSpec,
        cv_text: str,
        jd_text: str = "",
    ) -> InterviewBlueprint:
        if spec.duration_minutes > MAX_INTERVIEW_MINUTES:
            # Before any model call: a new plan may not be longer than an
            # interview may be booked (shared/contracts.py). Old plans still load.
            raise BlueprintGenerationError(
                f"The spec asks for {spec.duration_minutes} minutes; an interview can be "
                f"at most {MAX_INTERVIEW_MINUTES}. Shorten it on the profile, then generate again."
            )
        user = (
            f"EVALUATION SPEC:\n{spec.model_dump_json(indent=2)}\n\n"
            f"CANDIDATE CV:\n{cv_text}"
        )

        try:
            result = await request_structured(
                job="blueprint",
                model=self.model,
                client=self._client,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                response_model=GenerationResponse,
                temperature=0.4,  # used only by non-reasoning models
            )
        except TextModelError as exc:
            self.last_usage = exc.record
            raise BlueprintGenerationError(f"Blueprint model call failed: {exc}") from exc
        self.last_usage = result.record
        payload = result.payload

        # The documents are the only authority on what this field calls
        # things, so verification reads them rather than the model's word.
        report: dict = {}
        blueprint = build_blueprint(
            blueprint_id=blueprint_id,
            spec=spec,
            payload=payload,
            source_text=f"{jd_text}\n{cv_text}\n{spec.model_dump_json()}",
            report=report,
        )
        self.last_raw_questions = report.get("raw_questions")
        self.last_split_count = report.get("split", 0)
        return blueprint


#: A cap on how much field vocabulary reaches the live prompt. The live context
#: is kept small on purpose, and an interviewer reciting thirty terms sounds
#: like it is showing off rather than listening.
MAX_VOCABULARY = 12


def verify_vocabulary(proposed: list, source_text: str) -> list[str]:
    """Keep only the terms the documents actually use.

    The same shape as quote verification in the feedback scorer, for the same
    reason. A model asked for the vocabulary of a field will happily supply
    plausible terms nobody in this particular workplace uses, and jargon used
    wrongly is far worse than plain language: a specialist hears one misused
    term and stops trusting the interview. Borrowing only what the JD and the CV
    already say makes that impossible rather than unlikely.
    """
    haystack = re.sub(r"[^a-z0-9]+", " ", (source_text or "").lower())
    kept: list[str] = []
    seen: set[str] = set()

    for raw in proposed or []:
        if not isinstance(raw, str):
            continue
        term = raw.strip()
        needle = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
        if not needle or needle in seen:
            continue
        # A trailing "s" is the same term. The JD says "produce BRDs" and the
        # model proposes "BRD"; refusing that would discard the field's most
        # characteristic word on a grammatical technicality. Kept deliberately
        # narrow: anything looser stops being verification.
        forms = {needle, f"{needle}s", needle[:-1] if needle.endswith("s") else needle}
        if not any(f" {form} " in f" {haystack} " for form in forms if form):
            logger.info(f"dropped invented vocabulary: {term!r}")
            continue
        seen.add(needle)
        kept.append(term)
        if len(kept) >= MAX_VOCABULARY:
            break
    return kept


def build_register(payload: dict, source_text: str) -> InterviewRegister | None:
    raw = payload.get("domain_language")
    if not isinstance(raw, dict):
        return None
    vocabulary = verify_vocabulary(raw.get("vocabulary"), source_text)
    domain = str(raw.get("domain") or "").strip()
    if not domain and not vocabulary:
        return None
    return InterviewRegister(domain=domain, vocabulary=vocabulary)


# -- one ask per question (A-34) ---------------------------------------------------------
#
# The same deterministic count as Session A's fixture test
# (tests/test_realtime_ending.py `asks`), copied rather than imported, because
# production code does not import tests. One question mark is not one ask: a
# ", and what/how/..." join and a second sentence each add one. A bare reason
# (", and why that one?") belongs to its ask.

_JOINED = re.compile(r",\s*and\s+(?:what|how|why|who|when|where|which|did|do|does|if)\b", re.I)
_BARE_REASON = re.compile(r",\s*and\s+why(?:\s+(?:that|this)\s+one)?\s*\?$", re.I)
_ASK_SENTENCE = re.compile(r"^(?:tell me|walk me|take me|talk me|describe|give me|share)\b", re.I)
_SENTENCES = re.compile(r"(?<=[.?!])\s+")


def asks(question: str) -> int:
    """How many things a question asks."""
    sentences = [x.strip() for x in _SENTENCES.split(question) if x.strip()]
    count = sum(1 for x in sentences if x.endswith("?") or _ASK_SENTENCE.match(x))
    return count + len(_JOINED.findall(question)) - len(_BARE_REASON.findall(question))


def first_ask(question: str) -> str:
    """The question cut to its first ask; unchanged if it is already one ask.

    A backstop for the prompt rule, not a writer: it keeps the first ask
    verbatim (the first ask sentence, or the text before the first ", and
    what..." join, closed with a question mark) and invents nothing.
    """
    q = question.strip()
    for _ in range(3):
        if asks(q) <= 1:
            return q
        sentences = [x.strip() for x in _SENTENCES.split(q) if x.strip()]
        first = next((x for x in sentences if x.endswith("?") or _ASK_SENTENCE.match(x)), None)
        if len(sentences) > 1 and first is not None:
            q = first
            continue
        join = _JOINED.search(q)
        if join is None:
            return q
        q = q[: join.start()].rstrip(" ,") + "?"
    return q


def build_blueprint(
    *,
    blueprint_id: str,
    spec: EvaluationSpec,
    payload: dict,
    source_text: str = "",
    report: dict | None = None,
    opening_minutes: float | None = None,
) -> InterviewBlueprint:
    """Assemble and validate a blueprint from the model's raw output.

    `opening_minutes`: the opening's length; by default its share of the spec's
    duration. A refinement passes the plan's own, so refining never re-times it.

    `report`, when given, receives the model's questions before the one-ask
    backstop ("raw_questions") and how many were cut to their first ask ("split").

    Separated from the API call so the assembly and budgeting logic is testable
    without a network round trip — which is where the arithmetic bugs would be.
    """
    raw_plans = payload.get("competency_plans") or []
    by_id = {p.get("competency_id"): p for p in raw_plans if isinstance(p, dict)}

    missing = [c.id for c in spec.competencies if c.id not in by_id]
    if missing:
        raise BlueprintGenerationError(
            f"Model omitted plans for competencies: {missing}. "
            "Every competency in the spec must be interviewed."
        )

    if opening_minutes is None:
        opening_minutes = opening_minutes_for(spec.duration_minutes)
    budgets = allocate_minutes(spec=spec, plans_by_id=by_id, opening_minutes=opening_minutes)

    plans = []
    raw_questions: list[str] = []
    cuts: list[QuestionCut] = []
    for index, competency in enumerate(spec.competencies):
        raw = by_id[competency.id]
        questions = [q for q in (raw.get("seed_questions") or []) if isinstance(q, str) and q.strip()]
        raw_questions += questions
        # The prompt asks for one ask per question; code makes sure (A-34).
        # Every cut is recorded, with the original, so a changed question is
        # visible: a log line now, and question_cuts on the plan for good.
        kept = [first_ask(q) for q in questions]
        ids = question_ids_for(index, len(questions)) if questions else []
        for qid, original, cut_to in zip(ids, questions, kept):
            if original != cut_to:
                cuts.append(QuestionCut(competency_id=competency.id, question_id=qid,
                                        original=original, cut_to=cut_to))
                logger.warning(f"plan {blueprint_id}: {qid} ({competency.id}) cut to its first ask: "
                               f"{original!r} -> {cut_to!r}")
        questions = kept
        if not questions:
            raise BlueprintGenerationError(
                f"Competency '{competency.id}' has no seed questions."
            )
        plans.append(
            CompetencyPlan(
                competency_id=competency.id,
                name=raw.get("name") or competency.name,
                target_depth=(raw.get("target_depth") or competency.description).strip(),
                seed_questions=questions,
                time_budget_minutes=budgets[competency.id],
                # Numbered here, in code, never by the model. Plans are in spec
                # order, which is the order the realtime prompt numbers them in.
                question_ids=ids,
                key_ideas=_lines(raw.get("key_ideas")),
                level_signals=(str(raw.get("level_signals") or "").strip() or None),
                analytical_angles=_lines(raw.get("analytical_angles")),
            )
        )

    split = len(cuts)
    if report is not None:
        report["raw_questions"], report["split"] = raw_questions, split

    claims = [
        ClaimToVerify(claim=item["claim"].strip(), source=item.get("source", "cv"))
        for item in (payload.get("claims_to_verify") or [])
        if isinstance(item, dict) and str(item.get("claim", "")).strip()
    ]

    try:
        return InterviewBlueprint(
            blueprint_id=blueprint_id,
            evaluation_spec=spec,
            candidate_name=(payload.get("candidate_name") or None),
            candidate_summary=(payload.get("candidate_summary") or None),
            claims_to_verify=claims,
            opening_minutes=opening_minutes,
            closing_minutes=CLOSING_MINUTES,
            competency_plans=plans,
            suggested_opening=(payload.get("suggested_opening") or "").strip()
            or f"Greet the candidate and explain this is an interview for the "
            f"{spec.role_title} role.",
            interviewing_guidance=[
                line.strip()
                for line in (payload.get("interviewing_guidance") or [])
                if isinstance(line, str) and line.strip()
            ],
            domain_language=build_register(payload, source_text),
            question_cuts=cuts,
            opening_goals=_lines(payload.get("opening_goals")),
        )
    except Exception as exc:  # noqa: BLE001
        raise BlueprintGenerationError(f"Assembled blueprint failed validation: {exc}") from exc


def _lines(value) -> list[str]:
    """A list of non-empty strings from whatever the model returned (absent: empty)."""
    return [str(v).strip() for v in (value or []) if isinstance(v, str) and v.strip()]


def allocate_minutes(
    *, spec: EvaluationSpec, plans_by_id: dict[str, dict], opening_minutes: float | None = None
) -> dict[str, float]:
    """Split the interview's competency time by employer weight and CV emphasis.

    Two inputs, deliberately: the employer's `weight` says what matters for the
    role; the model's `emphasis` says where this candidate needs more probing.
    Multiplying them means a heavily-weighted competency that the CV already
    evidences well yields time to a lighter one the CV says nothing about.

    Computed in code because the contract validator rejects budgets that overrun
    the configured duration, and models cannot be trusted to make a set of
    numbers sum exactly.
    """
    opening = opening_minutes_for(spec.duration_minutes) if opening_minutes is None else opening_minutes
    available = spec.duration_minutes - opening - CLOSING_MINUTES
    if available <= 0:
        raise BlueprintGenerationError(
            f"Duration {spec.duration_minutes} min leaves no room for competencies "
            f"after {opening + CLOSING_MINUTES} min of opening and closing."
        )

    scores: dict[str, float] = {}
    for competency in spec.competencies:
        raw = plans_by_id.get(competency.id, {})
        try:
            emphasis = float(raw.get("emphasis", 1.0))
        except (TypeError, ValueError):
            emphasis = 1.0
        emphasis = min(max(emphasis, 0.5), 2.0)
        scores[competency.id] = max(competency.weight, 1e-6) * emphasis

    from loguru import logger

    total = sum(scores.values())
    minutes = {cid: available * score / total for cid, score in scores.items()}

    # Every competency the employer asked for must get a usable slot. Pure
    # proportional allocation can starve a low-weight one — an observed run gave
    # a competency 1.0 minute, which is not an interview section, it is a
    # formality. Raise anything under the floor and take the difference from the
    # sections with the most to spare.
    floor = min(MIN_SECTION_MINUTES, available / len(scores))
    if sum(minutes.values()) and any(v < floor for v in minutes.values()):
        deficit = sum(floor - v for v in minutes.values() if v < floor)
        donors = {cid: v - floor for cid, v in minutes.items() if v > floor}
        donatable = sum(donors.values())
        if donatable > 0:
            for cid, spare in donors.items():
                minutes[cid] -= deficit * (spare / donatable)
        for cid, value in minutes.items():
            if value < floor:
                minutes[cid] = floor
        logger.info(
            f"Raised thin competencies to a {floor:.1f} min floor in a "
            f"{spec.duration_minutes} min interview."
        )

    # Round to half minutes for legibility, then settle the drift in half-minute
    # steps so the total lands exactly on `available`.
    #
    # Dumping the whole drift on one section — the previous approach — could
    # starve it: seven equal competencies in a 20 minute interview each rounded
    # up, and the correction took 1.5 minutes off a single section, leaving it
    # with 1.0. Take from the largest, give to the smallest.
    rounded = {cid: round(value * 2) / 2 for cid, value in minutes.items()}
    drift = round(available - sum(rounded.values()), 2)
    while abs(drift) >= 0.5:
        if drift < 0:
            target = max(rounded, key=lambda cid: rounded[cid])
            rounded[target] = round(rounded[target] - 0.5, 2)
            drift += 0.5
        else:
            target = min(rounded, key=lambda cid: rounded[cid])
            rounded[target] = round(rounded[target] + 0.5, 2)
            drift -= 0.5
    if drift:  # sub-half-minute remainder
        target = max(rounded, key=lambda cid: rounded[cid])
        rounded[target] = round(rounded[target] + drift, 2)

    if floor < MIN_SECTION_MINUTES:
        # The spec asks for more competencies than the clock can cover properly.
        # Say so rather than quietly running a shallow interview.
        logger.warning(
            f"{len(scores)} competencies in {spec.duration_minutes} min leaves only "
            f"{floor:.1f} min each — below the {MIN_SECTION_MINUTES} min needed for "
            "a useful answer. Consider fewer competencies or a longer interview."
        )
    return rounded
