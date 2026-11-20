"""Model settings, the shared text-model helper, usage records, and F-017.

No keys and no network: the helper is driven with a fake client, and the
start-up failure with real (tiny) subprocesses.
"""

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from bot import config
from bot.brain import drivers
from shared.text_models import (
    PRICES,
    REASONING_MODELS,
    TextModelError,
    TextModelSchemaError,
    is_reasoning_model,
    price_usd,
    request_args,
    request_structured,
    strict_json_schema,
)

MODEL_VARS = ("OPENAI_TEXT_MODEL", "OPENAI_BLUEPRINT_MODEL", "OPENAI_FEEDBACK_MODEL",
              "OPENAI_VISION_MODEL", "OPENAI_AUDITOR_MODEL", "REALTIME_MODEL",
              "REALTIME_TRANSCRIPTION_MODEL", "REALTIME_TRANSCRIPTION_DELAY")


@pytest.fixture(autouse=True)
def clean_model_env(monkeypatch):
    for name in MODEL_VARS:
        monkeypatch.delenv(name, raising=False)


# -- settings ----------------------------------------------------------------------


def test_defaults():
    assert config.text_model() == "gpt-6.1-sol"
    assert config.blueprint_model() == config.vision_model() == "gpt-6.1-sol"
    # Scoring is pinned to gpt-4.1 for now (findings F-058), not the text model.
    assert config.feedback_model() == "gpt-4.1"
    assert config.auditor_model() == "gpt-4.1-mini"
    assert config.realtime_model() == "gpt-realtime-2.1"
    assert config.realtime_transcription_model() == "gpt-realtime-whisper"
    assert config.realtime_transcription_delay() is None


def test_the_text_model_moves_every_off_call_job_and_each_job_can_override(monkeypatch):
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "gpt-6.2-sol")
    assert config.blueprint_model() == config.vision_model() == "gpt-6.2-sol"
    assert config.feedback_model() == "gpt-4.1", "scoring does not follow the text model for now"
    monkeypatch.setenv("OPENAI_FEEDBACK_MODEL", "gpt-6.2-sol")
    assert config.feedback_model() == "gpt-6.2-sol", "an explicit setting still wins"
    assert config.blueprint_model() == "gpt-6.2-sol"
    assert config.model_for("feedback") == "gpt-6.2-sol"


def test_the_existing_variable_names_keep_working(monkeypatch):
    """OPENAI_BLUEPRINT_MODEL and OPENAI_FEEDBACK_MODEL were already the names
    (config.py before task 2), so an existing .env setting still wins."""
    monkeypatch.setenv("OPENAI_BLUEPRINT_MODEL", "gpt-4.1")
    monkeypatch.setenv("OPENAI_FEEDBACK_MODEL", "gpt-4.1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("DAILY_API_KEY", "k")
    settings = config.Settings.load()
    assert settings.blueprint_model == settings.feedback_model == "gpt-4.1"


def test_clarify_and_the_pipeline_judge_keep_their_pre_task_2_model(monkeypatch):
    """They pass a fixed temperature, which a reasoning model rejects."""
    assert config.legacy_blueprint_model() == "gpt-4.1"
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "gpt-6.2-sol")
    assert config.legacy_blueprint_model() == "gpt-4.1"
    monkeypatch.setenv("OPENAI_BLUEPRINT_MODEL", "gpt-4.1-mini")
    assert config.legacy_blueprint_model() == "gpt-4.1-mini"


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-5-mini", "gpt-6.1-sol-2026-04-30"])
def test_the_auditor_refuses_a_reasoning_model_at_start_up(monkeypatch, model):
    monkeypatch.setenv("OPENAI_AUDITOR_MODEL", model)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("DAILY_API_KEY", "k")
    with pytest.raises(config.InvalidConfig, match="never be one"):
        config.Settings.load()
    assert issubclass(config.InvalidConfig, config.MissingConfig), "run_bot.main catches it"


def test_the_transcription_delay_is_validated_and_sent_only_when_set(monkeypatch):
    from bot.services.realtime import build_realtime_llm, realtime_session_dict
    from pipecat.services.openai.realtime import events

    assert realtime_session_dict(instructions="i", tools=[])["audio"]["input"]["transcription"] == {
        "model": "gpt-realtime-whisper"}
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_DELAY", "high")
    llm = build_realtime_llm(api_key="k", instructions="i", tools=None)
    wire = events.SessionUpdateEvent(session=llm._settings.session_properties).model_dump(
        exclude_none=True, serialize_as_any=True)
    assert wire["session"]["audio"]["input"]["transcription"]["delay"] == "high"
    monkeypatch.setenv("REALTIME_TRANSCRIPTION_DELAY", "fastest")
    with pytest.raises(config.InvalidConfig, match="minimal, low, medium, high, xhigh"):
        config.realtime_transcription_delay()


