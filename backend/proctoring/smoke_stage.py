"""Session D's smoke stage (C-110) for the Track H integration: their room posts the five event
types to the real intake route, the interview ends, the stored integrity report is written and
read back through the real route. CONSTRUCTED interview, in the run's own test database."""

from scripts.smoke_interview import stage

IID = "int_smoke_d_integrity"
FIVE = ["looking_away", "face_missing", "second_face", "share_stopped", "second_monitor"]


@stage("d_integrity", owner="D")
def integrity(ctx) -> None:
    from proctoring import intake, stored
    from shared.interview_contracts import StoredIntegrityReport

    ctx.call(_seed)
    token = intake.issue_token(IID)
    ctx.secrets["their room's intake token"] = token  # the leak scan checks it appears nowhere
    events = [{"event_id": f"smoke-{k}", "kind": k, "source": "browser", "start_seconds": 10.0 * (n + 1),
               "end_seconds": 10.0 * (n + 1) + 3, "reminder": "recorded_only", "confidence": 0.5}
              for n, k in enumerate(FIVE)] + [{"event_id": "smoke-unknown", "kind": "gaze_away", "source": "browser",
                                               "start_seconds": 1.0, "reminder": "recorded_only"}]
    r = ctx.http("POST", f"/api/proctoring/interviews/{IID}/events", json={"events": events},
                 headers={"Authorization": f"Bearer {token}"})
    ctx.expect(r.status_code == 200, f"their room's batch answered {r.status_code}")
    body = r.json()
    ctx.expect(body["stored"] == 5, f"the five types are stored (stored {body['stored']})")
    ctx.expect([x["code"] for x in body["refused"]] == ["unknown_type"], f"the unknown type is refused: {body['refused']}")
    ctx.expect("gaze_away" not in r.text, "the request's unknown type is not echoed")
    bad = ctx.http("POST", f"/api/proctoring/interviews/{IID}/events", json={"events": events[:1]},
                   headers={"Authorization": "Bearer not-a-token"})
    ctx.expect(bad.status_code == 401, f"a wrong token answered {bad.status_code}")
    ctx.call(_end)
    ctx.call(stored.write_pending)
    ctx.sign_in()
    g = ctx.http("GET", f"/api/proctoring/interviews/{IID}/integrity")
    ctx.expect(g.status_code == 200, f"the stored report answered {g.status_code}")
    rep = ctx.validate(StoredIntegrityReport, g.json(), "the stored integrity report")
    ctx.expect(sorted(t.kind.value for t in rep.by_type) == sorted(FIVE), f"one total per type: {[t.kind.value for t in rep.by_type]}")
    ctx.expect(all(t.count == 1 and t.total_seconds == 3.0 for t in rep.by_type), "counts and durations")
    ctx.expect(rep.scored is False and rep.purpose == "signals_for_review", "signals for review, never a score")
    ctx.expect(ctx.secrets["meeting password"] not in g.text, "no meeting password in the report")
    ctx.out["d_integrity"] = {"intake": body, "report": rep.model_dump(mode="json")}


async def _seed() -> None:
    from datetime import UTC, datetime, timedelta

    from app import db

    now = datetime.now(UTC)
    async with db.get_sessionmaker()() as s:
        if await s.get(db.Interview, IID) is not None:
            return
        s.add(db.Interview(id=IID, candidate_id="cand_smoke0001", meeting_id="d-int-1", password="d-int-pw",
                           daily_room_name="r", daily_room_url="https://example.daily.co/r",
                           room_expires_at=now + timedelta(hours=1), status="in_progress",
                           started_at=now - timedelta(minutes=5)))
        await s.commit()


async def _end() -> None:
    from datetime import UTC, datetime, timedelta

    from app import db
    from proctoring.intake import LATE_GRACE_SECONDS

    async with db.get_sessionmaker()() as s:
        row = await s.get(db.Interview, IID)
        row.status = db.InterviewStatus.COMPLETED
        row.ended_at = datetime.now(UTC) - timedelta(seconds=LATE_GRACE_SECONDS + 1)
        await s.commit()
