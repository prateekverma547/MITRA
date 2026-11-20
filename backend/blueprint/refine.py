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

from shared.branding import PROSE_STYLE
from shared.contracts import EvaluationSpec, InterviewBlueprint

from blueprint.generate import BlueprintGenerationError, build_blueprint

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
- Every seed question is ONE question with exactly one question mark.
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
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def refine(
        self,
        *,
        blueprint: InterviewBlueprint,
        cv_text: str,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> RefinedBlueprint:
        spec: EvaluationSpec = blueprint.evaluation_spec

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
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            raise RefinementError(f"Refinement model call failed: {exc}") from exc

        reply = str(payload.get("reply", "")).strip() or "Updated the plan."

        # The gate: a revision that cannot be interviewed is not applied.
        try:
            revised = build_blueprint(
                blueprint_id=blueprint.blueprint_id,
                spec=spec,
                payload=payload,
                source_text=cv_text,
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

        return RefinedBlueprint(blueprint=revised, reply=reply)


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
