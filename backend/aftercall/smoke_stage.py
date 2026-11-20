"""Session D's smoke stage (C-110): the after-call runner and the second-voice check.

Free and deterministic: two small CONSTRUCTED tracks built from aftercall/fixtures
(one speaker; another person speaking mid-answer), their manifests in the run's
recordings folder, the switch forced on for this stage only. Checks the statuses,
the result shapes, no signal for one speaker, a signal for two, that a second run
changes nothing, that the smoke interview (whose replay has no Mitra end times)
is skipped rather than guessed, and that no words or secrets are in the output.
"""

from __future__ import annotations

import os
from pathlib import Path

from scripts.smoke_interview import stage

FIX = Path(__file__).resolve().parent / "fixtures"


@stage("aftercall", owner="D")
def after_call(ctx) -> None:
    from aftercall import models, runner, settings, store, testset as ts
    from shared.interview_contracts import AfterCallStatus, CandidateAudioCapture, CaptureManifest, SecondVoiceSignal

    try:
        models.verified_path(models.SPEAKER)
    except models.ModelUnavailable as exc:
        ctx.expect(False, f"{exc}")

    first_log = len(ctx.logs)  # every log line from here on is this stage's
    before = os.environ.get(settings.ENABLED_ENV)
    os.environ[settings.ENABLED_ENV] = "1"  # this stage only; restored below
    try:
        c, o, m = ([ts.load(FIX / "candidate_ls1462.wav")], [ts.load(FIX / "other_ls2428.wav")],
                   [ts.load(FIX / "mitra_cedar.wav")])
        out = {}
        for iid, kind in (("int_smoke_ac_one", "one_speaker"), ("int_smoke_ac_two", "two_mid_answer")):
            case = ts.build(kind, c, o, m, seed=0, seconds=60)
            ts.write(store.capture_dir(iid) / "candidate.wav", case.audio)
            store.manifest_path(iid).write_text(CaptureManifest(
                interview_id=iid, candidate_audio=CandidateAudioCapture(
                    relative_path=f"{iid}/candidate.wav", start_seconds=0.0,
                    duration_seconds=len(case.audio) / ts.SR)).model_dump_json())
            status = runner.run_job(iid, mitra_turns=case.mitra_turns)
            ctx.expect(status is not None, f"{kind}: the runner did not run")
            ctx.validate(AfterCallStatus, status.model_dump(mode="json"), f"{kind}: the status")
            states = {x.check.value: x.state.value for x in status.checks}
            ctx.expect(states == {"second_voice": "done", "screen_content": "skipped"}, f"{kind}: states {states}")
            signal = store.read_signal(iid)
            if kind == "one_speaker":
                ctx.expect(signal is None, "one speaker gave a second-voice signal")
            else:
                ctx.expect(signal is not None, "another person speaking gave no signal")
                ctx.validate(SecondVoiceSignal, signal.model_dump(mode="json"), "the signal")
                found = [(r.start_seconds, r.end_seconds) for r in signal.ranges]
                ctx.expect(ts.overlaps(found, case.truth) >= 1, f"the signal's ranges {found} miss {case.truth}")
            files = {p.name: p.read_text() for p in sorted(store.result_dir(iid).iterdir()) if p.is_file()}
            again = runner.run_job(iid, mitra_turns=case.mitra_turns)
            ctx.expect(again.checks == status.checks, f"{kind}: a second run changed the status")
            ctx.expect({p.name: p.read_text() for p in sorted(store.result_dir(iid).iterdir()) if p.is_file()} == files,
                       f"{kind}: a second run rewrote the results")
            out[kind] = {"status": status.model_dump(mode="json"),
                         "signal": signal.model_dump(mode="json") if signal else None}

        # The seeded smoke interview: completed, a manifest, found as work; its replay
        # has no Mitra end times, so the check must be skipped, never guessed.
        iid = ctx.iid
        store.capture_dir(iid).mkdir(parents=True, exist_ok=True)
        ts.write(store.capture_dir(iid) / "candidate.wav", ts.build("one_speaker", c, o, m, seed=1, seconds=30).audio)
        store.manifest_path(iid).write_text(CaptureManifest(interview_id=iid, candidate_audio=CandidateAudioCapture(
            relative_path=f"{iid}/candidate.wav", start_seconds=0.0, duration_seconds=30.0)).model_dump_json())
        ctx.expect(iid in ctx.call(runner.find_work), "the completed smoke interview was not found as work")
        turns = ctx.call(runner.mitra_turns_from_db, iid)
        status = runner.run_job(iid, mitra_turns=turns)
        check = next(x for x in status.checks if x.check.value == "second_voice")
        ctx.expect(turns is None and check.state.value == "skipped" and "Mitra's speaking times" in check.reason,
                   f"without Mitra's end times the check must be skipped (got {check.state.value})")
        ctx.expect(iid not in ctx.call(runner.find_work), "a finished interview was picked up again")
        out["smoke_interview"] = status.model_dump(mode="json")

        # No words: nothing anyone said in the seeded interview may appear in our output or logs.
        said = [t["text"] for t in ctx.replay["transcript"]["turns"] if len(t.get("text", "")) > 20]
        ours = [str(out)] + ctx.logs[first_log:]
        ctx.expect(any("after-call" in line for line in ctx.logs[first_log:]),
                   "the stage's log lines were not captured, so the word check would prove nothing")
        leaked = [s[:30] for s in said if any(s[:30] in text for text in ours)]
        ctx.expect(not leaked, f"words from the interview in the after-call output or logs: {leaked}")
        ctx.out["aftercall"] = out
    finally:
        if before is None:
            os.environ.pop(settings.ENABLED_ENV, None)
        else:
            os.environ[settings.ENABLED_ENV] = before
