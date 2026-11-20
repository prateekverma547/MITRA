"""Quote verification, round 4 (A-52): numbers by value, declared name fixes, the
negation guard. Written from the task's requirements; no keys, no network."""

import inspect

import pytest

from feedback.clean import declared_name_fixes
from feedback.score import QuoteAudit, verify_quote
from shared.contracts import QuoteMatch, Speaker, Transcript, TranscriptTurn


def transcript(*candidate_texts):
    turns, i = [], 0
    for text in candidate_texts:
        turns.append(TranscriptTurn(index=i, speaker=Speaker.INTERVIEWER, text="Tell me more.", at_seconds=float(i)))
        turns.append(TranscriptTurn(index=i + 1, speaker=Speaker.CANDIDATE, text=text, at_seconds=float(i + 1)))
        i += 2
    return Transcript(interview_id="int_q", turns=turns, duration_seconds=float(i))


def rule(quote, raw, fixes=None):
    audit = QuoteAudit(declared_fixes=fixes or {})
    found = verify_quote(quote, 1, transcript(raw), audit.declared_fixes, audit)
    return (audit.matched[0]["rule"] if found else None), found


# -- numbers by value -------------------------------------------------------------------------

@pytest.mark.parametrize("quote, raw", [
    ("We cut payout failures by 38%", "So we cut payout failures by thirty-eight percent last year."),
    ("We cut payout failures by thirty-eight percent", "we cut payout failures by 38% last year"),
    ("uptime was 99.95%", "and uptime was ninety-nine point nine five percent all year"),
    ("we reached 50k merchants", "we reached fifty thousand merchants by March"),
    ("2.4 million transactions a day", "about two point four million transactions a day"),
    ("2,400,000 transactions a day", "about 2.4 million transactions a day"),
    ("1.5 lakh orders a month", "around one lakh fifty thousand orders a month"),
    ("1,50,000 orders a month", "around 1.5 lakh orders a month"),
    ("a budget of 3 crore", "we had a budget of three crore for it"),
    ("latency fell to 250 milliseconds", "latency fell to two hundred and fifty milliseconds"),
])
def test_a_number_written_differently_matches_by_value(quote, raw):
    assert rule(quote, raw)[0] == "number_value"


@pytest.mark.parametrize("quote, raw", [
    ("we hired thirty engineers", "we hired 13 engineers"),
    ("we hired 30 engineers", "we hired thirteen engineers"),
    ("cut failures by 3 percent", "cut failures by 30 percent"),
    ("cut failures by 38%", "cut failures by thirty-nine percent"),
    ("2.4 million transactions", "two point five million transactions"),
    ("50k merchants", "fifteen thousand merchants"),
    ("1.5 lakh orders", "1.5 crore orders"),
    ("in 2019", "in twenty nineteen"),          # [20, 19] is not one value: left unmatched
])
def test_different_values_never_match(quote, raw):
    assert rule(quote, raw) == (None, None)


# -- the negation guard -----------------------------------------------------------------------

@pytest.mark.parametrize("quote, raw", [
    ("I did lead the migration", "I did not lead the migration, my manager did."),
    ("I did not lead the migration", "I did lead the migration."),
    ("We cut payout failures", "We can't pay out failures fast enough."),   # task 26's meaning flip
    ("We cut payout failures by 38%", "We can't pay out failures by thirty-eight percent."),
    ("we cut failures by 38%", "we didn't cut failures by thirty-eight percent"),
    ("I have led three launches", "I have never led three launches"),
    ("I did lead 3 migrations", "I did not lead three migrations"),
])
def test_a_negation_difference_never_matches(quote, raw):
    assert rule(quote, raw) == (None, None)


def test_a_negation_difference_never_matches_through_a_declared_fix():
    fixes = declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")]))
    assert rule("I did lead payouts at Razorpay", "I did not lead payouts at Razapay", fixes) == (None, None)


def test_the_guard_holds_even_if_alignment_ever_tolerates_an_extra_word():
    """The guard compares the negation words of the quote and of the words it was
    matched against; whole-word alignment makes the first line of defence, this is the
    second. Shown directly on the guard."""
    from feedback.score import _same_negations

    assert not _same_negations(["i", "did", "lead"], ["i", "did", "not", "lead"])
    assert not _same_negations(["we", "can", "pay"], ["we", "cant", "pay"])
    assert _same_negations(["we", "cut", "38", "percent"], ["we", "cut", "38", "percent"])


# -- declared name fixes ----------------------------------------------------------------------

def cleaned(index, fixes, guard="ok", status="ok"):
    return {"status": status, "turns": [
        {"index": index, "speaker": "candidate", "guard": guard, "text": "...",
         "corrections": [{"from_text": a, "to_text": b} for a, b in fixes]}]}


RAW = "I led payouts at Razapay for three years"
QUOTE = "I led payouts at Razorpay for three years"


