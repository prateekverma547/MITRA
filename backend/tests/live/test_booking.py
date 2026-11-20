"""Task 20: booking returns TwoLinks, with the duration set per booking.

Requirements from the Session C brief and its change of the same day: the
password once, in its own field, nowhere else; TwoLinks valid against its
contract; /admin's fields unchanged; duration default and override; 20-60 on
both engines; realtime trimming (grace first, then plan) to a hard stop by 58
minutes, said in one sentence; old 90-minute data still loads; expires_at with
a timezone.
"""

import json

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from live import booking
from shared.interview_contracts import TwoLinks
from tests.test_interviews import FakeRoom, blueprint_for

ADMIN = "test-admin-pw"
OLD_ADMIN_FIELDS = {"interview_id", "candidate_id", "meeting_id", "password", "status", "expires_at"}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/book.db")
    for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN)
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setenv("APP_BASE_URL", "https://mitra.example.com")
    monkeypatch.delenv("INTERVIEW_ENGINE", raising=False)  # realtime, the default
    from app import db, interviews, main

    async def fake_create_room(**kwargs):
        return FakeRoom()

    monkeypatch.setattr(interviews, "create_room", fake_create_room)
    await db.reset_engine()
    with TestClient(main.app) as c:
        c.post("/admin/login", json={"password": ADMIN})
        yield c
    await db.reset_engine()


def candidate(client, *, plan_minutes: int = 40, grace: int = 5, cid: str = "cand_book1") -> str:
    from app import db

    blueprint = blueprint_for(cid).model_dump(mode="json")
    blueprint["evaluation_spec"]["duration_minutes"] = plan_minutes
    blueprint["evaluation_spec"]["overrun_grace_minutes"] = grace

    async def seed():
        async with db.get_sessionmaker()() as s:
            if await s.get(db.Job, "job_book") is None:
                s.add(db.Job(id="job_book", jd_text="jd", spec_status=db.SpecStatus.READY))
                await s.flush()
            s.add(db.Candidate(id=cid, job_id="job_book", name="N", cv_text="cv",
                               blueprint=blueprint, blueprint_status=db.BlueprintStatus.READY))
            await s.commit()

    client.portal.call(seed)
    return cid


def book(client, cid, **body):
    return client.post(f"/candidates/{cid}/interviews", **({"json": body} if body else {}))


def stored(client, interview_id):
    from app import db

    async def read():
        async with db.get_sessionmaker()() as s:
            return await s.get(db.Interview, interview_id)

    return client.portal.call(read)


# -- shape ----------------------------------------------------------------------------


def test_the_response_keeps_the_old_fields_and_adds_valid_two_links(client):
    r = book(client, candidate(client))  # no body, exactly as /admin sends it
    assert r.status_code == 200, r.text
    body = r.json()
    assert OLD_ADMIN_FIELDS <= set(body)
    links = TwoLinks.model_validate(body["links"])  # the contract re-checks the URLs
    assert links.interview_id == body["interview_id"] and links.meeting_id == body["meeting_id"]
    assert links.candidate_join_url == "https://mitra.example.com/interview/#/join"
    assert links.live_view_url == f"https://mitra.example.com/console/#/live/{body['interview_id']}"
    assert links.duration_minutes == 40
    assert links.expires_at.utcoffset() is not None
    assert body["expires_at"].endswith(("Z", "+00:00"))


def test_without_app_base_url_the_links_use_the_requests_address(client, monkeypatch):
    monkeypatch.delenv("APP_BASE_URL")
    links = book(client, candidate(client)).json()["links"]
    assert links["candidate_join_url"] == "http://testserver/interview/#/join"


def test_the_password_is_only_in_its_own_field(client):
    from app import db

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        body = book(client, candidate(client)).json()
    finally:
        logger.remove(sink)
    password = body["password"]
    assert password and len(password) >= 6
    rest = {k: v for k, v in body.items() if k != "password"}
    assert password not in json.dumps(rest)               # not in links or any other field
    assert lines and not any(password in line for line in lines)  # not in a log line

    async def events():
        from sqlalchemy import select

        from live.models import InterviewEventRecord

        async with db.get_sessionmaker()() as s:
            return [json.dumps(p) for p in (await s.scalars(select(InterviewEventRecord.payload)))]

    assert not any(password in e for e in client.portal.call(events))  # not in the stream's store


