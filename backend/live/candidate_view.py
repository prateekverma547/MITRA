"""What the candidate's own pages are told about their interview (phase 5 polish,
PA-04): the real role, booked length and area names (CandidateInterviewDetails,
sent by the check and join routes after the credentials matched), and, during the
call, the position of the area Mitra is in (`current_area_index`, sent with the
status and clock answers the candidate app already receives with its proctoring
pass).

Candidate-safe by construction: names and positions only. No HR findings, no
question text, no ids, no scores, no internal codes. The area position comes from
the `area_changed` events the bot already stores (Session A's coordinator, through
bot/live_events.py), so no bot change is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from shared.interview_contracts import CandidateInterviewDetails

OPENING, CLOSING = "Opening", "Closing"
#: area_id -> position, per interview (the plan does not change during a call).
#: Bounded: cleared when it grows past this (an interview's map is rebuilt on demand).
_MAPS: dict[str, dict[str, int]] = {}
_MAX_MAPS = 256


def _plans(blueprint: dict | None) -> list[dict]:
    plans = (blueprint or {}).get("competency_plans") or []
    return [p for p in plans if isinstance(p, dict) and p.get("name")]


def area_names(blueprint: dict | None) -> list[str]:
    """The call's areas in order, as the coordinator numbers them (opening, each
    plan area, closing). Empty when there is no plan."""
    plans = _plans(blueprint)
    return [OPENING, *[str(p["name"]) for p in plans], CLOSING] if plans else []


def area_positions(blueprint: dict | None) -> dict[str, int]:
    """area_id (as `area_changed` carries it) -> its index in `area_names`."""
    plans = _plans(blueprint)
    if not plans:
        return {}
    positions = {"opening": 0, "closing": len(plans) + 1}
    positions.update({str(p.get("competency_id")): i + 1 for i, p in enumerate(plans)})
    return positions


def candidate_details(interview, candidate, now: datetime | None = None) -> CandidateInterviewDetails:
    """Only ever called after the meeting ID and password matched."""
    blueprint = (getattr(candidate, "blueprint", None) or {}) if candidate is not None else {}
    spec = blueprint.get("evaluation_spec") or {}
    booked = getattr(interview, "booked_duration_minutes", None) or spec.get("duration_minutes")
    started = interview.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return CandidateInterviewDetails(
        role_title=spec.get("role_title") or "this role",
        booked_minutes=int(booked) if booked else None,
        areas=area_names(blueprint),
        started_at=started,
        server_time=now or datetime.now(UTC),
    )


async def current_area_index(session, interview) -> int | None:
    """The index in `area_names` of the area Mitra is in now, from the latest stored
    `area_changed` event; None before the first one, or when it cannot be placed."""
    from app.db import Candidate
    from live.models import InterviewEventRecord

    latest = (await session.execute(
        select(InterviewEventRecord.payload)
        .where(InterviewEventRecord.interview_id == interview.id, InterviewEventRecord.kind == "area_changed")
        .order_by(InterviewEventRecord.seq.desc()).limit(1))).scalar_one_or_none()
    if latest is None:
        return None
    area_id = ((latest or {}).get("payload") or {}).get("area_id")
    positions = _MAPS.get(interview.id)
    if positions is None:
        blueprint = (await session.execute(
            select(Candidate.blueprint).where(Candidate.id == interview.candidate_id))).scalar_one_or_none()
        positions = area_positions(blueprint)
        if len(_MAPS) >= _MAX_MAPS:
            _MAPS.clear()
        _MAPS[interview.id] = positions
    return positions.get(str(area_id)) if area_id is not None else None
