"""The integration smoke test: one replayed interview through every backend stage.

Run from backend/:   uv run python scripts/smoke_interview.py [--out DIR] [--verbose]

Free, deterministic, a few seconds. No paid call and no Daily room: the only
stand-ins are the two text-model calls (the auditor's labels and the cleaner's
reply), each a fixed function passed through the real code's own injection
point. Everything else is the REAL code: the event store and relay, the
cleaner's guard, the auditor's placement, the report builder and quote
matching, Check Intelligence, the interview, header and recording routes, the
proctoring intake and the integrity report, all against a throwaway SQLite
database, through the real FastAPI app (in-process, with the admin sign-in).

Input: scripts/smoke/interview_replay.json, Session A's round 10 replay with
its constructed parts marked in "_about"; scripts/smoke/tiny.mp4, a 2-second
synthetic video (no person). The meeting password and every secret are made
up per run and never printed.

Each stage prints one line, PASS or FAIL with the reason; the exit code is
non-zero if any stage failed. Stages are a registry: see `stage` below, and
track_i_notes/C_findings.md (C-110) for how another session adds one.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DATA = BACKEND / "scripts" / "smoke"
IID = "int_smoke0000001"
CANDIDATE = "cand_smoke0001"
CANDIDATE_NAME = "Smoke Test Candidate"


# -- the registry ----------------------------------------------------------------------------


class StageFailed(AssertionError):
    """Raised by `Smoke.expect`; the stage's line shows its message."""


class StageSkipped(Exception):
    """Raised by a stage that cannot run yet, with the reason. Shown as SKIP,
    counted apart: never a silent pass."""


@dataclass
class Stage:
    name: str
    run: Callable[["Smoke"], None]
    owner: str
    last: bool = False


STAGES: list[Stage] = []


def stage(name: str, *, owner: str = "C", last: bool = False):
    """Register a stage. It receives the shared `Smoke` context, raises
    StageFailed (via ctx.expect) to fail, and may put what it produced in
    ctx.out[name] for later stages, for --out, and for the leak scan."""

    def register(fn: Callable[["Smoke"], None]):
        if any(s.name == name for s in STAGES):
            raise ValueError(f"smoke stage {name!r} is registered twice")
        STAGES.append(Stage(name, fn, owner, last))
        return fn

    return register


