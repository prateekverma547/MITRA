"""LLM adapter — the interview brain's model backend.

**Vendor swap point.** OpenAI for the POC; must remain swappable to Anthropic in
this one file (CLAUDE.md).

Note the boundary: this file supplies the *model*. From Milestone 3 the
interview *logic* lives in `bot/brain*` as a standalone text-in/text-out module
that never imports Pipecat. Keep conversation policy out of here.
"""

from pipecat.services.openai.llm import OpenAILLMService

# Which model runs the live conversation is set by config (OPENAI_LLM_MODEL,
# defaulting to gpt-4.1-mini). See CLAUDE.md model tiering: never a reasoning
# model on this path.
#
# Latency baseline on gpt-4.1 (session dev-42b6ab2a): generation_ms median
# 1307.6 across 6 turns (min 1115.8, max 1401.9), where generation = turn
# finalised -> first bot audio, i.e. LLM TTFT + TTS TTFB.
#
# The mini default is a bet on the sectioned-brain architecture keeping live
# context small enough that a mini-tier model suffices. The guardrail in
# CLAUDE.md decides it: if mini accepts hollow answers, drifts, or softens the
# role redirect, the default goes back to gpt-4.1. Probing quality outranks
# speed and cost.


def build_llm(
    *,
    api_key: str,
    system_instruction: str,
    model: str,
) -> OpenAILLMService:
    """Streaming chat completion service for the live conversation."""
    return OpenAILLMService(
        api_key=api_key,
        settings=OpenAILLMService.Settings(
            model=model,
            system_instruction=system_instruction,
            # Low but not zero: questions should vary naturally between
            # candidates without the interviewer becoming unpredictable.
            temperature=0.6,
        ),
    )
