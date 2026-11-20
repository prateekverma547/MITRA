"""The after-call runner: finds finished interviews with a capture manifest and runs
each check once.

- **Off unless switched on** (AFTERCALL_ENABLED). Switched off it does nothing at
  all: no folder, no file, no model loaded.
- **Once per check.** A check that is done or skipped is never run again; a
  failed one is tried again up to MAX_ATTEMPTS times in all; one check failing
  never stops another. A per-interview lock stops two runners doing the same work.
- **Gentle.** One job at a time. In the background loop each job runs in its own
  process at the lowest priority (nice 19), one processor thread, and the loop
  waits for it before starting the next. Pausing (AFTERCALL_PAUSED or a PAUSED
  file) stops between jobs and between steps of a check; a check stopped by a
  pause goes back to pending without using up an attempt.
- **Quiet about people.** Log lines carry the interview id, the check, its state,
  times and counts. Never what anyone said: the runner reads only the
  interviews' ids and states from the database, and the check never transcribes.

    python -m aftercall.runner job <interview_id>   one job (the loop's subprocess)
    python -m aftercall.runner once                 one pass over finished interviews
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import sys
import time
from datetime import UTC, datetime
from typing import Callable

from loguru import logger

from aftercall import models, settings, store
from aftercall import second_voice as sv
from shared.interview_contracts import (
    AfterCallCheck,
    AfterCallCheckName,
    AfterCallState,
    AfterCallStatus,
    EvidenceTrack,
    IntegrityEvent,
    IntegrityEvidence,
    IntegrityKind,
    IntegritySource,
    ReminderState,
    SecondVoiceSignal,
    TimeRange,
)

CHECKS = (AfterCallCheckName.SECOND_VOICE, AfterCallCheckName.SCREEN_CONTENT)
FINAL = (AfterCallState.DONE, AfterCallState.SKIPPED)


def _now() -> datetime:
    return datetime.now(UTC)


def _blank(interview_id: str) -> AfterCallStatus:
    return AfterCallStatus(interview_id=interview_id, updated_at=_now(),
                           checks=[AfterCallCheck(check=c, state=AfterCallState.PENDING) for c in CHECKS])


def _set(status: AfterCallStatus, check: AfterCallCheckName, **fields) -> AfterCallStatus:
    checks = [c.model_copy(update=fields) if c.check == check else c for c in status.checks]
    if not any(c.check == check for c in checks):
        checks.append(AfterCallCheck(check=check, **fields))
    # re-validate (a failed or skipped check must say why)
    return AfterCallStatus.model_validate({**status.model_dump(), "checks": [c.model_dump() for c in checks],
                                           "updated_at": _now()})


def needs_work(status: AfterCallStatus | None, attempts: dict[str, int]) -> bool:
    if status is None:
        return True
    for c in status.checks:
        if c.state in FINAL:
            continue
        if c.state == AfterCallState.FAILED and attempts.get(c.check.value, 0) >= settings.MAX_ATTEMPTS:
            continue
        return True
    return False


# -- the second-voice check, from a manifest -------------------------------------------------------


def mitra_times(manifest, from_transcript):
    """When Mitra spoke, on the interview clock: the manifest's `mitra_speaking` when the
    bot wrote it (C-139), else the transcript's audio times, else None (unknown)."""
    if manifest.mitra_speaking:
        return [(r.start_seconds, r.end_seconds) for r in manifest.mitra_speaking]
    return from_transcript


