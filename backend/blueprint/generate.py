"""Generate an InterviewBlueprint from a CV plus an EvaluationSpec.

**This is the product's IP.** A blueprint that reformats the JD into sections is
worthless — the employer could have written that. What makes a candidate-specific
interview worth conducting is that it targets *this* CV: the claims worth
testing, the gaps worth probing, the suspiciously round numbers, the two-year
jump with no explanation.

Reasoning tier (`OPENAI_BLUEPRINT_MODEL`), off the critical path, run once at CV
upload time and stored. Latency is irrelevant here; depth is not.

The time budgeting is done in code rather than by the model. Models are poor at
arithmetic that has to sum exactly, and the contract validator rejects a
blueprint whose sections overrun the configured duration — so we let the model
decide *relative emphasis* and compute the minutes ourselves.
"""

import json
import re
from dataclasses import dataclass

from loguru import logger
from openai import AsyncOpenAI

from shared.branding import PROSE_STYLE

from shared.contracts import (
    ClaimToVerify,
    CompetencyPlan,
    EvaluationSpec,
    InterviewBlueprint,
    InterviewRegister,
)

#: Reserved so the interview can open and close like a conversation rather than
#: starting mid-interrogation.
OPENING_MINUTES = 2.0
CLOSING_MINUTES = 2.0

#: Below this a section cannot reach any useful depth, so we would rather cover
#: fewer competencies properly than all of them badly.
MIN_SECTION_MINUTES = 3.0

SYSTEM = """\
You design a candidate-specific interview plan. You are given an evaluation spec
(what the employer wants tested) and a candidate's CV.

A generic plan is a failure. Anyone can ask "tell me about a time you
prioritised". Your job is to design questions that could only be asked of THIS
candidate, against THIS role.

Read the CV for:
- Specific claims worth verifying: quantified results, scope, ownership,
  leadership. "Cut costs 40%" invites "measured how, over what period, and what
  else changed?"
- Thin spots: a competency the spec cares about that the CV barely evidences.
  These need MORE time, not less — absence of evidence is what the interview is
  for.
- Ambiguities: unexplained gaps, very short tenures, titles that do not match
  the described scope, "we" doing all the work.
- Seniority mismatch: where the CV's claimed level and its described
  responsibilities disagree.

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
                        QUESTION — how long they have worked in this area, or
                        what they are working on now. It must contain an actual
                        question. Do NOT reference their CV, their employers or
                        any specific project here: this is a warm-up, and
                        opening with a deep question about a named employer
                        lands badly.",
  "interviewing_guidance": [
    "Instructions to the interviewer, calibrated to this candidate's seniority
     and this CV's particular weak points."
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
      "target_depth": "what a sufficient answer looks like AT THIS SENIORITY",
      "emphasis": 1.0,
      "seed_questions": ["3 or 4 questions written against this CV"]
    }
  ]
}

Rules:
- Exactly one plan per competency in the spec. Same ids. No extras, none missing.
- `emphasis` is a relative weight from 0.5 to 2.0 for how much interview time
  this competency needs FOR THIS CANDIDATE. Use it: raise it where the CV is
  thin or the claims are big and unverified, lower it where the CV already
  evidences the competency heavily. Do not return 1.0 for everything.
- `seed_questions` must reference this candidate's actual experience wherever
  possible. Name their products, employers, numbers. A question that would work
  unchanged for any candidate is a wasted question.
- **Each seed question must be ONE question containing exactly one question
  mark.** "Walk me through the product you led? What was your role?" is two
  questions; the candidate answers the easier one and the harder one is lost.
  Split it and keep the better half.
- **Order seed_questions from easiest to hardest.** The first must be broad
  enough for the candidate to choose their own example — the interviewer opens
  a topic with it, and leading with "how did you measure the model and what
  trade-offs did you make" gives them nowhere to stand. Later questions carry
  the demanding parts: the trade-off, the failure, the number and how it was
  measured.
- `target_depth` must be calibrated to the seniority in the spec.
- Never invent facts about the candidate. If the CV does not say it, do not
  assert it.
- Do not include time budgets. Those are computed separately.
"""

SYSTEM = f"{SYSTEM}\n\n{PROSE_STYLE}\n"


class BlueprintGenerationError(RuntimeError):
    pass


@dataclass
class BlueprintGenerator:
    """Builds a blueprint from a CV and an evaluation spec."""

    api_key: str
    model: str

    def __post_init__(self):
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def generate(
        self,
        *,
        blueprint_id: str,
        spec: EvaluationSpec,
        cv_text: str,
        jd_text: str = "",
    ) -> InterviewBlueprint:
        user = (
            f"EVALUATION SPEC:\n{spec.model_dump_json(indent=2)}\n\n"
            f"CANDIDATE CV:\n{cv_text}"
        )

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.4,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            raise BlueprintGenerationError(f"Blueprint model call failed: {exc}") from exc

        # The documents are the only authority on what this field calls
        # things, so verification reads them rather than the model's word.
        return build_blueprint(
            blueprint_id=blueprint_id,
            spec=spec,
            payload=payload,
            source_text=f"{jd_text}\n{cv_text}\n{spec.model_dump_json()}",
        )


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


def build_blueprint(
    *,
    blueprint_id: str,
    spec: EvaluationSpec,
    payload: dict,
    source_text: str = "",
) -> InterviewBlueprint:
    """Assemble and validate a blueprint from the model's raw output.

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

    budgets = allocate_minutes(spec=spec, plans_by_id=by_id)

    plans = []
    for competency in spec.competencies:
        raw = by_id[competency.id]
        questions = [q for q in (raw.get("seed_questions") or []) if isinstance(q, str) and q.strip()]
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
            )
        )

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
            opening_minutes=OPENING_MINUTES,
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
        )
    except Exception as exc:  # noqa: BLE001
        raise BlueprintGenerationError(f"Assembled blueprint failed validation: {exc}") from exc


def allocate_minutes(
    *, spec: EvaluationSpec, plans_by_id: dict[str, dict]
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
    available = spec.duration_minutes - OPENING_MINUTES - CLOSING_MINUTES
    if available <= 0:
        raise BlueprintGenerationError(
            f"Duration {spec.duration_minutes} min leaves no room for competencies "
            f"after {OPENING_MINUTES + CLOSING_MINUTES} min of opening and closing."
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
