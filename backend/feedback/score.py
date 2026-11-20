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

import re
from dataclasses import dataclass

from loguru import logger
from openai import AsyncOpenAI

from shared.branding import PROSE_STYLE
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
    ObservedStrength,
    QuoteMatch,
    RecommendationSignal,
    Speaker,
    Transcript,
)
from shared.text_models import TextModelError, pick, request_structured

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
- Each quote is WHOLE: one complete sentence, or one complete clause of a
  sentence (from its start to a comma, semicolon or full stop). Never start or
  end a quote in the middle of a clause. A fragment can leave out a "not" or a
  "never" and say the opposite of what they meant, and it is then refused.
- Each quote is at most two sentences and about 40 words. Pick the sentence
  that shows the evidence; use two or three short quotes rather than one long
  one. If a single sentence is longer than that, quote one whole clause of it.
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

STRENGTHS.
List what the transcript shows the candidate doing well, in
`strengths_observed`. Same evidence standard as a red flag: each strength names
the `competency_id` it belongs to and quotes the candidate showing it. A
strength with no quote does not go in. Describe what they did ("explained why
they chose X over Y, with the cost of each"), not a trait ("is a strong
engineer"). Fluency, confidence or a pleasant manner are not strengths here.
Few or none is a fine answer; do not pad the list to balance the gaps.

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
  "strengths_observed": [
    {"competency_id": "must match the spec exactly", "description": "...",
     "evidence": [{"turn_index": 12, "text": "exact words from that turn"}]}
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


# -- the reply's shape, for Structured Outputs ------------------------------------
# Exactly the JSON SYSTEM asks for, with fields picked from the contract types.
# FeedbackReport is not changed: build_report() still assembles it, verifying
# every quote against the transcript and adding at_seconds and the health blocks.

QuoteRef = pick(EvidenceQuote, "turn_index", "text", model_name="QuoteRef")
ScoredCompetency = pick(
    CompetencyScore, "competency_id", "name", "score", "coverage", "insufficient_signal",
    "rationale", model_name="ScoredCompetency", evidence=(list[QuoteRef], ...),
)
FlagRef = pick(ObservedRedFlag, "description", model_name="FlagRef", evidence=(list[QuoteRef], ...))
StrengthRef = pick(
    ObservedStrength, "competency_id", "description", model_name="StrengthRef",
    evidence=(list[QuoteRef], ...),
)
DealbreakerCleared = pick(
    ObservedRedFlag, "description", model_name="DealbreakerCleared", evidence=(list[QuoteRef], ...)
)
ScoringResponse = pick(
    FeedbackReport, "coverage_gaps", "summary", "recommendation",
    model_name="ScoringResponse",
    competency_scores=(list[ScoredCompetency], ...),
    red_flags_observed=(list[FlagRef], ...),
    strengths_observed=(list[StrengthRef], ...),
    dealbreakers_cleared=(list[DealbreakerCleared], ...),
)


@dataclass
class QuoteAudit:
    """What the verification pass did, so it can be logged and tested."""

    kept: int = 0
    reindexed: int = 0
    dropped: int = 0
    scores_downgraded: list[str] = None  # type: ignore[assignment]
    #: The cleaned transcript's declared name fixes, by turn index
    #: (feedback.clean.declared_name_fixes). Input to verification, not a result.
    declared_fixes: dict = None  # type: ignore[assignment]
    #: How many kept quotes matched by each rule (exact, normalised, number_value,
    #: declared_fix), and which rule each kept quote used, in order.
    rules: dict = None  # type: ignore[assignment]
    matched: list = None  # type: ignore[assignment]
    #: Quotes found in a turn but refused because a negation sits between the
    #: clause's start and the quote (A-55). They are then dropped as unverified.
    negation_rejected: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.scores_downgraded is None:
            self.scores_downgraded = []
        if self.declared_fixes is None:
            self.declared_fixes = {}
        if self.rules is None:
            self.rules = {}
        if self.matched is None:
            self.matched = []
        if self.negation_rejected is None:
            self.negation_rejected = []


def _normalise(text: str) -> str:
    """Loose enough to survive punctuation drift, strict enough to catch invention.

    The model tends to tidy a quote, fixing case and punctuation, while keeping
    the substance. That is a formatting difference, not a fabrication, and
    rejecting it would throw away real evidence. Comparing on lowercase letters,
    digits and spaces keeps those and still fails loudly on a sentence the
    candidate never said.

    **A dropped word does not survive.** Verification is a substring test on
    this form, so "I owned the rollout" is NOT found in "I, uh, owned the
    rollout." A quote with a filler word (or any word) removed is dropped as
    unverifiable (P2-02; tests/test_contracts_part2.py
    test_nothing_approximate_is_ever_kept).
    """
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


#: The rule that kept a quote (A-52, round 4), stored as the contract's own
#: QuoteMatch value (C-A7, added by Session C; C-97). Plain members, no fallback:
#: if a value ever disappears from the contract, this fails at import instead of
#: silently storing "normalised" for a quote that matched by number value or a
#: declared fix.
RULE_EXACT, RULE_NORMALISED, RULE_NUMBER_VALUE, RULE_DECLARED_FIX = (
    "exact", "normalised", "number_value", "declared_fix")
_RULE_TO_MATCH = {
    RULE_EXACT: QuoteMatch.EXACT,
    RULE_NORMALISED: QuoteMatch.NORMALISED,
    RULE_NUMBER_VALUE: QuoteMatch.NUMBER_VALUE,
    RULE_DECLARED_FIX: QuoteMatch.DECLARED_FIX,
}


def _find_all(needle: list[str], hay: list[str]) -> list[int]:
    n = len(needle)
    return [i for i in range(len(hay) - n + 1) if n and hay[i:i + n] == needle]


def _find_sublist(needle: list[str], hay: list[str]) -> int:
    found = _find_all(needle, hay)
    return found[0] if found else -1


def _same_negations(a: list[str], b: list[str]) -> bool:
    """The negation guard: the same negation words, in the same order. One
    "not", "never", "can't" or "didn't" more or less is a different statement."""
    from feedback.numbers import NEGATIONS

    return [w for w in a if w in NEGATIONS] == [w for w in b if w in NEGATIONS]


# -- the negation look-back (A-55, round 5) ------------------------------------
#
# A quote that starts just after a negation reverses what was said: "lead the
# migration" out of "I did not lead the migration". So every match, by every
# rule, looks back from its first word to the start of its clause; a negation
# word in that stretch means the quote does not verify (rejecting a true quote
# is the safer mistake, decided). The clause starts at:
#   - the turn's start;
#   - sentence or clause punctuation followed by a space or the end: . ? ! ; : ,
#     (followed by a space, so "2.4", "2,400" and "U.S." do not split);
#   - a spaced dash, or an em or en dash;
#   - a contrast word: but, however, although, though, whereas. These change
#     the polarity's scope; "and", "so" and "because" do NOT end it, because
#     "I didn't lead and own it" and "I'm not so sure we shipped it" carry the
#     negation forward, and accepting those would reverse the meaning.
# "not only" is not a negation ("not only did I lead it").
_CLAUSE_PUNCT = re.compile(r"[.?!;:,](?=\s|$)|\s[-\u2013\u2014]+\s|[\u2013\u2014]")
_CLAUSE_WORDS = frozenset({"but", "however", "although", "though", "whereas"})
#: A contraction the recogniser split ("didn' t", "can ’t"): joined first.
_SPLIT_CONTRACTION = re.compile(r"n\s*['\u2019]\s*t\b|n\s+t\b", re.IGNORECASE)


def _join_contractions(text: str) -> str:
    return _SPLIT_CONTRACTION.sub("nt", text or "")


def _marked(text: str, tokenise) -> tuple[list[str], list[bool]]:
    """The turn's words (by `tokenise`) and, for each, whether it starts a clause
    segment at punctuation. Concatenated, the words equal tokenise(text) except
    that no word runs across clause punctuation."""
    words, starts = [], []
    for segment in _CLAUSE_PUNCT.split(_join_contractions(text)):
        for i, w in enumerate(tokenise(segment)):
            words.append(w)
            starts.append(i == 0)
    return words, starts


def _negated_before(words: list[str], starts: list[bool], at: int) -> bool:
    """A negation between the clause's start and words[at]."""
    from feedback.numbers import NEGATIONS

    if at > 0 and starts[at]:
        return False
    j = at - 1
    while j >= 0:
        w = words[j]
        if w in _CLAUSE_WORDS:
            return False
        if w in NEGATIONS and not (w == "not" and j + 1 < len(words) and words[j + 1] == "only"):
            return True
        if starts[j]:
            return False
        j -= 1
    return False


def _apply_fixes(words: list[str], fixes: list[tuple[list[str], list[str]]],
                 starts: list[bool] | None = None) -> tuple[list[str], list[bool]]:
    out = list(words)
    marks = list(starts) if starts is not None else [False] * len(words)
    for src, dst in fixes:
        at = _find_sublist(src, out)
        if src and at >= 0:
            out[at:at + len(src)] = dst
            marks[at:at + len(src)] = [marks[at]] + [False] * (len(dst) - 1)
    return out, marks


def _normal_words(text: str) -> list[str]:
    # Any whitespace separates words: _normalise deletes a line break without a
    # space, which glued "performance.\nThere" into one word (round 5).
    return _normalise(re.sub(r"\s+", " ", text)).split()


def _match_rule(quote: str, turn_text: str,
                fixes: list[tuple[list[str], list[str]]]) -> tuple[str | None, bool]:
    """(rule, negated): which rule finds the quote in this raw turn, in order,
    and whether a match was found but refused by the negation look-back.

    1. exact: the trimmed quote, character for character;
    2. normalised: lower case, letters, digits and spaces, as whole words;
    3. number_value: every number compared by its value (feedback.numbers), all
       other words unchanged; only for a quote that contains a number;
    4. declared_fix: as 3, after this turn's own declared name fixes are applied
       to the raw words (feedback.clean.declared_name_fixes).
    Every rule: at least one place it matches must have no negation between its
    clause's start and the quote's first word (_negated_before). Rules 3 and 4
    also pass the negation guard on the matched words.
    """
    from feedback.numbers import value_tokens

    negated = False
    quote_j = _join_contractions(quote)
    # The quote is split exactly as the turn is (_marked): an unspaced dash
    # ("dramatically\u2014by") is two words on both sides, never one on one side.
    needle = _marked(quote_j, _normal_words)[0]
    if needle:
        words, starts = _marked(turn_text, _normal_words)
        places = _find_all(needle, words)
        if places:
            if any(not _negated_before(words, starts, at) for at in places):
                return (RULE_EXACT if quote in turn_text else RULE_NORMALISED), False
            negated = True
    q = _marked(quote_j, value_tokens)[0]
    # Rules 3 and 4 exist for numbers and declared names only. A quote with no
    # number gets rule 3 never (otherwise "fine-tuned" would match "fine tuned"
    # through the number tokeniser's hyphen handling: same words, but not a rule
    # this task added) and rule 4 only through a declared fix.
    has_number = any(re.fullmatch(r"\d+(?:\.\d+)?", w) for w in q)
    if not q:
        return None, negated
    raw, starts = _marked(turn_text, value_tokens)
    if has_number:
        for at in _find_all(q, raw):
            if not _same_negations(q, raw[at:at + len(q)]):
                continue
            if _negated_before(raw, starts, at):
                negated = True
                continue
            return RULE_NUMBER_VALUE, False
    if fixes:
        fixed, fixed_starts = _apply_fixes(raw, fixes, starts)
        if fixed != raw:
            for at in _find_all(q, fixed):
                if not _same_negations(q, fixed[at:at + len(q)]):
                    continue
                if _negated_before(fixed, fixed_starts, at):
                    negated = True
                    continue
                return RULE_DECLARED_FIX, False
    return None, negated


def verify_quote(
    raw_text: str, claimed_index: int, transcript: Transcript,
    declared_fixes: dict | None = None, audit: "QuoteAudit | None" = None,
) -> EvidenceQuote | None:
    """Return a quote anchored to a real turn, or None if it was never said.

    A quote found at a different index than claimed is repaired rather than
    dropped: the model miscounted, but the candidate did say the words. A quote
    found nowhere is dropped — that is the fabrication case.

    The rules, in order (`_match_rule`): exact, normalised, number value, and a
    name fix the cleaned transcript declared for THAT turn. Nothing approximate
    is ever kept: every rule compares whole words, and the two value-preserving
    rules refuse any difference in a negation word. `declared_fixes` maps a turn
    index to that turn's fixes; a fix is never used for another turn.
    """
    if not _normalise(raw_text):
        return None

    # Only the interview's candidate turns: pre-interview audio can never be
    # quoted as the candidate's words.
    candidate_turns = [t for t in transcript.interview_turns() if t.speaker == Speaker.CANDIDATE]
    claimed = next((t for t in candidate_turns if t.index == claimed_index), None)
    others = [t for t in candidate_turns if t is not claimed]
    fixes_by_turn = declared_fixes or {}
    refused_in = None

    for turn in ([claimed] if claimed is not None else []) + others:
        rule, negated = _match_rule(raw_text, turn.text, fixes_by_turn.get(turn.index) or [])
        if negated and rule is None and refused_in is None:
            refused_in = turn.index
        if rule is None:
            continue
        quote = raw_text.strip()
        if audit is not None:
            audit.rules[rule] = audit.rules.get(rule, 0) + 1
            audit.matched.append({"rule": rule, "turn_index": turn.index, "text": quote})
        return EvidenceQuote(
            text=quote,
            turn_index=turn.index,
            at_seconds=turn.at_seconds,
            speaker=Speaker.CANDIDATE,
            match=_RULE_TO_MATCH[rule],
            # When it was spoken, so a link seeks the recording correctly.
            audio_start_seconds=turn.audio_start_seconds,
            audio_end_seconds=turn.audio_end_seconds,
        )

    if refused_in is not None and audit is not None:
        # Found, but only just after a negation (A-55): dropped as unverified.
        audit.negation_rejected.append({"turn_index": refused_in, "text": raw_text.strip()})
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

        quote = verify_quote(text, claimed, transcript, audit.declared_fixes, audit)
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
    # Pre-interview audio is never shown to the scorer (task 19: flagged, kept).
    for turn in transcript.interview_turns():
        who = "INTERVIEWER" if turn.speaker == Speaker.INTERVIEWER else "CANDIDATE"
        lines.append(f"[turn {turn.index} | {turn.spoken_start:.0f}s] {who}: {turn.text}")
    return "\n".join(lines)


def _section_record(o: dict) -> dict:
    """What was asked, skipped and declined in one stored section outcome.

    C-A5 (round 6): from SectionOutcome's own fields. Outcomes stored before them
    (round 3 to 5) kept these in the "auditor" block, and are read from there:
    both are read, nothing is migrated, so a stored record is never rewritten.
    A field present on the outcome wins, even when empty."""
    block = o.get("auditor") or {}
    out = {"area_name": block.get("area_name")}
    for key, default in (("questions_asked", []), ("skipped_question_ids", []),
                         ("declined_question_ids", []), ("easier_variants", 0)):
        out[key] = o[key] if key in o else block.get(key, default)
    out["has_record"] = bool(block) or any(k in o for k in ("questions_asked", "skipped_question_ids"))
    return out


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
        audit = _section_record(o)
        if audit["has_record"]:
            # The realtime auditor's record (bot/auditor.py): what was asked, and
            # what the candidate skipped or declined. A skip or decline is not a
            # thin answer; the scorer must not read it as one.
            parts.append(f"asked={','.join(audit.get('questions_asked') or []) or 'none'}")
            if audit.get("skipped_question_ids"):
                parts.append(f"SKIPPED {','.join(q or '?' for q in audit['skipped_question_ids'])}")
            if audit.get("declined_question_ids"):
                parts.append(f"DECLINED {','.join(q or '?' for q in audit['declined_question_ids'])} "
                             f"(easier variant asked {audit.get('easier_variants', 0)}x)")
        lines.append(" · ".join(str(p) for p in parts))
    return "\n".join(lines)


@dataclass
class FeedbackScorer:
    api_key: str
    model: str

    def __post_init__(self) -> None:
        # Retries are the helper's (exactly one), not the SDK's.
        self._client = AsyncOpenAI(api_key=self.api_key, max_retries=0)
        self.last_usage = None

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
        declared_fixes: dict | None = None,
    ) -> FeedbackReport:
        outcomes = section_outcomes or []

        candidate_turns = [t for t in transcript.interview_turns() if t.speaker == Speaker.CANDIDATE]
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
            result = await request_structured(
                job="feedback",
                model=self.model,
                client=self._client,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user},
                ],
                response_model=ScoringResponse,
                temperature=0.2,  # used only by non-reasoning models
            )
        except TextModelError as exc:
            self.last_usage = exc.record
            raise FeedbackError(f"Feedback model call failed: {exc}") from exc
        self.last_usage = result.record
        payload = result.payload

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
            section_outcomes=outcomes,
            declared_fixes=declared_fixes,
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
    section_outcomes: list[dict] | None = None,
    declared_fixes: dict | None = None,
) -> FeedbackReport:
    """Validate and anchor the model's report. Pure — no network, so it is testable.

    This is where the report is made trustworthy: quotes are checked against the
    transcript, scores that lose their evidence are downgraded, and competencies
    the model forgot are added back as unassessed rather than silently missing.
    """
    audit = QuoteAudit(declared_fixes=declared_fixes or {})
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

    strengths = []
    for raw in payload.get("strengths_observed") or []:
        if not isinstance(raw, dict):
            continue
        competency_id = str(raw.get("competency_id", ""))
        if competency_id not in by_id:
            logger.warning(f"[{interview_id}] strength for unknown competency {competency_id!r}")
            continue
        evidence = _verify_evidence(raw.get("evidence"), transcript, audit)
        if not evidence:
            # No evidence, no strength: the same rule as a red flag.
            logger.warning(
                f"[{interview_id}] dropped an unevidenced strength: "
                f"{str(raw.get('description'))[:80]!r}"
            )
            continue
        strengths.append(
            ObservedStrength(
                competency_id=competency_id,
                description=str(raw.get("description", "")).strip(),
                evidence=evidence,
            )
        )

    dealbreakers = _dealbreaker_findings(payload, spec, red_flags, transcript, audit)

    gaps = [str(g) for g in (payload.get("coverage_gaps") or [])]
    for score in scored.values():
        if score.insufficient_signal and score.name not in " ".join(gaps):
            gaps.append(score.name)
    # Skips and declines from the realtime auditor, in code: always in the report,
    # whatever the model wrote, and never as a judgement of the candidate's ability.
    for o in section_outcomes or []:
        # Not `audit`: that name is the quote-verification record used below.
        auditor_block = _section_record(o)
        name = auditor_block.get("area_name") or o.get("section_id")
        skipped = [q for q in auditor_block.get("skipped_question_ids") or []]
        declined = [q for q in auditor_block.get("declined_question_ids") or []]
        # A-63: a None id is a question the auditor could not identify; it is filed
        # under the area it happened in, and says so.
        unknown = "a question (not identified; filed under the area it happened in)"
        if skipped:
            gaps.append(f"{name}: the candidate skipped {', '.join(q or unknown for q in skipped)}.")
        if declined:
            gaps.append(f"{name}: the candidate declined {', '.join(q or unknown for q in declined)}; "
                        f"an easier variant was asked {auditor_block.get('easier_variants', 0)} time(s).")

    ordered = [scored[c.id] for c in spec.competencies]

    report = FeedbackReport(
        interview_id=interview_id,
        blueprint_id=blueprint_id,
        role_title=spec.role_title,
        competency_scores=ordered,
        red_flags_observed=red_flags,
        strengths_observed=strengths,
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
        f"{len(audit.scores_downgraded)} scores downgraded; matched by rule {audit.rules}; "
        f"{len(audit.negation_rejected)} refused after a negation"
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