@dataclass
class Smoke:
    """What every stage shares."""

    client: Any                      # fastapi TestClient on the real app
    replay: dict
    tmp: Path
    secrets: dict[str, str]          # values that must never appear in an output
    out: dict[str, Any] = field(default_factory=dict)
    #: (where, text) of every HTTP answer and output, for the leak scan.
    texts: list[tuple[str, str]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    iid: str = IID

    def expect(self, condition: Any, reason: str) -> None:
        if not condition:
            raise StageFailed(reason)

    def validate(self, model, data, what: str):
        """Validate against a contract model; a failure fails the stage with the error."""
        from pydantic import ValidationError

        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise StageFailed(f"{what} does not match {model.__name__}: {exc.errors()[:2]}") from exc

    def call(self, fn, *args, **kwargs):
        """Run an async function on the app's own event loop (the database engine lives there)."""
        return self.client.portal.call(lambda: fn(*args, **kwargs))

    def http(self, method: str, path: str, *, record: bool = True, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if record:
            self.texts.append((f"{method} {path}", response.text))
        return response

    def sign_in(self) -> None:
        r = self.client.post("/admin/login", json={"password": self.secrets["admin password"]})
        self.expect(r.status_code == 200, f"admin sign-in answered {r.status_code}")

    def record(self, where: str, value: Any) -> None:
        self.texts.append((where, value if isinstance(value, str) else json.dumps(value, default=str)))


# -- helpers on the replay ---------------------------------------------------------------------

AREA_NUMBERS = {"opening": 1, "product_strategy": 2, "prioritization": 3, "closing": 8}


def _area_at(replay: dict, seconds: float) -> str:
    current = "opening"
    for a in replay["areas"]:
        if a["at_seconds"] <= seconds:
            current = a["area_id"]
    return current


def _blueprint() -> dict:
    return json.loads((BACKEND / "bot" / "fixtures" / "pm_senior.json").read_text())


# -- the stages (Session C's) ------------------------------------------------------------------


@stage("seed")
def seed(ctx: Smoke) -> None:
    """The interview as the bot leaves it: transcript, ending, coordinator records."""
    from app import db

    replay = ctx.replay
    blueprint = _blueprint()
    now = datetime.now(UTC)

    async def write():
        async with db.get_sessionmaker()() as s:
            s.add(db.Job(id="job_smoke", jd_text="jd", spec_status=db.SpecStatus.READY))
            await s.flush()
            s.add(db.Candidate(id=CANDIDATE, job_id="job_smoke", name=CANDIDATE_NAME, cv_text="cv",
                               blueprint_status=db.BlueprintStatus.READY, blueprint=blueprint))
            s.add(db.Interview(
                id=IID, candidate_id=CANDIDATE, meeting_id="428-193-756",
                password=ctx.secrets["meeting password"], daily_room_name="room-smoke",
                daily_room_url="https://example.daily.co/room-smoke", room_expires_at=now + timedelta(hours=24),
                status="completed", started_at=now - timedelta(seconds=400), ended_at=now - timedelta(seconds=5),
                transcript=replay["transcript"],
                session_metrics={"engine": "realtime", **replay["ending"], "ending": replay["ending"],
                                 "coordinator_events": replay["session_metrics"]["coordinator_events"]},
                booked_duration_minutes=40))
            await s.commit()

    ctx.call(write)
    ctx.expect(len(replay["transcript"]["turns"]) == 20, "the replay has 20 turns")


@stage("auditor_sections")
def auditor_sections(ctx: Smoke) -> None:
    """The real auditor (bot/auditor.py), labels from the replay through its
    injection point, skips and declines from the coordinator's records: the
    section outcomes it files, by the question's own area."""
    from types import SimpleNamespace

    from bot.auditor import RealtimeAuditor
    from shared.contracts import InterviewBlueprint, SectionOutcome

    replay = ctx.replay
    labels = {lab["turn_index"]: lab for lab in replay["labels"]}
    clock = {"t": 0.0}

    async def model(**kwargs):  # the stand-in: the replay's reading of each turn
        spoken = kwargs["messages"][1]["content"].split("INTERVIEWER TURN\n", 1)[1].strip()
        lab = labels[by_text[spoken]]
        return SimpleNamespace(payload={"label": lab["label"], "question_id": lab["question_id"],
                                        "coverage": "partial", "new_claims": [], "contradictions": []},
                               record=SimpleNamespace(cost_usd=0.0))

    turns = replay["transcript"]["turns"]
    by_text = {t["text"]: t["index"] for t in turns if t["speaker"] == "interviewer"}
    bp = InterviewBlueprint.model_validate(_blueprint())
    auditor = RealtimeAuditor(blueprint=bp, area_now=lambda: AREA_NUMBERS[_area_at(replay, clock["t"])],
                              request=model)
    happenings = [(t["audio_start_seconds"], "turn", t) for t in turns] + \
                 [(c["at_seconds"], "record", c) for c in replay["session_metrics"]["coordinator_events"]]

    async def run():
        for at, kind, item in sorted(happenings, key=lambda h: h[0]):
            clock["t"] = at
            area = AREA_NUMBERS[_area_at(replay, at)]
            if kind == "record":
                (auditor.note_skip if item["kind"] == "question_skipped" else auditor.note_decline)(area)
            elif item["speaker"] == "candidate":
                auditor.candidate_turn(item["index"], item["text"])
            else:
                await auditor.label_now(item["index"], item["text"], at)
        return auditor.summary(), auditor.section_outcomes()

    summary, outcomes = ctx.call(run)
    by_section = {}
    for o in outcomes:
        model_o = ctx.validate(SectionOutcome, o, f"section outcome {o.get('section_id')}")
        by_section[model_o.section_id] = model_o
    ps, pr = by_section.get("product_strategy"), by_section.get("prioritization")
    ctx.expect(ps and pr, f"sections filed: {sorted(by_section)}")
    ctx.expect(ps.skipped_question_ids == ["Q2.2"], f"Q2.2's skip is under its own area, product strategy "
                                                    f"(got {ps.skipped_question_ids})")
    ctx.expect(ps.declined_question_ids == ["Q2.3"] and ps.easier_variants == 1,
               f"Q2.3's decline and its easier variant are under product strategy "
               f"(got {ps.declined_question_ids}, {ps.easier_variants})")
    ctx.expect(pr.skipped_question_ids == ["Q3.1"], f"the ordinary skip Q3.1 stays in prioritization "
                                                    f"(got {pr.skipped_question_ids})")
    ctx.out["auditor_sections"] = {"summary": summary, "section_outcomes": outcomes}
    from app import db

    async def store():
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, IID)
            row.session_metrics = {**row.session_metrics, "auditor": summary}
            row.section_outcomes = outcomes
            await s.commit()

    ctx.call(store)


@stage("events_and_relay")
def events_and_relay(ctx: Smoke) -> None:
    """The bot's live events through the real store and the relay route,
    including a resume with Last-Event-ID."""
    from live import relay
    from live.emit import make_event
    from live.store import insert_events
    from shared.interview_contracts import (
        AreaChanged, EventSource, InterviewEnded, InterviewEvent, InterviewStarted, QuestionLabelled, Skip,
        TranscriptTurnEvent)

    replay = ctx.replay
    events = [make_event(IID, InterviewStarted(engine="realtime", booked_minutes=40), source=EventSource.BOT,
                         at_seconds=0.0)]
    events += [make_event(IID, AreaChanged(area_id=a["area_id"], area_name=a["area_name"]),
                          source=EventSource.COORDINATOR, at_seconds=a["at_seconds"]) for a in replay["areas"]]
    for t in replay["transcript"]["turns"]:
        events.append(make_event(IID, TranscriptTurnEvent(
            turn_index=t["index"], speaker=t["speaker"], text=t["text"], audio_start_seconds=t["audio_start_seconds"],
            area_id=_area_at(replay, t["audio_start_seconds"])), source=EventSource.BOT, at_seconds=t["at_seconds"]))
    for lab in replay["labels"]:
        fields = {k: lab[k] for k in ("turn_index", "label", "question_id", "follow_up_number", "area_id")}
        events.append(make_event(IID, QuestionLabelled(**fields), source=EventSource.AUDITOR,
                                 at_seconds=lab["at_seconds"]))
    events += [make_event(IID, Skip(area_id=s["area_id"], question_id=s["question_id"]),
                          source=EventSource.COORDINATOR, at_seconds=s["at_seconds"]) for s in replay["live_skips"]]
    end = replay["ending"]
    events.append(make_event(IID, InterviewEnded(ended_because=end["ended_because"], duration_seconds=200.0,
                                                 ending=end["ending"], hang_up_by=end["hang_up_by"]),
                             source=EventSource.BOT, at_seconds=200.0))
    events.sort(key=lambda e: e.at_seconds)
    stored, refused = ctx.call(insert_events, events)
    ctx.expect(len(stored) == len(events) and not refused, f"stored {len(stored)} of {len(events)}, refused {refused}")

    relay.END_GRACE_SECONDS, relay.POLL_SECONDS = 0.05, 0.01  # the stream ends promptly after interview_ended

    def stream(**kw) -> list[tuple[int, dict]]:
        with ctx.client.stream("GET", f"/api/live/interviews/{IID}/events", **kw) as r:
            ctx.expect(r.status_code == 200, f"the relay answered {r.status_code}")
            body = "".join(r.iter_text())
        ctx.record("relay stream", body)
        out = []
        for chunk in body.split("\n\n"):
            lines = dict(line.split(": ", 1) for line in chunk.splitlines() if ": " in line)
            if "id" in lines and "data" in lines:
                out.append((int(lines["id"]), json.loads(lines["data"])))
        return out

    ctx.expect(ctx.client.get(f"/api/live/interviews/{IID}/events").status_code == 401, "the relay needs sign-in")
    ctx.sign_in()
    everything = stream()
    ctx.expect(len(everything) == len(events), f"relayed {len(everything)} of {len(events)} events")
    middle = everything[len(everything) // 2][0]
    resumed = stream(headers={"Last-Event-ID": str(middle)})
    ctx.expect(resumed == [e for e in everything if e[0] > middle], "a resume returns exactly the rest, in order")
    parsed = [ctx.validate(InterviewEvent, data, f"event {seq}") for seq, data in everything]
    turns = [e.payload for e in parsed if e.kind.value == "transcript_turn"]
    missing = [t.turn_index for t in turns if not t.area_id]
    ctx.expect(not missing, f"every turn carries its area (missing on {missing})")
    skips = {e.payload.question_id: e.payload.area_id for e in parsed if e.kind.value == "skip"}
    ctx.expect(skips == {"Q2.2": "product_strategy", "Q3.1": "prioritization"},
               f"each live skip carries its question's own area (got {skips})")
    [ended] = [e.payload for e in parsed if e.kind.value == "interview_ended"]
    ctx.expect((ended.ended_because.value, ended.ending, ended.hang_up_by) ==
               ("interview_complete", "model_spoke", "bot_stopped_speaking"),
               f"the ending is right (got {ended.ended_because}, {ended.ending}, {ended.hang_up_by})")
    ctx.out["events_and_relay"] = [data for _, data in everything]


def _cleaning_model(turns: list[dict]):
    """The cleaner's stand-in: removes a leading filler, declares one name fix,
    and on one turn adds a number the raw text does not have (the guard must
    refuse that and keep the raw text)."""
    from types import SimpleNamespace

    async def model(**kwargs):
        wanted = [int(m) for m in re.findall(r"^(\d+): ", kwargs["messages"][1]["content"], re.M)]
        raw = {t["index"]: t["text"] for t in turns}
        reply = []
        for i in wanted:
            text, corrections = re.sub(r"^(Um|Uh),\s*", "", raw[i]), []
            if i == 3:
                text, corrections = text.replace("Razapay", "Razorpay"), [{"from_text": "Razapay",
                                                                           "to_text": "Razorpay"}]
            if i == 13:
                text = text.rstrip(".") + " in 2021."
            reply.append({"index": i, "text": text[0].upper() + text[1:], "corrections": corrections,
                          "uncertain": []})
        return SimpleNamespace(payload={"turns": reply}, record=SimpleNamespace(cost_usd=0.0))

    return model


@stage("cleaned_transcript")
def cleaned_transcript(ctx: Smoke) -> None:
    """The real cleaner (feedback/clean.py) and its guard; stored in the column."""
    from app import db
    from feedback.clean import clean_transcript, declared_name_fixes
    from shared.interview_contracts import CleanedTranscript

    raw = ctx.replay["transcript"]
    result = ctx.call(clean_transcript, raw, request=_cleaning_model(raw["turns"]))
    cleaned = ctx.validate(CleanedTranscript, result, "the cleaned transcript")
    ctx.expect(cleaned.status == "ok", f"cleaning status {cleaned.status} ({cleaned.reason})")
    turns = {t.index: t for t in cleaned.turns}
    ctx.expect(len(turns) == len(raw["turns"]), "one cleaned turn per raw turn")
    ctx.expect(turns[3].guard == "ok" and "Razorpay" in turns[3].text and not turns[3].text.startswith("Um"),
               f"turn 3 cleaned with its declared fix (guard {turns[3].guard})")
    ctx.expect(isinstance(turns[13].guard, list) and turns[13].text == raw["turns"][13]["text"],
               f"an added number is refused and the raw text kept (guard {turns[13].guard})")
    raw_texts = {t["index"]: t["text"] for t in raw["turns"]}
    ctx.expect(all(t.text == raw_texts[t.index] for t in cleaned.turns if t.speaker.value == "interviewer"),
               "Mitra's turns are copied unchanged")
    fixes = declared_name_fixes(cleaned.model_dump(mode="json"))
    ctx.expect(fixes.get(3), f"the declared fix for turn 3 is usable by scoring (got {fixes})")

    async def store():
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, IID)
            row.cleaned_transcript = cleaned.model_dump(mode="json")
            await s.commit()

    ctx.call(store)
    ctx.out["cleaned_transcript"] = cleaned.model_dump(mode="json")


@stage("scoring")
def scoring(ctx: Smoke) -> None:
    """The deterministic report builder (feedback/score.py build_report) and
    quote matching, with a fixed scorer payload: three real quotes (exact,
    number value, declared fix) and one invented quote that must be dropped."""
    from app import db
    from feedback.clean import declared_name_fixes
    from feedback.score import build_report, verify_quote
    from shared.contracts import FeedbackReport, InterviewBlueprint, Transcript

    bp = InterviewBlueprint.model_validate(_blueprint())
    transcript = Transcript.model_validate(ctx.replay["transcript"])
    fixes = declared_name_fixes(ctx.out["cleaned_transcript"])
    quotes = {"I built the case with finance and got sign-off.": ("exact", 7),
              "it grew revenue by 38%": ("number_value", 5),
              "I set the strategy for our payments product at Razorpay.": ("declared_fix", 3),
              "I tripled revenue in one quarter.": (None, 5)}
    payload = {"competency_scores": [{
        "competency_id": "product_strategy", "score": 3, "coverage": "partial", "rationale": "r",
        "evidence": [{"text": q, "turn_index": turn} for q, (_, turn) in quotes.items()]}],
        "summary": "Constructed summary for the smoke test.", "recommendation": "limited_evidence"}
    report = build_report(interview_id=IID, blueprint_id=bp.blueprint_id, spec=bp.evaluation_spec,
                          transcript=transcript, payload=payload,
                          section_outcomes=ctx.out["auditor_sections"]["section_outcomes"], declared_fixes=fixes)
    stored = report.model_dump(mode="json")
    ctx.validate(FeedbackReport, stored, "the report")
    kept = {q["text"]: q for s in stored["competency_scores"] for q in s["evidence"]}
    expected = {q: rule for q, (rule, _) in quotes.items() if rule}
    ctx.expect({q: kept[q]["match"] for q in kept} == expected,
               f"quotes kept with their rule {({q: kept[q]['match'] for q in kept})}, expected {expected}")
    ctx.expect("I tripled revenue in one quarter." not in kept, "an invented quote is dropped")
    for text, q in kept.items():  # every kept quote verifies again, on its own turn
        again = verify_quote(text, q["turn_index"], transcript, declared_fixes=fixes)
        ctx.expect(again is not None and again.turn_index == q["turn_index"], f"{text!r} verifies again")

    async def store_report():
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, IID)
            row.feedback_report, row.feedback_status = stored, "ready"
            await s.commit()

    ctx.call(store_report)
    ctx.out["scoring"] = stored


