"""Judge precision on contradictions — real LLM, tolerant suite.

Queued before Milestone 4 for a reason. Today a false positive costs one
slightly odd clarifying question. In the feedback report it becomes a written
claim that a real person contradicted themselves, shown to the people deciding
whether to hire them. Recall matters; precision matters more.

    RUN_BEHAVIOR_TESTS=1 uv run pytest tests/test_judge_precision.py -v -m behavior

Reports precision and recall over the labelled cases rather than asserting on
each one individually, so a single stubborn case does not block the suite while
the aggregate is sound.
"""

import os

import pytest

from bot.brain.brain import JudgmentRequest, Turn
from bot.brain.drivers import OpenAIJudge
from bot.config import Settings
from shared.contracts import KeyClaim

from tests.fixtures.contradiction_cases import CASES, ContradictionCase

pytestmark = [
    pytest.mark.behavior,
    pytest.mark.skipif(
        not os.environ.get("RUN_BEHAVIOR_TESTS"),
        reason="Real LLM calls. Set RUN_BEHAVIOR_TESTS=1 to run.",
    ),
]


@pytest.fixture(scope="module")
def judge() -> OpenAIJudge:
    settings = Settings.load()
    return OpenAIJudge(api_key=settings.openai_api_key, model=settings.blueprint_model)


def request_for(case: ContradictionCase) -> JudgmentRequest:
    """One section in which the candidate says the later statement.

    The earlier claim arrives as a carried claim, which is exactly how a real
    cross-section contradiction reaches the judge.
    """
    return JudgmentRequest(
        section_id="stakeholders",
        kind="depth",
        target_depth=(
            "Describes a real disagreement with a stakeholder, their own role in "
            "it, and the outcome."
        ),
        transcript=[
            Turn(
                index=0,
                speaker="interviewer",
                text="Tell me about a serious disagreement with a senior stakeholder.",
                section_id="stakeholders",
                at_seconds=0.0,
            ),
            Turn(
                index=1,
                speaker="candidate",
                text=case.later_statement,
                section_id="stakeholders",
                at_seconds=10.0,
            ),
        ],
        carried_claims=[
            KeyClaim(text=case.earlier_claim, section_id="product_strategy", turn_index=0)
        ],
    )


async def judge_flags(judge: OpenAIJudge, case: ContradictionCase) -> bool:
    judgment = await judge.assess(request_for(case))
    assert judgment is not None, f"judge returned nothing for {case.label}"
    return len(judgment.contradictions) > 0


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.category == "genuine"], ids=lambda c: c.label
)
async def test_genuine_contradictions_are_detected(judge, case):
    assert await judge_flags(judge, case), f"missed a real contradiction: {case.note}"


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.category != "genuine"], ids=lambda c: c.label
)
async def test_non_contradictions_are_not_flagged(judge, case):
    """Hedges, non-answers and open revisions must all pass unflagged.

    The revision cases matter most: a candidate who says "thinking about it
    more, I'm less sure" is being candid. Flagging that as a contradiction
    would penalise exactly the honesty an interview should reward.
    """
    assert not await judge_flags(judge, case), (
        f"false positive on a {case.category}: {case.note}"
    )


async def test_precision_and_recall_over_all_cases(judge):
    """Aggregate view, reported even when it passes."""
    results = [(case, await judge_flags(judge, case)) for case in CASES]

    true_pos = sum(1 for c, flagged in results if c.should_flag and flagged)
    false_pos = sum(1 for c, flagged in results if not c.should_flag and flagged)
    false_neg = sum(1 for c, flagged in results if c.should_flag and not flagged)

    precision = true_pos / (true_pos + false_pos) if (true_pos + false_pos) else 1.0
    recall = true_pos / (true_pos + false_neg) if (true_pos + false_neg) else 1.0

    print(f"\n  precision {precision:.2f}  recall {recall:.2f}")
    for case, flagged in results:
        if case.should_flag != flagged:
            verdict = "FALSE POSITIVE" if flagged else "MISSED"
            print(f"    {verdict:15} {case.label} ({case.category})")

    # Precision is weighted harder: a false accusation in a feedback report is
    # worse than a missed inconsistency, which a human reviewer can still catch
    # from the transcript.
    assert precision >= 0.8, f"too many false positives (precision {precision:.2f})"
    assert recall >= 0.6, f"missing too many real contradictions (recall {recall:.2f})"
