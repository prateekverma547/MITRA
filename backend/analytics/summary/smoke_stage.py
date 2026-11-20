"""Session D's smoke stage (C-110) for the phase 4 summary: the real routes on the
seeded smoke interview. The router is mounted on the run's own app here only
(mounting it in app/main.py is Session C's: a request)."""

from scripts.smoke_interview import stage


@stage("d_summary", owner="D")
def summary(ctx) -> None:
    from analytics.summary.routes import router
    from analytics.summary.shapes import InterviewSummary, JobList, JobSummary

    app = ctx.client.app
    if not any(getattr(r, "path", "").startswith("/api/analytics/summary") for r in app.routes):
        app.include_router(router)
    ctx.sign_in()
    r = ctx.http("GET", "/api/analytics/summary/jobs/job_smoke")
    ctx.expect(r.status_code == 200, f"the job summary answered {r.status_code}")
    job = ctx.validate(JobSummary, r.json(), "the job summary")
    ctx.expect(job.interviews >= 1 and sum(job.by_category.values()) == job.interviews,
               f"the ending counts add up ({job.by_category} vs {job.interviews})")
    per = round(sum(x.cost.recorded_usd for x in job.interview_rows), 6)
    ctx.expect(job.total_cost_usd.value is None or abs(job.total_cost_usd.value - per) < 1e-6,
               f"the job total equals the interviews' sum ({job.total_cost_usd.value} vs {per})")
    i = ctx.http("GET", f"/api/analytics/summary/interviews/{ctx.iid}")
    ctx.expect(i.status_code == 200, f"the interview summary answered {i.status_code}")
    row = ctx.validate(InterviewSummary, i.json(), "the interview summary")
    ctx.expect(row.ending.status == "completed" and row.ending.end_reason, "the ending is read")
    ctx.expect(any(d.phase == "ending" for d in row.decisions), "the ending decision is there")
    said = [t["text"][:30] for t in ctx.replay["transcript"]["turns"] if len(t.get("text", "")) > 20]
    leaked = [s for s in said if s in r.text or s in i.text]
    ctx.expect(not leaked, f"interview words in the summary: {leaked[:2]}")
    # Reconciliation on non-zero costs: the smoke interview's are stand-ins (0), which
    # would let a broken total pass (D-82). Two CONSTRUCTED interviews with recorded costs,
    # in a job of this stage's own, in the run's test database.
    ctx.call(_seed_costed_job)
    c = ctx.http("GET", "/api/analytics/summary/jobs/job_smoke_d_summary")
    ctx.expect(c.status_code == 200, f"the costed job summary answered {c.status_code}")
    cj = ctx.validate(JobSummary, c.json(), "the costed job summary")
    cper = round(sum(x.cost.recorded_usd for x in cj.interview_rows), 6)
    ctx.expect(cper > 0 and cj.total_cost_usd.value is not None and abs(cj.total_cost_usd.value - cper) < 1e-6,
               f"the job total equals the interviews' sum on real numbers ({cj.total_cost_usd.value} vs {cper})")
    ctx.expect(sum(cj.by_category.values()) == cj.interviews == 2, "the costed job's counts add up")
    plan = {p.part: p for p in cj.cost_by_part}
    ctx.expect(plan["plan_generation"].usd == 0.04 and plan["plan_refinement"].usd == 0.01,
               f"generation and refinement as separate parts (C-158): {plan['plan_generation'].usd}, "
               f"{plan['plan_refinement'].usd}")
    lst = ctx.http("GET", "/api/analytics/summary/jobs?recent=5")
    ctx.expect(lst.status_code == 200, f"the job list answered {lst.status_code}")
    jl = ctx.validate(JobList, lst.json(), "the job list")
    listed = {j.job_id: j for j in jl.jobs}
    ctx.expect({"job_smoke", "job_smoke_d_summary"} <= set(listed), f"the jobs with interviews are listed: {sorted(listed)}")
    ctx.expect(listed["job_smoke_d_summary"].interviews == 2, "the job list counts the interviews")
    times = [j.latest_interview_at for j in jl.jobs]
    ctx.expect(all(a >= b for a, b in zip(times, times[1:])), "the job list is newest first")
    ctx.expect(jl.recent_interviews is not None and 1 <= len(jl.recent_interviews) <= 5, "recent interviews, at most 5")
    o = ctx.http("GET", "/api/analytics/summary/operations")
    ctx.expect(o.status_code == 200, f"the operations summary answered {o.status_code}")
    ctx.out["d_summary"] = {"job": job.model_dump(mode="json", exclude={"interview_rows"}),
                            "interview": row.model_dump(mode="json")}


async def _seed_costed_job() -> None:
    from datetime import UTC, datetime, timedelta

    from app import db

    now = datetime.now(UTC)
    async with db.get_sessionmaker()() as s:
        if await s.get(db.Job, "job_smoke_d_summary") is not None:
            return
        s.add(db.Job(id="job_smoke_d_summary", jd_text="jd", spec_status=db.SpecStatus.READY))
        await s.commit()
    async with db.get_sessionmaker()() as s:
        s.add(db.Candidate(id="cand_smoke_d_summary", job_id="job_smoke_d_summary", name="Constructed", cv_text="cv",
                           blueprint_status=db.BlueprintStatus.READY,
                           text_model_calls=[{"job": "blueprint", "cost_usd": 0.04},
                                             {"job": "blueprint_refine", "cost_usd": 0.01}]))
        await s.commit()
    async with db.get_sessionmaker()() as s:
        for n, usd in enumerate((0.12, 0.31)):
            s.add(db.Interview(id=f"int_smoke_d_sum{n}", candidate_id="cand_smoke_d_summary", meeting_id=f"d-sum-{n}",
                               password=f"d-sum-pw-{n}", daily_room_name="r", daily_room_url="https://example.daily.co/r",
                               room_expires_at=now + timedelta(hours=1), status="completed",
                               created_at=now - timedelta(minutes=10 - n), started_at=now - timedelta(minutes=9),
                               ended_at=now - timedelta(minutes=2),
                               session_metrics={"ended_because": "interview_complete", "usage": {"estimated_usd": usd},
                                                "auditor": {"stats": {"usd": 0.002}},
                                                "text_model_calls": [{"job": "feedback", "cost_usd": 0.01}]}))
        await s.commit()