@stage("check_intel")
def check_intel(ctx: Smoke) -> None:
    from shared.interview_contracts import CheckIntelReport

    r = ctx.http("GET", f"/api/live/interviews/{IID}/check-intel")
    ctx.expect(r.status_code == 200, f"check-intel answered {r.status_code}")
    data = r.json()
    ctx.validate(CheckIntelReport, data, "Check Intelligence")
    rows = {row["turn_index"]: row for row in data["rows"]}
    ctx.expect(all(row["area_id"] for row in data["rows"]), "every row has an area")
    placed = {(n["type"], n["question_id"]): t for t, row in rows.items() for n in row["notes"]
              if n["type"] in ("skip", "decline")}
    ctx.expect(placed == {("skip", "Q2.2"): 8, ("skip", "Q3.1"): 10, ("decline", "Q2.3"): 14},
               f"skips and declines on their own question's row (got {placed})")
    ctx.expect(data["tiles"]["skips_and_declines"] == {"skips": 2, "declines": 1, "easier_variants": 1},
               f"counts {data['tiles']['skips_and_declines']}")
    ctx.expect(rows[18]["area_id"] == "closing", "the closing turn is in the closing area")
    ctx.expect(data["interview"]["ended_because"] == "interview_complete",
               f"ended_because {data['interview']['ended_because']}")
    ctx.out["check_intel"] = data