def test_a_declared_name_fix_for_that_turn_matches():
    fixes = declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")]))
    assert rule(QUOTE, RAW, fixes)[0] == "declared_fix"


def test_an_undeclared_name_change_never_matches():
    assert rule(QUOTE, RAW) == (None, None)
    other = declared_name_fixes(cleaned(1, [("Maridevan", "Meridian")]))
    assert rule(QUOTE, RAW, other) == (None, None)


def test_a_fix_declared_for_another_turn_never_matches():
    fixes = declared_name_fixes(cleaned(3, [("Razapay", "Razorpay")]))    # turn 3, quote is in turn 1
    t = transcript(RAW, "Something else entirely about routing.")
    audit = QuoteAudit(declared_fixes=fixes)
    assert verify_quote(QUOTE, 1, t, audit.declared_fixes, audit) is None


def test_only_accepted_name_fixes_are_declared():
    assert declared_name_fixes(cleaned(1, [("let", "led")])) == {}, "not a name"
    assert declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")], guard=["new words"])) == {}, "turn failed"
    assert declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")], status="missing")) == {}
    assert declared_name_fixes(cleaned(1, [("can't", "Can")])) == {}, "a negation changed"
    assert declared_name_fixes(None) == {}
    assert declared_name_fixes(cleaned(1, [("multi aquifer", "multi-acquirer")])) == {}, "not a name"


def test_a_declared_fix_never_carries_a_negation_through():
    fixes = declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")]))
    assert rule("I did not lead payouts at Razorpay", "I did lead payouts at Razapay", fixes) == (None, None)


# -- the record and the order -----------------------------------------------------------------

def test_each_rule_is_recorded_and_the_contract_field_is_set():
    assert rule("We shipped it in March.", "We shipped it in March.")[0] == "exact"
    r, q = rule("we shipped it in march", "Um, we shipped it in March, yes.")
    assert r == "normalised" and q.match is QuoteMatch.NORMALISED
    r, q = rule("by 38%", "by thirty-eight percent")
    # C-A7 / C-97: the contract's own values, exactly; never a fallback to "normalised".
    assert r == "number_value" and q.match is QuoteMatch.NUMBER_VALUE and q.match.value == "number_value"
    fixes = declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")]))
    r, q = rule(QUOTE, RAW, fixes)
    assert r == "declared_fix" and q.match is QuoteMatch.DECLARED_FIX and q.match.value == "declared_fix"


def test_nothing_looser_than_whole_words_is_kept():
    assert rule("owned the rollout", "I, uh, owned the rollout.")[0] == "exact"
    assert rule("I owned the rollout", "I, uh, owned the rollout.") == (None, None), "a dropped word"
    assert rule("lead", "it was misleading") == (None, None), "part of a word"
    assert rule("we fine-tuned a model", "we fine tuned a model") == (None, None), "no number: not rule 3"


def test_scoring_reads_the_declared_fixes_and_cleaning_runs_first():
    from bot import persistence
    from feedback import run

    src = inspect.getsource(persistence.save_interview_result)
    assert src.index("_clean_in_the_background(interview_id)") < src.index("_score_in_the_background(interview_id)")
    assert "stored_cleaned_transcript(interview)" in inspect.getsource(run.generate_feedback)
    assert "declared_name_fixes(cleaned_payload)" in inspect.getsource(run.generate_feedback)
    assert "declared_fixes=declared_fixes" in inspect.getsource(run.generate_feedback)


