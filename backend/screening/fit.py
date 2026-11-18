"""Screen a CV against a job: ratings with quotes, then the ten HR questions.

The model does two narrow things: it rates each competency on a fixed 0-4
scale, quoting the CV for each rating, and it writes questions. Everything
else is code:

- **Quotes are verified** against the redacted CV text. A quote that is not
  in the CV is dropped. A rating left with no verified quote becomes "no
  evidence" (None), never a number nobody can point to. This is the same rule
  report quotes follow (feedback/score.py), for the same reason: an invented
  line in a hiring record is the worst failure this product has.
- **The percentage is computed** by `shared.screening.compute_fit` from the
  ratings and the BU head's weights. The model is never asked for it.
- **The model sees the redacted CV only** (screening/redact.py).

Model slot: `OPENAI_SCREENING_MODEL`, defaulting to the blueprint model until
the shared text-model helper lands (docs/DEV_PLAN.md section 3).
"""

import json
import os
import re
from dataclasses import dataclass

from loguru import logger
from openai import AsyncOpenAI

from shared.branding import PROSE_STYLE
from shared.contracts import EvaluationSpec
from shared.screening import (
    DEFAULT_THRESHOLD_PERCENT,
    MAX_RATING,
    CompetencyRating,
    FitAnalysis,
    HRQuestion,
    HRQuestionSet,
)

from screening.redact import redact

FIT_PROMPT_VERSION = "fit-2026-11-18"
QUESTIONS_PROMPT_VERSION = "hr-questions-2026-11-18"

#: Shorter than this, a "quote" is a keyword, and a keyword proves nothing.
MIN_QUOTE_CHARS = 16
HR_QUESTION_COUNT = 10

FIT_SYSTEM = f"""\
You compare a CV with what a job needs, one competency at a time, for an HR
partner who will decide whether to interview this person. You do not decide.

Rate each competency on this scale, using only what the CV shows:
  0  the CV shows the opposite, or clearly lacks it where it should appear
  1  adjacent or tangential experience only
  2  claimed, but without specifics: a skill listed, a responsibility named
  3  specific work they did: a system, a project, a result
  4  specific, owned and measured, at the depth the job asks for
  null  the CV says nothing relevant. This is not a low score; use it whenever
        there is nothing to point to.

For every rating from 1 to 4, copy one to three short passages from the CV
WORD FOR WORD as evidence: a bullet or a sentence fragment of at least six
words. Do not paraphrase, do not fix spelling, do not join two passages. A
passage you cannot copy exactly is not evidence; use null instead.

Parts of the CV have been replaced with [candidate], [institution], [email],
[phone], [link] or [removed]. That is deliberate. Never guess what was there,
and never let a missing name or institution change a rating.

Judge experience against what the job asks for, in one plain sentence.

Return JSON only:
{{
  "ratings": [
    {{"competency_id": "...", "rating": 0, "evidence": ["..."], "reasoning": "one sentence"}}
  ],
  "summary": "two or three sentences for HR: what the CV shows and what it leaves open",
  "experience_note": "one sentence comparing their experience with the job's expectation"
}}

{PROSE_STYLE}
"""

QUESTIONS_SYSTEM = f"""\
You write the questions a human HR partner will ask in a short HR round, before
a separate technical interview. Write exactly {HR_QUESTION_COUNT} questions.

The HR round is about the person, not technical depth: how they work with
others, how they communicate, how they handle pressure and bad news, what they
want next, and the practical facts the job description raises (location,
hours, notice period). Aim several questions at what the CV leaves open: a
competency with no evidence, a gap, a short tenure, a claim with no detail.

Each question is one plain sentence an HR partner can read aloud. Each comes
with a one-line reason for HR saying what it is for. Never ask about age,
family, religion, caste, health, nationality or anything else unrelated to the
job. Never ask the same thing twice in different words.

Return JSON only:
{{"questions": [{{"text": "...", "why": "..."}}]}}

{PROSE_STYLE}
"""


class ScreeningError(RuntimeError):
    pass


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def verify_quote(quote: str, cv_normalised: str) -> bool:
    """True if every part of the quote appears in the CV, ignoring case and punctuation."""
    parts = [p for p in re.split(r"\.\.\.|…", quote) if _normalise(p)]
    if not parts or len(_normalise(quote)) < MIN_QUOTE_CHARS:
        return False
    return all(_normalise(p) in cv_normalised for p in parts)


def _sampling(model: str) -> dict:
    # Reasoning models reject `temperature`. The shared helper that knows this
    # properly is Track I's; until it lands, the same rule bot/brain/drivers.py uses.
    if re.match(r"^(o\d|gpt-5|gpt-6)", model):
        return {}
    return {"temperature": 0.2}


def screening_model(default: str) -> str:
    return os.environ.get("OPENAI_SCREENING_MODEL") or default


def threshold_percent() -> float:
    """The org's screening cut-off. A setting, not a constant (decision 5)."""
    try:
        return float(os.environ.get("SCREENING_THRESHOLD_PERCENT") or DEFAULT_THRESHOLD_PERCENT)
    except ValueError:
        return DEFAULT_THRESHOLD_PERCENT