# -- duration ------------------------------------------------------------------------


def test_duration_defaults_to_the_plan_and_is_stored(client):
    body = book(client, candidate(client, plan_minutes=30)).json()
    assert body["links"]["duration_minutes"] == 30
    assert stored(client, body["interview_id"]).booked_duration_minutes == 30


def test_duration_can_be_set_per_booking(client):
    body = book(client, candidate(client), duration_minutes=25).json()
    assert body["links"]["duration_minutes"] == 25
    assert stored(client, body["interview_id"]).booked_duration_minutes == 25


@pytest.mark.parametrize("engine", ["realtime", "pipeline"])
@pytest.mark.parametrize("minutes", [19, 61, 90, 0, -5])
def test_durations_outside_20_to_60_are_refused_on_both_engines(client, minutes, engine, monkeypatch):
    monkeypatch.setenv("INTERVIEW_ENGINE", engine)
    r = book(client, candidate(client), duration_minutes=minutes)
    assert r.status_code == 422 and "20 to 60" in r.json()["detail"]


@pytest.mark.parametrize("booked,planned,grace,note_start", [
    (50, 50, 5, None),
    (55, 55, 3, "The overrun allowance is 3 minutes instead of 5"),
    (60, 56, 2, "Planned for 56 minutes of questions so the interview finishes within the 60-minute session limit."),
])
def test_realtime_trims_grace_then_plan_and_says_so(client, booked, planned, grace, note_start):
    body = book(client, candidate(client), duration_minutes=booked).json()
    assert (body["booked_minutes"], body["planned_minutes"], body["grace_minutes"]) == (booked, planned, grace)
    assert body["links"]["duration_minutes"] == booked  # HR sees what they chose
    if note_start is None:
        assert body["timing_note"] is None
    else:
        assert body["timing_note"].startswith(note_start)
    row = stored(client, body["interview_id"])
    assert (row.booked_duration_minutes, row.planned_duration_minutes, row.planned_grace_minutes) == (
        booked, planned, grace)


def test_every_realtime_booking_has_its_hard_stop_by_58_minutes():
    for booked in range(20, 61):
        for grace in range(0, 16):
            t = booking.booked_timing(plan_minutes=40, grace_minutes=grace, requested=booked, engine="realtime")
            assert t.planned_minutes + t.grace_minutes <= booking.HARD_STOP_BY_MINUTES, (booked, grace)
            assert t.grace_minutes <= grace  # grace is only ever shrunk
            assert t.grace_minutes >= min(grace, booking.MIN_GRACE_MINUTES)
            assert (t.note is not None) == (t.planned_minutes < booked or t.grace_minutes < grace)


def test_the_measured_tails_fit_inside_the_two_minute_margin():
    margin = (booking.SESSION_LIMIT_MINUTES - booking.HARD_STOP_BY_MINUTES) * 60
    assert booking.PRE_INTERVIEW_SECONDS_MEASURED + booking.GOODBYE_TAIL_SECONDS < margin


def test_the_pipeline_engine_is_not_trimmed_but_is_held_to_60(client, monkeypatch):
    monkeypatch.setenv("INTERVIEW_ENGINE", "pipeline")
    body = book(client, candidate(client), duration_minutes=60).json()
    assert (body["planned_minutes"], body["grace_minutes"], body["timing_note"]) == (60, 5, None)


def test_duration_defaults_to_the_plan_and_an_old_90_minute_plan_needs_a_shorter_booking(client):
    cid = candidate(client, plan_minutes=90)
    r = book(client, cid)
    assert r.status_code == 422 and "20 to 60" in r.json()["detail"]
    assert book(client, cid, duration_minutes=45).json()["booked_minutes"] == 45


