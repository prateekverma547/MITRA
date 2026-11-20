"""The technical round: MITRA's plan, the booking, how a round ends, and sending
the result to the BU head. Daily and the blueprint generator are stubbed."""

from datetime import UTC, datetime, timedelta

import pytest

from hiring import technical
from tests.console_support import org_client, sign_in  # noqa: F401


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    """A room without Daily, and a plan builder that marks the plan ready (and
    renames the candidate, as the real generator does from the CV)."""
    from bot.services import daily

    async def room(**kwargs):
        class R:
            name, url = "tech-room", "https://example.daily.co/tech-room"
        return R()

    built: list[str] = []

    async def builder(candidate_id: str):
        from app import db
        from app.db import BlueprintStatus, Candidate

        built.append(candidate_id)
        async with db.get_sessionmaker()() as session:
            candidate = await session.get(Candidate, candidate_id)
            candidate.blueprint_status = BlueprintStatus.READY
            candidate.blueprint = {"spec": {"duration_minutes": 35}}
            candidate.name = "Name From The CV"
            await session.commit()

    monkeypatch.setattr(daily, "create_room", room)
    monkeypatch.setattr(technical, "_plan_builder", lambda: builder)
    return built


async def at_stage(client, label: str, stage: str, plan: str | None = None) -> str:
    from app import db
    from app.db import Candidate

    cid = client.demo.candidate_ids[label]
    async with db.get_sessionmaker()() as session:
        candidate = await session.get(Candidate, cid)
        candidate.stage = stage
        if plan:
            candidate.blueprint_status = plan
            candidate.blueprint = {"spec": {"duration_minutes": 35}} if plan == "ready" else None
        await session.commit()
    return cid


async def interview_of(cid: str):
    from app import db

    async with db.get_sessionmaker()() as session:
        return await technical.latest_technical(session, cid)


async def set_interview(cid: str, **fields) -> None:
    from app import db

    async with db.get_sessionmaker()() as session:
        interview = await technical.latest_technical(session, cid)
        for k, v in fields.items():
            setattr(interview, k, v)
        await session.commit()


# ---- MITRA's plan --------------------------------------------------------------------------------


async def test_proceeding_to_technical_builds_the_plan_and_keeps_hrs_name(org_client, stubs):
    cid = await at_stage(org_client, "ai", "hr_done")
    sign_in(org_client, "hr@mitra.local")
    name = org_client.get(f"/api/candidates/{cid}").json()["name"]
    assert org_client.post(f"/api/candidates/{cid}/move", json={"to": "ready_for_technical"}).status_code == 200

    assert stubs == [cid]
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["plan"]["status"] == "ready" and view["plan"]["minutes"] == 35
    assert view["can_book"]
    # The generator renames from the CV; the hiring flow keeps the name HR gave.
    assert view["candidate"]["name"] == name


async def test_a_round_cannot_be_booked_before_the_plan_is_ready(org_client):
    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="failed")
    sign_in(org_client, "hr@mitra.local")
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert not view["can_book"] and view["can_retry_plan"]
    refused = org_client.post(f"/api/candidates/{cid}/technical")
    assert refused.status_code == 409 and "plan" in refused.json()["detail"]

    retried = org_client.post(f"/api/candidates/{cid}/technical/plan")
    assert retried.status_code == 202
    assert org_client.get(f"/api/candidates/{cid}/technical").json()["can_book"]


async def test_the_sweep_starts_plans_nobody_started(org_client, stubs):
    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="pending")
    await technical.sweep_once()
    for task in list(technical._tasks):
        await task
    assert stubs == [cid]


# ---- booking ---------------------------------------------------------------------------------------


