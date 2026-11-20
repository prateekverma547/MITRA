"""Measurement only: what the candidate's browser saw during a MITRA round. Track H.

Investigation of 2026-11-20: the first MITRA round after the console is opened is
slow, later ones in the same tab are fast. The stored reply timings show the slow
part is OpenAI's turn detection (deciding the candidate has finished), not the
model and not MITRA's start-up. What the browser was doing is not recorded
anywhere, so the room reports it here: how long the face check took to load,
main-thread stalls during the call, how long joining took, whether this was the
tab's first call, and the microphone's processing settings.

Numbers and flags only, never audio, video or text; authorised by the same
purpose-bound proctoring token the room already holds. Kept as one JSON line per
report in `sessions/diagnostics/<interview id>.jsonl`, read by
`scripts/latency_report.py`. Not a product feature: remove once the cause is known.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, Request
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from app.recordings import SESSIONS_DIR
from hiring.api import router

DIAGNOSTICS_DIR = SESSIONS_DIR / "diagnostics"


class LongTasks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=0)
    total_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)
    first_minute_count: int = Field(ge=0)
    first_minute_ms: float = Field(ge=0)


class MicSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    echo_cancellation: bool | None = None
    noise_suppression: bool | None = None
    auto_gain_control: bool | None = None
    sample_rate: int | None = None


class RoomDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: "minute" (sent once a minute) or "end" (sent as the call ends).
    phase: str = Field(max_length=16)
    seconds_in_call: float = Field(ge=0)
    #: How long the page had been open when the call started, and how many calls
    #: this tab had already made: the fresh-tab versus warm-tab question.
    tab_age_seconds_at_join: float = Field(ge=0)
    earlier_calls_in_tab: int = Field(ge=0)
    face_check_load_ms: float | None = Field(default=None, ge=0)
    face_check_status: str | None = Field(default=None, max_length=16)
    join_request_ms: float | None = Field(default=None, ge=0)
    daily_join_ms: float | None = Field(default=None, ge=0)
    long_tasks: LongTasks
    mic: MicSettings | None = None
    browser: str | None = Field(default=None, max_length=80)


def _bearer(request: Request) -> str | None:
    scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


@router.post("/room/diagnostics/{interview_id}", status_code=204)
async def room_diagnostics(interview_id: str, body: RoomDiagnostics, request: Request) -> None:
    from proctoring import intake

    if not intake.token_valid_for(_bearer(request), interview_id):
        raise HTTPException(status_code=401, detail="Not allowed to report for this interview.")
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    line = {"received_at": datetime.now(UTC).isoformat(), **body.model_dump(mode="json")}
    path: Path = DIAGNOSTICS_DIR / f"{interview_id}.jsonl"
    with path.open("a") as handle:
        handle.write(json.dumps(line) + "\n")
    logger.info(f"[{interview_id}] room diagnostics ({body.phase}): face check {body.face_check_load_ms} ms, "
                f"long tasks {body.long_tasks.count} ({body.long_tasks.total_ms:.0f} ms), "
                f"earlier calls in tab {body.earlier_calls_in_tab}")
