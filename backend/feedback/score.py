"""Scoring a finished interview against the employer's evaluation spec.

Reasoning tier, entirely off the conversational path (`OPENAI_FEEDBACK_MODEL`).
Nothing here ever runs while someone is talking.

**The output is evidence for a human, never a decision.** Nothing in this system
auto-rejects anyone (CLAUDE.md), so the report carries a signal about how strong
the evidence is — not a verdict on the person.

The one rule everything else serves: **a quote that is not in the transcript
must never reach the report.** A model asked for verbatim evidence will
occasionally produce a plausible sentence the candidate never said, and a
fabricated quote inside a hiring record is the worst failure this product has —
worse than a missing score, worse than an empty report. So every quote the model
returns is checked against the transcript in code before it is kept, and a score
whose evidence does not survive that check is downgraded to "insufficient
signal" rather than published on the strength of something invented.
"""

import json
import re
from dataclasses import dataclass

from loguru import logger
from openai import AsyncOpenAI

from shared.branding import PROSE_STYLE
from shared.contracts import (
    CompetencyScore,
    Contradiction,
    CoverageLevel,
    EvaluationSpec,
    EvidenceQuote,
    FeedbackReport,
    ObservedRedFlag,
    RecommendationSignal,
    Speaker,
    Transcript,
)

SYSTEM = """\
You are writing an interview feedback report for the hiring manager who will
read the transcript themselves. You are not deciding anything. Someone's job is
affected by what you write, so understate rather than overstate.

You are given the evaluation spec, the transcript with numbered turns, and what
the interviewer's own state machine recorded about each section.

SCORING, per competency in the spec, 0-5:
  0-1  no relevant capability evidenced
  2    generic or textbook answers, no concrete experience behind them
  3    real experience, described at a normal level of detail
  4    specific, owned decisions with trade-offs and outcomes
  5    the above plus insight into what they got wrong and would change

EVIDENCE IS MANDATORY AND MUST BE VERBATIM.
- Every score cites at least one quote, copied EXACTLY from a candidate turn.
  Not summarised, not tidied, not corrected. Copy the characters as they are.
- Give the `turn_index` of the turn you copied it from.
- If you cannot find a real quote for a competency, do NOT invent one and do NOT
  score it. Set `score` to null and `insufficient_signal` to true. That is a
  correct, useful answer — a guessed number is not.

DECLINING TO ANSWER IS NOT A LOW SCORE.
The section data tells you how many turns the candidate declined. Declining is
not evidence of inability — it means they were disengaged, uncomfortable, or
done. Where a competency went unevidenced because they declined, say exactly
that in the rationale and mark it insufficient_signal. Never write it up as a
weakness. "Declined to answer" and "answered poorly" say different things about
a person, and only one of them is about their ability.

WHERE THE INTERVIEW RAN SHORT.
If a section was squeezed or never properly reached, that is the interview's
failing, not the candidate's. Mark it insufficient_signal and list it in
`coverage_gaps`.

RED FLAGS.
Only report a red flag from the spec if you can quote the candidate evidencing
it. A red flag with no quote does not go in.

TONE.
Describe what they said and what it shows. Do not speculate about personality,
motivation, culture fit, or anything you cannot point at in the transcript. Do
not recommend hiring or rejecting — that is not your call and the reader has not
asked for it.

Return JSON exactly in this shape:

{
  "competency_scores": [
    {
      "competency_id": "must match the spec exactly",
      "name": "...",
      "score": 3.5,
      "coverage": "not_started" | "insufficient" | "partial" | "sufficient",
      "insufficient_signal": false,
      "rationale": "2-3 sentences on what the evidence shows",
      "evidence": [{"turn_index": 12, "text": "exact words from that turn"}]
    }
  ],
  "red_flags_observed": [
    {"description": "...", "evidence": [{"turn_index": 8, "text": "..."}]}
  ],
  "coverage_gaps": ["competency name: why the interview could not tell"],
  "summary": "A short written assessment for the reader. What the transcript
              supports, what it does not, and what a human should probe next.
              Plain language, no verdict.",
  "recommendation": "strong_evidence_for" | "some_evidence_for" | "mixed"
                    | "limited_evidence" | "insufficient_signal"
}

`recommendation` describes the STRENGTH OF THE EVIDENCE GATHERED, not whether to
hire. An excellent candidate given a short interview is "limited_evidence".
"""