@stage("interview_routes")
def interview_routes(ctx: Smoke) -> None:
    """GET /interviews/{id} (the report data) and the console's header route."""
    from shared.contracts import FeedbackReport, Transcript
    from shared.interview_contracts import CleanedTranscript, InterviewHeader

    anon = ctx.client.__class__(ctx.client.app)
    ctx.expect(anon.get(f"/interviews/{IID}").status_code == 401, "the interview route needs sign-in")
    ctx.expect(anon.get(f"/api/live/interviews/{IID}/header").status_code == 401, "the header route needs sign-in")
    r = ctx.http("GET", f"/interviews/{IID}", record=False)
    ctx.expect(r.status_code == 200, f"the interview route answered {r.status_code}")
    body = r.json()
    # F-059 (on hold, C-101): this route still returns the meeting password in
    # its `password` field. Checked to be exactly that field and nothing else;
    # the rest goes to the leak scan.
    ctx.expect(body.pop("password") == ctx.secrets["meeting password"], "the known F-059 field is as expected")
    ctx.record(f"GET /interviews/{IID} (without its password field)", body)
    ctx.validate(Transcript, body["transcript"], "the transcript")
    cleaned = ctx.validate(CleanedTranscript, body["cleaned_transcript"], "the cleaned transcript")
    ctx.expect(cleaned.status == "ok", f"the cleaned transcript is served ({cleaned.reason})")
    report = ctx.validate(FeedbackReport, body["feedback_report"], "the stored report")
    ctx.expect(len(report.competency_scores[0].evidence) == 3, "the served report has the three verified quotes")
    h = ctx.http("GET", f"/api/live/interviews/{IID}/header")
    header = ctx.validate(InterviewHeader, h.json(), "the header")
    ctx.expect(header.candidate_name == CANDIDATE_NAME and header.round == "technical",
               f"the header names the candidate ({header.candidate_name}, {header.round})")
    ctx.out["interview_routes"] = {"interview": body, "header": h.json()}


