"""Seed a ready-to-join technical interview for local testing.

    cd backend && uv run python scripts/seed_interview.py
    cd backend && uv run python scripts/seed_interview.py --fixture sre_staff --duration 60 \\
        --engine realtime --recording off

What it does, through the app's own code:
1. loads a fixture blueprint from bot/fixtures/ with `bot.blueprint_source.
   load_blueprint` (read only; validated against InterviewBlueprint);
2. stores a job and a candidate with that plan, the way the admin routes store
   them once a spec is confirmed and a plan is ready (the admin flow reaches
   that point through paid model calls, which a seed should not make);
3. books the interview through the real booking route (`POST /candidates/{id}/
   interviews`, in process): the duration rule (20-60, trimmed on realtime),
   TwoLinks, and a real Daily room (one API call, no participant minutes);
4. prints what a tester needs: the candidate join URL, the meeting ID and the
   meeting password. These are local test credentials it has just created, so
   printing them to the developer's own terminal is the point. No API key or
   secret is ever printed.

The engine and recording are not per-interview settings in this codebase: the
server that spawns the bot reads INTERVIEW_ENGINE and RECORDING_ENABLED from
its environment at join. The script uses the engine for the booking rule and
prints the environment the server must be started with.

**Refuses a database that is not local**, judged from the database URL itself
(SQLite, or Postgres on localhost), not from which machine it runs on: run
from a laptop with DATABASE_URL pointing at Railway, a machine check would pass
and write test credentials into production (C-06).
"""

import argparse
import asyncio
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


class NotLocal(RuntimeError):
    pass


def database_is_local(url: str) -> bool:
    """True for SQLite, or Postgres on this machine. Judged from the URL only."""
    if url.startswith("sqlite"):
        return True
    host = (urlsplit(url).hostname or "").lower()
    return url.startswith(("postgres", "postgresql")) and host in LOCAL_HOSTS


def check_local() -> str:
    from app import db

    if db.is_deployed():
        raise NotLocal("Refusing: this is a deployed instance.")
    url = db.database_url()
    if not database_is_local(url):
        host = urlsplit(url).hostname or "?"
        raise NotLocal(f"Refusing: the database is not local (host {host}). Seeds write test "
                       "credentials; point DATABASE_URL at SQLite or a local Postgres.")
    return url


async def seed(*, fixture: str, duration: int | None, engine: str, base_url: str) -> dict:
    """Create the job, candidate and booked interview. Returns what to print."""
    import httpx

    from app import db
    from bot.blueprint_source import load_blueprint

    check_local()
    os.environ["INTERVIEW_ENGINE"] = engine  # the booking rule reads it
    os.environ.setdefault("APP_BASE_URL", base_url)
    # The in-process admin sign-in needs a password; this process only, never printed.
    os.environ.setdefault("ADMIN_PASSWORD", secrets.token_urlsafe(24))

    blueprint = load_blueprint(blueprint_id=fixture)
    spec = blueprint.evaluation_spec
    job_id, candidate_id = f"job_{uuid.uuid4().hex[:12]}", f"cand_{uuid.uuid4().hex[:12]}"
    await db.create_all()
    async with db.get_sessionmaker()() as session:
        session.add(db.Job(id=job_id, title=spec.role_title, business_unit="Seeded",
                           jd_text=f"(seeded from bot/fixtures/{fixture}.json)",
                           evaluation_spec=spec.model_dump(mode="json"),
                           spec_status=db.SpecStatus.READY))
        await session.flush()
        session.add(db.Candidate(id=candidate_id, job_id=job_id, name=blueprint.candidate_name or "Seeded Candidate",
                                 cv_text=f"(seeded from bot/fixtures/{fixture}.json)",
                                 blueprint=blueprint.model_copy(update={"blueprint_id": candidate_id})
                                 .model_dump(mode="json"),
                                 blueprint_status=db.BlueprintStatus.READY,
                                 blueprint_generated_at=datetime.now(UTC)))
        await session.commit()

    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url) as client:
        r = await client.post("/admin/login", json={"password": os.environ["ADMIN_PASSWORD"]})
        r.raise_for_status()
        r = await client.post(f"/candidates/{candidate_id}/interviews",
                              json={"duration_minutes": duration} if duration else None)
        if r.status_code != 200:
            raise RuntimeError(f"Booking refused ({r.status_code}): {r.json().get('detail')}")
        booked = r.json()
    return {"fixture": fixture, "job_id": job_id, "candidate_id": candidate_id, "engine": engine, **booked}


def report(seeded: dict, *, recording: bool) -> str:
    links = seeded["links"]
    lines = [
        f"Seeded a technical interview from bot/fixtures/{seeded['fixture']}.json",
        "",
        f"  Candidate join URL : {links['candidate_join_url']}",
        f"  Meeting ID         : {seeded['meeting_id']}",
        f"  Meeting password   : {seeded['password']}",
        "",
        f"  Interview          : {seeded['interview_id']}  (candidate {seeded['candidate_id']})",
        f"  Live view          : {links['live_view_url']}",
        f"  Duration           : booked {seeded['booked_minutes']} min, runs {seeded['planned_minutes']} "
        f"+ {seeded['grace_minutes']} grace",
    ]
    if seeded.get("timing_note"):
        lines.append(f"                       {seeded['timing_note']}")
    lines += [
        f"  Room expires       : {seeded['expires_at']}",
        "",
        "Start the server with this engine and recording setting, then join:",
        f"  INTERVIEW_ENGINE={seeded['engine']} RECORDING_ENABLED={'1' if recording else ''} "
        "uv run uvicorn app.main:app --port 8000",
    ]
    return "\n".join(lines)


async def main() -> int:
    fixtures = sorted(p.stem for p in (Path(__file__).resolve().parents[1] / "bot" / "fixtures").glob("*.json"))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", default="pm_senior", choices=fixtures)
    parser.add_argument("--duration", type=int, default=None, help="20-60 minutes; default: the plan's")
    parser.add_argument("--engine", default="realtime", choices=["realtime", "pipeline"])
    parser.add_argument("--recording", default="off", choices=["on", "off"])
    parser.add_argument("--base-url", default=os.environ.get("APP_BASE_URL") or "http://localhost:8000")
    args = parser.parse_args()

    import bot.config  # noqa: F401  (loads .env the way the app does: DAILY_API_KEY; never printed)

    try:
        seeded = await seed(fixture=args.fixture, duration=args.duration, engine=args.engine,
                            base_url=args.base_url)
    except (NotLocal, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    finally:
        from app.db import reset_engine

        await reset_engine()
    print(report(seeded, recording=args.recording == "on"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