SYSTEM = f"{SYSTEM}\n\n{PROSE_STYLE}\n"


class FeedbackError(RuntimeError):
    pass


@dataclass
class QuoteAudit:
    """What the verification pass did, so it can be logged and tested."""

    kept: int = 0
    reindexed: int = 0
    dropped: int = 0
    scores_downgraded: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.scores_downgraded is None:
            self.scores_downgraded = []


def _normalise(text: str) -> str:
    """Loose enough to survive punctuation drift, strict enough to catch invention.

    The model tends to tidy a quote — dropping a filler word, fixing punctuation
    — while keeping the substance. That is a formatting difference, not a
    fabrication, and rejecting it would throw away real evidence. Comparing on
    lowercase alphanumerics keeps those and still fails loudly on a sentence the
    candidate never said.
    """
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def verify_quote(
    raw_text: str, claimed_index: int, transcript: Transcript
) -> EvidenceQuote | None:
    """Return a quote anchored to a real turn, or None if it was never said.

    A quote found at a different index than claimed is repaired rather than
    dropped: the model miscounted, but the candidate did say the words. A quote
    found nowhere is dropped — that is the fabrication case.
    """
    needle = _normalise(raw_text)
    if not needle:
        return None

    candidate_turns = [t for t in transcript.turns if t.speaker == Speaker.CANDIDATE]

    exact = next((t for t in candidate_turns if t.index == claimed_index), None)
    if exact is not None and needle in _normalise(exact.text):
        return EvidenceQuote(
            text=raw_text.strip(),
            turn_index=exact.index,
            at_seconds=exact.at_seconds,
            speaker=Speaker.CANDIDATE,
        )

    for turn in candidate_turns:
        if needle in _normalise(turn.text):
            return EvidenceQuote(
                text=raw_text.strip(),
                turn_index=turn.index,
                at_seconds=turn.at_seconds,
                speaker=Speaker.CANDIDATE,
            )

    return None


def _verify_evidence(
    raw: list[dict], transcript: Transcript, audit: QuoteAudit
) -> list[EvidenceQuote]:
    kept: list[EvidenceQuote] = []
    for item in raw or []:
        text = str(item.get("text", ""))
        try:
            claimed = int(item.get("turn_index", -1))
        except (TypeError, ValueError):
            claimed = -1

        quote = verify_quote(text, claimed, transcript)
        if quote is None:
            audit.dropped += 1
            logger.warning(
                f"dropped an unverifiable quote (turn {claimed}): {text[:80]!r}"
            )
            continue
        if quote.turn_index != claimed:
            audit.reindexed += 1
        audit.kept += 1
        kept.append(quote)
    return kept


def _render_transcript(transcript: Transcript) -> str:
    """Numbered turns, so the model has an index to cite."""
    lines = []
    for turn in transcript.turns:
        who = "INTERVIEWER" if turn.speaker == Speaker.INTERVIEWER else "CANDIDATE"
        lines.append(f"[turn {turn.index} | {turn.at_seconds:.0f}s] {who}: {turn.text}")
    return "\n".join(lines)


def _render_sections(outcomes: list[dict]) -> str:
    """What the brain observed, including what it could not get to."""
    if not outcomes:
        return "(no section data recorded)"
    lines = []
    for o in outcomes:
        parts = [
            f"section {o.get('section_id')}",
            f"coverage={o.get('coverage')}",
            f"turns={o.get('turns_spent')}",
        ]
        if o.get("declined_turns"):
            parts.append(f"DECLINED {o['declined_turns']} turns")
        if o.get("shortfall_reason"):
            parts.append(f"SQUEEZED: {o['shortfall_reason']}")
        lines.append(" · ".join(str(p) for p in parts))
    return "\n".join(lines)