@stage("recording")
def recording(ctx: Smoke) -> None:
    """The signed-in recording routes with a real (tiny) MP4: offset, ranges, views."""
    from app import db, recordings
    from shared.interview_contracts import RecordingInfo

    source = DATA / "tiny.mp4"
    ctx.expect(recordings.verify_recording_file(source, source.stat().st_size) is None, "the fixture verifies")
    target = recordings.recording_file(IID)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    whole = target.read_bytes()

    async def store():
        async with db.get_sessionmaker()() as s:
            row = await s.get(db.Interview, IID)
            row.recording_status, row.recording_path = "stored", target.name
            row.recording_bytes, row.recording_offset_seconds = len(whole), 11.68
            row.recording_expires_at = datetime.now(UTC) + timedelta(days=10)
            await s.commit()

    ctx.call(store)
    media = f"/api/live/interviews/{IID}/recording/media"
    anon = ctx.client.__class__(ctx.client.app)
    ctx.expect(anon.get(media).status_code == 401, "the recording needs sign-in")
    info = ctx.validate(RecordingInfo, ctx.http("GET", f"/api/live/interviews/{IID}/recording").json(), "RecordingInfo")
    ctx.expect(info.offset_seconds == 11.68 and info.media_url == media and info.status == "stored",
               f"info: {info.status}, offset {info.offset_seconds}, {info.media_url}")
    full = ctx.client.get(media)
    ctx.expect(full.status_code == 200 and full.content == whole, "the whole file, byte for byte")
    ctx.expect(full.headers.get("cache-control") == "private, no-store", "never cached by a shared proxy")
    ctx.expect(full.headers.get("x-recording-offset-seconds") == "11.68", "the offset header")
    mid = len(whole) // 2
    part = ctx.client.get(media, headers={"Range": f"bytes={mid}-{mid + 99}"})
    ctx.expect(part.status_code == 206 and part.content == whole[mid:mid + 100], "a range request seeks exactly")
    views = ctx.call(_views)
    ctx.expect(views == 2, f"every view recorded (got {views})")
    ctx.out["recording"] = info.model_dump(mode="json")


async def _views() -> int:
    from sqlalchemy import func, select

    from app import db
    from live.models import RecordingViewRecord

    async with db.get_sessionmaker()() as s:
        return await s.scalar(select(func.count()).select_from(RecordingViewRecord)
                              .where(RecordingViewRecord.interview_id == IID))


@stage("notes")
def notes_stage(ctx: Smoke) -> None:
    """A watcher's note: stamped by the server with time and area, on the live
    stream, listed apart from evidence, refused without sign-in."""
    from live.store import all_events
    from shared.interview_contracts import WatcherNote, WatcherNotesSection

    path = f"/api/live/interviews/{IID}/notes"
    anon = ctx.client.__class__(ctx.client.app)
    ctx.expect(anon.post(path, json={"text": "x"}).status_code == 401, "notes need sign-in")
    r = ctx.http("POST", path, json={"text": "Clear trade-off reasoning on the payments rollout."})
    ctx.expect(r.status_code == 200, f"adding a note answered {r.status_code}: {r.text[:200]}")
    note = ctx.validate(WatcherNote, r.json(), "the note")
    ctx.expect(note.at_seconds > 200 and note.area_id == "closing",
               f"stamped with the interview time and the area then (got {note.at_seconds}, {note.area_id})")
    events = ctx.call(all_events, IID)
    added = [e for e in events if e.kind.value == "note_added"]
    ctx.expect(len(added) == 1 and added[0].payload.note_id == note.note_id, "a note_added event on the stream")
    section = ctx.validate(WatcherNotesSection, ctx.http("GET", path).json(), "the notes section")
    ctx.expect([n.note_id for n in section.notes] == [note.note_id] and section.scored is False,
               "listed, and never scored")
    ctx.out["notes"] = section.model_dump(mode="json")


