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
from dataclasses import dataclass, field

from loguru import logger
from openai import AsyncOpenAI

from shared.branding import PROSE_STYLE
from shared.usage import ModelUsage
from shared.contracts import (
    QuestioningHealth,
    CompetencyScore,
    ConversationHealth,
    Contradiction,
    CoverageLevel,
    DealbreakerFinding,
    DealbreakerState,
    EvaluationSpec,
    EvidenceQuote,
    FeedbackReport,
    JudgmentHealth,
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

WHERE THE CANDIDATE COULD NOT BE HEARD.
You may be told the recording was poor. If so, thin or broken answers are
evidence about the connection, not about the person. Say that plainly in the
rationale and mark the competency insufficient_signal rather than scoring it
low. Someone with a cheap headset must not read as someone who could not
answer.

WHERE THE INTERVIEWER REPEATED ITSELF.
You may be told the interviewer asked the same question more than once. If so:
- Do NOT describe the candidate as repetitive, circular, or as giving the same
  answer twice. Somebody asked the same question twice and got the same answer,
  which is what a person does. Say the interviewer repeated itself, or say
  nothing about it.
- A thin topic there is the interview's failing. Mark it insufficient_signal and
  list it in `coverage_gaps`.
- **Do not raise a red flag that turns on something being absent.** "No real
  examples", "cannot explain why", "avoids the question" — none of those can be
  concluded from a topic that spent its questions asking one thing repeatedly.
  Not being asked is not the same as failing to answer. A red flag still stands
  if you can quote the candidate evidencing it directly.

WHERE THE INTERVIEW RAN SHORT.
If a section was squeezed or never properly reached, that is the interview's
failing, not the candidate's. Mark it insufficient_signal and list it in
`coverage_gaps`.

RED FLAGS.
Only report a red flag from the spec if you can quote the candidate evidencing
it. A red flag with no quote does not go in.

DEALBREAKERS THE CANDIDATE PUT TO REST.
Separately from the above, list any dealbreaker the transcript shows the
candidate doing the OPPOSITE of, in `dealbreakers_cleared`. Same evidence
standard as a red flag, in the other direction: quote them, or leave it out.

The bar is what they did, not what they avoided:
- "Avoids negative results" is cleared by them VOLUNTEERING a bad result and
  what they did about it. It is not cleared by an interview that never asked.
- "Cannot explain why a metric was chosen" is cleared by them giving the reason
  for a choice. It is not cleared by them naming the metric.
- "Cannot describe a time they did X" is cleared by an actual occasion with
  specifics. It is not cleared by them describing how X is done in general.

Silence never clears anything. If the topic was never raised, if they were
asked and said little, or if you would have to reason from an absence to get
there, LEAVE IT OUT. Something you leave out is reported as never having been
put to them, which is the honest outcome and costs the candidate nothing. A
dealbreaker wrongly called cleared is a false statement about a person in a
hiring record, and it is the worse error by a wide margin.

A dealbreaker cannot be in both lists. If they did the thing and also the
opposite of it at different moments, that is the red flag, not a clearance.

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
  "dealbreakers_cleared": [
    {"description": "the dealbreaker, copied from the spec",
     "evidence": [{"turn_index": 8, "text": "..."}]}
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


def _match_dealbreaker(description: str, spec: EvaluationSpec) -> str | None:
    """Which spec dealbreaker the model meant, or None.

    The model is asked to copy the text and mostly does, but it paraphrases and
    reflows often enough that equality alone loses real findings. Matched on
    lowercase alphanumeric containment either way, then on a distinctive-word
    majority, which is the same shape as quote verification: loose enough to
    survive tidying, strict enough that it cannot land on the wrong one.

    **This is not a similarity rule over free text.** Both sides are a closed,
    employer-authored list of four, and an unmatched description is dropped
    rather than guessed at, so the failure mode is a dealbreaker reading
    `not_reached` when it was cleared. That is the safe direction.
    """
    said = _normalise(description)
    if not said:
        return None
    for flag in spec.red_flags:
        target = _normalise(flag)
        if target and (target in said or said in target):
            return flag
    said_words = {w for w in said.split() if len(w) > 3}
    if not said_words:
        return None
    for flag in spec.red_flags:
        words = {w for w in _normalise(flag).split() if len(w) > 3}
        if words and len(words & said_words) >= max(2, (len(words) + 1) // 2):
            return flag
    return None


def _dealbreaker_findings(
    payload: dict,
    spec: EvaluationSpec,
    red_flags: list[ObservedRedFlag],
    transcript: Transcript,
    audit: QuoteAudit,
) -> list[DealbreakerFinding]:
    """Every dealbreaker in the spec, in spec order, with what could be said.

    **`not_reached` is the residue and is never the model's to assert.** It is
    whatever is left once the fired list and the cleared list have been matched
    back to the spec, so the only way to report coverage of a dealbreaker is to
    produce a verified quote for it. A model that says nothing produces
    `not_reached`, which is the truthful reading of having said nothing.

    A clearance whose quotes do not survive verification falls back to
    `not_reached` rather than being dropped the way an unevidenced red flag is.
    Dropping is right for a flag because the entry itself is the accusation;
    here the entry is the report's account of what it could establish, and the
    honest account of an unverifiable clearance is that nothing was established.

    Firing wins ties. The prompt already says a dealbreaker cannot be in both
    lists, and if the model does it anyway the flag is the finding.
    """
    fired: dict[str, list[EvidenceQuote]] = {}
    for flag in red_flags:
        matched = _match_dealbreaker(flag.description, spec)
        if matched is not None:
            fired.setdefault(matched, []).extend(flag.evidence)

    cleared: dict[str, list[EvidenceQuote]] = {}
    for raw in payload.get("dealbreakers_cleared") or []:
        if not isinstance(raw, dict):
            continue
        matched = _match_dealbreaker(str(raw.get("description", "")), spec)
        if matched is None or matched in fired:
            continue
        evidence = _verify_evidence(raw.get("evidence"), transcript, audit)
        if not evidence:
            logger.warning(
                f"dropped an unevidenced clearance: {str(raw.get('description'))[:80]!r}"
            )
            continue
        cleared.setdefault(matched, []).extend(evidence)

    findings = []
    for flag in spec.red_flags:
        if flag in fired:
            state, evidence = DealbreakerState.OBSERVED, fired[flag]
        elif flag in cleared:
            state, evidence = DealbreakerState.CLEARED, cleared[flag]
        else:
            state, evidence = DealbreakerState.NOT_REACHED, []
        findings.append(
            DealbreakerFinding(dealbreaker=flag, state=state, evidence=evidence)
        )
    return findings


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
            # **Was "SQUEEZED:", which was true of one writer and false of the
            # four that actually fire.** `_rebalance_remaining` is the only
            # thing that squeezes anything, and it has never run in any
            # interview on record. What does set this is a section the candidate
            # declined, one that ran out of time, or one the interview never
            # reached — and the model was being told each of those was a squeeze.
            #
            # One neutral label carries all of them because the reason is
            # already a whole sentence naming which it was: "Candidate declined
            # to answer on this topic", "Section ended on budget_exhausted
            # after 6 candidate turn(s)", "Candidate chose to end the interview
            # before this competency was covered". The label only has to stop
            # contradicting them.
            parts.append(f"SHORTFALL: {o['shortfall_reason']}")
        lines.append(" · ".join(str(p) for p in parts))
    return "\n".join(lines)


@dataclass
class FeedbackScorer:
    api_key: str
    model: str
    #: Counted per call, so the cost of a report is recorded with the report.
    calls: int = field(default=0, init=False)
    prompt_tokens: int = field(default=0, init=False)
    completion_tokens: int = field(default=0, init=False)
    cached_tokens: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._client = AsyncOpenAI(api_key=self.api_key)

    def usage(self) -> ModelUsage:
        return ModelUsage(
            calls=self.calls,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_tokens=self.cached_tokens,
        )

    def _record(self, response) -> None:
        self.calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_tokens or 0
        self.completion_tokens += usage.completion_tokens or 0
        details = getattr(usage, "prompt_tokens_details", None)
        self.cached_tokens += getattr(details, "cached_tokens", 0) or 0

    async def score(
        self,
        *,
        interview_id: str,
        blueprint_id: str,
        spec: EvaluationSpec,
        transcript: Transcript,
        section_outcomes: list[dict] | None = None,
        contradictions: list[dict] | None = None,
        health: ConversationHealth | None = None,
        judgment_health: JudgmentHealth | None = None,
        questioning: QuestioningHealth | None = None,
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
                health=health,
                judgment_health=judgment_health,
                questioning=questioning,
                reason=(
                    "The candidate did not say anything that was recorded, so "
                    "there is nothing to assess. This says nothing about them."
                ),
            )

        channel = ""
        if health is not None and health.degraded:
            channel = f"RECORDING QUALITY:\n{health.as_sentence}\n\n"

        conduct = ""
        if questioning is not None and questioning.degraded:
            conduct = f"HOW THE INTERVIEW WAS CONDUCTED:\n{questioning.as_sentence}\n\n"

        user = (
            f"EVALUATION SPEC:\n{spec.model_dump_json(indent=2)}\n\n"
            f"{channel}"
            f"{conduct}"
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
            self._record(response)
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
            health=health,
            judgment_health=judgment_health,
            questioning=questioning,
        )


def build_report(
    *,
    interview_id: str,
    blueprint_id: str,
    spec: EvaluationSpec,
    transcript: Transcript,
    payload: dict,
    contradictions: list[dict] | None = None,
    health: ConversationHealth | None = None,
    judgment_health: JudgmentHealth | None = None,
    questioning: QuestioningHealth | None = None,
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

    dealbreakers = _dealbreaker_findings(payload, spec, red_flags, transcript, audit)

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
        dealbreakers=dealbreakers,
        contradictions=[Contradiction.model_validate(c) for c in (contradictions or [])],
        summary=str(payload.get("summary", "")).strip() or "No summary was produced.",
        recommendation=_recommendation(
            payload.get("recommendation"), ordered, spec, health, judgment_health,
            questioning,
        ),
        coverage_gaps=gaps,
        conversation_health=health,
        judgment_health=judgment_health,
        questioning_health=questioning,
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
    raw: object,
    scores: list[CompetencyScore],
    spec: EvaluationSpec,
    health: "ConversationHealth | None" = None,
    judgment_health: "JudgmentHealth | None" = None,
    questioning: "QuestioningHealth | None" = None,
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

    # A confident reading cannot be drawn from a recording the candidate could
    # not be heard through. Capped rather than discarded: what was captured is
    # still worth reading, it just cannot carry a strong claim.
    if health is not None and health.degraded and signal in (
        RecommendationSignal.STRONG_EVIDENCE_FOR,
        RecommendationSignal.SOME_EVIDENCE_FOR,
    ):
        return RecommendationSignal.LIMITED_EVIDENCE

    # Nor from an interview most of whose analysis never ran. Same cap, same
    # reasoning, one layer up: the evidence is thin for a reason that has
    # nothing to do with the candidate, and the signal has to say so rather
    # than let a quiet report read as a quiet person. Checked separately from
    # the channel above so neither degradation can hide the other.
    if judgment_health is not None and judgment_health.degraded and signal in (
        RecommendationSignal.STRONG_EVIDENCE_FOR,
        RecommendationSignal.SOME_EVIDENCE_FOR,
    ):
        return RecommendationSignal.LIMITED_EVIDENCE

    # Nor from an interview that spent its questions asking one thing several
    # times. Same cap, same reasoning, and checked separately from the two above
    # so no degradation can hide another: an interview can be well recorded,
    # fully analysed, and still have gathered less than its length suggests.
    if questioning is not None and questioning.degraded and signal in (
        RecommendationSignal.STRONG_EVIDENCE_FOR,
        RecommendationSignal.SOME_EVIDENCE_FOR,
    ):
        return RecommendationSignal.LIMITED_EVIDENCE

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
    health: ConversationHealth | None = None,
    judgment_health: JudgmentHealth | None = None,
    questioning: QuestioningHealth | None = None,
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
        # Nothing was assessed, so nothing was put to them. Stated rather
        # than left empty: an empty list reads as "no dealbreakers", which is
        # the opposite of what an empty report means.
        dealbreakers=[
            DealbreakerFinding(dealbreaker=f, state=DealbreakerState.NOT_REACHED)
            for f in spec.red_flags
        ],
        summary=reason,
        recommendation=RecommendationSignal.INSUFFICIENT_SIGNAL,
        coverage_gaps=[c.name for c in spec.competencies],
        conversation_health=health,
        judgment_health=judgment_health,
        questioning_health=questioning,
        interview_duration_seconds=transcript.duration_seconds,
    )
