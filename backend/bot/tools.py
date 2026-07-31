"""Letting the interviewer act on what it already understands.

Detecting "I want to stop" by pattern will never work, and there is a
measurement rather than an opinion behind that. The pattern matcher in
`brain/withdrawal.py` was rewritten once after failing live, and even rewritten
it misses every one of these:

    "Can we wrap this up?"          "This isn't for me, sorry"
    "I'd like to call it a day"     "I need to jump off"
    "Actually I'm no longer interested"     "Bas, ab band karo"

Sixteen out of sixteen natural phrasings, because a phrase list only contains
what somebody thought of in advance.

The model reading those sentences understands all of them perfectly well. It
just had no way to act, so its understanding stayed trapped in prose we then
tried to pattern-match back out. This gives it a function to call instead.

**This does not put a judgement on the critical path.** It is the same inference
call the interviewer was already making; the tool costs a few tokens of schema
and nothing else. No second model, no second request, nothing to wait for. The
brain acts on the tool call, so compliance is structural rather than a matter of
the model following an instruction in prose, which the contradiction work showed
is the difference between 100% and 0%.

Patterns stay as the instant path. They fire in microseconds with no model
involved, and they cover the explicit cases that turn up most.
"""

from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from bot.brain.withdrawal import asked_explicitly

END_INTERVIEW = "end_interview"

#: Written to be easy to call correctly and hard to call by accident. The
#: "not this" cases are the ones that would end an interview somebody wanted to
#: continue, which is the only expensive mistake available here.
_END_INTERVIEW_SCHEMA = FunctionSchema(
    name=END_INTERVIEW,
    description=(
        "Call this the moment the candidate asks to stop or leave the "
        "interview, in any wording and in any language. Call it instead of "
        "replying, not as well as replying.\n\n"
        "Examples that MUST call it: 'I don't want to continue', 'just end "
        "the interview', 'can we wrap this up', 'this isn't for me', 'I need "
        "to go', 'I'm not comfortable doing this', 'can we reschedule', "
        "'I've changed my mind', 'I'd like to call it a day'.\n\n"
        "Do NOT call it when they are talking about something other than this "
        "interview: 'we had to end that project early', 'I want to stop doing "
        "manual QA', 'I'd rather not answer that one', 'I don't want to talk "
        "about my last employer'. Declining a question is not leaving.\n\n"
        "If you are unsure whether they mean this interview or something they "
        "are describing, do not call it. Ask them."
    ),
    properties={
        "said": {
            "type": "string",
            "description": (
                "What the candidate said, in their words, so the record shows "
                "why the interview ended."
            ),
        },
        "explicit": {
            "type": "boolean",
            "description": (
                "True when they instructed you plainly ('end the interview'). "
                "False when it is softer or could be about something else, in "
                "which case they will be asked once to confirm."
            ),
        },
    },
    required=["said", "explicit"],
)

TOOLS = ToolsSchema(standard_tools=[_END_INTERVIEW_SCHEMA])


def register(llm, brain, on_called=None) -> None:
    """Wire the tool into the live LLM service.

    The handler moves the brain to the closing and then returns a result, which
    is what makes the interviewer actually say goodbye.

    **Returning nothing does not work**, and it failed live exactly that way.
    Pipecat only runs the follow-up completion `if frame.result:`, so a handler
    returning None produces no speech at all: the candidate asked to stop, the
    tool fired correctly, and then thirty seconds of silence while the brain sat
    in the closing waiting for a goodbye that was never generated. The result
    below is deliberately truthy for that reason.

    What it says is left to the closing prompt, which the brain has already
    switched to, so the farewell reads the same however the interview ended.
    """

    async def handle_end_interview(params) -> None:
        arguments: dict[str, Any] = params.arguments or {}
        said = str(arguments.get("said", "")).strip()
        explicit = bool(arguments.get("explicit", True))

        # The two mechanisms check each other, because each is strong where
        # the other is weak. Measured against gpt-4.1-mini: the model caught
        # 10 of 10 phrasings the patterns miss entirely, and called the tool on
        # 2 of 6 sentences it should have left alone, including "we had to end
        # that project early because of budget". The patterns produced no false
        # positives at all but only catch wording somebody thought of first.
        #
        # So: recall from the model, precision from the patterns. When both
        # agree the interview ends at once. When only the model does, they are
        # asked once, which costs a sentence if it was wrong and an interview
        # if it was not.
        corroborated = asked_explicitly(said) or asked_explicitly(
            brain.last_candidate_text()
        )
        act_now = explicit and corroborated

        logger.info(
            f"interviewer called {END_INTERVIEW}: model_explicit={explicit} "
            f"patterns_agree={corroborated} -> {'ending' if act_now else 'asking'} "
            f"| said={said!r}"
        )
        brain.candidate_asked_to_stop(said=said, explicit=act_now)
        if on_called is not None:
            on_called(said=said, explicit=explicit)

        # Truthy, or Pipecat never runs the completion that speaks the
        # goodbye. See the docstring: returning None is silence.
        await params.result_callback(
            {
                "ended": act_now,
                "next": (
                    "The interview is over. Thank them for their time in one or "
                    "two short sentences and say goodbye. Do not ask anything."
                )
                if act_now
                else (
                    "Ask, in one short sentence, whether they would like to end "
                    "here or carry on. Do not ask why."
                ),
            }
        )

    llm.register_function(END_INTERVIEW, handle_end_interview)
