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

from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallResultProperties

from bot.brain.withdrawal import CONFIRMATION_QUESTION, asked_explicitly, asked_to_skip_question

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
        "Do NOT call it when they want to leave THIS QUESTION and carry on: "
        "'can we skip it', 'I don't know this one, can we skip it', 'can we "
        "move on', 'next question', 'can we come back to that'. Asking to skip "
        "a question is asking for the next question, not for the interview "
        "to end. Say that is fine and ask the next numbered question.\n\n"
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
        # So: recall from the model, precision from the patterns. Since decision
        # 11 the patterns no longer decide whether to end at once (every request
        # is confirmed once); they still tell a real request to stop from a
        # request to skip a question.
        heard = brain.last_candidate_text()
        corroborated = asked_explicitly(said) or asked_explicitly(heard)

        # **A third outcome, because the offer itself was the bug.** Live in
        # `int_7e31aaff4ab2` the candidate said "right now, I don't know the
        # answer of this question. Can we skip it?" and was asked whether they
        # would like to end the interview. The corroboration above worked — the
        # patterns disagreed, so nothing ended — but being asked to confirm
        # leaving is not a free sentence when all they wanted was a different
        # question.
        #
        # Only ever consulted when the patterns did NOT corroborate, so a
        # candidate who plainly asks to stop is never mistaken for a skip. And
        # `asked_to_skip_question` refuses to call anything a skip if it names
        # the interview, carries an end verb, or holds any stop phrasing at all.
        skipping = not corroborated and (
            asked_to_skip_question(said) or asked_to_skip_question(heard)
        )

        if skipping:
            # The declined turn is recorded by `observe`, which sees the same
            # utterance through `asked_to_skip_question` and routes it to the
            # refusal machinery: acknowledge, and ask something else on the same
            # topic. Nothing to do here but decline to offer to stop.
            logger.info(
                f"interviewer called {END_INTERVIEW} on a request to skip a "
                f"question -> treating as a declined question | said={said!r}"
            )
            if on_called is not None:
                on_called(said=said, explicit=explicit)
            await params.result_callback(
                {
                    "ended": False,
                    "next": (
                        "They asked to skip that question, not to end the "
                        "interview. Say that is fine in one short sentence, then "
                        "ask a different question. Do not ask why, and do not "
                        "ask whether they want to stop."
                    ),
                }
            )
            return

        # Decision 11 (docs/DEV_PLAN.md:85): every request gets one
        # confirmation, spoken by code word for word; any yes, or a second
        # request, ends it. The brain decides which of those this call is.
        outcome = brain.candidate_asked_to_stop(said=said, explicit=explicit)
        logger.info(
            f"interviewer called {END_INTERVIEW}: model_explicit={explicit} "
            f"patterns_agree={corroborated} -> {outcome} | said={said!r}"
        )
        if on_called is not None:
            on_called(said=said, explicit=explicit)

        if outcome == "offered":
            # Spoken by code, not by the model, and the model does not reply
            # this turn (run_llm=False), so it cannot add a second question.
            brain.take_confirmation_due()
            await params.llm.push_frame(TTSSpeakFrame(CONFIRMATION_QUESTION))
            await params.result_callback(
                {"ended": False, "confirmation_spoken": CONFIRMATION_QUESTION},
                properties=FunctionCallResultProperties(run_llm=False),
            )
            return

        if outcome == "declined":
            # They have just said no to the confirmation. Neither end nor ask
            # again: carry on.
            await params.result_callback(
                {
                    "ended": False,
                    "next": "They said they want to carry on. Continue the "
                    "interview with your next question. Do not mention stopping.",
                }
            )
            return

        # "withdrew" or "already_withdrawn": the brain is in the closing.
        # Truthy, or Pipecat never runs the completion that speaks the
        # goodbye. See the docstring: returning None is silence.
        await params.result_callback(
            {
                "ended": True,
                "next": (
                    "The interview is over. Thank them for their time in one or "
                    "two short sentences and say goodbye. Do not ask anything."
                ),
            },
            properties=FunctionCallResultProperties(run_llm=outcome == "withdrew"),
        )

    llm.register_function(END_INTERVIEW, handle_end_interview)
