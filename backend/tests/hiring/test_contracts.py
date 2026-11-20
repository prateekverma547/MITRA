"""The hiring contracts: their fixtures, their arithmetic, and the stage rules.

These are the shapes Track I and the console build against, so a fixture that
stopped matching its contract, or a score a model could type in, would break
the other track silently. Each test below pins one of those.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.screening import (
    CompetencyRating,
    FitAnalysis,
    HRQuestion,
    HRQuestionSet,
    HRRoundEvaluation,
    compute_fit,
)
from shared.stages import (
    ALLOWED_MOVES,
    REASON_REQUIRED,
    TERMINAL,
    Actor,
    Decision,
    allowed_next,
    can_move,
)
from shared.stages import (
    CandidateStage as S,
)

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"
DOCUMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "documents"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _words(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---- fixtures ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name, contract",
    [
        ("fit_analysis.json", FitAnalysis),
        ("hr_questions.json", HRQuestionSet),
        ("hr_round_evaluation.json", HRRoundEvaluation),
        ("decision.json", Decision),
    ],
)
def test_every_fixture_matches_its_contract(name, contract):
    contract.model_validate(load(name))


def test_every_cv_quote_in_the_fit_fixture_is_really_in_the_cv():
    """A fixture is copied by whoever builds against it. An invented quote here
    would teach the screen to show evidence nobody wrote."""
    cv = _words((DOCUMENTS / "cv_karandeep_aiml.txt").read_text())
    fit = FitAnalysis.model_validate(load("fit_analysis.json"))
    for rating in fit.ratings:
        for quote in rating.evidence:
            assert _words(quote) in cv, f"{rating.competency_id}: {quote!r} is not in the CV"


def test_the_fixtures_describe_the_same_candidate():
    fit = load("fit_analysis.json")
    assert load("hr_questions.json")["candidate_id"] == fit["candidate_id"]
    assert load("hr_round_evaluation.json")["candidate_id"] == fit["candidate_id"]
    assert load("decision.json")["candidate_id"] == fit["candidate_id"]


# ---- the fit score ----------------------------------------------------------


def rating(cid: str, weight: float, value: int | None) -> CompetencyRating:
    evidence = ["a line from the CV"] if value else []
    return CompetencyRating(
        competency_id=cid, competency_name=cid, weight=weight, rating=value, evidence=evidence
    )


def test_the_score_is_weight_times_rating_over_the_assessed_weight():
    # The dry run in the plan: 200 / 360 = 55.6%, judged on 90% of the weight.
    ratings = [
        rating("llm", 25, 3),
        rating("adapt", 20, 2),
        rating("eval", 20, 2),
        rating("voice", 15, 1),
        rating("deploy", 10, 3),
        rating("client", 10, None),
    ]
    assert compute_fit(ratings) == (55.6, 90.0)


def test_no_evidence_is_left_out_of_the_score_not_counted_as_zero():
    with_gap = [rating("a", 0.5, 4), rating("b", 0.5, None)]
    with_zero = [rating("a", 0.5, 4), rating("b", 0.5, 0)]
    assert compute_fit(with_gap) == (100.0, 50.0)
    assert compute_fit(with_zero) == (50.0, 100.0)


def test_a_cv_with_no_evidence_at_all_scores_nothing_and_says_so():
    assert compute_fit([rating("a", 1, None)]) == (0.0, 0.0)


def test_a_model_typed_score_that_disagrees_with_the_ratings_is_refused():
    ratings = [rating("a", 1, 2)]
    with pytest.raises(ValidationError, match="compute_fit"):
        FitAnalysis(
            candidate_id="c", job_id="j", ratings=ratings,
            score_percent=80.0, assessed_weight_percent=100.0, summary="",
        )


def test_a_rating_above_zero_must_quote_the_cv():
    with pytest.raises(ValidationError, match="must quote the CV"):
        CompetencyRating(competency_id="a", competency_name="A", weight=1, rating=3)


def test_the_threshold_only_labels_and_defaults_to_fifty():
    low = FitAnalysis.from_ratings(
        candidate_id="c", job_id="j", ratings=[rating("a", 1, 1)], summary=""
    )
    assert low.threshold_percent == 50.0
    assert low.score_percent == 25.0
    assert low.below_threshold


# ---- HR questions -----------------------------------------------------------


def questions(n: int) -> list[HRQuestion]:
    return [HRQuestion(id=f"hr{i}", text=f"Q{i}", why="w") for i in range(n)]


def test_the_hr_round_gets_exactly_ten_questions():
    HRQuestionSet(candidate_id="c", job_id="j", questions=questions(10))
    for n in (9, 11):
        with pytest.raises(ValidationError):
            HRQuestionSet(candidate_id="c", job_id="j", questions=questions(n))


def test_question_ids_are_unique():
    qs = questions(10)
    qs[1] = HRQuestion(id="hr0", text="dup", why="w")
    with pytest.raises(ValidationError, match="unique"):
        HRQuestionSet(candidate_id="c", job_id="j", questions=qs)


# ---- the stage machine ------------------------------------------------------


def test_hr_cannot_reject_after_the_technical_round():
    """Decided 2026-11-18: after the technical round HR can only send the
    candidate to the BU head, and the BU head decides. Since 2026-11-20 HR may also
    book another MITRA round (a retake); still never reject."""
    assert not can_move(S.TECHNICAL_DONE, S.REJECTED, Actor.HR)
    assert allowed_next(S.TECHNICAL_DONE, Actor.HR) == [S.WITH_BU_HEAD, S.TECHNICAL_BOOKED]
    assert can_move(S.WITH_BU_HEAD, S.REJECTED, Actor.BU_HEAD)


def test_hr_rejects_at_screening_and_after_the_hr_round():
    assert can_move(S.SCREENED, S.REJECTED, Actor.HR)
    assert can_move(S.HR_DONE, S.REJECTED, Actor.HR)


def test_the_system_never_makes_a_judgement():
    """System moves record facts. Rejecting, parking or deciding is a person's."""
    judgements = {S.REJECTED, S.PARKED, S.SHORTLISTED, S.ON_HOLD, S.READY_FOR_HR,
                  S.READY_FOR_TECHNICAL, S.WITH_BU_HEAD}
    for (frm, to), who in ALLOWED_MOVES.items():
        if Actor.SYSTEM in who and to in judgements:
            # Moving a booking back to "ready" after a cancellation or no-show is
            # the one place the system lands on a ready stage, and it is a fact.
            assert frm in {S.HR_BOOKED, S.TECHNICAL_BOOKED}, (frm, to)


