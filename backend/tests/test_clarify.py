"""Deterministic tests for clarification-chat response handling.

No LLM. These cover the parsing and spec-repair layer — the parts that must
behave regardless of what the model returns.
"""

import pytest

from blueprint.clarify import ClarificationChat, ClarificationError, _build_spec


class FakeCompletions:
    def __init__(self, payload: str):
        self._payload = payload

    async def create(self, **kwargs):
        class Message:
            content = self._payload

        class Choice:
            message = Message()

        class Response:
            choices = [Choice()]

        return Response()


def chat_returning(payload: str) -> ClarificationChat:
    chat = ClarificationChat(api_key="sk-test", model="test")
    chat._client.chat.completions = FakeCompletions(payload)
    return chat


async def test_null_reply_is_rejected_rather_than_printed_as_None():
    """`str(None)` is the truthy string "None", which once reached the employer
    as a message reading literally "AI : None"."""
    chat = chat_returning('{"reply": null, "done": false}')

    with pytest.raises(ClarificationError, match="no reply text"):
        await chat.next_turn(jd_text="a job", history=[])


async def test_missing_reply_is_rejected():
    chat = chat_returning('{"done": false}')

    with pytest.raises(ClarificationError, match="no reply text"):
        await chat.next_turn(jd_text="a job", history=[])


async def test_inferred_items_are_carried_through():
    """Assumptions the employer never stated must reach the caller so the panel
    can show them, instead of passing on a skimmed "looks right"."""
    chat = chat_returning(
        '{"reply": "Here is the summary.", "done": false, '
        '"inferred": ["Seniority inferred from the JD.", "  ", 7]}'
    )

    turn = await chat.next_turn(jd_text="a job", history=[])

    assert turn.inferred == ["Seniority inferred from the JD."]


async def test_done_without_a_spec_does_not_end_the_conversation():
    chat = chat_returning('{"reply": "Almost there.", "done": true, "spec": null}')

    turn = await chat.next_turn(jd_text="a job", history=[])

    assert turn.done is False
    assert turn.spec is None


def test_weights_are_normalised_rather_than_rejected():
    """Models reliably return sensible relative weights that sum to 0.99.
    Failing a whole conversation over rounding would be absurd."""
    spec = _build_spec(
        {
            "role_title": "Senior PM",
            "seniority": "Senior",
            "experience_expectation": "10+ years",
            "competencies": [
                {"id": "a", "name": "A", "description": "a", "weight": 0.5},
                {"id": "b", "name": "B", "description": "b", "weight": 0.4},
                {"id": "c", "name": "C", "description": "c", "weight": 0.05},
            ],
        }
    )

    assert sum(c.weight for c in spec.competencies) == pytest.approx(1.0)
    # Relative ordering survives normalisation.
    assert spec.competencies[0].weight > spec.competencies[1].weight


def test_genuinely_malformed_spec_still_fails():
    with pytest.raises(ClarificationError, match="invalid EvaluationSpec"):
        _build_spec({"role_title": "Senior PM"})  # no competencies, no seniority


def test_duration_outside_the_allowed_range_is_rejected():
    with pytest.raises(ClarificationError):
        _build_spec(
            {
                "role_title": "Senior PM",
                "seniority": "Senior",
                "experience_expectation": "10+ years",
                "duration_minutes": 240,
                "competencies": [
                    {"id": "a", "name": "A", "description": "a", "weight": 1.0}
                ],
            }
        )
