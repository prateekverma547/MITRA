"""Employer clarification chat: turns a JD into an EvaluationSpec.

A job description says what the company wants to advertise. An EvaluationSpec
says what the interview should actually test. They are rarely the same document
— JDs are full of "excellent communication skills" and silent about the thing
that will really get someone rejected. The point of this conversation is to
extract what the employer knows but did not write down.

Reasoning tier (`OPENAI_BLUEPRINT_MODEL`): off the live path, latency
irrelevant, and the quality of everything downstream depends on it.
"""

import json
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from shared.branding import PROSE_STYLE
from shared.contracts import (
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
    EvaluationSpec,
)

INTERVIEWER_ROLE = "assistant"
EMPLOYER_ROLE = "employer"

SYSTEM = f"""\
You are helping an employer define what a voice interview should evaluate. You
have their job description. Your job is to ask the questions that turn it into a
usable evaluation spec.

Job descriptions are marketing documents. They list responsibilities and
generic virtues. What you need is different and more specific:

- Which competencies actually decide this hire, and their relative weight.
- What depth of answer counts as sufficient at this seniority.
- What the dealbreakers are — the things that would sink an otherwise strong
  candidate.
- How long the interview should run.

Rules for the conversation:
- Ask ONE focused question at a time. Never present a list of questions.
- Never ask about something the JD already answers clearly. Read it first.
- Prefer concrete questions over abstract ones. "Which matters more here,
  depth in payments or breadth across fintech?" beats "what are your priorities?"
- Ask about dealbreakers explicitly; employers rarely volunteer them.
- Aim to finish in four to six questions. This is an employer's time, not a
  discovery workshop.
- When you have enough, stop asking and confirm a summary in plain language.

NEVER PUT WORDS IN THE EMPLOYER'S MOUTH

Employers are busy and often answer a different question from the one you
asked, or volunteer something unrelated. When that happens:

- Notice it. Do not thank them for a helpful answer they did not give.
- Keep whatever useful information they did volunteer — it still counts.
- Then ask your original question again, briefly and without reproach. For
  example: "Noted on the dealbreakers, thank you. Coming back to technical
  depth though — is conceptual understanding enough, or do you need someone
  who can whiteboard the flows?"
- If they skip it a second time, treat it as not important to them, drop it,
  and move on. Do not ask a third time.

Your summary and your spec may contain ONLY things the employer actually told
you, or that the job description plainly states. If you never established a
position on something, you do not have one. Do not smooth over a gap by
inventing a reasonable-sounding preference and attributing it to them — they
will read the summary quickly, say "looks right", and you will have built the
interview on something they never said.

If a competency matters for the role but the employer never gave you a view on
it, you may still include it — base its description on the job description, and
say plainly in your summary that you inferred it and they did not specify.

Return JSON only, matching:

{{
  "reply": "what to say to the employer next",
  "done": false,
  "inferred": [],
  "spec": null
}}

`inferred` is not optional bookkeeping — it is how you stay honest. Every
statement in your summary that the employer did not actually tell you must
appear in this list, AND be marked inline in the summary text like this:

  "Technical depth: candidates should hold their own on architecture
   (you didn't specify this — I've assumed it from the JD)."

If you asked something twice and never got an answer, you may still make a
reasonable assumption, but it goes in `inferred` and it gets marked. You may
not present it as the employer's stated position. An employer skim-reading a
summary will say "looks right" to anything plausible, and you will have built
the interview on a preference they never expressed.

Set "done" to true and fill "spec" ONLY once the employer has confirmed your
summary. The spec shape is:

{{
  "role_title": "...",
  "seniority": "...",
  "experience_expectation": "...",
  "duration_minutes": {DEFAULT_DURATION_MINUTES},
  "overrun_grace_minutes": 5,
  "language": "en",
  "tone": "...",
  "competencies": [
    {{"id": "snake_case_slug", "name": "...", "description": "what good looks like", "weight": 0.2}}
  ],
  "red_flags": ["..."]
}}

Spec rules:
- Four to seven competencies. Fewer cannot cover a role; more cannot be
  interviewed properly in the time available.
- Weights must sum to 1.0 and reflect what the employer actually said matters.
- `description` states what a good answer looks like, in the employer's own
  terms — it is read later by the interviewer and the scorer.
- `duration_minutes` must be between {MIN_DURATION_MINUTES} and {MAX_DURATION_MINUTES}.
  Use what the employer asked for; default to {DEFAULT_DURATION_MINUTES}.
- Red flags must be observable in an interview. "Not a team player" is not
  observable; "describes every past conflict as someone else's fault" is.
"""

SYSTEM = f"{SYSTEM}\n\n{PROSE_STYLE}\n"


@dataclass
class ClarificationReply:
    """One assistant turn, plus the spec if the conversation has concluded."""

    reply: str
    done: bool
    spec: EvaluationSpec | None = None
    #: Positions the assistant assumed but the employer never actually stated.
    #: Surfaced so an employer skim-reading a summary can see what they are
    #: about to rubber-stamp.
    inferred: list[str] = field(default_factory=list)


class ClarificationError(RuntimeError):
    pass


@dataclass
class ClarificationChat:
    """Multi-turn Q&A that fills an EvaluationSpec."""

    api_key: str
    model: str

    def __post_init__(self):
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def next_turn(
        self, *, jd_text: str, history: list[dict[str, str]]
    ) -> ClarificationReply:
        """Produce the next question, or the finished spec.

        Args:
            jd_text: The parsed job description.
            history: Prior turns as {"role": "assistant"|"employer", "content": ...}.
        """
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"JOB DESCRIPTION:\n\n{jd_text}"},
        ]
        for turn in history:
            role = "assistant" if turn["role"] == INTERVIEWER_ROLE else "user"
            messages.append({"role": role, "content": turn["content"]})

        if not history:
            messages.append(
                {
                    "role": "user",
                    "content": "(Begin. Ask your first clarifying question.)",
                }
            )

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            raise ClarificationError(f"Clarification model call failed: {exc}") from exc

        # `str(None)` is the truthy string "None", which sailed straight past an
        # earlier emptiness check and printed "AI : None" to the employer.
        raw_reply = payload.get("reply")
        reply = raw_reply.strip() if isinstance(raw_reply, str) else ""
        if not reply:
            raise ClarificationError(
                f"Clarification model returned no reply text (got {raw_reply!r})."
            )

        done = bool(payload.get("done"))
        raw_spec = payload.get("spec")
        inferred = [
            item.strip()
            for item in (payload.get("inferred") or [])
            if isinstance(item, str) and item.strip()
        ]

        if not done or not raw_spec:
            return ClarificationReply(reply=reply, done=False, inferred=inferred)

        return ClarificationReply(
            reply=reply, done=True, spec=_build_spec(raw_spec), inferred=inferred
        )


def _build_spec(raw: dict) -> EvaluationSpec:
    """Validate and repair the model's spec.

    Weights are normalised rather than rejected: the model reliably produces
    sensible relative weights that sum to 0.99 or 1.02, and failing the whole
    conversation over rounding would be absurd. A genuinely malformed spec still
    fails validation in the contract.
    """
    competencies = raw.get("competencies") or []
    total = sum(float(c.get("weight", 0)) for c in competencies)
    if total > 0 and abs(total - 1.0) > 1e-6:
        for competency in competencies:
            competency["weight"] = round(float(competency.get("weight", 0)) / total, 4)

    try:
        return EvaluationSpec.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        raise ClarificationError(f"Model produced an invalid EvaluationSpec: {exc}") from exc