async def test_the_service_sends_the_delay_on_the_wire(monkeypatch):
    """Pipecat's own dump would drop the subclass field (F-051)."""
    from pipecat.services.openai.realtime import events

    from bot.services.realtime import build_realtime_llm

    monkeypatch.setenv("REALTIME_TRANSCRIPTION_DELAY", "low")
    llm = build_realtime_llm(api_key="k", instructions="i", tools=None)
    sent = []

    async def ws_send(message):
        sent.append(message)

    llm._ws_send = ws_send
    await llm.send_client_event(events.SessionUpdateEvent(session=llm._settings.session_properties))
    assert sent[0]["session"]["audio"]["input"]["transcription"]["delay"] == "low"


# -- request arguments -----------------------------------------------------------------


def test_reasoning_models_get_no_temperature_and_an_effort():
    assert request_args("gpt-6.1-sol", temperature=0.4) == {"reasoning_effort": "medium"}
    assert request_args("gpt-6.1-sol-2026-04-30", temperature=0.4) == {"reasoning_effort": "medium"}
    assert request_args("gpt-6.1-sol", temperature=0.4, effort="low") == {"reasoning_effort": "low"}


def test_other_models_keep_their_temperature():
    assert request_args("gpt-4.1", temperature=0.4) == {"temperature": 0.4}
    assert request_args("gpt-4.1-mini") == {}
    assert is_reasoning_model("gpt-4.1") is False


def test_drivers_reads_the_same_table_with_its_rows_unchanged():
    assert drivers.LOWEST_EFFORT is REASONING_MODELS
    assert {k: REASONING_MODELS[k] for k in
            ("gpt-5-mini", "gpt-5-nano", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.6-luna")} == {
        "gpt-5-mini": "minimal", "gpt-5-nano": "minimal", "gpt-5.4-mini": "none",
        "gpt-5.4-nano": "none", "gpt-5.6-luna": "none"}


# -- schemas -----------------------------------------------------------------------------


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def _response_models():
    from blueprint.generate import GenerationResponse
    from blueprint.refine import RefinementResponse
    from feedback.score import ScoringResponse

    return [GenerationResponse, RefinementResponse, ScoringResponse]


@pytest.mark.parametrize("model_cls", _response_models(), ids=lambda m: m.__name__)
def test_the_schema_meets_strict_structured_outputs(model_cls):
    schema = strict_json_schema(model_cls)
    text = json.dumps(schema)
    assert schema["type"] == "object"
    for banned in ('"$ref"', '"$defs"', '"default"', '"title"', '"allOf"'):
        assert banned not in text
    for obj in _objects(schema):
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])


def test_the_scoring_schema_keeps_the_contracts_types():
    schema = strict_json_schema(_response_models()[2])
    item = schema["properties"]["competency_scores"]["items"]["properties"]
    assert item["coverage"]["enum"] == ["not_started", "insufficient", "partial", "sufficient"]
    assert item["score"]["anyOf"] == [{"maximum": 5.0, "minimum": 0.0, "type": "number"}, {"type": "null"}]
    assert "insufficient_signal" in schema["properties"]["recommendation"]["enum"]


# -- the helper, with a fake client ----------------------------------------------------


class Tiny(BaseModel):
    ok: bool


def _response(content, prompt=100, completion=20, cached=40, reasoning=8):
    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))
    message = SimpleNamespace(content=content, refusal=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _timeout():
    return openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))


def _bad_request():
    return openai.BadRequestError("bad", response=httpx.Response(
        400, request=httpx.Request("POST", "https://api.openai.com")), body=None)