@dataclass
class Screener:
    api_key: str
    model: str

    def __post_init__(self):
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def _json(self, system: str, user: str) -> dict:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                **_sampling(self.model),
            )
            return json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            raise ScreeningError(f"The screening model call failed: {exc}") from exc

    async def analyse_fit(
        self, *, candidate_id: str, job_id: str, spec: EvaluationSpec, jd_text: str,
        cv_text: str, candidate_name: str | None,
    ) -> tuple[FitAnalysis, str]:
        """The fit analysis, and the redacted CV it was judged on."""
        redacted = redact(cv_text, candidate_name=candidate_name)
        competencies = [
            {"competency_id": c.id, "name": c.name, "what good looks like": c.description}
            for c in spec.competencies
        ]
        user = (
            f"THE JOB: {spec.role_title}, {spec.seniority}. Experience expected: "
            f"{spec.experience_expectation}.\n\n"
            f"COMPETENCIES TO RATE:\n{json.dumps(competencies, indent=2)}\n\n"
            f"JOB DESCRIPTION (may be empty):\n{jd_text.strip()[:6000]}\n\n"
            f"THE CV:\n{redacted.text}"
        )
        payload = await self._json(FIT_SYSTEM, user)
        fit = build_fit(
            payload, spec=spec, cv_text=redacted.text, candidate_id=candidate_id, job_id=job_id,
            removed=redacted.removed, model=self.model,
        )
        return fit, redacted.text

    async def hr_questions(
        self, *, candidate_id: str, job_id: str, spec: EvaluationSpec, jd_text: str,
        redacted_cv: str, fit: FitAnalysis,
    ) -> HRQuestionSet:
        open_points = [
            f"{r.competency_name}: {'no evidence in the CV' if r.rating is None else f'{r.rating}/4'}"
            for r in fit.ratings
        ]
        user = (
            f"THE JOB: {spec.role_title}. Experience expected: {spec.experience_expectation}.\n\n"
            f"WHAT SCREENING FOUND:\n{fit.summary}\n" + "\n".join(open_points) + "\n\n"
            f"JOB DESCRIPTION (may be empty):\n{jd_text.strip()[:4000]}\n\n"
            f"THE CV:\n{redacted_cv[:8000]}"
        )
        for attempt in (1, 2):
            payload = await self._json(QUESTIONS_SYSTEM, user)
            raw = [q for q in (payload.get("questions") or []) if isinstance(q, dict) and str(q.get("text", "")).strip()]
            if len(raw) >= HR_QUESTION_COUNT:
                questions = [
                    HRQuestion(id=f"hr{i + 1}", text=str(q["text"]).strip(), why=str(q.get("why", "")).strip())
                    for i, q in enumerate(raw[:HR_QUESTION_COUNT])
                ]
                return HRQuestionSet(
                    candidate_id=candidate_id, job_id=job_id, questions=questions,
                    model=self.model, prompt_version=QUESTIONS_PROMPT_VERSION,
                )
            logger.warning(f"[{candidate_id}] HR questions: got {len(raw)} of {HR_QUESTION_COUNT}, attempt {attempt}")
        raise ScreeningError(f"The model wrote {len(raw)} HR questions instead of {HR_QUESTION_COUNT}.")


def build_fit(
    payload: dict, *, spec: EvaluationSpec, cv_text: str, candidate_id: str, job_id: str,
    removed: list[str], model: str,
) -> FitAnalysis:
    """Turn the model's answer into a FitAnalysis, keeping only what the CV supports."""
    cv_normalised = _normalise(cv_text)
    by_id = {
        str(r.get("competency_id")): r
        for r in (payload.get("ratings") or [])
        if isinstance(r, dict)
    }
    ratings: list[CompetencyRating] = []
    dropped = 0
    for competency in spec.competencies:
        raw = by_id.get(competency.id, {})
        value = raw.get("rating")
        rating = value if isinstance(value, int) and 0 <= value <= MAX_RATING else None
        evidence = [q.strip() for q in (raw.get("evidence") or []) if isinstance(q, str) and q.strip()]
        verified = [q for q in evidence if verify_quote(q, cv_normalised)]
        dropped += len(evidence) - len(verified)
        reasoning = str(raw.get("reasoning") or "").strip()
        if rating is not None and rating > 0 and not verified:
            # Nothing it said can be found in the CV, so there is nothing to
            # point to. "No evidence" is the honest result, not the number.
            rating = None
            reasoning = (reasoning + " " if reasoning else "") + "(The quoted evidence could not be found in the CV.)"
        ratings.append(
            CompetencyRating(
                competency_id=competency.id, competency_name=competency.name,
                weight=competency.weight, rating=rating, evidence=verified[:3] if rating else [],
                reasoning=reasoning,
            )
        )
    if dropped:
        logger.info(f"[{candidate_id}] screening dropped {dropped} quote(s) not found in the CV")
    return FitAnalysis.from_ratings(
        candidate_id=candidate_id, job_id=job_id, ratings=ratings,
        threshold_percent=threshold_percent(),
        summary=str(payload.get("summary") or "").strip(),
        experience_note=str(payload.get("experience_note") or "").strip() or None,
        identity_fields_removed=removed, model=model, prompt_version=FIT_PROMPT_VERSION,
    )