@dataclass
class FeedbackScorer:
    api_key: str
    model: str

    def __post_init__(self) -> None:
        self._client = AsyncOpenAI(api_key=self.api_key)

    async def score(
        self,
        *,
        interview_id: str,
        blueprint_id: str,
        spec: EvaluationSpec,
        transcript: Transcript,
        section_outcomes: list[dict] | None = None,
        contradictions: list[dict] | None = None,
    ) -> FeedbackReport:
        outcomes = section_outcomes or []

        candidate_turns = [t for t in transcript.turns if t.speaker == Speaker.CANDIDATE]
        if not candidate_turns:
            # Nothing was said. Asking a model to score silence invites it to
            # fill the silence in.
            return _empty_report(
                interview_id=interview_id,
                blueprint_id=blueprint_id,
                spec=spec,
                transcript=transcript,
                reason=(
                    "The candidate did not say anything that was recorded, so "
                    "there is nothing to assess. This says nothing about them."
                ),
            )

        user = (
            f"EVALUATION SPEC:\n{spec.model_dump_json(indent=2)}\n\n"
            f"WHAT THE INTERVIEWER RECORDED PER SECTION:\n{_render_sections(outcomes)}\n\n"
            f"TRANSCRIPT:\n{_render_transcript(transcript)}"
        )

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            payload = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            raise FeedbackError(f"Feedback model call failed: {exc}") from exc

        return build_report(
            interview_id=interview_id,
            blueprint_id=blueprint_id,
            spec=spec,
            transcript=transcript,
            payload=payload,
            contradictions=contradictions or [],
        )


def build_report(
    *,
    interview_id: str,
    blueprint_id: str,
    spec: EvaluationSpec,
    transcript: Transcript,
    payload: dict,
    contradictions: list[dict] | None = None,
) -> FeedbackReport:
    """Validate and anchor the model's report. Pure — no network, so it is testable.

    This is where the report is made trustworthy: quotes are checked against the
    transcript, scores that lose their evidence are downgraded, and competencies
    the model forgot are added back as unassessed rather than silently missing.
    """
    audit = QuoteAudit()
    by_id = {c.id: c for c in spec.competencies}
    scored: dict[str, CompetencyScore] = {}

    for raw in payload.get("competency_scores") or []:
        competency_id = str(raw.get("competency_id", ""))
        competency = by_id.get(competency_id)
        if competency is None:
            logger.warning(f"[{interview_id}] score for unknown competency {competency_id!r}")
            continue

        evidence = _verify_evidence(raw.get("evidence"), transcript, audit)
        score = raw.get("score")
        insufficient = bool(raw.get("insufficient_signal"))

        # The rule the contract also enforces: no evidence, no score. Reaching
        # here means every quote behind this score failed verification.
        if not evidence and score is not None:
            audit.scores_downgraded.append(competency_id)
            logger.warning(
                f"[{interview_id}] {competency_id}: score {score} had no verifiable "
                f"evidence; downgraded to insufficient signal"
            )
            score = None
            insufficient = True
        if insufficient:
            score = None

        rationale = str(raw.get("rationale", "")).strip()
        if competency_id in audit.scores_downgraded:
            rationale = (
                "No verifiable transcript evidence supports a score here. "
                + rationale
            ).strip()

        scored[competency_id] = CompetencyScore(
            competency_id=competency_id,
            name=competency.name,
            score=None if score is None else float(score),
            coverage=_coverage(raw.get("coverage"), bool(evidence)),
            rationale=rationale or "No rationale was given.",
            evidence=evidence,
            insufficient_signal=insufficient or not evidence,
        )

    # A competency the model skipped is not absent from the report — silence
    # there would read as "nothing to say" rather than "never assessed".
    for competency in spec.competencies:
        if competency.id in scored:
            continue
        scored[competency.id] = CompetencyScore(
            competency_id=competency.id,
            name=competency.name,
            score=None,
            coverage=CoverageLevel.INSUFFICIENT,
            rationale="This competency was not assessed in the report.",
            evidence=[],
            insufficient_signal=True,
        )

    red_flags = []
    for raw in payload.get("red_flags_observed") or []:
        evidence = _verify_evidence(raw.get("evidence"), transcript, audit)
        if not evidence:
            # An unevidenced red flag is an accusation. It does not go in.
            logger.warning(
                f"[{interview_id}] dropped an unevidenced red flag: "
                f"{str(raw.get('description'))[:80]!r}"
            )
            continue
        red_flags.append(
            ObservedRedFlag(description=str(raw.get("description", "")), evidence=evidence)
        )

    gaps = [str(g) for g in (payload.get("coverage_gaps") or [])]
    for score in scored.values():
        if score.insufficient_signal and score.name not in " ".join(gaps):
            gaps.append(score.name)

    ordered = [scored[c.id] for c in spec.competencies]

    report = FeedbackReport(
        interview_id=interview_id,
        blueprint_id=blueprint_id,
        role_title=spec.role_title,
        competency_scores=ordered,
        red_flags_observed=red_flags,
        contradictions=[Contradiction.model_validate(c) for c in (contradictions or [])],
        summary=str(payload.get("summary", "")).strip() or "No summary was produced.",
        recommendation=_recommendation(payload.get("recommendation"), ordered, spec),
        coverage_gaps=gaps,
        interview_duration_seconds=transcript.duration_seconds,
    )

    logger.info(
        f"[{interview_id}] report built: {audit.kept} quotes verified, "
        f"{audit.reindexed} re-anchored, {audit.dropped} dropped as unverifiable, "
        f"{len(audit.scores_downgraded)} scores downgraded"
    )
    return report


