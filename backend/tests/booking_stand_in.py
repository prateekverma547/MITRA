"""A stand-in for Session A's ONE text-model call inside `blueprint.refine.refine_for_booking`
(C-A23, C-176): no network, no cost. Everything else in A's function is real: the HR
mapping, the sizing to the booked length, the goal checks (`goal_problems`).

`stand_in(...)` answers both requests A's function makes:
- `_FindingsResponse` (HR findings): each finding probed in `probes` {index: (area_id,
  probe)}, every other `not_technical`;
- `_GoalsResponse` (goals): one goal per question id in the request, worded so that A's
  checks accept it (not a question, short, neutral, not the question's words).

`fail` names a request that raises instead ("findings" or "goals"); `delay` waits first.
Each call is recorded in `.calls` (the response model's name). Costs: one usage record a
call, job "blueprint" as A's helper writes it (A relabels goal writing itself).
"""

from __future__ import annotations

import asyncio
import functools
import re
from types import SimpleNamespace


def stand_in(probes: dict | None = None, *, fail: str | None = None, delay: float = 0.0):
    from shared.text_models import TextModelError

    calls: list[str] = []
    probes = probes or {}

    async def request(*, job, messages, response_model, **kw):
        name = response_model.__name__
        calls.append(name)
        if delay:
            await asyncio.sleep(delay)
        which = "goals" if name == "_GoalsResponse" else "findings"
        if fail == which:
            raise TextModelError(f"stand-in: the {which} call failed")
        content = messages[1]["content"]  # A's first user message; a retry appends a third
        if which == "goals":
            ids = re.findall(r"^(Q\d+\.\d+): ", content.split("QUESTIONS (id: question):\n", 1)[-1], re.M)
            payload = {"goals": [{"id": q, "goal": f"Their own part and the result in case {q}"} for q in ids]}
        else:
            count = len(re.findall(r"^\d+: ", content.split("NOTES (index: text):\n", 1)[-1], re.M))
            payload = {"findings": [
                {"index": i, "outcome": "probed", "area_id": probes[i][0], "probe": probes[i][1]} if i in probes
                else {"index": i, "outcome": "not_technical"} for i in range(count)]}
        record = {"job": job, "model": "stand-in", "input_tokens": 900, "output_tokens": 120, "cost_usd": 0.0,
                  "outcome": "ok"}
        return SimpleNamespace(payload=payload, record=SimpleNamespace(to_dict=lambda: dict(record)))

    request.calls = calls
    return request


def booking_function(request):
    """Session A's real refine_for_booking with only its model call stood in."""
    from blueprint.refine import refine_for_booking

    return functools.partial(refine_for_booking, request=request)