async def test_hr_books_a_real_round_with_credentials(org_client):
    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="ready")
    sign_in(org_client, "hr@mitra.local")
    booked = org_client.post(f"/api/candidates/{cid}/technical")
    assert booked.status_code == 201
    body = booked.json()
    assert body["candidate"]["stage"] == "technical_booked"
    b = body["booking"]
    assert b["meeting_id"] and b["password"] and b["candidate_join_url"].endswith(f"/meet?id={b['meeting_id']}")
    assert b["duration_minutes"] == 35 and b["status"] == "scheduled" and b["live_view_url"] is None
    interview = await interview_of(cid)
    assert interview.round == "technical" and interview.meeting_id == b["meeting_id"]

    # Booking twice is refused; the BU head sees the booking but not the password.
    assert org_client.post(f"/api/candidates/{cid}/technical").status_code == 409
    sign_in(org_client, "ai.head@mitra.local")
    seen = org_client.get(f"/api/candidates/{cid}/technical").json()["booking"]
    assert seen["meeting_id"] == b["meeting_id"] and seen["password"] is None
    assert org_client.post(f"/api/candidates/{cid}/technical").status_code == 403


async def test_a_booking_nobody_joined_is_cancelled_with_a_reason(org_client):
    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="ready")
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/technical")
    assert org_client.post(f"/api/candidates/{cid}/technical/cancel", json={"reason": " "}).status_code == 422
    done = org_client.post(f"/api/candidates/{cid}/technical/cancel", json={"reason": "Candidate asked to move it."})
    assert done.status_code == 200 and done.json()["candidate"]["stage"] == "ready_for_technical"
    assert done.json()["booking"] is None and (await interview_of(cid)).status == "expired"


async def test_a_round_in_progress_cannot_be_cancelled(org_client):
    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="ready")
    sign_in(org_client, "hr@mitra.local")
    org_client.post(f"/api/candidates/{cid}/technical")
    await set_interview(cid, status="in_progress")
    assert org_client.post(f"/api/candidates/{cid}/technical/cancel", json={"reason": "No."}).status_code == 409


# ---- how a round ends ----------------------------------------------------------------------------


async def booked(client) -> str:
    cid = await at_stage(client, "ai", "ready_for_technical", plan="ready")
    sign_in(client, "hr@mitra.local")
    client.post(f"/api/candidates/{cid}/technical")
    return cid


async def test_a_scored_interview_moves_the_candidate_to_technical_done(org_client):
    cid = await booked(org_client)
    await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="generating")
    assert await technical.sweep_once() == 0  # still scoring: wait

    await set_interview(cid, feedback_status="ready", feedback_report={"recommendation": "some_evidence_for", "competency_scores": []})
    assert await technical.sweep_once() == 1
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["candidate"]["stage"] == "technical_done" and view["has_report"] and view["can_send"]
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["actor"] == "system" and "some evidence for" in last["model_recommendation"]


async def test_a_ready_report_is_sendable_the_moment_hr_opens_it(org_client):
    """No wait for the 30-second sweep: reading the round settles it, so the page
    never shows "Report ready" beside a stage that cannot be sent yet."""
    cid = await booked(org_client)
    await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="ready",
                        feedback_report={"recommendation": "some_evidence_for", "competency_scores": []})
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["candidate"]["stage"] == "technical_done" and view["can_send"]
    assert await technical.sweep_once() == 0  # already settled: nothing left for the sweep


async def test_a_report_that_never_arrives_does_not_hold_the_candidate_forever(org_client):
    cid = await booked(org_client)
    ended = datetime.now(UTC) - technical.REPORT_GRACE - timedelta(minutes=1)
    await set_interview(cid, status="completed", ended_at=ended, feedback_status="pending")
    assert await technical.sweep_once() == 1
    assert org_client.get(f"/api/candidates/{cid}/technical").json()["candidate"]["stage"] == "technical_done"


