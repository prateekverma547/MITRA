"""Real-LLM drivers for the text-mode harness.

Two roles, deliberately separate:

- `OpenAIInterviewer` turns a `TurnPlan` into the next spoken line. In text mode
  this is a single non-streaming call. **In production this path is not used** —
  generation stays in the streaming Pipecat pipeline, because a non-streaming
  call cannot hand the first token to TTS early. Same brain, different driver.
- `OpenAIJudge` fulfils the brain's off-path judgement requests: coverage,
  claims, contradictions.

Model choice follows the tiering policy in CLAUDE.md. The judge is off the
critical path, so it may use a stronger model than the live conversation.

Nothing here imports Pipecat.
"""

import json
from dataclasses import dataclass, field

from loguru import logger
from openai import AsyncOpenAI

from bot.brain.brain import Judgment, JudgmentRequest, TurnPlan
from shared.contracts import Contradiction, CoverageLevel, KeyClaim, UnexplainedTerm

JUDGE_SYSTEM = """\
You assess one section of a job interview. You are not talking to the candidate.

You will be given the target depth for the section, the section's transcript, and
claims the candidate made earlier in the interview.

Return JSON only, matching this shape:

{
  "coverage": "insufficient" | "partial" | "sufficient",
  "rationale": "one sentence on why",
  "claims": ["a specific, checkable thing the candidate asserted", ...],
  "contradictions": [
    {"earlier_claim": "...", "later_statement": "...", "why": "..."}
  ],
  "unexplained_terms": [
    {"term": "the exact words they used", "said": "the sentence they said it in"}
  ]
}

Rules:
- "sufficient" only if the answers actually meet the stated target depth. A
  fluent answer that never names a concrete decision is "partial" at best.
- Claims must be things the candidate actually said, phrased tightly enough to
  quote back to them. Never invent one. Prefer specifics over summaries.
- If there is nothing to report for a field, return an empty list.

UNEXPLAINED TERMS — TWO TESTS, IN ORDER, AND THE FIRST ONE IS NEW

A term earns its way onto this list. It is not put there by being undefined.

STEP 1. IS THE CLAIM STANDING ON THIS WORD?

Take what the candidate is claiming around the term and ask one question: if I
do not know what this word means, can I still judge that claim?

  If YES, the word is a detail inside a claim that stands without it. LEAVE IT
  OUT, however technical it sounds and however undefined it is.

  Only if NO does it go to step 2.

This DOES stand on the word, so it goes on to step 2:
  "I have introduced the layer of a session management concurrency, which
   allows the multiple users to make the payment at the same time."
The entire claim is that this thing solved the problem. Not knowing what it is
leaves nothing to assess. The word is carrying the claim.

This does NOT, so it is left out:
  "We trained it with bf16 LoRA on a SageMaker ml.g7e.2xlarge, on a
   Mistral-format jinja template."
The claim is about the training setup they chose and why. Whether or not you
know what bf16 is, that claim is equally established or equally not. The word is
a detail inside it.

**This is not a rule about who the candidate is.** A senior person who cannot
explain a word their own claim rests on is exactly the case worth catching, and
seniority never removes a term from this list. The test is what the claim needs,
never what the speaker ought to know.

**Nor is it a list of common words.** Any term can pass or fail depending on the
sentence it is in. "Fine tuning" is a detail in "we fine-tuned it on our own
pairs" and is load-bearing in "fine tuning is how we fixed the accuracy
problem", where nothing else accounts for the fix.

**And the section decides what is needed.** You are given the target depth. If
this section assesses how somebody explains things to non-technical people, and
they have described how they explained something, then the mechanics of the term
are not what is being assessed here. Leave it out.

STEP 2. DID THEY SAY WHAT IT DOES?

Look for the sentence where they say what the thing DOES. Anywhere in this
transcript, not only in their latest answer. It does not have to be correct,
complete or well put. It has to describe what happens: an input, a step, a
check, or a consequence that follows.
  If you find such a sentence, the term is EXPLAINED. Leave it out.

STEP 3. Only a term that reached step 2 and failed it goes on the list.

**A restatement is not an explanation, and this distinction is the whole point.**
These do NOT pass step 1, so the term stays on the list:
- Naming the effect without the mechanism: "it lets several people do it at the
  same time".
- Defining the term with itself: "concurrency means things happen concurrently".
- Attributing it to an unexplained thing: "the layer handles that".

This DOES pass step 1, so the term comes off the list: "every request gets a
unique id, and we check that id before processing, so a repeat cannot go through
twice." It names an input, a check and a consequence. It is rough and it is an
explanation.

Do NOT report:
- A tool or product named as a choice. "We used Postgres", "we run on
  Kubernetes". Naming what you used is not claiming to have explained a
  mechanism.
- Ordinary words of the field used in passing, where nothing rests on them.

**Your list is REPLACED each time you are asked, never added to.** It is not a
running tally of everything technical that has been said. A term you reported
last turn and that has since been explained must be absent now: that absence is
the only way the interviewer learns it can stop asking, and a list that only
grows means it never stops.

Order them with the one the interviewer has already asked about first.

CONTRADICTIONS — BE STRICT

Report one only when the two statements CANNOT BOTH BE TRUE as stated. Apply
that test literally. Measured precision without these exclusions was 0.50.

The following are NOT contradictions. Never report them:

- Hedges and non-answers. "I'd have to think about it", "I can't remember the
  exact numbers." Saying nothing conflicts with nothing.
- Qualified or uncertain agreement. "I think it helped, though I couldn't swear
  the pricing change alone caused it." Caution about causation is not a
  reversal; it is usually a sign of a careful thinker.
- Openly revised or softened views. "Thinking about it more, I'm less sure it
  was clearly right." A candidate updating their view in front of you is
  candour. Reporting it as a contradiction punishes honesty.
- Explicit self-corrections. "Sorry, I misspoke earlier, it was the fourth
  quarter." Someone correcting a detail is being accurate, not inconsistent.
- Added nuance that complicates the earlier claim without negating it. "To be
  fair, my director pushed back hard and I only held that line after we agreed
  a review date."
- Vagueness or deflection. "It was really a team effort." Evasive, but not a
  claim that conflicts with anything.

A genuine contradiction looks like: "I owned that decision and made the final
call myself" against "it was forced on us by the CFO and I lost that argument."
Both cannot describe the same decision.

When in doubt, do not report it. A false contradiction becomes a written claim
about a real person's honesty, shown to the people deciding whether to hire
them. A missed one can still be caught by the human reading the transcript.
"""