def second_voice_check(interview_id: str, manifest, mitra_turns, should_stop: Callable[[], bool]) -> tuple[SecondVoiceSignal | None, dict]:
    """Run the check on the manifest's WAV. `mitra_turns` are Mitra's speaking times on
    the interview clock (None: unknown). Returns the signal (or None) and numbers for the log."""
    cap = manifest.candidate_audio
    if cap is None:
        raise sv.CheckSkipped("no candidate audio was captured for this interview")
    if (cap.sample_rate_hz, cap.channels, cap.sample_format) != (sv.SR, 1, "s16le"):
        raise sv.CheckSkipped("the captured audio is not 16 kHz mono 16-bit")
    mitra_turns = mitra_times(manifest, mitra_turns)
    if mitra_turns is None:
        raise sv.CheckSkipped(sv.NO_MITRA_TIMES)  # before any work: never guess (D-51)
    path = store.audio_file(manifest)
    if not path.is_file():
        raise FileNotFoundError("the candidate audio named in the manifest is missing")
    turns = [(a - cap.start_seconds, b - cap.start_seconds) for a, b in mitra_turns]
    result = sv.analyse_file(path, turns, should_stop=should_stop)  # read in pieces (D-62)
    numbers = {"audio_s": round(result.audio_seconds, 1), "speech_s": round(result.speech_seconds, 1),
               "windows": len(result.windows), "ranges": len(result.ranges),
               "mitra_windows_excluded": result.mitra_excluded_windows}
    if not result.ranges:
        return None, numbers
    offset = cap.start_seconds
    ranges = [TimeRange(start_seconds=round(a + offset, 2), end_seconds=round(b + offset, 2)) for a, b in result.ranges]
    event_id = f"aftercall-second-voice-{interview_id}"
    signal = SecondVoiceSignal(
        interview_id=interview_id, ranges=ranges, confidence=result.confidence,
        method=sv.METHOD, method_version=sv.METHOD_VERSION,
        wording=sv.wording([(r.start_seconds, r.end_seconds) for r in ranges]),
        integrity_event_id=event_id,
    )
    return signal, numbers


def integrity_event(signal: SecondVoiceSignal) -> IntegrityEvent:
    """The Integrity section's event that this signal backs (recorded only, never shown live)."""
    first, last = signal.ranges[0], signal.ranges[-1]
    return IntegrityEvent(
        event_id=signal.integrity_event_id, kind=IntegrityKind.SECOND_VOICE, source=IntegritySource.AFTER_CALL,
        start_seconds=first.start_seconds, end_seconds=last.end_seconds, repeats=len(signal.ranges),
        reminder=ReminderState.RECORDED_ONLY, detail="on the candidate's own audio track",
        evidence=[IntegrityEvidence(track=EvidenceTrack.AUDIO, clip_start_seconds=r.start_seconds,
                                    clip_end_seconds=r.end_seconds) for r in signal.ranges],
    )


# -- one job -------------------------------------------------------------------------------------------