@stage("hr_findings_hook")
def hr_findings_hook(ctx: Smoke) -> None:
    """Booking prepares the plan through Session A's REAL refine_for_booking (C-A23):
    HR findings, sized to the booking, goals written. Only its model call is stood in
    (its own request= parameter; no paid call), and the Daily room. Checks the booking
    answer, the refined plan, its HR record, the goals, the stored costs, and that both
    model calls really happened (a hook that skipped them fails here)."""
    import functools
    from types import SimpleNamespace

    from app import db, interviews
    from blueprint.refine import refine_for_booking
    from live import hr_hook
    from shared.contracts import InterviewBlueprint

    texts = ["Unclear on how to size an A/B test or decide when it had run long enough.",
             "Described prioritisation only as stakeholder pressure, with no trade-off reasoning.",
             "Notice period is 90 days and expects a 30 percent raise.",
             "Seemed a bit off in places.",
             "Spoke about roadmaps only in terms of features shipped, not outcomes."]  # 5: no cap (C-A13)
    probes = {0: ("metrics", "How do you decide how many users an experiment needs, and when to stop it?"),
              1: ("prioritization", "Can you walk me through a trade-off where you said no to something valuable?")}
    calls = []

    async def request(**kw):  # the model's reply, in the shape A's function asks for
        name = kw["response_model"].__name__
        calls.append(name)
        if name == "_GoalsResponse":
            ids = re.findall(r"^(Q\d+\.\d+): ", kw["messages"][1]["content"], re.M)
            payload = {"goals": [{"id": q, "goal": f"Their own part and the result in case {q}"} for q in ids]}
        else:
            payload = {"findings": [{"index": i, "outcome": "probed", "area_id": probes[i][0], "probe": probes[i][1]}
                                    if i in probes else {"index": i, "outcome": "not_technical"}
                                    for i in range(len(texts))]}
        record = {"job": kw["job"], "model": "smoke-stand-in", "input_tokens": 900, "output_tokens": 120,
                  "cost_usd": 0.0, "outcome": "ok"}
        return SimpleNamespace(payload=payload, record=SimpleNamespace(to_dict=lambda: dict(record)))

    fixture = ctx.tmp / "hr_findings.json"
    fixture.write_text(json.dumps({CANDIDATE: texts}))

    async def stand_in_room(**kw):
        return SimpleNamespace(name="room-smoke-hr", url="https://example.daily.co/room-smoke-hr",
                               expires_at=int(datetime.now(UTC).timestamp()) + 3600)

    real_function, real_room = hr_hook.refinement_function, interviews.create_room
    os.environ[hr_hook.FIXTURE_ENV] = str(fixture)
    hr_hook.refinement_function = lambda: functools.partial(refine_for_booking, request=request)
    interviews.create_room = stand_in_room
    try:
        r = ctx.client.post(f"/candidates/{CANDIDATE}/interviews")  # its answer holds a password: not recorded
    finally:
        hr_hook.refinement_function, interviews.create_room = real_function, real_room
        os.environ.pop(hr_hook.FIXTURE_ENV, None)
    ctx.expect(r.status_code == 200, f"the booking answered {r.status_code}: {r.text[:200]}")
    ctx.secrets["booked meeting password"] = r.json()["password"]
    ctx.expect(calls == ["_FindingsResponse", "_GoalsResponse"],
               f"the HR and goals model calls happened once each (calls: {calls})")
    ctx.expect(r.json()["hr_findings_note"] == "Plan refined with HR findings: 2 of 5 probed.",
               f"the booking says what happened: {r.json()['hr_findings_note']!r}")

    async def stored():
        async with db.get_sessionmaker()() as s:
            c = await s.get(db.Candidate, CANDIDATE)
            return c.blueprint, c.text_model_calls

    plan_json, costs = ctx.call(stored)
    plan = ctx.validate(InterviewBlueprint, plan_json, "the refined plan")
    ctx.expect(plan.hr_findings is not None and [u.outcome for u in plan.hr_findings.findings] ==
               ["probed", "probed", "not_technical", "not_technical", "not_technical"],
               f"the plan's HR record ({plan.hr_findings})")
    for use in plan.hr_findings.findings:
        if use.outcome == "probed":
            area = next(p for p in plan.competency_plans if p.competency_id == use.area_id)
            asked = dict(zip(area.question_ids, area.seed_questions))
            ctx.expect(asked.get(use.question_id) == probes[texts.index(use.finding)][1],
                       f"{use.question_id} in {use.area_id} is the probe")
    ctx.expect(all(g for p in plan.competency_plans for g in p.question_goals), "every question has a goal")
    ctx.expect([c["job"] for c in costs[-2:]] == ["blueprint_refine", "blueprint_goals"],
               f"the refinement's and the goals' costs stored (C-A14, C-176): {costs}")
    ctx.expect(bool(r.json().get("plan_note")), f"the booking says what was done to the plan: {r.json().get('plan_note')!r}")
    ctx.out["hr_findings_hook"] = {"note": r.json()["hr_findings_note"],
                                   "record": plan.hr_findings.model_dump(mode="json"), "stored_costs": costs}