async def test_the_request_asks_for_a_strict_schema_and_records_usage():
    client = FakeClient(_response('{"ok": true}'))
    result = await request_structured(job="feedback", model="gpt-6.1-sol", client=client,
                                      messages=[], response_model=Tiny, temperature=0.2)
    sent = client.calls[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert "temperature" not in sent and sent["reasoning_effort"] == "medium"
    r = result.record
    assert (r.job, r.model, r.input_tokens, r.cached_input_tokens, r.output_tokens, r.reasoning_tokens) == (
        "feedback", "gpt-6.1-sol", 100, 40, 20, 8)
    # 60 uncached x $2 + 40 cached x $0.10 + 20 x $10, per million.
    assert r.cost_usd == pytest.approx((60 * 2.0 + 40 * 0.10 + 20 * 10.0) / 1_000_000)
    assert r.price_known is True and "pricing" in r.price_source


async def test_a_non_reasoning_model_gets_its_temperature():
    client = FakeClient(_response('{"ok": true}'))
    await request_structured(job="blueprint", model="gpt-4.1", client=client, messages=[],
                             response_model=Tiny, temperature=0.4)
    assert client.calls[0]["temperature"] == 0.4 and "reasoning_effort" not in client.calls[0]


async def test_an_unpriced_model_is_recorded_as_unknown_never_guessed():
    client = FakeClient(_response('{"ok": true}'))
    result = await request_structured(job="feedback", model="some-new-model", client=client,
                                      messages=[], response_model=Tiny)
    assert result.record.cost_usd is None and result.record.price_known is False
    assert price_usd("some-new-model", input_tokens=1, cached_input_tokens=0, output_tokens=1) is None
    assert set(PRICES) >= {"gpt-6.1-sol", "gpt-4.1", "gpt-4.1-mini"}


async def test_a_transient_failure_is_retried_once():
    client = FakeClient(_timeout(), _response('{"ok": true}'))
    result = await request_structured(job="feedback", model="gpt-4.1", client=client,
                                      messages=[], response_model=Tiny, retry_delay_s=0)
    assert result.record.attempts == 2


async def test_a_second_transient_failure_raises_a_clear_error():
    client = FakeClient(_timeout(), _timeout())
    with pytest.raises(TextModelError, match="feedback call to gpt-4.1 failed after 2 attempt"):
        await request_structured(job="feedback", model="gpt-4.1", client=client,
                                 messages=[], response_model=Tiny, retry_delay_s=0)


async def test_a_bad_request_is_not_retried():
    client = FakeClient(_bad_request(), _response('{"ok": true}'))
    with pytest.raises(TextModelError, match="after 1 attempt"):
        await request_structured(job="feedback", model="gpt-4.1", client=client,
                                 messages=[], response_model=Tiny, retry_delay_s=0)
    assert len(client.calls) == 1


async def test_a_reply_that_does_not_validate_is_a_schema_error():
    client = FakeClient(_response('{"nope": 1}'))
    with pytest.raises(TextModelSchemaError) as info:
        await request_structured(job="feedback", model="gpt-4.1", client=client,
                                 messages=[], response_model=Tiny)
    assert info.value.record.outcome == "schema_error"


def test_scoring_usage_is_stored_in_the_session_metrics():
    from feedback.run import _record_usage
    from shared.text_models import UsageRecord

    interview = SimpleNamespace(session_metrics={"disconnects": 0})
    scorer = SimpleNamespace(last_usage=UsageRecord(job="feedback", model="gpt-6.1-sol", input_tokens=5))
    _record_usage(interview, scorer)
    _record_usage(interview, scorer)
    assert interview.session_metrics["disconnects"] == 0
    assert [c["job"] for c in interview.session_metrics["text_model_calls"]] == ["feedback", "feedback"]
    _record_usage(interview, SimpleNamespace(last_usage=None))  # no model call: nothing added
    assert len(interview.session_metrics["text_model_calls"]) == 2


# -- F-017: a bot that fails at start-up -------------------------------------------------


@pytest.fixture
async def interview_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/f017.db")
    from app import db

    await db.reset_engine()
    await db.create_all()
    yield db
    await db.reset_engine()


async def _seed(db, status):
    from app.db import Candidate, Interview, Job

    iid = f"int_{uuid.uuid4().hex[:12]}"
    async with db.get_sessionmaker()() as s:
        s.add(Job(id=f"j{iid}", title="PM", jd_text="jd"))
        s.add(Candidate(id=f"c{iid}", job_id=f"j{iid}", name="A", cv_text="cv"))
        s.add(Interview(id=iid, candidate_id=f"c{iid}", meeting_id=f"{uuid.uuid4().int % 10**9:09d}",
                        password="p", daily_room_name="r", daily_room_url="u",
                        room_expires_at=datetime.now(UTC) + timedelta(hours=1), status=status))
        await s.commit()
    return iid


async def _status(db, iid):
    from app.db import Interview

    async with db.get_sessionmaker()() as s:
        iv = await s.get(Interview, iid)
        return str(iv.status), iv.failure_reason


async def test_an_unfinished_interview_is_marked_failed_with_a_reason(interview_db):
    from app.db import InterviewStatus
    from bot.persistence import mark_failed_if_unfinished

    iid = await _seed(interview_db, InterviewStatus.IN_PROGRESS)
    assert await mark_failed_if_unfinished(interview_id=iid, reason="could not start") is True
    assert await _status(interview_db, iid) == ("failed", "could not start")


async def test_a_saved_interview_is_never_overwritten(interview_db):
    from app.db import InterviewStatus
    from bot.persistence import mark_failed_if_unfinished

    iid = await _seed(interview_db, InterviewStatus.COMPLETED)
    assert await mark_failed_if_unfinished(interview_id=iid, reason="x") is False
    assert (await _status(interview_db, iid))[0] == "completed"


async def test_a_bot_that_dies_without_writing_is_noticed(interview_db):
    """The web-process backstop: a real child process that exits with code 3."""
    from app.db import InterviewStatus
    from bot.persistence import watch_bot_exit

    iid = await _seed(interview_db, InterviewStatus.IN_PROGRESS)
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import sys; sys.exit(3)")
    released = []
    await watch_bot_exit(iid, process, on_exit=lambda: released.append(iid))
    status, reason = await _status(interview_db, iid)
    assert status == "failed" and "code 3" in reason
    assert released == [iid], "the capacity slot is released at once"


def test_the_bot_marks_its_own_start_up_failure(tmp_path, monkeypatch):
    """run_bot.main on the real start-up path: the blueprint cannot be found, so
    the bot exits before any OpenAI or Daily call. Sync on purpose: main() runs
    its own event loop."""
    from app import db
    from app.db import InterviewStatus
    from bot import run_bot

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/own.db")

    async def setup():
        await db.reset_engine()
        await db.create_all()
        iid = await _seed(db, InterviewStatus.IN_PROGRESS)
        await db.reset_engine()
        return iid

    async def read(iid):
        result = await _status(db, iid)
        await db.reset_engine()
        return result

    iid = asyncio.run(setup())
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("DAILY_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["run_bot", "--room-url", "https://x.daily.co/r",
                                      "--interview-id", iid, "--blueprint-id", "not-a-candidate"])
    with pytest.raises(SystemExit) as exited:
        run_bot.main()
    assert exited.value.code == 1
    status, reason = asyncio.run(read(iid))
    assert status == "failed"
    assert reason.startswith("The interviewer could not start: No interview blueprint")