@pytest.mark.parametrize("how", ["failed", "room_expired"])
async def test_a_round_that_did_not_happen_goes_back_with_the_reason(org_client, how):
    cid = await booked(org_client)
    if how == "failed":
        await set_interview(cid, status="failed", failure_reason="failed_to_start")
    else:
        await set_interview(cid, room_expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert await technical.sweep_once() == 1
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["candidate"]["stage"] == "ready_for_technical" and view["can_book"]
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["actor"] == "system" and "Book it again" in last["reason"]


# ---- sending the result -------------------------------------------------------------------------


async def test_hr_sends_a_finished_result_to_the_bu_head_with_a_note(org_client):
    cid = await at_stage(org_client, "ai", "technical_booked")
    sign_in(org_client, "hr@mitra.local")
    # Not done yet: only the round finishing makes it technical_done.
    assert org_client.post(f"/api/candidates/{cid}/send-to-bu-head", json={}).status_code == 409

    await at_stage(org_client, "ai", "technical_done")
    sent = org_client.post(f"/api/candidates/{cid}/send-to-bu-head", json={"note": "Strong on evaluation."})
    assert sent.status_code == 200
    assert sent.json()["candidate"]["stage"] == "with_bu_head"
    assert sent.json()["sent_to_bu_head"]["note"] == "Strong on evaluation."
    last = org_client.get(f"/api/candidates/{cid}/decisions").json()["decisions"][-1]
    assert last["actor"] == "hr" and last["to"] == "with_bu_head"


async def test_only_hr_sends_to_the_bu_head(org_client):
    cid = await at_stage(org_client, "ai", "technical_done")
    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.post(f"/api/candidates/{cid}/send-to-bu-head", json={}).status_code == 403


# ---- the live room ---------------------------------------------------------------------------------

PLAN = {
    "evaluation_spec": {"duration_minutes": 30},
    "opening_minutes": 2, "closing_minutes": 2,
    "competency_plans": [
        {"competency_id": "core", "name": "Core craft", "time_budget_minutes": 14},
        {"competency_id": "ship", "name": "Shipping and ownership", "time_budget_minutes": 12},
    ],
}


async def live_booked(client) -> str:
    from app import db
    from app.db import Candidate

    cid = await booked(client)
    async with db.get_sessionmaker()() as session:
        (await session.get(Candidate, cid)).blueprint = PLAN
        await session.commit()
    return cid


async def test_the_live_room_follows_the_round_through_every_state(org_client):
    cid = await live_booked(org_client)
    url = f"/api/candidates/{cid}/technical/live"
    live = org_client.get(url).json()
    # The booked length (35, the plan's when it was booked) is what the round runs to,
    # not the plan's length now (Track H, 2026-11-20: HR picks each booking's length).
    assert live["state"] == "waiting" and live["planned_minutes"] == 35
    assert [s["name"] for s in live["plan"]["sections"]] == ["Opening", "Core craft", "Shipping and ownership", "Closing"]

    await set_interview(cid, consent_accepted_at=datetime.now(UTC))
    assert org_client.get(url).json()["state"] == "consented"

    # Five minutes in: past the 2-minute opening, inside "Core craft" by the clock.
    await set_interview(cid, status="in_progress", started_at=datetime.now(UTC) - timedelta(minutes=5),
                        focus_events=[{"at_seconds": 90, "seconds": 12, "kind": "hidden"},
                                      {"at_seconds": 200, "seconds": 3, "kind": "blur"}])
    live = org_client.get(url).json()
    assert live["state"] == "live" and 290 < live["elapsed_seconds"] < 320
    assert live["plan"]["current"] == "core"
    events = live["integrity"]["events"]
    assert [e["at_seconds"] for e in events] == [200, 90]  # newest first
    assert events[1]["label"] == "Left the interview tab for 12 seconds"
    assert {c["kind"]: c["count"] for c in live["integrity"]["counts"]} == {"hidden": 1, "blur": 1}
    assert "score" not in str(live["integrity"]).lower()

    await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="generating")
    assert org_client.get(url).json()["state"] == "scoring"
    await set_interview(cid, feedback_status="ready")
    done = org_client.get(url).json()
    assert done["state"] == "done" and done["plan"]["current"] is None


