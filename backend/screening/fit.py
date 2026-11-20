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

from hiring import usage
from screening.redact import redact
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

FIT_PROMPT_VERSION = "fit-2026-11-18"
QUESTIONS_PROMPT_VERSION = "hr-questions-2026-11-20-prescreen-template"

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

#: The HR round is the pre-screening call before the MITRA round (product owner,
#: 2026-11-20): these ten, in this order, for every candidate. Code owns the fixed
#: wording; the model writes only the four questions that come from this CV (`None`
#: here), each with a general fallback when the CV does not give what it needs.
HR_TEMPLATE: tuple[tuple[str, str | None, str, str], ...] = (
    ("hr1", "Walk me through your profile in two minutes.",
     "Listen for a clear, ordered story of their career and what they did themselves.", ""),
    ("hr2", None, "Listen for a genuine reason to move, and whether this role fits it.",
     "Why are you looking for a change now?"),
    ("hr3", "What is your current CTC, and how is it split between fixed and variable?",
     "Note the figures for the offer discussion.", ""),
    ("hr4", "What is your expected CTC?", "Check it against the budget for the role.", ""),
    ("hr5", "What is your notice period? Is it negotiable or can it be bought out? Are you already serving it?",
     "Note the earliest realistic joining date.", ""),
    ("hr6", None, "Check they can work where and how the role needs.",
     "Where are you based, and are you open to the location and way of working this role needs?"),
    ("hr7", None, "Listen for whether the focus they want matches what this role needs.",
     "Which kind of work do you want your next role to focus on?"),
    ("hr8", None, "Clarify the timeline; a neutral question, never a challenge.",
     "Is there anything in your career timeline worth clarifying, such as a gap, an overlap or a short stint?"),
    ("hr9", "Do you have other offers or interviews in progress?", "Note competing timelines.", ""),
    ("hr10", "What do you know about our company, and why does this role interest you?",
     "Listen for real interest and some preparation.", ""),
)

QUESTIONS_SYSTEM = """\
You fill in four questions of a human HR partner's pre-screening call, from the
candidate's CV and the job description. The other questions of the call are fixed
and are not yours to write. Each of yours is one or two plain, friendly sentences an
HR partner reads aloud as written, asking one thing.

hr2, their current role: do not write a question. Give the employer of their
current role (the one with no end date, or "present") and the month it started, as
the CV states it: {"employer": "Acme", "since": "2024-05"}. Empty strings when the CV
does not say. The question is built from these.
hr6, location and way of working: ask where they are based. If the CV or the job
description says how they work now (hybrid, remote, office) or where the role is,
say so and ask whether they are open to what the role needs (working fully from the
office, relocating). Example: "Where are you based? You are hybrid now, so are you
open to working fully from the office or relocating?"
hr7, the focus they want: name the main areas their CV covers (two to four, in their
own field's words) and ask which one they want their next role to focus on.
hr8, the timeline: ask about one real point in their dates that a recruiter would
clarify: work that began before a degree ended, an overlap, a gap of several months,
a very short stint. Ask it neutrally, as a question, never as a suspicion. Example:
"You joined Acme in May 2024 but graduated in June 2024. Did you join before
finishing your degree, or did an internship convert to a full-time role?" If the
dates show nothing like that, return an empty text for hr8.

Use only what the CV and the job description actually say. A question whose detail
they do not give gets an empty text, and a general question is used instead. Never
ask about age, marital status, family plans, children, religion, caste, health,
disability, nationality, appearance or anything else unrelated to the job.

Return JSON only:
{"hr2": {"employer": "...", "since": "YYYY-MM"}, "hr6": "...", "hr7": "...", "hr8": "..."}

{PROSE_STYLE}
""".replace("{PROSE_STYLE}", PROSE_STYLE)


_NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                 "eleven", "twelve")


def tenure_words(since: str, today=None) -> str | None:
    """'2024-05' -> 'about two and a half years', counted in code: the model got this
    wrong ("about six months" for two and a half years)."""
    import re
    from datetime import date

    match = re.fullmatch(r"\s*(\d{4})-(\d{1,2})\s*", since or "")
    if not match:
        return None
    today = today or date.today()
    year, month = int(match.group(1)), int(match.group(2))
    months = (today.year - year) * 12 + (today.month - month)
    if not 1 <= month <= 12 or months < 0 or months > 50 * 12:
        return None
    if months < 1:
        return "less than a month"
    if months < 12:
        return f"about {_NUMBER_WORDS[months]} month{'s' if months != 1 else ''}" if months <= 12 else None
    halves = round(months / 6)  # to the nearest half year
    whole, half = divmod(halves, 2)
    years = _NUMBER_WORDS[whole] if whole < len(_NUMBER_WORDS) else str(whole)
    if half:
        return f"about {years} and a half years" if whole else "about half a year"
    return f"about {years} year{'s' if whole != 1 else ''}"