#: Reasoning models must be pinned before they go anywhere near a conversation.
#:
#: DECISIONS.md forbids a reasoning model on the live path, and the rule stands.
#: What it protects against is a model that thinks for a variable and
#: occasionally long time before speaking, which a candidate cannot tell apart
#: from a failure. That is behaviour, not a name, so a GPT-5 model pinned to no
#: reasoning at all is testable against the same bar as any other.
#:
#: The settings differ by generation and were probed rather than assumed:
#: gpt-5-mini and gpt-5-nano take "minimal" and reject "none"; gpt-5.4 and
#: gpt-5.6 take "none" and reject "minimal". Anything that cannot be pinned is
#: excluded rather than tested.
LOWEST_EFFORT = {
    "gpt-5-mini": "minimal",
    "gpt-5-nano": "minimal",
    "gpt-5.4-mini": "none",
    "gpt-5.4-nano": "none",
    "gpt-5.6-luna": "none",
}


@dataclass
class OpenAIInterviewer:
    """Generates the interviewer's next line from a TurnPlan."""

    # repr=False: the generated __repr__ would print the key into any log,
    # traceback or test failure that shows this object. Found when a failing
    # behaviour test printed the judge and wrote the key to its output.
    api_key: str = field(repr=False)
    model: str
    temperature: float = 0.6
    calls: int = field(default=0, init=False)
    prompt_tokens: int = field(default=0, init=False)
    completion_tokens: int = field(default=0, init=False)
    cached_tokens: int = field(default=0, init=False)

    def __post_init__(self):
        self._client = AsyncOpenAI(api_key=self.api_key)

    def _tuning(self) -> dict:
        effort = LOWEST_EFFORT.get(self.model)
        if effort is not None:
            # These reject `temperature` outright.
            return {"reasoning_effort": effort}
        return {"temperature": self.temperature}

    def _record(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_tokens or 0
        self.completion_tokens += usage.completion_tokens or 0
        details = getattr(usage, "prompt_tokens_details", None)
        self.cached_tokens += getattr(details, "cached_tokens", 0) or 0

    async def respond(self, plan: TurnPlan) -> str:
        self.calls += 1
        messages = [{"role": "system", "content": plan.system_instruction}]
        messages.extend(plan.messages)
        if not plan.messages:
            messages.append(
                {
                    "role": "user",
                    "content": "(The candidate has just joined and is waiting for you.)"
                    if plan.section_id == "opening"
                    else "(Begin this part of the interview.)",
                }
            )

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_completion_tokens=400,
            **self._tuning(),
        )
        self._record(response)
        return (response.choices[0].message.content or "").strip()


