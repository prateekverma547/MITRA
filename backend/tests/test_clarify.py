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
    """Still refused, but the message is now for an employer rather than a
    validator: no Pydantic output, and it ends in a question they can answer."""
    with pytest.raises(ClarificationError, match="Could you tell me again"):
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


# -- interview length --------------------------------------------------------
#
# The contract allows 20 to 90 minutes. The chat never asked about it, so every
# spec silently took the 40-minute default. An employer who asked for something
# shorter got a Pydantic validation error with a link to Pydantic's own
# documentation, and then could not escape it: the number they had been told was
# "noted" stayed in the conversation, so every retry regenerated the same
# invalid spec. Four replies, four identical failures, then 502s.


import json

from shared.contracts import (
    DEFAULT_DURATION_MINUTES,
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
)

VALID_COMPETENCIES = [
    {"id": "a", "name": "A", "description": "d", "weight": 0.5},
    {"id": "b", "name": "B", "description": "d", "weight": 0.5},
]


def done_payload(**spec_overrides) -> str:
    spec = {
        "role_title": "Senior Product Manager",
        "seniority": "Senior",
        "experience_expectation": "10+ years",
        "duration_minutes": DEFAULT_DURATION_MINUTES,
        "overrun_grace_minutes": 5,
        "language": "en",
        "tone": "warm",
        "competencies": VALID_COMPETENCIES,
        "red_flags": [],
    }
    spec.update(spec_overrides)
    return json.dumps({"reply": "Summary confirmed.", "done": True, "inferred": [], "spec": spec})


async def test_the_chat_is_told_to_ask_how_long_the_interview_runs():
    """It never did, which is why every spec took the default."""
    from blueprint.clarify import SYSTEM

    assert "YOU MUST ASK THIS" in SYSTEM
    assert "How long should this interview run?" in SYSTEM


async def test_the_range_is_stated_in_the_prompt_with_no_room_to_negotiate():
    """Removing the alternative beats adding emphasis (DECISIONS.md)."""
    from blueprint.clarify import SYSTEM

    assert f"ONLY LENGTHS THAT EXIST ARE {MIN_DURATION_MINUTES} TO {MAX_DURATION_MINUTES}" in SYSTEM
    assert "do not negotiate toward it" in SYSTEM


async def test_a_duration_under_the_minimum_does_not_produce_a_spec():
    chat = chat_returning(done_payload(duration_minutes=5))

    reply = await chat.next_turn(jd_text="jd", history=[])

    assert reply.spec is None
    assert reply.done is False, "an out-of-range spec must not complete the conversation"


async def test_an_out_of_range_duration_is_explained_in_plain_language():
    """A recruiter was shown a Pydantic error linking to Pydantic's docs."""
    chat = chat_returning(done_payload(duration_minutes=5))

    reply = await chat.next_turn(jd_text="jd", history=[])

    assert "validation error" not in reply.reply.lower()
    assert "pydantic" not in reply.reply.lower()
    assert "type=greater_than_equal" not in reply.reply
    # It states the rule and offers the nearest thing that works.
    assert str(MIN_DURATION_MINUTES) in reply.reply
    assert str(MAX_DURATION_MINUTES) in reply.reply
    assert "5 minutes" in reply.reply


async def test_a_rejected_spec_continues_the_conversation_instead_of_erroring():
    """The escape from the dead end.

    The correction comes back as an ordinary assistant turn, so it is stored in
    the history like any other. The next attempt therefore sees it, and cannot
    regenerate the identical spec from the identical context.
    """
    chat = chat_returning(done_payload(duration_minutes=5))

    reply = await chat.next_turn(jd_text="jd", history=[])

    # Not an exception, so `POST /clarify` does not 502 and the turn is stored.
    assert reply.done is False
    assert reply.reply
    assert reply.spec is None


async def test_a_duration_over_the_maximum_is_refused_the_same_way():
    chat = chat_returning(done_payload(duration_minutes=180))

    reply = await chat.next_turn(jd_text="jd", history=[])

    assert reply.spec is None
    assert "180 minutes" in reply.reply
    assert str(MAX_DURATION_MINUTES) in reply.reply


async def test_a_duration_inside_the_range_reaches_the_spec():
    """The regression guard for the bug that started this: the number the
    employer chose has to survive all the way to the stored value."""
    chat = chat_returning(done_payload(duration_minutes=25))

    reply = await chat.next_turn(jd_text="jd", history=[])

    assert reply.done is True
    assert reply.spec is not None
    assert reply.spec.duration_minutes == 25, "the employer's chosen length was lost"


async def test_the_boundaries_themselves_are_accepted():
    for minutes in (MIN_DURATION_MINUTES, MAX_DURATION_MINUTES):
        reply = await chat_returning(done_payload(duration_minutes=minutes)).next_turn(
            jd_text="jd", history=[]
        )
        assert reply.spec is not None and reply.spec.duration_minutes == minutes


async def test_a_malformed_spec_still_gets_a_sentence_rather_than_a_trace():
    """Any other validation failure must not leak the validator either."""
    # Weights are normalised before validation, so an out-of-range weight is
    # repaired rather than refused. A missing required field is not.
    payload = json.loads(done_payload())
    del payload["spec"]["role_title"]
    chat = chat_returning(json.dumps(payload))

    reply = await chat.next_turn(jd_text="jd", history=[])

    assert reply.spec is None
    assert "validation error" not in reply.reply.lower()
    assert reply.reply.endswith("?"), "a dead end needs a way forward"