def test_the_bu_head_acts_only_once_the_candidate_is_with_them():
    for (frm, _), who in ALLOWED_MOVES.items():
        if Actor.BU_HEAD in who:
            assert frm in {S.WITH_BU_HEAD, S.ON_HOLD}


def test_rejection_always_needs_a_reason():
    assert S.REJECTED in REASON_REQUIRED


def test_terminal_stages_have_no_way_out_and_every_other_stage_has_one():
    sources = {frm for frm, _ in ALLOWED_MOVES}
    for stage in S:
        if stage in TERMINAL:
            assert stage not in sources, stage
        else:
            assert stage in sources, f"{stage} is a dead end"


def test_every_stage_is_reachable_from_new():
    reachable, frontier = {S.NEW}, [S.NEW]
    while frontier:
        here = frontier.pop()
        for (frm, to) in ALLOWED_MOVES:
            if frm == here and to not in reachable:
                reachable.add(to)
                frontier.append(to)
    assert reachable == set(S)


def test_a_high_score_on_too_little_of_the_job_does_not_pass_the_screen():
    thin = FitAnalysis.from_ratings(
        candidate_id="c", job_id="j", summary="",
        ratings=[rating("a", 0.2, 4), rating("b", 0.8, None)],
    )
    assert thin.score_percent == 100.0 and not thin.below_threshold
    assert not thin.enough_evidence and not thin.passes_screen
    full = FitAnalysis.from_ratings(
        candidate_id="c", job_id="j", summary="",
        ratings=[rating("a", 0.8, 3), rating("b", 0.2, None)],
    )
    assert full.enough_evidence and full.passes_screen
