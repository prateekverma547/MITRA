"""Which models can this key call? Track I's model slots against the account.

    cd backend && uv run python scripts/check_models.py            # free: list only
    cd backend && uv run python scripts/check_models.py --probe    # a few cents

Default: lists the models the key can see (GET /v1/models, free) and, for each
Track I slot (bot/config.py), the model it resolves to and whether it is listed.
Also says whether gpt-6.2-sol is listed (the target text model, DEV_PLAN.md
section 3).

--probe: for each distinct text-slot model, one tiny Structured Outputs request
through the shared helper (shared/text_models.py), reporting callable or not,
tokens, latency and cost. For a reasoning model, a second tiny request sends
`temperature`, to check the rule the helper relies on (reasoning models get no
temperature). Realtime models are not probed here: scripts/realtime_smoke.py
does that.

The key is read the way the code reads it and never printed.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel  # noqa: E402

from bot import config  # noqa: E402  (loads .env)
from shared.text_models import (  # noqa: E402
    TextModelError,
    is_reasoning_model,
    request_structured,
)

TEXT_SLOTS = {
    "OPENAI_TEXT_MODEL": config.text_model,
    "OPENAI_BLUEPRINT_MODEL": config.blueprint_model,
    "OPENAI_FEEDBACK_MODEL": config.feedback_model,
    "OPENAI_VISION_MODEL": config.vision_model,
    "OPENAI_AUDITOR_MODEL": config.auditor_model,
}
REALTIME_SLOTS = {
    "REALTIME_MODEL": config.realtime_model,
    "REALTIME_TRANSCRIPTION_MODEL": config.realtime_transcription_model,
}


class Probe(BaseModel):
    ok: bool
    word: str


PROBE_MESSAGES = [
    {"role": "system", "content": "Reply with ok=true and word set to the word 'ready'."},
    {"role": "user", "content": "Check."},
]


async def listed_models(client) -> set[str]:
    page = await client.models.list()
    return {m.id async for m in page} if hasattr(page, "__aiter__") else {m.id for m in page.data}


async def probe(model: str, client) -> dict:
    row = {"model": model, "reasoning": is_reasoning_model(model)}
    try:
        result = await request_structured(job="probe", model=model, client=client,
                                          messages=PROBE_MESSAGES, response_model=Probe)
        r = result.record
        row.update(callable=True, reply=result.payload, input=r.input_tokens,
                   output=r.output_tokens, reasoning_tokens=r.reasoning_tokens,
                   latency_s=r.latency_s, cost_usd=r.cost_usd)
    except TextModelError as exc:
        row.update(callable=False, error=str(exc)[:300])
    if row["reasoning"]:
        try:
            await client.chat.completions.create(
                model=model, messages=PROBE_MESSAGES, temperature=0.2, max_completion_tokens=50,
            )
            row["temperature"] = "accepted"
        except Exception as exc:  # noqa: BLE001
            row["temperature"] = f"rejected: {str(exc)[:200]}"
    return row


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--probe", action="store_true", help="send tiny paid requests")
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (value not inspected).")
        return 2

    from openai import AsyncOpenAI

    client = AsyncOpenAI(max_retries=0)
    listed = await listed_models(client)
    print(f"{len(listed)} models visible to this key")
    print("gpt-6.1-sol listed:", "gpt-6.1-sol" in listed, "| gpt-6.2-sol listed:", "gpt-6.2-sol" in listed)
    print("\nTrack I slots:")
    for name, resolve in {**TEXT_SLOTS, **REALTIME_SLOTS}.items():
        try:
            model = resolve()
        except config.InvalidConfig as exc:
            print(f"  {name:30} INVALID: {exc}")
            continue
        source = "env" if os.environ.get(name) else "default"
        print(f"  {name:30} {model:24} ({source})  listed={model in listed}")
    if not args.probe:
        return 0

    print("\nProbe (one tiny structured-output request per distinct text model):")
    models = []
    for resolve in TEXT_SLOTS.values():
        try:
            m = resolve()
        except config.InvalidConfig:
            continue
        if m not in models:
            models.append(m)
    for model in models:
        row = await probe(model, client)
        print(" ", row)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