def test_build_report_uses_declared_fixes_end_to_end():
    import json
    from pathlib import Path

    from feedback.score import build_report
    from shared.contracts import InterviewBlueprint

    bp = InterviewBlueprint.model_validate(json.loads(
        (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text()))
    spec = bp.evaluation_spec
    cid = spec.competencies[0].id
    payload = {"competency_scores": [{"competency_id": cid, "score": 3, "coverage": "partial", "rationale": "r",
                                      "evidence": [{"text": QUOTE, "turn_index": 1},
                                                   {"text": "we cut failures by 38%", "turn_index": 3}]}],
               "summary": "s", "recommendation": "limited_evidence"}
    t = transcript(RAW, "and we cut failures by thirty-eight percent")
    with_fix = build_report(interview_id="int_q", blueprint_id="bp", spec=spec, transcript=t, payload=payload,
                            declared_fixes=declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")])))
    without = build_report(interview_id="int_q", blueprint_id="bp", spec=spec, transcript=t, payload=payload)
    assert [q.text for q in with_fix.competency_scores[0].evidence] == [QUOTE, "we cut failures by 38%"]
    assert [q.text for q in without.competency_scores[0].evidence] == ["we cut failures by 38%"]


# -- the negation look-back (A-55, round 5) ---------------------------------------------------

@pytest.mark.parametrize("quote, raw", [
    ("lead the migration", "I did not lead the migration, my manager did."),
    ("shipped the payments API", "We never shipped the payments API."),
    ("own the rollout", "I didn't own the rollout."),
    ("own the rollout", "I didn’t own the rollout."),           # curly apostrophe
    ("own the rollout", "I did n't own the rollout."),               # contraction split by the recogniser
    ("own the rollout", "I didn' t own the rollout."),
    ("own the rollout", "I did not own the rollout."),
    ("lead the migration", "Honestly I couldn't lead the migration."),
    ("own the rollout", "I didn't lead and own the rollout."),       # "and" carries the negation
    ("sure we shipped it", "I'm not so sure we shipped it."),        # "so" carries it too
    ("cut failures by 38%", "We did not cut failures by thirty-eight percent."),     # rule 3
    ("I didn't enjoy it", "I can't say I didn't enjoy it."),         # alone, it reverses the sentence
])
def test_a_quote_taken_from_just_after_a_negation_never_verifies(quote, raw):
    assert rule(quote, raw) == (None, None)


def test_a_quote_after_a_negation_never_verifies_through_a_declared_fix():
    fixes = declared_name_fixes(cleaned(1, [("Razapay", "Razorpay")]))
    assert rule("led payouts at Razorpay", "I never led payouts at Razapay.", fixes) == (None, None)
    assert rule("led payouts at Razorpay", "I led payouts at Razapay.", fixes)[0] == "declared_fix"


@pytest.mark.parametrize("quote, raw", [
    ("did I lead the migration", "Not only did I lead the migration, I also ran the cutover."),
    ("I can't say I didn't enjoy it", "I can't say I didn't enjoy it."),
    ("I led the migration", "I didn't get the budget, but I led the migration."),   # previous clause
    ("I led the migration anyway", "We had no budget, I led the migration anyway."),
    ("I led the migration", "That was not my call. I led the migration."),
    ("I did not lead the migration", "I did not lead the migration, my manager did."),  # the honest quote
    ("lead the migration", "I did not lead the migration in 2019, but in 2021 I did lead the migration."),
    ("we shipped it in March", "It was not easy; we shipped it in March."),
    ("cut failures by 38%", "It wasn't easy, but we cut failures by thirty-eight percent."),
])
def test_a_negation_in_another_clause_or_inside_the_quote_does_not_reject(quote, raw):
    assert rule(quote, raw)[0] is not None


def test_a_dash_or_a_line_break_inside_a_quote_is_still_the_same_words():
    assert rule("cut failure rates dramatically\u2014by almost half", "We cut failure rates dramatically\u2014by almost half.")[0] == "exact"
    assert rule("overall performance. There was some risk", "overall performance.\nThere was some risk.")[0] == "normalised"


def test_the_known_false_rejection_is_recorded_not_hidden():
    """'enjoy it' out of "I can't say I didn't enjoy it" is TRUE (a double negative), but
    a negation sits before it in its clause, so it is refused: the safer mistake, decided.
    It is counted in the audit so the rate is known."""
    audit = QuoteAudit()
    assert verify_quote("enjoy it", 1, transcript("I can't say I didn't enjoy it."), None, audit) is None
    assert audit.negation_rejected == [{"turn_index": 1, "text": "enjoy it"}]


def test_a_refused_quote_takes_the_existing_unverified_path_in_the_report():
    import json
    from pathlib import Path

    from feedback.score import build_report
    from shared.contracts import InterviewBlueprint

    spec = InterviewBlueprint.model_validate(json.loads(
        (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text())).evaluation_spec
    cid = spec.competencies[0].id
    payload = {"competency_scores": [{"competency_id": cid, "score": 4, "coverage": "partial", "rationale": "Led it.",
                                      "evidence": [{"text": "lead the migration", "turn_index": 1}]}],
               "red_flags_observed": [], "summary": "s", "recommendation": "limited_evidence"}
    report = build_report(interview_id="int_q", blueprint_id="bp", spec=spec, payload=payload,
                          transcript=transcript("I did not lead the migration, my manager did."))
    score = report.competency_scores[0]
    assert score.evidence == [] and score.score is None and score.insufficient_signal
    assert score.rationale.startswith("No verifiable transcript evidence supports a score here.")
    assert score.name in report.coverage_gaps


# -- what the scorer is asked to quote (round 7, A-58) -----------------------------------------

def test_the_scoring_prompt_asks_for_whole_clause_quotes():
    from feedback.score import SYSTEM

    assert "Each quote is WHOLE: one complete sentence, or one complete clause" in SYSTEM
    assert "Never start or\n  end a quote in the middle of a clause." in SYSTEM


def test_the_scoring_prompt_limits_quote_length():
    from feedback.score import SYSTEM

    assert "Each quote is at most two sentences and about 40 words." in SYSTEM
