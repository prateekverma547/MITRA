"""Deterministic tests for the judge driver. No network, no real LLM.

`tests/test_judge_precision.py` covers what the judge *decides*, against a real
model. This covers what it does when the call fails, which is the part that runs
in every live interview and used to happen in complete silence.

The contract is deliberately narrow: a judge failure returns None so the brain
falls back to heuristics, and it says so out loud so a dead judge is
distinguishable from a quiet candidate.
"""

from contextlib import contextmanager

from loguru import logger

from bot.brain.brain import JudgmentRequest, Turn
from bot.brain.drivers import OpenAIJudge


@contextmanager
def captured_warnings():
    """Collect what loguru emits, since it does not route through caplog."""
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


class ExplodingClient:
    """Stands in for AsyncOpenAI when the model name is wrong or the key is dead."""

    class chat:
        class completions:
            @staticmethod
            async def create(**kwargs):
                raise RuntimeError("model_not_found")


def a_request() -> JudgmentRequest:
    return JudgmentRequest(
        section_id="product_strategy",
        kind="depth",
        target_depth="a named decision with a trade-off behind it",
        transcript=[
            Turn(
                index=0,
                speaker="candidate",
                text="I moved us to usage based pricing.",
                section_id="product_strategy",
                at_seconds=4.0,
            )
        ],
        carried_claims=[],
    )


def a_judge(model: str = "gpt-4.1-does-not-exist") -> OpenAIJudge:
    judge = OpenAIJudge(api_key="sk-not-a-real-key", model=model)
    judge._client = ExplodingClient()
    return judge


async def test_a_failed_judgement_still_returns_none():
    """The fallback the brain depends on. Unchanged, and it must stay that way."""
    assert await a_judge().assess(a_request()) is None


async def test_a_failed_judgement_says_so():
    judge = a_judge()

    with captured_warnings() as warnings:
        await judge.assess(a_request())

    assert warnings, "the judge failed silently"
    assert any("judgement failed" in line for line in warnings)


async def test_the_log_line_names_the_section_and_the_model():
    """The likeliest cause is a bad OPENAI_BLUEPRINT_MODEL, so name it."""
    judge = a_judge(model="gpt-4.1-does-not-exist")

    with captured_warnings() as warnings:
        await judge.assess(a_request())

    line = "\n".join(warnings)
    assert "product_strategy" in line
    assert "gpt-4.1-does-not-exist" in line
    assert "RuntimeError" in line


async def test_the_call_is_still_counted_when_it_fails():
    """`calls` counts attempts, not successes; a session of failures is visible."""
    judge = a_judge()

    await judge.assess(a_request())
    await judge.assess(a_request())

    assert judge.calls == 2