@stage("rejoin")
def rejoin_stage(ctx: Smoke) -> None:
    """The rejoin pass and its exchange. The Daily token call is the one
    stand-in (no Daily room in the smoke test); the bot is a registered
    stand-in process, so 'interviewer alive' is the registry's real answer."""
    from types import SimpleNamespace

    from app import capacity, db
    from app.interviews import RejoinExchangeResponse
    from bot.services import daily
    from live import rejoin

    running = "int_smoke0000002"
    now = datetime.now(UTC)

    async def seed_running():
        async with db.get_sessionmaker()() as s:
            s.add(db.Interview(id=running, candidate_id=CANDIDATE, meeting_id="428-193-757",
                               password=ctx.secrets["meeting password"], daily_room_name="room-smoke-2",
                               daily_room_url="https://example.daily.co/room-smoke-2",
                               room_expires_at=now + timedelta(hours=24), status="in_progress",
                               started_at=now - timedelta(seconds=90), planned_duration_minutes=40,
                               planned_grace_minutes=5))
            await s.commit()
            ended = await rejoin.issue_pass(await s.get(db.Interview, IID))
            live = await rejoin.issue_pass(await s.get(db.Interview, running))
        return ended, live

    minted = []

    async def stand_in_token(**kw):
        minted.append(kw)
        return "smoke-fresh-candidate-token"

    real_token, real_bots = daily.create_meeting_token, dict(capacity.registry._bots)
    daily.create_meeting_token = stand_in_token
    capacity.registry._bots[running] = SimpleNamespace(returncode=None)
    try:
        ended_pass, live_pass = ctx.call(seed_running)
        ctx.secrets["rejoin pass (ended)"], ctx.secrets["rejoin pass"] = ended_pass.value, live_pass.value
        r = ctx.http("POST", "/api/live/rejoin", json={"interview_id": IID, "rejoin_pass": ended_pass.value})
        ctx.expect(r.status_code == 410 and r.json()["reason"] == "interview_ended",
                   f"a pass for an ended interview is refused (got {r.status_code} {r.text[:120]})")
        r = ctx.client.post("/api/live/rejoin", json={"interview_id": running, "rejoin_pass": live_pass.value})
        ctx.expect(r.status_code == 200, f"the exchange answered {r.status_code}: {r.text[:200]}")
        body = ctx.validate(RejoinExchangeResponse, r.json(), "the exchange answer")
        ctx.secrets["next rejoin pass"] = body.rejoin_pass
        ctx.record("POST /api/live/rejoin (without its token and next pass)",
                   {k: v for k, v in r.json().items() if k not in ("token", "rejoin_pass")})
        ctx.expect(minted and minted[0]["room_name"] == "room-smoke-2"
                   and minted[0]["user_id"] == f"candidate:{running}", "a fresh candidate token for the SAME room")
        again = ctx.http("POST", "/api/live/rejoin", json={"interview_id": running, "rejoin_pass": live_pass.value})
        ctx.expect(again.status_code == 401 and again.json()["reason"] == "invalid_pass", "a pass works once")
        ctx.out["rejoin"] = {"room_url": body.room_url, "rejoins_counted": body.rejoins_counted,
                             "rejoins_left": body.rejoins_left}
    finally:
        daily.create_meeting_token = real_token
        capacity.registry._bots.clear()
        capacity.registry._bots.update(real_bots)


@stage("proctoring")
def proctoring(ctx: Smoke) -> None:
    """The proctoring intake (with the join's intake token) and the integrity report."""
    from proctoring.intake import issue_token
    from shared.interview_contracts import IntegrityReport

    token = issue_token(IID)
    ctx.secrets["intake token"] = token
    events = [  # constructed browser measurements inside the replay's 200 s
        {"event_id": "ie_smoke_1", "kind": "tab_hidden", "source": "browser", "start_seconds": 30.0,
         "end_seconds": 36.0, "reminder": "nudged", "reminder_index": 1, "detail": "tab hidden for 6 s"},
        {"event_id": "ie_smoke_2", "kind": "looking_away", "source": "browser", "start_seconds": 100.0,
         "end_seconds": 104.0, "repeats": 2, "reminder": "recorded_only", "detail": "about 4 s in total"}]
    path = f"/api/proctoring/interviews/{IID}/events"
    ctx.expect(ctx.client.post(path, json={"events": events}).status_code == 401, "the intake needs its token")
    r = ctx.http("POST", path, json={"events": events}, headers={"Authorization": f"Bearer {token}"})
    ctx.expect(r.status_code == 200, f"the intake answered {r.status_code}: {r.text[:200]}")
    report = ctx.validate(IntegrityReport, ctx.http("GET", f"/api/proctoring/interviews/{IID}/report").json(),
                          "the integrity report")
    ctx.expect({e.event_id for e in report.events} == {"ie_smoke_1", "ie_smoke_2"},
               f"both events in the report (got {[e.event_id for e in report.events]})")
    ctx.out["proctoring"] = report.model_dump(mode="json")


#: Things that look like a signed link or a token in a URL.
SIGNED_LINK = re.compile(r"(X-Amz-Signature|X-Amz-Credential|Signature=|[?&](token|access_token|sig)=)", re.I)


