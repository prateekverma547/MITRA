"""Model calls and what each stage costs: pricing, recording that never breaks
the call it records, the trace view and the costs by stage."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hiring import usage
from tests.console_support import org_client, sign_in  # noqa: F401


def response(prompt=1000, cached=400, completion=200, content='{"ratings": []}'):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached), completion_tokens_details=None,
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


# ---- prices ---------------------------------------------------------------------------------


def test_text_cost_counts_cached_tokens_at_their_own_price():
    # gpt-4.1: $2 per 1M input, $0.50 cached, $8 output.
    usd = usage.text_cost("gpt-4.1", input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=100_000)
    assert usd == pytest.approx(600_000 * 2 / 1e6 + 400_000 * 0.5 / 1e6 + 100_000 * 8 / 1e6)
    # A dated snapshot is priced as its base model.
    assert usage.text_cost("gpt-4.1-2025-04-14", input_tokens=1000) == usage.text_cost("gpt-4.1", input_tokens=1000)


def test_an_unknown_model_is_unpriced_never_guessed(monkeypatch):
    assert usage.text_cost("gpt-6.1-sol", input_tokens=1000, output_tokens=10) is None
    monkeypatch.setenv("MODEL_PRICES_JSON", '{"gpt-6.1-sol": {"input": 1.0, "cached_input": 0.1, "output": 4.0}}')
    assert usage.text_cost("gpt-6.1-sol", input_tokens=1_000_000) == pytest.approx(1.0)


def test_transcription_is_priced_by_the_minute():
    assert usage.text_cost("gpt-4o-transcribe-diarize", input_tokens=0, audio_seconds=600) == pytest.approx(0.06)
    assert usage.text_cost("gpt-4o-transcribe-diarize", input_tokens=0) is None  # no duration, no price


def test_a_technical_interview_is_priced_from_the_engines_token_counts():
    counts = {"input_tokens": 1_000_000, "input_audio_tokens": 800_000, "cached_input_tokens": 0,
              "cached_input_audio_tokens": 0, "output_tokens": 100_000, "output_audio_tokens": 100_000,
              "estimated_usd": 99.0}
    usd, known = usage.realtime_cost("gpt-realtime", counts)
    assert known and usd == pytest.approx(800_000 * 32 / 1e6 + 200_000 * 4 / 1e6 + 100_000 * 64 / 1e6)
    # A model our table does not know falls back to the engine's own estimate.
    assert usage.realtime_cost("gpt-realtime-2.1", counts) == (99.0, True)
    assert usage.realtime_cost("gpt-realtime-2.1", {}) == (None, False)


# ---- recording -------------------------------------------------------------------------------


async def calls():
    from sqlalchemy import select

    from app import db
    from hiring.models import ModelCall

    async with db.get_sessionmaker()() as session:
        return list((await session.execute(select(ModelCall).order_by(ModelCall.id))).scalars())


async def test_recording_a_call_keeps_metadata_and_the_org(org_client):
    cid = org_client.demo.candidate_ids["ai"]
    t0 = usage.started()
    await usage.record(stage="cv_screening", purpose="CV fit", model="gpt-4.1", started_at=t0, response=response(),
                       candidate_id=cid, summary={"rated": 3}, output_preview="x" * 500)
    [row] = await calls()
    assert row.org_id == org_client.demo.org_id and row.job_id == org_client.demo.job_ids["AI Engineer"]
    assert (row.input_tokens, row.cached_input_tokens, row.output_tokens) == (1000, 400, 200)
    assert row.price_known and row.cost_usd == pytest.approx(600 * 2 / 1e6 + 400 * 0.5 / 1e6 + 200 * 8 / 1e6)
    assert len(row.output_preview) == 280 and row.status == "ok"


async def test_recording_never_breaks_the_call(org_client, monkeypatch):
    from app import db

    def broken():
        raise RuntimeError("database is down")

    monkeypatch.setattr(db, "get_sessionmaker", broken)
    monkeypatch.setattr(usage, "get_sessionmaker", broken)
    await usage.record(stage="cv_screening", purpose="CV fit", model="gpt-4.1", started_at=usage.started())


async def test_the_screener_records_success_and_failure(org_client, monkeypatch):
    from screening.fit import Screener, ScreeningError

    screener = Screener(api_key="sk-test", model="gpt-4.1")
    cid, jid = org_client.demo.candidate_ids["ai"], org_client.demo.job_ids["AI Engineer"]

    async def ok(**kwargs):
        return response()

    async def fails(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(screener._client.chat.completions, "create", ok)
    payload, _, _ = await screener._json("s", "u", stage="cv_screening", purpose="p", prompt_version="v",
                                          candidate_id=cid, job_id=jid)
    assert payload == {"ratings": []}

    monkeypatch.setattr(screener._client.chat.completions, "create", fails)
    with pytest.raises(ScreeningError):
        await screener._json("s", "u", stage="cv_screening", purpose="p", prompt_version="v", candidate_id=cid, job_id=jid)
    [failed] = await calls()  # the successful one is recorded by the caller, after building the fit
    assert failed.status == "error" and "rate limited" in failed.error and failed.cost_usd == 0.0


# ---- the trace view and costs ----------------------------------------------------------------


async def seed(client):
    from app import db
    from app.db import Interview

    cid = client.demo.candidate_ids["ai"]
    for stage, model, resp in [("cv_screening", "gpt-4.1", response()), ("hr_questions", "gpt-4.1", response()),
                               ("hr_evaluation", "gpt-6.1-sol", response())]:
        await usage.record(stage=stage, purpose=stage, model=model, started_at=usage.started(), response=resp, candidate_id=cid)
    async with db.get_sessionmaker()() as session:
        session.add(Interview(
            id="int_tech", candidate_id=cid, meeting_id="1", password="p", daily_room_name="r",
            daily_room_url="https://x", room_expires_at=datetime.now(UTC) + timedelta(hours=1), round="technical",
            status="completed", ended_at=datetime.now(UTC),
            session_metrics={"realtime_model": "gpt-realtime-2.1",
                             "usage": {"input_tokens": 10, "output_tokens": 5, "estimated_usd": 1.25, "minutes": 30}},
        ))
        await session.commit()
    return cid


async def test_costs_are_grouped_by_stage_per_candidate(org_client):
    await seed(org_client)
    sign_in(org_client, "admin@mitra.local")
    data = org_client.get("/api/transparency/costs?days=0").json()
    stages = {s["key"]: s for s in data["by_stage"]}
    assert stages["cv_screening"]["calls"] == 1 and stages["cv_screening"]["cost_usd"] > 0
    assert stages["hr_evaluation"]["unpriced"] == 1  # gpt-6.1-sol has no price set
    assert stages["technical_interview"]["cost_usd"] == pytest.approx(1.25)
    assert stages["technical_plan"]["not_measured"]
    assert data["totals"]["candidates"] == 1
    assert data["totals"]["per_candidate_usd"] == pytest.approx(data["totals"]["cost_usd"])
    assert [s["key"] for s in data["by_stage"]][:3] == ["job_understanding", "cv_screening", "hr_questions"]


async def test_the_trace_lists_and_opens_each_call(org_client):
    await seed(org_client)
    sign_in(org_client, "admin@mitra.local")
    listed = org_client.get("/api/transparency/calls").json()
    assert listed["total"] == 4 and listed["summary"]["unpriced"] == 1
    tech = next(i for i in listed["items"] if i["stage"] == "technical_interview")
    detail = org_client.get(f"/api/transparency/calls/{tech['id']}").json()
    assert detail["summary"]["minutes"] == 30 and "estimate" in detail["cost_note"]
    one = next(i for i in listed["items"] if i["stage"] == "cv_screening")
    assert org_client.get(f"/api/transparency/calls/{one['id']}").json()["input_tokens"] == 1000
    only = org_client.get("/api/transparency/calls?stage=hr_questions").json()
    assert only["total"] == 1


@pytest.mark.parametrize("who", ["hr@mitra.local", "ai.head@mitra.local"])
async def test_only_the_org_admin_sees_calls_and_costs(org_client, who):
    sign_in(org_client, who)
    assert org_client.get("/api/transparency/calls").status_code == 403
    assert org_client.get("/api/transparency/costs").status_code == 403