def _current_role_question(written) -> str | None:
    if not isinstance(written, dict):
        return None
    employer = str(written.get("employer") or "").strip()
    if not employer:
        return None
    if employer.isupper() and len(employer) > 4 and " " not in employer:
        employer = employer.title()  # "APPINVENTIV" as written in a CV; "IBM" and "TCS" stay
    tenure = tenure_words(str(written.get("since") or ""))
    lead = f"You have been at {employer} for {tenure}." if tenure else f"You are at {employer} now."
    return f"{lead} Why are you looking for a change now?"


def build_hr_questions(payload: dict) -> list["HRQuestion"]:
    """The ten, in order: the fixed ones as written, the CV ones as the model wrote
    them, or their general fallback when it left one empty."""
    out = []
    for qid, fixed, why, fallback in HR_TEMPLATE:
        text = fixed
        if qid == "hr2":
            text = _current_role_question(payload.get("hr2")) or fallback
        elif text is None:
            written = payload.get(qid)
            written = (written.get("text") if isinstance(written, dict) else written) or ""
            text = str(written).strip() or fallback
        out.append(HRQuestion(id=qid, text=text, why=why))
    return out


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

    async def _json(self, system: str, user: str, *, stage: str, purpose: str, prompt_version: str,
                    candidate_id: str, job_id: str) -> tuple[dict, object, float]:
        """One structured call. Returns the payload, the raw response and when it started, for recording."""
        t0 = usage.started()
        response = None
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                **_sampling(self.model),
            )
            return json.loads(response.choices[0].message.content or "{}"), response, t0
        except Exception as exc:  # noqa: BLE001
            await usage.record(
                stage=stage, purpose=purpose, model=self.model, started_at=t0, response=response, error=exc,
                status="schema_error" if response is not None else "error", prompt_version=prompt_version,
                candidate_id=candidate_id, job_id=job_id,
            )
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
        payload, response, t0 = await self._json(
            FIT_SYSTEM, user, stage="cv_screening", purpose="CV fit against the job", prompt_version=FIT_PROMPT_VERSION,
            candidate_id=candidate_id, job_id=job_id,
        )
        fit = build_fit(
            payload, spec=spec, cv_text=redacted.text, candidate_id=candidate_id, job_id=job_id,
            removed=redacted.removed, model=self.model,
        )
        await usage.record(
            stage="cv_screening", purpose="CV fit against the job", model=self.model, started_at=t0, response=response,
            prompt_version=FIT_PROMPT_VERSION, candidate_id=candidate_id, job_id=job_id,
            summary={
                "competencies": len(fit.ratings),
                "rated": sum(1 for r in fit.ratings if r.rating is not None),
                "no_evidence": sum(1 for r in fit.ratings if r.rating is None),
                "quotes_kept": sum(len(r.evidence) for r in fit.ratings),
                "score_percent": fit.score_percent,
                "identity_fields_removed": len(redacted.removed),
            },
            output_preview=fit.summary,
        )
        return fit, redacted.text

    async def hr_questions(
        self, *, candidate_id: str, job_id: str, spec: EvaluationSpec, jd_text: str,
        redacted_cv: str, fit: FitAnalysis,
    ) -> HRQuestionSet:
        # What the screening found about skills is left out on purpose: it pulls
        # the questions towards technical depth, which is the MITRA round's job.
        from datetime import date

        user = (
            f"TODAY: {date.today().isoformat()}\n\n"
            f"THE JOB: {spec.role_title}. Experience expected: {spec.experience_expectation}.\n\n"
            f"JOB DESCRIPTION, for location, work mode and anything practical (may be empty):\n"
            f"{jd_text.strip()[:4000]}\n\n"
            f"THE CV:\n{redacted_cv[:8000]}"
        )
        payload, response, t0 = await self._json(
            QUESTIONS_SYSTEM, user, stage="hr_questions", purpose="Questions for the HR round",
            prompt_version=QUESTIONS_PROMPT_VERSION, candidate_id=candidate_id, job_id=job_id,
        )
        questions = build_hr_questions(payload if isinstance(payload, dict) else {})
        written = [q for q, (qid, fixed, _, fallback) in zip(questions, HR_TEMPLATE) if fixed is None and q.text != fallback]
        await usage.record(
            stage="hr_questions", purpose="Questions for the HR round", model=self.model, started_at=t0,
            response=response, prompt_version=QUESTIONS_PROMPT_VERSION, candidate_id=candidate_id, job_id=job_id,
            summary={"from_the_cv": len(written), "general": 4 - len(written)},
            output_preview=questions[1].text,
        )
        return HRQuestionSet(
            candidate_id=candidate_id, job_id=job_id, questions=questions,
            model=self.model, prompt_version=QUESTIONS_PROMPT_VERSION,
        )


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
