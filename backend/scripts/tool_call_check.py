"""Does the interviewer model actually call end_interview, and only then?

The unit tests cover the wiring. This is the behaviour question: given the
phrasings a pattern list will never contain, does the model reach for the tool,
and does it leave real answers alone?

    PYTHONPATH=. uv run python scripts/tool_call_check.py

One short model call per line. Cheap.
"""
import asyncio, json
from openai import AsyncOpenAI
from bot.config import Settings
from bot.tools import TOOLS

SHOULD_END = [
    "Can we wrap this up?",
    "I'd like to call it a day",
    "This isn't for me, sorry",
    "I'm not comfortable doing this anymore",
    "I need to jump off",
    "Actually I'm no longer interested",
    "Something has come up, I need to go",
    "I don't think I want to do this",
    "Bas, ab band karo",
    "I don't want to continue this interview. Just end this interview.",
]

SHOULD_NOT_END = [
    "We had to end that project early because of budget",
    "I want to stop doing manual QA, that's why I moved",
    "I'd rather not answer that one",
    "I don't want to talk about my last employer",
    "I'm done with that project now, it shipped last year",
    "Sorry, I should explain, we actually built two of them.",
]

SYSTEM = (
    "You are Mitra, an AI interviewer conducting a voice interview for a "
    "Business Analyst role. Ask one question per turn."
)


async def called(client, model, said) -> bool:
    tools = [{"type": "function", "function": {
        "name": t.name, "description": t.description,
        "parameters": {"type": "object", "properties": t.properties, "required": t.required},
    }} for t in TOOLS.standard_tools]
    r = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "assistant", "content": "Tell me about a project you led."},
                  {"role": "user", "content": said}],
        tools=tools, temperature=0.2,
    )
    return bool(r.choices[0].message.tool_calls)


async def main():
    s = Settings.load()
    client = AsyncOpenAI(api_key=s.openai_api_key)
    print(f"model: {s.llm_model}\n")

    print("SHOULD end the interview")
    hits = 0
    for t in SHOULD_END:
        ok = await called(client, s.llm_model, t)
        hits += ok
        print(f"  {'CALLED ' if ok else 'MISSED '} {t!r}")
    print(f"  -> {hits}/{len(SHOULD_END)}\n")

    print("SHOULD NOT end the interview")
    false_positives = 0
    for t in SHOULD_NOT_END:
        ok = await called(client, s.llm_model, t)
        false_positives += ok
        print(f"  {'CALLED ' if ok else 'ok     '} {t!r}")
    print(f"  -> {false_positives} false positives of {len(SHOULD_NOT_END)}")

asyncio.run(main())