def leaks(texts: list[tuple[str, str]], secrets_: dict[str, str]) -> list[str]:
    found = []
    for where, text in texts:
        for name, value in secrets_.items():
            if value and value in text:
                found.append(f"the {name} in {where}")
        if SIGNED_LINK.search(text):
            found.append(f"a signed link or token in {where}")
    return found


@stage("leak_scan", last=True)
def leak_scan(ctx: Smoke) -> None:
    """No meeting password, intake token, admin password, session secret or
    signed link in any answer, output or log line of the run."""
    control = leaks([("planted", f"x {ctx.secrets['meeting password']} https://s.example/f.mp4?X-Amz-Signature=a")],
                    ctx.secrets)
    ctx.expect(len(control) == 2, f"the scan finds a planted password and link (found {control})")
    for name, value in ctx.out.items():
        ctx.record(f"output of {name}", value)
    ctx.texts.append(("the run's log", "\n".join(ctx.logs)))
    found = leaks(ctx.texts, ctx.secrets)
    ctx.expect(not found, "; ".join(found))
    ctx.out["leak_scan"] = {"texts_scanned": len(ctx.texts), "log_lines": len(ctx.logs)}


# -- other sessions' stages ----------------------------------------------------------------------


def discover_plugins(roots: tuple[Path, ...] | None = None) -> list[str]:
    """Import every `smoke_stage.py` under backend/ and frontend/ (not tests,
    virtual environments or node_modules). Each registers its stages with
    @stage; they run after Session C's and before the leak scan."""
    # A plugin does `from scripts.smoke_interview import stage`: make that the
    # registry this run is using, whether it was started as a script or imported.
    sys.modules.setdefault("scripts.smoke_interview", sys.modules[__name__])
    found = []
    skip = {".venv", "node_modules", "tests", "__pycache__", "dist"}
    for root in roots or (BACKEND, REPO / "frontend"):
        for path in sorted(root.rglob("smoke_stage.py")):
            if skip & set(path.relative_to(root).parts):
                continue
            spec = importlib.util.spec_from_file_location(f"smoke_plugin_{len(found)}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            found.append(str(path))
    return found


# -- running -------------------------------------------------------------------------------------


def _environment(tmp: Path, secrets_: dict[str, str]) -> None:
    os.environ.update({
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp / 'smoke.db'}",
        "ADMIN_PASSWORD": secrets_["admin password"],
        "SESSION_SECRET": secrets_["session secret"],
        "RECORDINGS_DIR": str(tmp / "recordings"),
        "RECORDING_SWEEP_DISABLED": "1",
        # Never used: no stage calls a provider. Set so nothing can reach a real key.
        "OPENAI_API_KEY": "smoke-unused", "ELEVENLABS_API_KEY": "smoke-unused",
        "ELEVENLABS_VOICE_ID": "smoke-unused", "DAILY_API_KEY": "smoke-unused",
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="write each stage's output as <stage>.json here")
    parser.add_argument("--verbose", action="store_true", help="print the traceback of a failed stage")
    args = parser.parse_args(argv)

    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="mitra_smoke_"))
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    secrets_ = {"meeting password": "".join(secrets.choice(alphabet) for _ in range(8)),
                "admin password": secrets.token_urlsafe(24), "session secret": secrets.token_urlsafe(48)}
    _environment(tmp, secrets_)
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)

    from fastapi.testclient import TestClient
    from loguru import logger

    from app import db, main as app_main

    plugins = discover_plugins()
    ordered = [s for s in STAGES if not s.last] + [s for s in STAGES if s.last]
    replay = json.loads((DATA / "interview_replay.json").read_text())
    failures = skipped = 0
    try:
        asyncio.run(db.reset_engine())
        with TestClient(app_main.app) as client:
            ctx = Smoke(client=client, replay=replay, tmp=tmp, secrets=secrets_)
            sink = logger.add(lambda m: ctx.logs.append(str(m)), level="DEBUG")
            try:
                for s in ordered:
                    t0 = time.monotonic()
                    try:
                        s.run(ctx)
                        print(f"PASS  {s.name:<20} {time.monotonic() - t0:5.2f}s  ({s.owner})")
                    except StageSkipped as exc:
                        skipped += 1
                        print(f"SKIP  {s.name:<20} {time.monotonic() - t0:5.2f}s  ({s.owner})  {exc}")
                    except Exception as exc:  # noqa: BLE001  (one line per stage, then carry on)
                        failures += 1
                        reason = str(exc) if isinstance(exc, StageFailed) else f"{type(exc).__name__}: {exc}"
                        print(f"FAIL  {s.name:<20} {time.monotonic() - t0:5.2f}s  ({s.owner})  {reason[:400]}")
                        if args.verbose:
                            traceback.print_exc()
            finally:
                logger.remove(sink)
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                for name, value in ctx.out.items():
                    (args.out / f"{name}.json").write_text(json.dumps(value, indent=1, default=str))
        asyncio.run(db.reset_engine())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the database held the made-up password
    plural = "" if len(plugins) == 1 else "s"
    print(f"{'FAILED' if failures else 'OK'}: {len(ordered) - failures - skipped} of {len(ordered)} stages passed, "
          f"{skipped} skipped, {failures} failed, in {time.monotonic() - started:.1f}s "
          f"({len(plugins)} plugin file{plural})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