async def test_a_round_that_did_not_happen_says_so_in_the_live_room(org_client):
    cid = await live_booked(org_client)
    await set_interview(cid, status="failed", failure_reason="failed_to_start")
    live = org_client.get(f"/api/candidates/{cid}/technical/live").json()
    assert live["state"] == "did_not_happen" and live["failure_reason"] == "failed_to_start"


async def test_the_live_room_is_scoped_like_the_candidate(org_client):
    cid = await live_booked(org_client)
    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.get(f"/api/candidates/{cid}/technical/live").status_code == 200
    sign_in(org_client, "sales.head@mitra.local")
    assert org_client.get(f"/api/candidates/{cid}/technical/live").status_code == 404


async def test_the_report_carries_the_browsers_integrity_facts(org_client):
    cid = await live_booked(org_client)
    await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="ready",
                        feedback_report={"recommendation": "mixed", "competency_scores": []},
                        focus_events=[{"at_seconds": 30, "seconds": 75, "kind": "hidden"}])
    integrity = org_client.get(f"/api/candidates/{cid}/results").json()["technical"]["integrity"]
    assert integrity["total"] == 1 and integrity["events"][0]["label"] == "Left the interview tab for 1 min 15 s"


async def test_a_covered_camera_and_a_stopped_share_reach_the_live_room_and_the_report(org_client):
    """The room posts its integrity events to Track I's intake with the proctoring token
    from the join; HR's live room and the report read them, in plain words."""
    from proctoring.intake import issue_token

    cid = await booked(org_client)
    await set_interview(cid, status="in_progress", started_at=datetime.now(UTC) - timedelta(minutes=3))
    iid = org_client.get(f"/api/candidates/{cid}/technical").json()["booking"]["interview_id"]
    events = [
        {"event_id": "meet-ab-1", "kind": "face_missing", "source": "browser", "start_seconds": 40.0,
         "end_seconds": 52.0, "reminder": "nudged", "reminder_index": 1, "repeats": 1, "detail": "no face in view"},
        {"event_id": "meet-ab-2", "kind": "share_stopped", "source": "browser", "start_seconds": 90.0,
         "end_seconds": 125.0, "reminder": "technical_prompt", "reminder_index": None, "repeats": 1,
         "detail": "screen share stopped"},
    ]
    posted = org_client.post(f"/api/proctoring/interviews/{iid}/events", json={"events": events},
                             headers={"Authorization": f"Bearer {issue_token(iid)}"})
    assert posted.status_code == 200 and posted.json()["stored"] == 2, posted.text

    live = org_client.get(f"/api/candidates/{cid}/technical/live").json()["integrity"]
    assert [e["label"] for e in live["events"]] == [
        "Screen sharing stopped for 35 seconds; asked to share the whole screen again",
        "No face in view for 12 seconds; reminder 1 of 3 shown",
    ]
    assert {c["kind"]: c["count"] for c in live["counts"]} == {"share_stopped": 1, "face_missing": 1}

    await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="ready",
                        feedback_report={"recommendation": "mixed", "competency_scores": []})
    report = org_client.get(f"/api/candidates/{cid}/results").json()["technical"]["integrity"]
    assert report["total"] == 2 and report["events"][1]["kind"] == "face_missing"


def test_a_face_out_of_view_is_worded_as_a_warning_shown_throughout():
    """Track H, 2026-11-20: the face warning stays on screen while the face is gone, every
    time, recorded as a technical prompt (shared/interview_contracts TECHNICAL_PROMPT_KINDS)."""
    from shared.interview_contracts import TECHNICAL_PROMPT_KINDS, IntegrityKind

    assert IntegrityKind.FACE_MISSING in TECHNICAL_PROMPT_KINDS
    label = technical._integrity_label({"kind": "face_missing", "start_seconds": 40.0, "end_seconds": 52.0,
                                        "reminder": "technical_prompt", "reminder_index": None})
    assert label == "No face in view for 12 seconds; a warning was on screen until their face was back in view"


# ---- more than one MITRA round (product owner, 2026-11-20) -----------------------------------


