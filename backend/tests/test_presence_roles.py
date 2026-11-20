"""Only the candidate drives the interview (F-028 fix, option a).

Pipecat calls `on_client_connected` / `on_client_disconnected` for every
participant (pipecat/transports/daily/transport.py:3031,3043). A watcher
joining used to reconnect OpenAI and replay an opening; a watcher leaving used
to end the interview. These tests drive the REAL engine entry points
(`run_realtime_bot`, `run_bot`) and fire participants through Pipecat's own
`DailyTransport._on_participant_joined/_left`, the methods daily-python's
callbacks call. Nothing connects: the worker, the runner and OpenAI's
reconnect are recorded instead of run, and the database is a temporary SQLite.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bot.presence import (
    CANDIDATE_MARK_PREFIX,
    DAILY_USER_ID_MAX,
    RoomPresence,
    candidate_user_id,
)

ENGINES = ["realtime", "pipeline"]


def person(pid, *, user_id=None, owner=False, name="Asha Rao"):
    """A participant in daily-python's shape."""
    info = {"userName": name, "isOwner": owner}
    if user_id:
        info["userId"] = user_id
    return {"id": pid, "info": info}


@pytest.fixture
async def room(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "DAILY_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        monkeypatch.setenv(key, "test")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setenv("RECORDING_ENABLED", "")
    # The candidate audio capture (bot/capture.py) writes its manifest under here.
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/presence.db")
    from app import db

    await db.reset_engine()
    await db.create_all()
    yield _Room(tmp_path, monkeypatch)
    await db.reset_engine()


class _Room:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.mp = monkeypatch

    async def seed(self) -> str:
        from app import db
        from app.db import Candidate, Interview, InterviewStatus, Job

        iid = f"int_{uuid.uuid4().hex[:12]}"
        async with db.get_sessionmaker()() as s:
            s.add(Job(id=f"job-{iid}", title="PM", jd_text="jd"))
            s.add(Candidate(id=f"cand-{iid}", job_id=f"job-{iid}", name="Asha", cv_text="cv"))
            s.add(Interview(
                id=iid, candidate_id=f"cand-{iid}", meeting_id=f"{uuid.uuid4().int % 10**9:09d}",
                password="abcdefhj", daily_room_name="r", daily_room_url="https://x.daily.co/r",
                room_expires_at=datetime.now(UTC) + timedelta(hours=1),
                status=InterviewStatus.IN_PROGRESS))
            await s.commit()
        return iid

    async def saved(self, iid) -> dict:
        from app import db
        from app.db import Interview

        async with db.get_sessionmaker()() as s:
            iv = await s.get(Interview, iid)
            return {"status": str(iv.status), "metrics": iv.session_metrics or {}}

    async def run(self, engine, steps, *, interview_id=None):
        """Run one session on `engine`; `steps` get a Recorder and drive it."""
        rec = Recorder()
        mod = _patch_engine(self.mp, engine, rec, self.tmp_path)
        import feedback.run

        self.mp.setattr(feedback.run, "generate_feedback", _no_scoring)

        async def scenario(_self):
            for step in steps:
                if rec.cancelled:
                    break  # the real runner stops here
                await step(rec)

        self.mp.setattr(mod.WorkerRunner, "add_workers", _noop)
        self.mp.setattr(mod.WorkerRunner, "run", scenario)
        kwargs = dict(room_url="https://x.daily.co/r", token=None, session_id="t",
                      blueprint_id="pm_senior", interview_id=interview_id)
        if engine == "realtime":
            await mod.run_realtime_bot(**kwargs)
        else:
            await mod.run_bot(**kwargs)
        return rec


async def _noop(*a, **k):
    return None


async def _no_scoring(interview_id):
    return None


class Recorder:
    def __init__(self):
        self.queued = []
        self.cancelled = 0
        self.reconnects = 0
        self.transport = None
        self.presence = None
        self.coordinator = None
        self.brain = None

    @property
    def openings(self):
        return sum(1 for q in self.queued if q in ("LLMRunFrame", "TTSSpeakFrame"))

    async def join(self, participant):
        if self.transport._input is not None:
            self.transport._input.capture_participant_audio = _noop
        await self.transport._on_participant_joined(participant)
        await self.settle()

    async def leave(self, participant, reason="leftCall"):
        await self.transport._on_participant_left(participant, reason)
        await self.settle()

    async def settle(self):
        await asyncio.sleep(0.01)
        pending = [t for _, t in list(self.transport._event_tasks) if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)


def _patch_engine(mp, engine, rec, tmp_path):
    mp.setenv("INTERVIEW_ENGINE", engine)  # run_bot dispatches on it
    if engine == "realtime":
        import bot.run_realtime as mod
        from bot.services.realtime import MitraRealtimeService

        async def reconnect(self):
            rec.reconnects += 1

        mp.setattr(MitraRealtimeService, "reconnect", reconnect)
        mp.setattr(mod, "GREETING_SETTLE_SECONDS", 0.0)
        mp.setattr(mod.Settings, "load", staticmethod(
            lambda: SimpleNamespace(openai_api_key="test", daily_api_key="test")))
        real_coord = mod.RealtimeCoordinator

        def coord(**kw):
            rec.coordinator = real_coord(**kw)
            return rec.coordinator

        mp.setattr(mod, "RealtimeCoordinator", coord)
    else:
        import bot.run_bot as mod
        from bot.config import Settings

        async def verify_voice(**kw):
            return "voice"

        mp.setattr(mod, "verify_voice", verify_voice)
        mp.setattr(mod, "GREETING_SETTLE_SECONDS", 0.0)
        mp.setattr(mod.Settings, "load", staticmethod(lambda: Settings(
            openai_api_key="test", elevenlabs_api_key="test",
            elevenlabs_voice_id="test", daily_api_key="test")))
        real_brain = mod.InterviewBrain

        def brain(*a, **k):
            rec.brain = real_brain(*a, **k)
            return rec.brain

        mp.setattr(mod, "InterviewBrain", brain)
    mp.setattr(mod, "SESSIONS_DIR", tmp_path / "sessions")

    real_worker = mod.PipelineWorker

    class RecordingWorker(real_worker):
        async def queue_frames(self, frames):
            rec.queued.extend(type(f).__name__ for f in frames)

        async def cancel(self, *a, **k):
            rec.cancelled += 1

    mp.setattr(mod, "PipelineWorker", RecordingWorker)
    real_transport = mod.build_transport

    def transport(**kw):
        rec.transport = real_transport(**kw)
        return rec.transport

    mp.setattr(mod, "build_transport", transport)
    real_presence = mod.RoomPresence

    def presence(**kw):
        rec.presence = real_presence(**kw)
        return rec.presence

    mp.setattr(mod, "RoomPresence", presence)
    return mod


def _finish(rec):
    """The interview reaches its natural end, the way each engine marks it."""
    if rec.coordinator is not None:
        rec.coordinator._over = True  # what finish_interview's _mark_over sets
    else:
        rec.brain._finished = True  # what the brain sets at brain.py's closing


# -- the mark ------------------------------------------------------------------


def test_the_candidate_mark_fits_dailys_user_id_limit():
    """Interview ids are "int_" + 12 hex (app/interviews.py create_interview)."""
    mark = candidate_user_id(f"int_{uuid.uuid4().hex[:12]}")
    assert mark.startswith(CANDIDATE_MARK_PREFIX)
    assert len(mark) == 26 <= DAILY_USER_ID_MAX


async def test_the_join_route_marks_the_candidates_token(tmp_path, monkeypatch):
    """The real route: the candidate's token carries the mark; the bot's does not."""
    from fastapi.testclient import TestClient

    from tests.test_interviews import FakeRoom, blueprint_for  # noqa: F401

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/join.db")
    for key in ("OPENAI_API_KEY", "DAILY_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")  # no Daily calls from the lifespan
    from app import db, interviews, main

    await db.reset_engine()
    tokens = []

    async def fake_room(**kw):
        return FakeRoom()

    async def fake_token(**kw):
        tokens.append(kw)
        return "tok"

    async def fake_start_bot(**kw):
        return None

    monkeypatch.setattr(interviews, "create_room", fake_room)
    monkeypatch.setattr(interviews, "create_meeting_token", fake_token)
    monkeypatch.setattr(interviews, "_start_bot", fake_start_bot)
    with TestClient(main.app) as client:
        client.post("/admin/login", json={"password": "pw"})
        from app.db import BlueprintStatus, Candidate, Job

        async def seed():
            async with db.get_sessionmaker()() as s:
                s.add(Job(id="j1", title="PM", jd_text="jd"))
                s.add(Candidate(id="c1", job_id="j1", name="Asha", cv_text="cv",
                                blueprint=json.loads(blueprint_for("c1").model_dump_json()),
                                blueprint_status=BlueprintStatus.READY))
                await s.commit()

        client.portal.call(seed)
        booked = client.post("/candidates/c1/interviews").json()
        r = client.post("/interviews/join", json={
            "meeting_id": booked["meeting_id"], "password": booked["password"], "consent": True})
    assert r.status_code == 200, r.text
    assert tokens[-1]["user_id"] == candidate_user_id(booked["interview_id"])
    assert tokens[-1]["is_owner"] is False
    await db.reset_engine()


# -- the engines ---------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_watcher_joining_does_not_restart_the_interview(room, engine):
    iid = await room.seed()
    mark = candidate_user_id(iid)
    rec = await room.run(engine, [
        lambda r: r.join(person("c1", user_id=mark)),
        lambda r: r.join(person("w1", name="HR")),
    ], interview_id=iid)
    assert rec.openings == 1
    assert rec.reconnects == (1 if engine == "realtime" else 0)
    assert rec.cancelled == 0
    events = rec.presence.summary()["watcher_events"]
    assert [e["kind"] for e in events] == ["joined"]
    assert rec.presence.summary()["peak_others"] == 1


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_watcher_leaving_does_not_end_the_interview(room, engine):
    iid = await room.seed()
    mark = candidate_user_id(iid)
    rec = await room.run(engine, [
        lambda r: r.join(person("c1", user_id=mark)),
        lambda r: r.join(person("w1", name="HR")),
        lambda r: r.leave(person("w1", name="HR")),
    ], interview_id=iid)
    assert rec.cancelled == 0
    assert rec.presence.disconnects == 0
    assert rec.presence.candidate_present is True
    saved = await room.saved(iid)
    assert [e["kind"] for e in saved["metrics"]["watcher_events"]] == ["joined", "left"]


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_second_tab_neither_restarts_nor_ends_the_interview(room, engine):
    iid = await room.seed()
    mark = candidate_user_id(iid)
    rec = await room.run(engine, [
        lambda r: r.join(person("tab1", user_id=mark)),
        lambda r: r.join(person("tab2", user_id=mark)),
        lambda r: r.leave(person("tab1", user_id=mark)),
    ], interview_id=iid)
    assert rec.openings == 1
    assert rec.reconnects == (1 if engine == "realtime" else 0)
    assert rec.cancelled == 0, "one tab closing while another is connected is not a drop"
    assert rec.presence.disconnects == 0


async def test_the_last_candidate_connection_leaving_ends_as_dropped_on_the_pipeline(room):
    """The pipeline engine (the fallback) still ends at the drop: rejoin is realtime's."""
    iid = await room.seed()
    mark = candidate_user_id(iid)
    rec = await room.run("pipeline", [
        lambda r: r.join(person("tab1", user_id=mark)),
        lambda r: r.join(person("tab2", user_id=mark)),
        lambda r: r.leave(person("tab1", user_id=mark)),
        lambda r: r.leave(person("tab2", user_id=mark), "networkTimeout"),
    ], interview_id=iid)
    assert rec.cancelled == 1
    saved = await room.saved(iid)
    assert saved["metrics"]["ended_because"] == "candidate_dropped"
    assert saved["metrics"]["disconnects"] == 1
    # No new status (a shared database change); the reason says it was a drop.
    assert saved["status"] == "completed"


async def test_the_last_candidate_connection_leaving_waits_then_ends_as_did_not_return(room):
    """Realtime, phase 3 wave 1: the last connection leaving starts the rejoin wait
    (REJOIN_GRACE_SECONDS, 90 s; shortened here); nothing ends while it runs; nobody
    back by the end of it ends the interview as candidate_did_not_return."""
    import functools

    import bot.run_realtime
    from bot.rejoin import RejoinController

    room.mp.setattr(bot.run_realtime, "RejoinController", functools.partial(RejoinController, grace_seconds=0.6))
    # The capture is checked below, so it is switched on here rather than taken from
    # whatever the developer's .env says (it is on unless set off).
    room.mp.setenv("CANDIDATE_AUDIO_CAPTURE", "1")
    iid = await room.seed()
    mark = candidate_user_id(iid)
    seen = {}

    async def during_the_wait(r):
        await asyncio.sleep(0.3)
        seen["ended_during_wait"] = r.cancelled + r.queued.count("EndWorkerFrame")

    async def after_the_wait(r):
        for _ in range(60):
            if "EndWorkerFrame" in r.queued:
                break
            await asyncio.sleep(0.05)

    rec = await room.run("realtime", [
        lambda r: r.join(person("tab1", user_id=mark)),
        lambda r: r.join(person("tab2", user_id=mark)),
        lambda r: r.leave(person("tab1", user_id=mark)),
        lambda r: r.leave(person("tab2", user_id=mark), "networkTimeout"),
        during_the_wait,
        after_the_wait,
    ], interview_id=iid)
    assert seen["ended_during_wait"] == 0, "the interview ended at the drop instead of waiting"
    # Ended by the coordinator's hang-up (an EndWorkerFrame), as on the real cut.
    assert rec.queued.count("EndWorkerFrame") == 1 and rec.cancelled == 0
    saved = await room.saved(iid)
    assert saved["metrics"]["ended_because"] == "candidate_did_not_return"
    assert saved["metrics"]["disconnects"] == 1
    connection = saved["metrics"]["connection"]
    assert connection["grace_seconds"] == 0.6 and len(connection["drops"]) == 1
    assert connection["drops"][0]["ended_seconds"] is None and connection["rejoin_count"] == 0
    assert saved["status"] == "completed"
    # The capture is wired in: its manifest is where Session D's reader looks (no audio
    # frames reach this harness, so it records none), and its summary is in the metrics.
    from aftercall import store

    manifest = store.read_manifest(iid)
    assert manifest is not None and manifest.candidate_audio is None
    assert saved["metrics"]["capture"]["chunks_queued"] == 0


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_natural_end_is_not_a_drop(room, engine):
    iid = await room.seed()
    mark = candidate_user_id(iid)

    async def finish(r):
        _finish(r)

    rec = await room.run(engine, [
        lambda r: r.join(person("c1", user_id=mark)),
        finish,
        lambda r: r.leave(person("c1", user_id=mark)),
    ], interview_id=iid)
    assert rec.cancelled == 1
    saved = await room.saved(iid)
    assert saved["metrics"]["ended_because"] != "candidate_dropped"
    assert saved["metrics"]["disconnects"] == 0


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_token_without_the_mark_falls_back_to_the_first_non_owner(room, engine):
    """Compatibility with tokens minted before the mark existed."""
    iid = await room.seed()
    rec = await room.run(engine, [
        lambda r: r.join(person("owner-1", owner=True, name="Someone with owner rights")),
        lambda r: r.join(person("c1")),             # no user_id: the fallback candidate
        lambda r: r.join(person("w1", name="HR")),  # later, unmarked: a watcher
        lambda r: r.leave(person("w1", name="HR")),
    ], interview_id=iid)
    assert rec.openings == 1
    assert rec.cancelled == 0
    summary = rec.presence.summary()
    assert summary["candidate_identified_by"] == "fallback_first_non_owner"
    assert [e["participant_id"] for e in summary["watcher_events"]] == ["owner-1", "w1", "w1"]


def test_a_marked_candidate_outranks_a_fallback_guess():
    presence = RoomPresence(candidate_mark="candidate:int_x")
    assert presence.observe_join(person("early")).starts_interview is True  # guessed
    presence.observe_join(person("real", user_id="candidate:int_x"))
    # The guess is demoted: its leaving no longer counts as the candidate going.
    assert presence.observe_leave(person("early")).role == "watcher"
    assert presence.candidate_identified_by == "user_id"


def test_another_interviews_candidate_is_a_watcher():
    presence = RoomPresence(candidate_mark="candidate:int_a")
    assert presence.observe_join(person("x", user_id="candidate:int_b")).role == "watcher"


# -- stale note ids --------------------------------------------------------------


async def test_note_ids_are_forgotten_when_the_connection_is_remade():
    from bot.realtime_coordinator import RealtimeCoordinator
    from bot.blueprint_source import load_blueprint

    class FakeService:
        connection_generation = 1

        def __init__(self):
            self.created, self.deleted, self.n = [], [], 0

        async def insert_note(self, text):
            self.n += 1
            self.created.append(f"note-{self.n}")
            return f"note-{self.n}"

        async def delete_note(self, item_id):
            self.deleted.append(item_id)

    llm = FakeService()
    coord = RealtimeCoordinator(blueprint=load_blueprint(), llm=llm)
    # Round 14: notes go through tell(); the newest TIME note replaces the previous one.
    await coord.tell("one", kind="time", reply=False)
    await coord.tell("two", kind="time", reply=False)
    assert llm.deleted == ["note-1"], "same connection: the old note is replaced"
    llm.connection_generation = 2  # reconnect()
    await coord.tell("three", kind="time", reply=False)
    assert llm.deleted == ["note-1"], "note-2 belonged to the old connection; never deleted"
    await coord.tell("four", kind="time", reply=False)
    assert llm.deleted == ["note-1", "note-3"]


def _error_event(code):
    return json.dumps({"type": "error", "event_id": "e1", "error": {
        "type": "invalid_request_error", "code": code, "message": f"{code} happened",
        "param": None, "event_id": None}})


async def _receive(llm, messages):
    """Pipecat's REAL receive loop over a fake websocket; returns how many
    messages it consumed and the errors it pushed."""
    consumed, errors = [], []

    class FakeWS:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for m in messages:
                consumed.append(m)
                yield m

    async def push_error(error_msg=None, **kw):
        errors.append(error_msg)

    llm._websocket = FakeWS()
    llm.push_error = push_error
    await llm._receive_task_handler()
    return len(consumed), errors


async def test_a_stale_note_delete_error_is_not_fatal():
    from bot.services.realtime import NON_FATAL_ERROR_CODE, build_realtime_llm

    llm = build_realtime_llm(api_key="test", instructions="x", tools=None)
    deleted = json.dumps({"type": "conversation.item.deleted", "event_id": "e2", "item_id": "abc"})
    consumed, errors = await _receive(llm, [_error_event(NON_FATAL_ERROR_CODE), deleted, deleted])
    assert consumed == 3, "the loop must keep reading after the non-fatal error"
    assert errors == []


async def test_every_other_openai_error_is_still_fatal():
    from bot.services.realtime import build_realtime_llm

    llm = build_realtime_llm(api_key="test", instructions="x", tools=None)
    deleted = json.dumps({"type": "conversation.item.deleted", "event_id": "e2", "item_id": "abc"})
    consumed, errors = await _receive(llm, [_error_event("invalid_value"), deleted])
    assert consumed == 1, "Pipecat's handling is unchanged for other errors"
    assert len(errors) == 1