async def test_the_join_route_watches_the_bot_it_spawns(tmp_path, monkeypatch):
    """Real route and real _start_bot; the 'bot' is a child that exits 7 without
    writing anything, so only the web process's exit watcher can fail it."""
    from fastapi.testclient import TestClient

    from tests.test_interviews import FakeRoom, blueprint_for

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/route.db")
    for key in ("OPENAI_API_KEY", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    from app import db, interviews, main
    from app.db import BlueprintStatus, Candidate, Interview, Job

    await db.reset_engine()

    async def fake_room(**kw):
        return FakeRoom()

    async def fake_token(**kw):
        return "tok"

    real_exec = asyncio.create_subprocess_exec

    async def short_lived_bot(*args, **kwargs):
        return await real_exec(sys.executable, "-c", "import sys; sys.exit(7)")

    monkeypatch.setattr(interviews, "create_room", fake_room)
    monkeypatch.setattr(interviews, "create_meeting_token", fake_token)
    monkeypatch.setattr(interviews.asyncio, "create_subprocess_exec", short_lived_bot)
    with TestClient(main.app) as client:
        async def seed():
            async with db.get_sessionmaker()() as s:
                s.add(Job(id="j1", title="PM", jd_text="jd"))
                s.add(Candidate(id="c1", job_id="j1", name="A", cv_text="cv",
                                blueprint=json.loads(blueprint_for("c1").model_dump_json()),
                                blueprint_status=BlueprintStatus.READY))
                await s.commit()

        client.portal.call(seed)
        client.post("/admin/login", json={"password": "pw"})
        booked = client.post("/candidates/c1/interviews").json()
        joined = client.post("/interviews/join", json={
            "meeting_id": booked["meeting_id"], "password": booked["password"], "consent": True})
        assert joined.status_code == 200

        async def status():
            async with db.get_sessionmaker()() as s:
                iv = await s.get(Interview, booked["interview_id"])
                return str(iv.status), iv.failure_reason

        for _ in range(100):
            current = client.portal.call(status)
            if current[0] != "in_progress":
                break
            await asyncio.sleep(0.05)
    assert current[0] == "failed" and "code 7" in current[1]
    from app.capacity import registry

    assert booked["interview_id"] not in registry._bots
    await db.reset_engine()