async def test_hr_can_book_as_many_mitra_rounds_as_needed_and_each_keeps_its_report(org_client):
    cid = await booked(org_client)
    await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="ready",
                        feedback_report={"recommendation": "mixed", "competency_scores": []})
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["candidate"]["stage"] == "technical_done" and view["can_book_again"] and view["can_send"]
    assert [r["number"] for r in view["rounds"]] == [1]

    for retake, signal in ((2, "some_evidence_for"), (3, "strong_evidence_for")):
        again = org_client.post(f"/api/candidates/{cid}/technical")
        assert again.status_code == 201 and again.json()["candidate"]["stage"] == "technical_booked", again.text
        await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="ready",
                            feedback_report={"recommendation": signal, "competency_scores": []})
        view = org_client.get(f"/api/candidates/{cid}/technical").json()
        assert view["candidate"]["stage"] == "technical_done"
        assert [r["number"] for r in view["rounds"]] == list(range(1, retake + 1))

    results = org_client.get(f"/api/candidates/{cid}/results").json()["technical"]
    assert [r["recommendation"] for r in results["rounds"]] == ["mixed", "some_evidence_for", "strong_evidence_for"]
    first = results["rounds"][0]["interview_id"]
    older = org_client.get(f"/api/candidates/{cid}/results", params={"interview": first}).json()["technical"]
    assert older["interview_id"] == first


async def test_a_cancelled_retake_leaves_the_earlier_report_ready_to_send(org_client):
    cid = await booked(org_client)
    await set_interview(cid, status="completed", ended_at=datetime.now(UTC), feedback_status="ready",
                        feedback_report={"recommendation": "mixed", "competency_scores": []})
    org_client.get(f"/api/candidates/{cid}/technical")
    org_client.post(f"/api/candidates/{cid}/technical")
    r = org_client.post(f"/api/candidates/{cid}/technical/cancel", json={"reason": "Candidate asked to move it"})
    assert r.status_code == 200 and r.json()["candidate"]["stage"] == "technical_done" and r.json()["can_send"]


async def test_hr_picks_the_rounds_length_and_the_booking_runs_to_it(org_client):
    """Track I's rule (live/booking.py): 20 to 60 minutes; 60 runs as 56 on the realtime engine."""
    from app import db

    cid = await at_stage(org_client, "ai", "ready_for_technical", plan="ready")
    sign_in(org_client, "hr@mitra.local")
    view = org_client.get(f"/api/candidates/{cid}/technical").json()
    assert view["plan"]["bookable_minutes"] == [20, 30, 40, 50, 60]
    assert org_client.post(f"/api/candidates/{cid}/technical", json={"duration_minutes": 75}).status_code == 422
    booked = org_client.post(f"/api/candidates/{cid}/technical", json={"duration_minutes": 45})
    assert booked.status_code == 201 and booked.json()["booking"]["duration_minutes"] == 45
    async with db.get_sessionmaker()() as session:
        interview = await technical.latest_technical(session, cid)
    assert (interview.booked_duration_minutes, interview.planned_duration_minutes) == (45, 45)


def test_each_round_is_sized_to_its_own_length_when_it_starts():
    """The stored plan keeps every area; the interviewer sizes it per booking (Session A's
    size_to_booking, then apply_booked_timing), so a 20-minute round drops what cannot fit
    and a later 40-minute retake of the same plan still has it."""
    from blueprint.refine import size_to_booking
    from bot.blueprint_source import load_blueprint
    from bot.booked_time import apply_booked_timing

    full = load_blueprint()
    short = apply_booked_timing(size_to_booking(full, 20), planned_minutes=20, grace_minutes=2)
    longer = apply_booked_timing(size_to_booking(full, 40), planned_minutes=40, grace_minutes=5)
    assert len(short.competency_plans) < len(full.competency_plans) == len(longer.competency_plans)
    assert short.not_planned and short.evaluation_spec.duration_minutes == 20
