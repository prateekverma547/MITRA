"""The org admin's screens: people, business units, consent, retention and
deletion requests. The rules here protect access (nobody locks the
organisation out of itself) and the candidate (what is erased is really gone,
and what decided their outcome is still on record)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tests.console_support import PASSWORD, org_client, sign_in  # noqa: F401


def admin(client):
    sign_in(client, "admin@mitra.local")


def user_id(client, email):
    return client.demo.user_ids[email]


# ---- people --------------------------------------------------------------------------------------


async def test_only_an_admin_reaches_the_admin_screens(org_client):
    for email in ("hr@mitra.local", "ai.head@mitra.local"):
        sign_in(org_client, email)
        assert org_client.get("/api/admin/users").status_code == 403
        assert org_client.get("/api/admin/retention").status_code == 403
    admin(org_client)
    listed = org_client.get("/api/admin/users").json()
    assert {u["email"] for u in listed["users"]} >= {"admin@mitra.local", "hr@mitra.local"}
    assert listed["counts"]["by_role"]["org_admin"] == 1


async def test_an_invited_person_signs_in_with_the_password_shown_once(org_client):
    from app import db
    from hiring.models import AuditEvent

    admin(org_client)
    made = org_client.post("/api/admin/users", json={"name": "Neha", "email": "Neha@Mitra.local", "role": "hr"})
    assert made.status_code == 200, made.text
    password = made.json()["temporary_password"]
    assert len(password) >= 10 and made.json()["user"]["email"] == "neha@mitra.local"

    org_client.cookies.clear()
    assert org_client.post("/api/auth/login", json={"email": "neha@mitra.local", "password": password}).status_code == 200

    async with db.get_sessionmaker()() as session:
        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
    assert "user_invited" in actions


async def test_the_bu_rule_holds_for_invites_and_changes(org_client):
    admin(org_client)
    bu = org_client.demo.bu_ids["AI, ML & Data Science"]
    no_bu = org_client.post("/api/admin/users", json={"name": "X", "email": "x@mitra.local", "role": "bu_head"})
    assert no_bu.status_code == 422 and "business unit" in no_bu.json()["detail"]
    hr_with_bu = org_client.post("/api/admin/users", json={"name": "Y", "email": "y@mitra.local", "role": "hr", "bu_id": bu})
    assert hr_with_bu.status_code == 422

    hr = user_id(org_client, "hr@mitra.local")
    assert org_client.post(f"/api/admin/users/{hr}", json={"role": "bu_head"}).status_code == 422
    moved = org_client.post(f"/api/admin/users/{hr}", json={"role": "bu_head", "bu_id": bu}).json()["user"]
    assert moved["role"] == "bu_head" and moved["bu_name"] == "AI, ML & Data Science"
    # Back to HR: the BU goes with the role.
    back = org_client.post(f"/api/admin/users/{hr}", json={"role": "hr"}).json()["user"]
    assert back["bu_id"] is None


async def test_the_organisation_cannot_lock_itself_out(org_client):
    admin(org_client)
    me = user_id(org_client, "admin@mitra.local")
    self_off = org_client.post(f"/api/admin/users/{me}", json={"active": False})
    assert self_off.status_code == 409 and "yourself" in self_off.json()["detail"]

    # A second admin may not demote the last other admin either.
    made = org_client.post("/api/admin/users", json={"name": "Two", "email": "two@mitra.local", "role": "org_admin"})
    two = made.json()["user"]["id"]
    assert org_client.post(f"/api/admin/users/{two}", json={"active": False}).status_code == 200
    org_client.cookies.clear()
    org_client.post("/api/auth/login", json={"email": "two@mitra.local", "password": made.json()["temporary_password"]})
    # Two is deactivated, so their session is refused.
    assert org_client.get("/api/admin/users").status_code == 401

    admin(org_client)
    org_client.post(f"/api/admin/users/{two}", json={"active": True})
    org_client.cookies.clear()
    org_client.post("/api/auth/login", json={"email": "two@mitra.local", "password": made.json()["temporary_password"]})
    assert org_client.post(f"/api/admin/users/{me}", json={"role": "hr"}).status_code == 200
    last = org_client.post(f"/api/admin/users/{two}", json={"role": "hr"})
    assert last.status_code == 409


async def test_a_deactivated_person_is_refused_and_a_reset_password_works(org_client):
    admin(org_client)
    hr = user_id(org_client, "hr@mitra.local")
    org_client.post(f"/api/admin/users/{hr}", json={"active": False})
    org_client.cookies.clear()
    assert org_client.post("/api/auth/login", json={"email": "hr@mitra.local", "password": PASSWORD}).status_code == 401

    admin(org_client)
    org_client.post(f"/api/admin/users/{hr}", json={"active": True})
    reset = org_client.post(f"/api/admin/users/{hr}/reset-password").json()["temporary_password"]
    org_client.cookies.clear()
    assert org_client.post("/api/auth/login", json={"email": "hr@mitra.local", "password": PASSWORD}).status_code == 401
    assert org_client.post("/api/auth/login", json={"email": "hr@mitra.local", "password": reset}).status_code == 200


async def test_business_units_are_created_renamed_and_kept_unique(org_client):
    admin(org_client)
    made = org_client.post("/api/admin/business-units", json={"name": "Quality Assurance"})
    assert made.status_code == 200
    assert org_client.post("/api/admin/business-units", json={"name": "quality assurance"}).status_code == 409
    ai = org_client.demo.bu_ids["AI, ML & Data Science"]
    assert org_client.post(f"/api/admin/business-units/{ai}", json={"name": "Sales"}).status_code == 409
    assert org_client.post(f"/api/admin/business-units/{ai}", json={"name": "AI Research"}).status_code == 200

    units = {b["name"]: b for b in org_client.get("/api/admin/business-units").json()["business_units"]}
    assert units["AI Research"]["heads"] == 1 and units["AI Research"]["open_jobs"] == 1
    assert units["AI Research"]["candidates"] == 1
    # The job's text copy of the BU name follows the rename.
    jobs = org_client.get("/api/search?q=AI Engineer").json()["jobs"]
    assert jobs[0]["bu_name"] == "AI Research"


# ---- privacy ---------------------------------------------------------------------------------------


async def closed_candidate(client, tmp_path, monkeypatch, *, days_ago: int) -> tuple[str, str]:
    """The AI candidate, rejected `days_ago` days ago, with a CV, a screening, an
    HR round with a transcript and evaluation, and a stored recording file."""
    from app import db, recordings
    from app.db import Candidate, Interview, RecordingStatus
    from hiring.models import DecisionRecord, HRRound, Screening

    monkeypatch.setenv(recordings.RECORDINGS_DIR_ENV, str(tmp_path / "rec"))
    (tmp_path / "rec").mkdir(exist_ok=True)
    (tmp_path / "rec" / "round.mp4").write_bytes(b"video")
    cid = client.demo.candidate_ids["ai"]
    when = datetime.now(UTC) - timedelta(days=days_ago)
    async with db.get_sessionmaker()() as session:
        candidate = await session.get(Candidate, cid)
        candidate.stage = "rejected"
        candidate.source_filename = "cv.pdf"
        session.add(DecisionRecord(org_id=client.demo.org_id, candidate_id=cid, from_stage="hr_done",
                                   to_stage="rejected", actor="hr", reason="Not a fit.", created_at=when))
        session.add(Screening(candidate_id=cid, status="ready", fit_analysis={"x": 1}, hr_questions={"questions": []}))
        interview = Interview(
            id="int_test_1", candidate_id=cid, meeting_id="123456", password="pw", daily_room_name="r",
            daily_room_url="https://example.daily.co/r", room_expires_at=when, status="completed",
            round="hr", transcript={"turns": []}, consent_accepted_at=when,
            recording_status=RecordingStatus.STORED, recording_path="round.mp4", org_id=client.demo.org_id,
        )
        session.add(interview)
        await session.flush()
        session.add(HRRound(interview_id=interview.id, candidate_id=cid, notes="Clear.",
                            transcript=[{"index": 0, "speaker": "candidate", "text": "hello"}],
                            evaluation={"summary": "ok"}))
        await session.commit()
    return cid, interview.id


async def test_consent_records_show_who_consented(org_client, tmp_path, monkeypatch):
    await closed_candidate(org_client, tmp_path, monkeypatch, days_ago=1)
    admin(org_client)
    records = org_client.get("/api/admin/consent").json()
    assert records["summary"] == {"consented": 1, "missing": 0}
    assert records["items"][0]["round"] == "hr" and records["items"][0]["consent_accepted_at"]
    assert org_client.get("/api/admin/consent?show=missing").json()["total"] == 0


async def test_retention_counts_only_what_is_past_its_period(org_client, tmp_path, monkeypatch):
    await closed_candidate(org_client, tmp_path, monkeypatch, days_ago=60)
    admin(org_client)
    due = {c["key"]: c["due"] for c in org_client.get("/api/admin/retention").json()["categories"]}
    # Defaults: CVs 180 days, recordings 30, transcripts 365.
    assert due == {"cv": 0, "recording": 1, "transcript": 0}

    changed = org_client.post("/api/admin/retention", json={"cv": 30}).json()
    assert {c["key"]: c["due"] for c in changed["categories"]}["cv"] == 1
    assert org_client.post("/api/admin/retention", json={"cv": 0}).status_code == 422


async def test_a_retention_run_removes_that_kind_of_data_and_nothing_else(org_client, tmp_path, monkeypatch):
    from app import db
    from app.db import Candidate, Interview
    from hiring.models import AuditEvent, DecisionRecord, HRRound

    cid, interview_id = await closed_candidate(org_client, tmp_path, monkeypatch, days_ago=60)
    admin(org_client)
    run = org_client.post("/api/admin/retention/recording/run").json()
    assert run["erased"] == 1
    assert not (tmp_path / "rec" / "round.mp4").exists()

    async with db.get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        assert interview.recording_status == "expired" and interview.recording_path is None
        assert interview.transcript is not None  # a different category, still within its period
        assert (await session.get(Candidate, cid)).cv_text
        assert (await session.execute(select(HRRound))).scalar_one().evaluation is not None
        assert (await session.execute(select(DecisionRecord).where(DecisionRecord.candidate_id == cid))).scalars().all()
        assert "retention_run" in (await session.execute(select(AuditEvent.action))).scalars().all()
    # Nothing is due any more, so a second run erases nothing.
    assert org_client.post("/api/admin/retention/recording/run").json()["erased"] == 0


async def test_a_completed_deletion_request_erases_the_person_but_keeps_the_decisions(org_client, tmp_path, monkeypatch):
    from app import db
    from app.db import Candidate, Interview
    from hiring.models import DecisionRecord, HRRound, Screening

    cid, interview_id = await closed_candidate(org_client, tmp_path, monkeypatch, days_ago=1)
    admin(org_client)
    assert org_client.post("/api/admin/deletion-requests", json={}).status_code == 422
    logged = org_client.post("/api/admin/deletion-requests",
                             json={"candidate_id": cid, "contact_note": "Emailed HR on 18 Nov."}).json()
    assert logged["status"] == "open" and logged["candidate_name"] == "Sample AI candidate"
    assert org_client.get("/api/admin/deletion-requests").json()["open"] == 1

    assert org_client.post(f"/api/admin/deletion-requests/{logged['id']}/complete", json={"resolution": " "}).status_code == 422
    done = org_client.post(f"/api/admin/deletion-requests/{logged['id']}/complete", json={"resolution": "Erased all data."})
    assert done.status_code == 200 and done.json()["status"] == "completed"
    assert not (tmp_path / "rec" / "round.mp4").exists()

    async with db.get_sessionmaker()() as session:
        candidate = await session.get(Candidate, cid)
        assert candidate.name == "Erased candidate" and candidate.cv_text == "" and candidate.source_filename is None
        interview = await session.get(Interview, interview_id)
        assert interview.transcript is None and interview.recording_status == "deleted"
        hr = (await session.execute(select(HRRound))).scalar_one()
        assert hr.transcript is None and hr.evaluation is None and hr.notes is None
        screening = (await session.execute(select(Screening))).scalar_one()
        assert screening.fit_analysis is None and screening.hr_questions is None
        decisions = (await session.execute(select(DecisionRecord).where(DecisionRecord.candidate_id == cid))).scalars().all()
        assert decisions and decisions[0].reason == "Not a fit."
    # A closed request cannot be resolved again.
    assert org_client.post(f"/api/admin/deletion-requests/{logged['id']}/refuse", json={"resolution": "x"}).status_code == 409


async def test_a_refused_deletion_request_keeps_the_data_and_says_why(org_client, tmp_path, monkeypatch):
    from app import db
    from app.db import Candidate

    cid, _ = await closed_candidate(org_client, tmp_path, monkeypatch, days_ago=1)
    admin(org_client)
    logged = org_client.post("/api/admin/deletion-requests", json={"candidate_id": cid}).json()
    assert org_client.post(f"/api/admin/deletion-requests/{logged['id']}/refuse", json={"resolution": ""}).status_code == 422
    refused = org_client.post(f"/api/admin/deletion-requests/{logged['id']}/refuse",
                              json={"resolution": "Legal hold: an open dispute."}).json()
    assert refused["status"] == "refused" and refused["resolved_by"] == "Org Admin"
    async with db.get_sessionmaker()() as session:
        assert (await session.get(Candidate, cid)).name == "Sample AI candidate"
    assert (tmp_path / "rec" / "round.mp4").exists()