def _coverage(raw: object, has_evidence: bool) -> CoverageLevel:
    try:
        level = CoverageLevel(str(raw))
    except ValueError:
        level = CoverageLevel.PARTIAL if has_evidence else CoverageLevel.INSUFFICIENT
    # Coverage must never flatter (CLAUDE.md): without a surviving quote there
    # is no basis for claiming the ground was covered.
    if not has_evidence:
        return CoverageLevel.INSUFFICIENT
    return level


#: How much of the employer's weighted spec must actually have been assessed
#: before the report is allowed to sound confident. Set high on purpose: the
#: reader's instinct is to skim to this line, so it must not read as a confident
#: verdict on an interview that only covered part of what they asked for.
CONFIDENT_SIGNAL_REQUIRES_WEIGHT = 0.75


def _recommendation(
    raw: object, scores: list[CompetencyScore], spec: EvaluationSpec
) -> RecommendationSignal:
    """Trust the model's signal, but never let it overstate thin evidence.

    Measured by **weight, not count**. The employer already said what matters:
    covering one of two competencies is a different thing depending on whether
    it was the 60% one or the 40% one, and counting cannot tell them apart.
    """
    try:
        signal = RecommendationSignal(str(raw))
    except ValueError:
        signal = RecommendationSignal.INSUFFICIENT_SIGNAL

    assessed = {s.competency_id for s in scores if s.score is not None}
    if not assessed:
        return RecommendationSignal.INSUFFICIENT_SIGNAL

    total = sum(c.weight for c in spec.competencies) or 1.0
    covered = sum(c.weight for c in spec.competencies if c.id in assessed) / total

    if covered < CONFIDENT_SIGNAL_REQUIRES_WEIGHT and signal in (
        RecommendationSignal.STRONG_EVIDENCE_FOR,
        RecommendationSignal.SOME_EVIDENCE_FOR,
    ):
        # Too much of what the employer cares about went unassessed. Whatever
        # was seen, the interview did not gather enough to support a confident
        # signal — and saying so is the honest answer, not a hedge.
        return RecommendationSignal.LIMITED_EVIDENCE
    return signal


def _empty_report(
    *,
    interview_id: str,
    blueprint_id: str,
    spec: EvaluationSpec,
    transcript: Transcript,
    reason: str,
) -> FeedbackReport:
    return FeedbackReport(
        interview_id=interview_id,
        blueprint_id=blueprint_id,
        role_title=spec.role_title,
        competency_scores=[
            CompetencyScore(
                competency_id=c.id,
                name=c.name,
                score=None,
                coverage=CoverageLevel.INSUFFICIENT,
                rationale=reason,
                evidence=[],
                insufficient_signal=True,
            )
            for c in spec.competencies
        ],
        summary=reason,
        recommendation=RecommendationSignal.INSUFFICIENT_SIGNAL,
        coverage_gaps=[c.name for c in spec.competencies],
        interview_duration_seconds=transcript.duration_seconds,
    )
