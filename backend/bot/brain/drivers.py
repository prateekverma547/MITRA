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

from openai import AsyncOpenAI

from bot.brain.brain import Judgment, JudgmentRequest, TurnPlan
from shared.contracts import Contradiction, CoverageLevel, KeyClaim

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
  ]
}

Rules:
- "sufficient" only if the answers actually meet the stated target depth. A
  fluent answer that never names a concrete decision is "partial" at best.
- Claims must be things the candidate actually said, phrased tightly enough to
  quote back to them. Never invent one. Prefer specifics over summaries.
- If there is nothing to report for a field, return an empty list.

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


@dataclass
class OpenAIInterviewer:
    """Generates the interviewer's next line from a TurnPlan."""

    api_key: str
    model: str
    temperature: float = 0.6
    calls: int = field(default=0, init=False)

    def __post_init__(self):
        self._client = AsyncOpenAI(api_key=self.api_key)

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
            temperature=self.temperature,
            max_tokens=200,
        )
        return (response.choices[0].message.content or "").strip()


@dataclass
class OpenAIJudge:
    """Assesses depth, extracts claims, and spots contradictions."""

    api_key: str
    model: str
    calls: int = field(default=0, init=False)

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
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception:
            # A judge failure must never derail an interview — the brain simply
            # falls back to heuristics for this section.
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

        return Judgment(
            section_id=request.section_id,
            coverage=coverage,
            rationale=str(payload.get("rationale", "")).strip() or None,
            claims=claims,
            contradictions=contradictions,
        )
