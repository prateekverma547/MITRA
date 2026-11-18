"""Sign-in, roles and BU scoping: who can see and do what.

The rule under test everywhere here: the account decides the role, and a BU
head sees nothing outside their BU, not even that it exists.
"""

import time

import pytest
from sqlalchemy import select

from org import auth
from shared.stages import Role

from tests.console_support import PASSWORD, org_client, sign_in  # noqa: F401


# ---- passwords and tokens ----------------------------------------------------


def test_a_password_verifies_and_a_wrong_one_does_not():
    stored = auth.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("correct horse batterY", stored)
    assert not auth.verify_password("x", "not-a-hash")


def test_the_same_password_hashes_differently_each_time():
    assert auth.hash_password("same password") != auth.hash_password("same password")


def test_a_session_token_round_trips_and_rejects_tampering(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    token = auth.issue_session("user_abc")
    assert auth.read_session(token) == "user_abc"
    payload, _, signature = token.rpartition(".")
    forged = payload.replace("user_abc", "user_xyz") + "." + signature
    assert auth.read_session(forged) is None
    assert auth.read_session("garbage") is None
    assert auth.read_session(None) is None


def test_an_expired_session_is_refused(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    old = auth.issue_session("user_abc", now=time.time() - auth.SESSION_SECONDS - 1)
    assert auth.read_session(old) is None


def test_changing_the_secret_signs_everyone_out(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "first")
    token = auth.issue_session("user_abc")
    monkeypatch.setenv("SESSION_SECRET", "second")
    assert auth.read_session(token) is None


# ---- the app -------------------------------------------------------------------


async def test_signing_in_returns_the_role_the_account_has(org_client):
    response = org_client.post(
        "/api/auth/login", json={"email": "AI.Head@mitra.local", "password": PASSWORD}
    )
    assert response.status_code == 200
    me = response.json()
    assert me["role"] == "bu_head"
    assert me["bu_name"] == "AI"
    assert "password_hash" not in me
    assert org_client.get("/api/auth/me").json()["email"] == "ai.head@mitra.local"


async def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(org_client):
    wrong = org_client.post("/api/auth/login", json={"email": "hr@mitra.local", "password": "nope-nope-nope"})
    unknown = org_client.post("/api/auth/login", json={"email": "nobody@mitra.local", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_the_session_cookie_is_http_only(org_client):
    response = org_client.post("/api/auth/login", json={"email": "hr@mitra.local", "password": PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


async def test_signed_out_is_401_and_signing_out_ends_it(org_client):
    assert org_client.get("/api/auth/me").status_code == 401
    sign_in(org_client, "hr@mitra.local")
    assert org_client.get("/api/auth/me").status_code == 200
    org_client.post("/api/auth/logout")
    assert org_client.get("/api/auth/me").status_code == 401


async def test_a_deactivated_account_is_refused_on_its_next_request(org_client):
    from app import db
    from org.models import User

    sign_in(org_client, "hr@mitra.local")
    async with db.get_sessionmaker()() as session:
        user = (await session.execute(select(User).where(User.email == "hr@mitra.local"))).scalar_one()
        user.active = False
        await session.commit()
    assert org_client.get("/api/auth/me").status_code == 401


async def test_a_deployed_instance_without_a_session_secret_refuses_sign_in(org_client, monkeypatch):
    monkeypatch.delenv("SESSION_SECRET")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "test")
    response = org_client.post("/api/auth/login", json={"email": "hr@mitra.local", "password": PASSWORD})
    assert response.status_code == 503
    assert "SESSION_SECRET" in response.json()["detail"]


async def test_sign_ins_are_audited(org_client):
    from app import db
    from hiring.models import AuditEvent

    sign_in(org_client, "hr@mitra.local")
    org_client.post("/api/auth/login", json={"email": "hr@mitra.local", "password": "wrong-wrong"})
    async with db.get_sessionmaker()() as session:
        actions = [e.action for e in (await session.execute(select(AuditEvent))).scalars()]
    assert "signed_in" in actions
    assert "sign_in_failed" in actions


# ---- scoping -------------------------------------------------------------------


async def make_legacy_job() -> str:
    """A job from the old /admin panel: no organisation, no BU."""
    from app import db
    from app.db import Job

    async with db.get_sessionmaker()() as session:
        session.add(Job(id="job_legacy", title="Old admin job", jd_text="jd"))
        await session.commit()
    return "job_legacy"


def home_jobs(client) -> dict[str, list[str]]:
    home = client.get("/api/home").json()
    return {bu["name"]: [j["title"] for j in bu["jobs"]] for bu in home["business_units"]}


async def test_a_bu_head_sees_only_their_own_bu(org_client):
    await make_legacy_job()
    sign_in(org_client, "ai.head@mitra.local")
    assert home_jobs(org_client) == {"AI": ["AI Engineer"]}
    assert [bu["name"] for bu in org_client.get("/api/business-units").json()] == ["AI"]


async def test_hr_and_admin_see_every_bu_and_old_admin_jobs(org_client):
    await make_legacy_job()
    for email in ("hr@mitra.local", "admin@mitra.local"):
        sign_in(org_client, email)
        jobs = home_jobs(org_client)
        assert jobs["AI"] == ["AI Engineer"]
        assert jobs["Finance"] == ["Financial Analyst"]
        assert jobs["Unassigned"] == ["Old admin job"]


async def test_home_counts_candidates_by_stage(org_client):
    sign_in(org_client, "hr@mitra.local")
    home = org_client.get("/api/home").json()
    ai = next(bu for bu in home["business_units"] if bu["name"] == "AI")
    assert ai["jobs"][0]["candidates_by_stage"] == {"screened": 1}


async def test_a_bu_head_cannot_learn_another_bus_candidate_exists(org_client):
    finance_candidate = org_client.demo.candidate_ids["finance"]
    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.get(f"/api/candidates/{finance_candidate}/decisions").status_code == 404
    assert org_client.get("/api/candidates/cand_does_not_exist/decisions").status_code == 404


# ---- moving candidates ----------------------------------------------------------


async def test_hr_moves_a_screened_candidate_and_the_move_is_recorded(org_client):
    candidate = org_client.demo.candidate_ids["ai"]
    sign_in(org_client, "hr@mitra.local")
    assert org_client.get(f"/api/candidates/{candidate}/decisions").json()["next"] == [
        "ready_for_hr", "parked", "rejected",
    ]

    moved = org_client.post(f"/api/candidates/{candidate}/move", json={"to": "ready_for_hr"})
    assert moved.status_code == 200
    history = org_client.get(f"/api/candidates/{candidate}/decisions").json()
    assert history["stage"] == "ready_for_hr"
    assert history["decisions"][-1] | {"at": None} == {
        "from": "screened", "to": "ready_for_hr", "actor": "hr",
        "actor_user_id": org_client.demo.user_ids["hr@mitra.local"],
        "reason": None, "model_recommendation": None, "at": None,
    }


async def test_a_rejection_without_a_reason_is_refused(org_client):
    candidate = org_client.demo.candidate_ids["ai"]
    sign_in(org_client, "hr@mitra.local")
    refused = org_client.post(f"/api/candidates/{candidate}/move", json={"to": "rejected", "reason": "  "})
    assert refused.status_code == 422
    done = org_client.post(
        f"/api/candidates/{candidate}/move",
        json={"to": "rejected", "reason": "No production experience with LLMs."},
    )
    assert done.status_code == 200


async def test_a_move_the_table_does_not_allow_is_a_conflict(org_client):
    candidate = org_client.demo.candidate_ids["ai"]
    sign_in(org_client, "hr@mitra.local")
    response = org_client.post(f"/api/candidates/{candidate}/move", json={"to": "shortlisted"})
    assert response.status_code == 409


async def test_a_bu_head_cannot_move_a_candidate_before_it_reaches_them(org_client):
    candidate = org_client.demo.candidate_ids["ai"]
    sign_in(org_client, "ai.head@mitra.local")
    response = org_client.post(
        f"/api/candidates/{candidate}/move", json={"to": "rejected", "reason": "x"}
    )
    assert response.status_code == 403


async def test_an_org_admin_reads_but_never_moves(org_client):
    candidate = org_client.demo.candidate_ids["ai"]
    sign_in(org_client, "admin@mitra.local")
    assert org_client.get(f"/api/candidates/{candidate}/decisions").json()["next"] == []
    assert org_client.post(f"/api/candidates/{candidate}/move", json={"to": "ready_for_hr"}).status_code == 403


async def test_hr_cannot_reject_after_the_technical_round_but_the_bu_head_can(org_client):
    from app import db
    from app.db import Candidate

    candidate = org_client.demo.candidate_ids["ai"]
    async with db.get_sessionmaker()() as session:
        (await session.get(Candidate, candidate)).stage = "technical_done"
        await session.commit()

    sign_in(org_client, "hr@mitra.local")
    reject = {"to": "rejected", "reason": "Weak on evaluation."}
    assert org_client.post(f"/api/candidates/{candidate}/move", json=reject).status_code == 409
    assert org_client.post(f"/api/candidates/{candidate}/move", json={"to": "with_bu_head"}).status_code == 200

    sign_in(org_client, "finance.head@mitra.local")
    assert org_client.post(f"/api/candidates/{candidate}/move", json=reject).status_code == 404

    sign_in(org_client, "ai.head@mitra.local")
    assert org_client.post(f"/api/candidates/{candidate}/move", json=reject).status_code == 200


async def test_the_system_records_facts_through_the_same_function(org_client):
    from app import db
    from app.db import Candidate
    from hiring.models import DecisionRecord
    from hiring.stages import StageError, move

    async with db.get_sessionmaker()() as session:
        candidate = Candidate(id="cand_new", job_id=org_client.demo.job_ids["AI Engineer"], cv_text="cv")
        session.add(candidate)
        await session.flush()
        record = await move(session, candidate, "screened", by=None, model_recommendation="score 62%")
        assert record.actor == "system" and record.actor_user_id is None
        with pytest.raises(StageError) as refused:
            await move(session, candidate, "rejected", by=None, reason="x")
        assert refused.value.status == 403
        await session.commit()
        rows = (await session.execute(select(DecisionRecord).where(DecisionRecord.candidate_id == "cand_new"))).scalars().all()
    assert [(r.from_stage, r.to_stage) for r in rows] == [("new", "screened")]


# ---- every console route, every role ---------------------------------------------


def console_routes():
    from app.main import app
    from tests.route_walk import api_routes

    return [r for r in api_routes(app.routes) if r.path.startswith("/api/")]


PUBLIC = {("POST", "/api/auth/login"), ("POST", "/api/auth/logout")}


def _url(path: str) -> str:
    return path.replace("{candidate_id}", "cand_x").replace("{job_id}", "job_x")


async def test_every_console_route_is_401_signed_out(org_client):
    org_client.cookies.clear()
    for route in console_routes():
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in PUBLIC:
                continue
            response = org_client.request(method, _url(route.path), json={})
            assert response.status_code == 401, f"{method} {route.path} -> {response.status_code}"


async def test_every_role_restricted_route_refuses_the_other_roles(org_client):
    from tests.route_walk import guards_of

    emails = {Role.ORG_ADMIN: "admin@mitra.local", Role.HR: "hr@mitra.local", Role.BU_HEAD: "ai.head@mitra.local"}
    checked = 0
    for route in console_routes():
        allowed = next(
            (getattr(c, "allowed_roles") for c in guards_of(route) if hasattr(c, "allowed_roles")),
            None,
        )
        if allowed is None:
            continue
        for role, email in emails.items():
            if role in allowed:
                continue
            sign_in(org_client, email)
            for method in route.methods - {"HEAD", "OPTIONS"}:
                response = org_client.request(method, _url(route.path), json={"to": "ready_for_hr"})
                assert response.status_code == 403, f"{role} {method} {route.path} -> {response.status_code}"
                checked += 1
    assert checked, "no role-restricted console routes were found; the sweep is checking nothing"


# ---- account rules -----------------------------------------------------------------


async def test_account_rules_keep_scoping_sound(org_client):
    from app import db
    from org.users import UserError, create_user

    org_id, ai_bu = org_client.demo.org_id, org_client.demo.bu_ids["AI"]
    cases = [
        dict(role=Role.BU_HEAD, bu_id=None, email="a@x.io", password="long-enough-1"),
        dict(role=Role.HR, bu_id=ai_bu, email="b@x.io", password="long-enough-1"),
        dict(role=Role.HR, bu_id=None, email="c@x.io", password="short"),
        dict(role=Role.HR, bu_id=None, email="hr@mitra.local", password="long-enough-1"),
        dict(role=Role.HR, bu_id=None, email="not-an-email", password="long-enough-1"),
    ]
    async with db.get_sessionmaker()() as session:
        for case in cases:
            with pytest.raises(UserError):
                await create_user(session, org_id=org_id, name="n", **case)