@dataclass
class OpenAIJudge:
    """Assesses depth, extracts claims, and spots contradictions."""

    api_key: str = field(repr=False)  # see OpenAIInterviewer
    model: str
    calls: int = field(default=0, init=False)
    prompt_tokens: int = field(default=0, init=False)
    completion_tokens: int = field(default=0, init=False)
    cached_tokens: int = field(default=0, init=False)

    def __post_init__(self):
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def assess(self, request: JudgmentRequest) -> Judgment | None:
        self.calls += 1

        transcript = "\n".join(
            f"{'INTERVIEWER' if t.speaker == 'interviewer' else 'CANDIDATE'}: {t.text}"
            for t in request.transcript
        )
        earlier = "\n".join(f"- {c.text}" for c in request.carried_claims) or "(none yet)"

        user = (
            f"TARGET DEPTH FOR THIS SECTION:\n{request.target_depth}\n\n"
            f"CLAIMS FROM EARLIER SECTIONS:\n{earlier}\n\n"
            f"THIS SECTION'S TRANSCRIPT:\n{transcript}"
        )

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=700,
            )
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.prompt_tokens += usage.prompt_tokens or 0
                self.completion_tokens += usage.completion_tokens or 0
                details = getattr(usage, "prompt_tokens_details", None)
                self.cached_tokens += getattr(details, "cached_tokens", 0) or 0
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            # A judge failure must never derail an interview — the brain simply
            # falls back to heuristics for this section.
            #
            # It must not be *invisible* either, which it was. Every judgement
            # failing looks identical to a candidate who never said anything
            # specific: no claims extracted, nothing carried between sections,
            # no contradictions. The model name is logged because the likeliest
            # cause is a bad OPENAI_BLUEPRINT_MODEL, whose name suggests it only
            # affects offline work.
            logger.warning(
                f"judgement failed for section {request.section_id!r} "
                f"on model {self.model!r}: {type(exc).__name__}: {exc}"
            )
            return None

        return self._to_judgment(request, payload)

    def _to_judgment(self, request: JudgmentRequest, payload: dict) -> Judgment:
        last_index = request.transcript[-1].index if request.transcript else 0

        coverage = None
        raw_coverage = str(payload.get("coverage", "")).lower().strip()
        if raw_coverage in {level.value for level in CoverageLevel}:
            coverage = CoverageLevel(raw_coverage)

        claims = [
            KeyClaim(text=text.strip(), section_id=request.section_id, turn_index=last_index)
            for text in payload.get("claims", [])
            if isinstance(text, str) and text.strip()
        ]

        earlier_by_text = {c.text: c for c in request.carried_claims}
        contradictions = []
        for item in payload.get("contradictions", []):
            if not isinstance(item, dict):
                continue
            earlier_claim = str(item.get("earlier_claim", "")).strip()
            later = str(item.get("later_statement", "")).strip()
            if not earlier_claim or not later:
                continue
            source = earlier_by_text.get(earlier_claim)
            contradictions.append(
                Contradiction(
                    earlier_claim=earlier_claim,
                    earlier_section_id=source.section_id if source else "unknown",
                    later_statement=later,
                    section_id=request.section_id,
                    turn_index=last_index,
                )
            )

        terms = []
        for item in payload.get("unexplained_terms", []):
            if not isinstance(item, dict):
                continue
            term = str(item.get("term", "")).strip()
            if not term:
                continue
            terms.append(
                UnexplainedTerm(
                    term=term,
                    said=str(item.get("said", "")).strip(),
                    section_id=request.section_id,
                    turn_index=last_index,
                )
            )

        return Judgment(
            section_id=request.section_id,
            coverage=coverage,
            rationale=str(payload.get("rationale", "")).strip() or None,
            claims=claims,
            contradictions=contradictions,
            unexplained_terms=terms,
        )