def test_old_data_up_to_90_minutes_still_loads():
    from shared.contracts import EvaluationSpec, InterviewBlueprint

    old = blueprint_for("cand_old").model_dump(mode="json")
    old["evaluation_spec"]["duration_minutes"] = 90
    assert InterviewBlueprint.model_validate(old).evaluation_spec.duration_minutes == 90
    assert EvaluationSpec.model_validate(old["evaluation_spec"]).hard_stop_minutes == 95
    links = TwoLinks(interview_id="int_x", meeting_id="1", candidate_join_url="https://a.example/interview/",
                     live_view_url="https://a.example/console/", duration_minutes=90)
    assert links.duration_minutes == 90


async def test_new_plans_over_60_are_refused_before_any_model_call():
    from blueprint.generate import BlueprintGenerationError, BlueprintGenerator
    from blueprint.refine import BlueprintRefiner, RefinementError

    long = blueprint_for("cand_long")
    long.evaluation_spec.duration_minutes = 75
    generator = BlueprintGenerator(api_key="test", model="gpt-4.1")
    with pytest.raises(BlueprintGenerationError, match="at most 60"):
        await generator.generate(blueprint_id="b", spec=long.evaluation_spec, cv_text="cv")
    assert generator.last_usage is None  # no call was made
    refiner = BlueprintRefiner(api_key="test", model="gpt-4.1")
    with pytest.raises(RefinementError, match="at most 60"):
        await refiner.refine(blueprint=long, cv_text="cv", message="more depth")


def test_a_refused_duration_creates_no_room_and_no_interview(client, monkeypatch):
    from app import interviews

    rooms = []

    async def counting_room(**kw):
        rooms.append(kw)
        return FakeRoom()

    monkeypatch.setattr(interviews, "create_room", counting_room)
    cid = candidate(client)
    assert book(client, cid, duration_minutes=61).status_code == 422
    assert rooms == []
    assert client.get(f"/candidates/{cid}/interviews").json() == []


# -- everything else stays -----------------------------------------------------------


def test_one_live_session_ready_blueprint_and_admin_still_hold(client):
    cid = candidate(client)
    assert book(client, cid).status_code == 200
    assert book(client, cid).status_code == 409
    client.cookies.clear()
    assert book(client, cid).status_code == 401


async def _cancel_all(cid):
    from sqlalchemy import select

    from app import db

    async with db.get_sessionmaker()() as s:
        for row in await s.scalars(select(db.Interview).where(db.Interview.candidate_id == cid)):
            row.status = db.InterviewStatus.COMPLETED
        await s.commit()


# -- Track H handoff (C-170): 20, 30, 40 and one of each invalid kind ----------------------------


@pytest.mark.parametrize("minutes", [20, 30, 40])
def test_twenty_thirty_and_forty_are_booked_with_a_hard_stop_by_58(client, minutes):
    body = book(client, candidate(client), duration_minutes=minutes).json()
    row = stored(client, body["interview_id"])
    assert body["booked_minutes"] == row.booked_duration_minutes == minutes
    assert body["planned_minutes"] + body["grace_minutes"] <= 58  # the hard stop
    assert body["links"]["duration_minutes"] == minutes


@pytest.mark.parametrize("value,kind", [(19, "too short"), (61, "too long"), ("forty", "text"),
                                        (30.5, "a fraction"), (True, "a yes/no value")])
def test_each_kind_of_invalid_duration_is_refused_in_plain_words_and_books_nothing(client, value, kind):
    from app.interviews import DURATION_NOT_A_NUMBER

    cid = candidate(client)
    r = book(client, cid, duration_minutes=value)
    assert r.status_code == 422, kind
    text = json.dumps(r.json())
    if isinstance(value, int) and not isinstance(value, bool):
        assert f"An interview can be 20 to 60 minutes long; {value} was asked for." in text
    else:
        assert DURATION_NOT_A_NUMBER in text and "1 was asked for" not in text
    assert client.get(f"/candidates/{cid}/interviews").json() == []