def run_job(interview_id: str, *, mitra_turns: list[tuple[float, float]] | None = None,
            should_stop: Callable[[], bool] | None = None) -> AfterCallStatus | None:
    """Run every check of one interview that still needs running. Returns its status,
    or None when nothing could start (switched off, no manifest yet, busy).

    `mitra_turns`: when Mitra spoke, on the interview clock (mitra_turns_from_db); the
    manifest's `mitra_speaking` takes precedence when set. Without either the
    second-voice check is skipped and says why (D-51)."""
    if not settings.enabled():
        logger.debug("after-call checks are switched off (AFTERCALL_ENABLED)")
        return None
    should_stop = should_stop or settings.paused
    interview_id = store.safe_id(interview_id)
    try:
        manifest = store.read_manifest(interview_id)
    except Exception as exc:  # noqa: BLE001  (a bad manifest is that interview's problem only)
        logger.warning(f"[{interview_id}] after-call: the capture manifest cannot be used ({type(exc).__name__})")
        manifest = "invalid"
    if manifest is None:
        return None
    lock = store.Lock(interview_id)
    if not lock.acquire():
        logger.info(f"[{interview_id}] after-call: another runner holds this interview; left alone")
        return None
    with lock:
        status = store.read_status(interview_id) or _blank(interview_id)
        attempts = store.read_attempts(interview_id)
        for check in CHECKS:
            current = next((c for c in status.checks if c.check == check), None)
            state = current.state if current else AfterCallState.PENDING
            if state in FINAL:
                continue
            if state in (AfterCallState.FAILED, AfterCallState.RUNNING) and \
                    attempts.get(check.value, 0) >= settings.MAX_ATTEMPTS:
                if state == AfterCallState.RUNNING:  # the last attempt died part-way
                    status = _set(status, check, state=AfterCallState.FAILED, finished_at=_now(),
                                  reason=f"stopped part-way {settings.MAX_ATTEMPTS} times")
                    store.write_status(status)
                continue
            if should_stop():
                logger.info(f"[{interview_id}] after-call: paused before {check.value}")
                break
            if check == AfterCallCheckName.SCREEN_CONTENT:
                status = _set(status, check, state=AfterCallState.SKIPPED, finished_at=_now(),
                              reason="not built yet (wave 2: screen frames)")
                store.write_status(status)
                continue
            if manifest == "invalid":
                status = _set(status, check, state=AfterCallState.FAILED, finished_at=_now(),
                              reason="the capture manifest could not be read")
                attempts[check.value] = settings.MAX_ATTEMPTS
                store.write_attempts(interview_id, attempts)
                store.write_status(status)
                continue
            attempts[check.value] = attempts.get(check.value, 0) + 1
            store.write_attempts(interview_id, attempts)
            status = _set(status, check, state=AfterCallState.RUNNING, started_at=_now(), finished_at=None, reason=None)
            store.write_status(status)
            t0, c0 = time.monotonic(), time.process_time()
            try:
                signal, numbers = second_voice_check(interview_id, manifest, mitra_turns, should_stop)
            except sv.Paused:
                attempts[check.value] -= 1  # a pause is not an attempt
                store.write_attempts(interview_id, attempts)
                status = _set(status, check, state=AfterCallState.PENDING, started_at=None)
                store.write_status(status)
                logger.info(f"[{interview_id}] after-call {check.value}: paused part-way; will run again")
                break
            except sv.CheckSkipped as exc:
                status = _set(status, check, state=AfterCallState.SKIPPED, finished_at=_now(), reason=str(exc)[:300])
                store.write_status(status)
                logger.info(f"[{interview_id}] after-call {check.value}: skipped")
                continue
            except Exception as exc:  # noqa: BLE001
                why = "a model file is missing or changed" if isinstance(exc, models.ModelUnavailable) else \
                    f"{type(exc).__name__}"
                status = _set(status, check, state=AfterCallState.FAILED, finished_at=_now(),
                              reason=f"attempt {attempts[check.value]} of {settings.MAX_ATTEMPTS} failed: {why}")
                store.write_status(status)
                logger.warning(f"[{interview_id}] after-call {check.value}: failed (attempt "
                               f"{attempts[check.value]} of {settings.MAX_ATTEMPTS}, {type(exc).__name__})")
                continue
            if signal is None:
                store.remove_signal(interview_id)
            else:
                store.write_signal(signal)
                store.write_events(interview_id, [integrity_event(signal)])
            status = _set(status, check, state=AfterCallState.DONE, finished_at=_now(), reason=None)
            store.write_status(status)
            logger.info(f"[{interview_id}] after-call {check.value}: done in {time.monotonic() - t0:.1f}s "
                        f"(cpu {time.process_time() - c0:.1f}s) {json.dumps(numbers)}")
        return status


# -- finding work ------------------------------------------------------------------------------------


async def finished_interviews() -> list[str]:
    """Ids of completed interviews. Reads ids only: never a transcript or a password."""
    from sqlalchemy import select

    from app.db import Interview, InterviewStatus, get_sessionmaker

    async with get_sessionmaker()() as session:
        rows = await session.execute(select(Interview.id).where(Interview.status == InterviewStatus.COMPLETED))
        return [r[0] for r in rows]


