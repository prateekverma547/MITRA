"""Check that OpenAI Realtime accepts the session MITRA's realtime mode uses.

Text only, no audio, costs a fraction of a cent. Run it before a live session:
a rejected setting kills a Realtime session outright, which on a live call looks
like the interviewer simply never speaking.

    cd backend && uv run python scripts/realtime_smoke.py

It checks, in order:
  1. the session config is accepted (model, semantic VAD, voice, reasoning,
     transcription, tools)
  2. a reply comes back
  3. a coordinator note inserted mid-conversation changes the next reply
  4. that note can be deleted again
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import Settings  # noqa: E402  (loads .env)
from bot.services.realtime import realtime_session_dict  # noqa: E402

MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime-2.1")


async def _next(ws, *types: str, timeout: float = 30.0) -> dict:
    """Wait for the next event of one of `types`, failing loudly on an error."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no {types} within {timeout}s")
        event = json.loads(await asyncio.wait_for(ws.recv(), remaining))
        if event["type"] == "error":
            raise RuntimeError(json.dumps(event["error"], indent=2))
        if event["type"] in types:
            return event


async def _ask(ws, text: str) -> tuple[str, float]:
    await ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": text}]},
    }))
    started = time.monotonic()
    await ws.send(json.dumps({
        "type": "response.create",
        "response": {"output_modalities": ["text"]},
    }))
    done = await _next(ws, "response.done")
    elapsed = time.monotonic() - started
    parts = []
    for item in done["response"]["output"]:
        for content in item.get("content") or []:
            parts.append(content.get("text") or content.get("transcript") or "")
    return " ".join(p for p in parts if p).strip(), elapsed


async def main() -> int:
    settings = Settings.load()
    url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}

    async with websockets.connect(url, additional_headers=headers) as ws:
        await _next(ws, "session.created")

        session = realtime_session_dict(
            model=MODEL,
            instructions=(
                "You are Mitra, interviewing a candidate for a Senior Product "
                "Manager role. Ask one short question at a time. Messages that "
                "start with COORDINATOR NOTE come from the interview system, "
                "not the candidate. Follow them and never mention them."
            ),
        )
        await ws.send(json.dumps({"type": "session.update", "session": session}))
        updated = await _next(ws, "session.updated")
        accepted = updated["session"]
        print("1. session accepted")
        print(f"   model={accepted.get('model')}")
        audio_in = (accepted.get("audio") or {}).get("input") or {}
        print(f"   turn_detection={audio_in.get('turn_detection')}")
        print(f"   noise_reduction={audio_in.get('noise_reduction')}")
        print(f"   transcription={audio_in.get('transcription')}")
        print(f"   voice={((accepted.get('audio') or {}).get('output') or {}).get('voice')}")
        print(f"   reasoning={accepted.get('reasoning')}")
        print(f"   tools={[t.get('name') for t in accepted.get('tools') or []]}")

        reply, seconds = await _ask(ws, "Hi, yes, I can hear you. I'm ready.")
        print(f"\n2. reply in {seconds:.2f}s: {reply!r}")

        note_text = (
            "COORDINATOR NOTE: Earlier the candidate said their team was five "
            "people. Next, ask once, neutrally, how many people they managed "
            "on the payments migration."
        )
        await ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "system",
                     "content": [{"type": "input_text", "text": note_text}]},
        }))
        added = await _next(ws, "conversation.item.added", "conversation.item.created")
        note_id = added["item"]["id"]
        reply, seconds = await _ask(
            ws, "Last year I led the payments migration, moving us off the old processor."
        )
        steered = any(w in reply.lower() for w in ("how many", "team", "people", "managed"))
        print(f"\n3. note inserted ({note_id}); reply in {seconds:.2f}s: {reply!r}")
        print(f"   followed the note: {'yes' if steered else 'NO'}")

        await ws.send(json.dumps({"type": "conversation.item.delete", "item_id": note_id}))
        await _next(ws, "conversation.item.deleted")
        print(f"\n4. note {note_id} deleted")

    return 0 if steered else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