async def mitra_turns_from_db(interview_id: str) -> list[tuple[float, float]] | None:
    """When Mitra spoke, from the stored transcript's audio times. Reads the times only:
    no text is kept, returned or logged. None when any of Mitra's turns has no audio
    times (then the check cannot tell Mitra's voice apart and is skipped)."""
    from sqlalchemy import select

    from app.db import Interview, get_sessionmaker

    async with get_sessionmaker()() as session:
        raw = (await session.execute(select(Interview.transcript).where(Interview.id == interview_id))).scalar()
    if not raw:
        return None
    turns = []
    for t in raw.get("turns", []):
        if t.get("speaker") != "interviewer":
            continue
        a, b = t.get("audio_start_seconds"), t.get("audio_end_seconds")
        if a is None or b is None:
            return None
        turns.append((float(a), float(b)))
    return turns


async def job_mitra_turns(interview_id: str) -> list[tuple[float, float]] | None:
    """Mitra's times for one job: the transcript is read ONLY when the manifest does not
    carry them (C-139); run_job then prefers the manifest's own."""
    try:
        manifest = store.read_manifest(interview_id)
    except Exception:  # noqa: BLE001  (run_job reports a bad manifest)
        manifest = None
    if manifest is not None and manifest.mitra_speaking:
        return None
    return await mitra_turns_from_db(interview_id)


async def find_work() -> list[str]:
    """Completed interviews whose manifest exists and whose checks still need running."""
    out = []
    for iid in await finished_interviews():
        try:
            if store.manifest_path(iid).is_file() and needs_work(store.read_status(iid), store.read_attempts(iid)):
                out.append(iid)
        except store.BadInput:
            continue
    return out


def _gentle() -> None:
    try:
        os.nice(19)
    except OSError:
        pass
    os.environ.setdefault("OMP_NUM_THREADS", "1")


async def run_job_in_subprocess(interview_id: str) -> dict:
    """One job in its own low-priority process. Returns its measured cost."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "aftercall.runner", "job", store.safe_id(interview_id),
        stdout=asyncio.subprocess.PIPE, stderr=None, cwd=str(settings.PACKAGE_DIR.parent),
    )
    out, _ = await proc.communicate()
    try:
        cost = json.loads(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        cost = {}
    cost["exit_code"] = proc.returncode
    logger.info(f"[{interview_id}] after-call job finished {json.dumps(cost)}")
    return cost


async def run_pass() -> int:
    """One pass: every interview with work, one at a time. Returns how many jobs ran."""
    if not settings.enabled() or settings.paused():
        return 0
    n = 0
    for iid in await find_work():
        if settings.paused():
            break
        await run_job_in_subprocess(iid)
        try:  # its after-call result goes into the stored integrity report now (D-93)
            from proctoring.stored import store as store_integrity_report

            await store_integrity_report(iid)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{iid}] integrity report not updated ({type(exc).__name__})")
        n += 1
    return n


async def run_forever() -> None:
    """The background loop (to be started by the app's lifespan: a request for Session C)."""
    while True:
        try:
            # The stored integrity report (Session D, D-93): written for every ended interview,
            # whatever the after-call switch; rewritten when late events or results arrive.
            from proctoring.stored import write_pending

            await write_pending()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"integrity reports pass failed, will run again ({type(exc).__name__})")
        try:
            await run_pass()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"after-call pass failed, will run again ({type(exc).__name__})")
        await asyncio.sleep(settings.LOOP_SECONDS)


def main(argv: list[str]) -> int:
    if argv[:1] == ["job"] and len(argv) == 2:
        _gentle()
        t0 = time.monotonic()
        iid = store.safe_id(argv[1])
        turns = asyncio.run(job_mitra_turns(iid)) if settings.enabled() else None
        status = run_job(iid, mitra_turns=turns)
        r = resource.getrusage(resource.RUSAGE_SELF)
        rss_mb = r.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
        print(json.dumps({"wall_s": round(time.monotonic() - t0, 2), "cpu_s": round(r.ru_utime + r.ru_stime, 2),
                          "peak_rss_mb": round(rss_mb, 1), "nice": os.nice(0),
                          "states": {c.check.value: c.state.value for c in status.checks} if status else None}))
        return 0
    if argv[:1] == ["once"]:
        print(asyncio.run(run_pass()))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
