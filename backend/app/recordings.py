"""The life of an interview recording: collect it, play it, delete it.

A recording exists so a person can watch an interview back. That is the whole
purpose, and it is worth being explicit about what it is not: nothing here reads
the video, scores it, measures attention or hands it to a model. Proctoring is
out of scope (CLAUDE.md) and this does not quietly reopen it. The only consumer
is somebody with the panel open.

**Why there is a sweep at all.** Daily composites the call in its own cloud and
puts the result in its own storage; there is no mode that writes to our disk
while the call runs. So the file arrives in two steps, and the gap between them
is however long Daily takes to finish compositing, which is not something a
request can wait on. The sweep closes that gap, and while it is there it also
enforces retention. One loop, two jobs, both of which are "look at what the
clock says and act on it".

No queue and no scheduler, per CLAUDE.md: this is an asyncio task started by the
app's lifespan. That is sound because replicas are pinned at 1. **If that ever
changes, every replica would run its own sweep**, and two of them downloading the
same recording is the least of it.

Retention is ten days from the interview, which is the number the candidate is
shown on the consent notice. Both read `RECORDING_RETENTION_DAYS`.
"""

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy import or_, select, update

from app.auth import require_admin
from app.db import Interview, RecordingStatus, get_sessionmaker
from bot.config import _TRUTHY, SESSIONS_DIR, Settings
from bot.services.daily import delete_recording, download_recording, find_recording
from shared.contracts import RECORDING_RETENTION_DAYS

router = APIRouter(tags=["recordings"])

#: Same auth as the rest of the panel, deliberately. Anyone with the admin
#: password can watch any recording, which is the access model this product has
#: today for transcripts and reports as well. Narrowing it is real work and it is
#: not this change.
ADMIN_ONLY = [Depends(require_admin)]

#: The default home for recordings: under the directory that already holds
#: local run artifacts, which is gitignored. One subdirectory so they do not mix
#: with the session JSON files. **That directory is on the container's own disk,
#: which a redeploy wipes** (D-24), so the folder comes from a setting:
RECORDINGS_DIR = SESSIONS_DIR / "recordings"

#: Where recordings are stored, when set: a persistent disk mounted into the
#: container (a Railway volume, say). Empty means RECORDINGS_DIR. A storage
#: bucket is the pre-pilot answer (C_findings); this setting is what lets a
#: disk stand in until then without a code change.
RECORDINGS_DIR_ENV = "RECORDINGS_DIR"

#: Rows written before the setting existed store "recordings/<id>.mp4",
#: relative to SESSIONS_DIR. New rows store "<id>.mp4", relative to the
#: recordings folder, so the folder can move without rewriting rows.
_LEGACY_PREFIX = "recordings/"

#: Every byte of a recording is served with this: only the signed-in reviewer's
#: own browser may keep it (`private`: never a shared proxy or CDN), and not on
#: disk either (`no-store`): it is a recording of a person.
RECORDING_CACHE_CONTROL = "private, no-store"

#: How often the sweep runs. Nothing here is urgent: a recording that appears
#: five minutes later is fine, and a retention window measured in days does not
#: need a minute-accurate deletion.
SWEEP_INTERVAL_SECONDS = 5 * 60

#: How long after an interview ends to keep waiting for Daily to produce the
#: file before calling it lost. Generous, because compositing a long call is not
#: instant, and the cost of waiting is a panel that says "still being prepared"
#: while the cost of giving up early is a recording thrown away that had arrived.
COLLECT_GIVE_UP_HOURS = 2

#: Failed downloads in a row before the recording is shown as failing
#: (C-103). The sweep runs every 5 minutes and a healthy download succeeds on
#: its first attempt (C-90, A-61: one sweep each), so three failures in a row
#: means 10 to 15 minutes of failing: not a passing network blip, and early
#: enough that a reviewer opening the report sees why there is no video. It
#: changes what is SHOWN, never whether it is retried: the sweep keeps trying
#: every 5 minutes for as long as Daily still has the file, which is until
#: retention deletes Daily's copy (10 days from recording start).
DOWNLOAD_FAILING_AFTER = 3

#: The state the console sees for a recording at that point (RecordingInfo).
#: Not a database status: the row stays "recording" (Daily has the file, it is
#: not ours yet), so the sweep, retention, deletion and the old panel, which
#: shows any status it does not know as "not recorded", are unchanged.
DOWNLOAD_FAILING = "download_failing"
#: Stored here once, but the file is no longer on this server's disk (a redeploy
#: without a mounted volume, a lost disk). Shown as its own state, never as
#: "stored" with nothing to play (PA-07, C-164).
FILE_MISSING = "file_missing"

#: Set this to switch the background loop off. The tests drive the sweep directly
#: so they can watch what it does; a loop running underneath them would make
#: calls to Daily nobody asked for.
SWEEP_DISABLED_ENV = "RECORDING_SWEEP_DISABLED"


def retention_deadline(from_time: datetime) -> datetime:
    return from_time + timedelta(days=RECORDING_RETENTION_DAYS)


def recordings_root() -> Path:
    """The folder recordings are written to: the setting, else RECORDINGS_DIR."""
    configured = os.environ.get(RECORDINGS_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else RECORDINGS_DIR


def recording_file(interview_id: str) -> Path:
    return recordings_root() / f"{interview_id}.mp4"


def stored_path(interview: Interview) -> Path | None:
    """The absolute path of a stored recording, or None if there is not one.

    The stored value is relative: to the recordings folder (new rows), or to
    `SESSIONS_DIR` (rows written before RECORDINGS_DIR existed). It is checked
    for containment before use: it comes out of the database, and a path from a
    database that is allowed to escape its directory is how a read of an
    interview record turns into a read of anything on the host.
    """
    if not interview.recording_path:
        return None
    base = SESSIONS_DIR if interview.recording_path.startswith(_LEGACY_PREFIX) else recordings_root()
    candidate = (base / interview.recording_path).resolve()
    root = base.resolve()
    if not candidate.is_relative_to(root):
        logger.error(
            f"[{interview.id}] recording_path points outside the sessions "
            f"directory and was ignored: {interview.recording_path!r}"
        )
        return None
    return candidate


# -- collecting --------------------------------------------------------------


async def collect_one(interview_id: str, *, api_key: str) -> str:
    """Fetch one finished recording from Daily onto our own disk.

    Returns a short word describing what happened, for the sweep to log and the
    tests to assert on: `stored`, `waiting`, `unavailable`, or `error`.

    The order matters. The file is downloaded and verified first, and only then
    is Daily's copy deleted. Deleting first, or in the same step, would mean a
    failed download loses the only copy there is.
    """
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None or interview.recording_status != RecordingStatus.RECORDING:
            return "waiting"
        room_name = interview.daily_room_name
        ended_at = interview.ended_at
        room_expires_at = interview.room_expires_at

    claim = await _claim(interview_id)
    if claim is None:
        return "busy"  # another sweep is collecting it right now: not a failure
    try:
        return await _collect(interview_id, api_key=api_key, room_name=room_name, ended_at=ended_at,
                              room_expires_at=room_expires_at)
    finally:
        await _release(interview_id, claim)


#: How long a sweep's claim on one recording lasts. Long enough for a slow
#: download of an hour's recording; a claim left by a dead process expires.
COLLECT_LEASE_MINUTES = 30


async def _claim(interview_id: str) -> str | None:
    """Claim the collection of one recording, atomically: one conditional
    UPDATE, so of two sweeps at the same moment exactly one wins."""
    import secrets

    now = datetime.now(UTC)
    claim = secrets.token_hex(8)
    async with get_sessionmaker()() as session:
        result = await session.execute(
            update(Interview)
            .where(Interview.id == interview_id,
                   Interview.recording_status == RecordingStatus.RECORDING,
                   or_(Interview.recording_collect_until.is_(None), Interview.recording_collect_until < now))
            .values(recording_collect_by=claim,
                    recording_collect_until=now + timedelta(minutes=COLLECT_LEASE_MINUTES)))
        await session.commit()
    return claim if result.rowcount == 1 else None


async def _release(interview_id: str, claim: str) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Interview).where(Interview.id == interview_id, Interview.recording_collect_by == claim)
            .values(recording_collect_by=None, recording_collect_until=None))
        await session.commit()


async def _collect(interview_id: str, *, api_key: str, room_name: str, ended_at, room_expires_at) -> str:

    try:
        found = await find_recording(api_key=api_key, room_name=room_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{interview_id}] could not ask Daily about the recording: {exc}")
        return "error"

    if found is None:
        # Daily has nothing finished for this room. Either the call is still
        # running, or compositing is not done, or there never will be one.
        if _waited_long_enough(ended_at=ended_at, room_expires_at=room_expires_at):
            await _mark_unavailable(
                interview_id,
                "The interview finished but Daily never produced a recording for "
                "it. There is nothing to watch back.",
            )
            return "unavailable"
        return "waiting"

    destination = recording_file(interview_id)
    try:
        written = await download_recording(
            api_key=api_key, recording_id=found.id, destination=destination
        )
    except Exception as exc:  # noqa: BLE001
        # Left in `recording` so the next sweep tries again. Daily still has the
        # file, so nothing is lost by failing here.
        await _download_failed(interview_id, download_failure_reason(exc))
        return "error"

    # Verified before anything else happens: Daily's copy is deleted below,
    # and after that this file is the only one there is. A file that fails is
    # removed and the sweep tries again; Daily's copy is never touched.
    problem = verify_recording_file(destination, written)
    if problem:
        destination.unlink(missing_ok=True)
        await _download_failed(interview_id, "The downloaded file was not a whole, playable video.",
                               detail=problem)
        return "error"

    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            return "error"
        interview.recording_status = RecordingStatus.STORED
        interview.recording_id = found.id
        interview.recording_path = destination.name  # relative to recordings_root()
        interview.recording_bytes = written
        # A success ends any failing state (C-103).
        interview.recording_download_failures = 0
        interview.recording_error = None
        # recording_offset_seconds is NOT touched: the bot measured it from the
        # start acknowledgement (D-05). Daily's `start_ts` (found.start_ts) is
        # whole seconds and 2.3 s early, so it must never replace it.
        if interview.recording_expires_at is None:
            interview.recording_expires_at = retention_deadline(
                interview.recording_started_at or interview.created_at
            )
        await session.commit()

    logger.info(f"[{interview_id}] recording stored, {written / 1e6:.1f} MB")

    # One copy, on our disk, as decided. Daily's copy goes now that ours is
    # verified. If this fails the recording id stays on the record, so the
    # deletion path will try again when the file is removed or expires.
    try:
        await delete_recording(api_key=api_key, recording_id=found.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[{interview_id}] our copy is stored but Daily's copy could not be "
            f"deleted: {exc}. It will be retried when this recording is deleted."
        )
    return "stored"


#: Messages from bot/services/daily.py download_recording that are safe to
#: reword: they carry a status or a byte count, never the link.
_HTTP_STATUS = re.compile(r"\((\d{3})\)")
_BYTES = re.compile(r"\((\d+) of (\d+) bytes\)")


def download_failure_reason(exc: BaseException) -> str:
    """A short, plain reason for a failed download, for a reviewer to read.

    Built from what is known to be safe (an HTTP status, a byte count, the
    exception's type name), never from the exception's text: an HTTP library's
    error can quote the request's address, and Daily's download address is a
    signed link that must never reach a log or the database.
    """
    text = str(exc)
    status = _HTTP_STATUS.search(text)
    if text.startswith("Downloading the recording failed") and status:
        code = int(status.group(1))
        if code in (401, 403):
            return f"Download refused by Daily ({code})."
        if code == 404:
            return "Daily's download link found no file (404)."
        return f"Daily's storage answered {code}."
    if text.startswith("Daily access link failed") and status:
        return f"Daily would not give a download link ({status.group(1)})."
    if text.startswith("Daily returned an access link with no download URL"):
        return "Daily gave no download link."
    counted = _BYTES.search(text)
    if text.startswith("The recording download was incomplete") and counted:
        return f"The download stopped early ({counted.group(1)} of {counted.group(2)} bytes)."
    if text.startswith("The recording downloaded as an empty file"):
        return "The download was empty."
    return f"The download did not complete ({type(exc).__name__})."


async def _download_failed(interview_id: str, reason: str, *, detail: str | None = None) -> None:
    """Count one failed download; at DOWNLOAD_FAILING_AFTER in a row, show why.

    Logged loudly once, when the failures reach the threshold; every other
    attempt is one quiet line. `reason` is already plain and link-free; `detail`
    (a verification finding, never an error's text) goes to the log only.
    """
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            return
        failures = (interview.recording_download_failures or 0) + 1
        interview.recording_download_failures = failures
        if failures >= DOWNLOAD_FAILING_AFTER:
            interview.recording_error = (
                f"{reason} The recording is still at Daily and is tried again every "
                f"{SWEEP_INTERVAL_SECONDS // 60} minutes.")
        await session.commit()
    if failures == DOWNLOAD_FAILING_AFTER:
        logger.error(
            f"[{interview_id}] RECORDING DOWNLOAD FAILING: {failures} attempts in a row have "
            f"failed ({reason}). Daily's copy is kept and the sweep keeps trying; the "
            f"recording is shown as {DOWNLOAD_FAILING!r} until one succeeds."
        )
    else:
        logger.info(f"[{interview_id}] recording download attempt {failures} failed: {reason}"
                    + (f" ({detail})" if detail else ""))


def console_status(interview: Interview, *, file_present: bool | None = None) -> str:
    """The recording's state as the console shows it (RecordingInfo.status):
    the database status, or DOWNLOAD_FAILING for a recording still at Daily
    whose last DOWNLOAD_FAILING_AFTER downloads all failed, or FILE_MISSING for
    one stored here whose file is gone (`file_present` False; None: not checked)."""
    if (interview.recording_status == RecordingStatus.RECORDING
            and (interview.recording_download_failures or 0) >= DOWNLOAD_FAILING_AFTER):
        return DOWNLOAD_FAILING
    if interview.recording_status == RecordingStatus.STORED and file_present is False:
        return FILE_MISSING
    return str(interview.recording_status)


def verify_recording_file(path: Path, expected_bytes: int) -> str | None:
    """None when the file is whole and opens as an MP4; otherwise what is wrong.

    "Whole": on disk (flushed), with exactly the bytes the download counted.
    "Opens": the top-level MP4 boxes start with `ftyp`, include `moov` (the
    index a player needs) and `mdat` (the media), and tile the file exactly. A
    download cut short ends inside a box, so its declared size runs past the
    end of the file; that is how truncation is caught without a decoder.
    """
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
            size = os.fstat(handle.fileno()).st_size
            if size == 0:
                return "the file is empty"
            if size != expected_bytes:
                return f"{size} bytes on disk, {expected_bytes} downloaded"
            kinds: list[str] = []
            position = 0
            while position < size:
                handle.seek(position)
                header = handle.read(8)
                if len(header) < 8:
                    return f"a box header is cut off at byte {position}"
                box_size, kind = int.from_bytes(header[:4], "big"), header[4:8]
                if box_size == 1:
                    large = handle.read(8)
                    if len(large) < 8:
                        return f"a box header is cut off at byte {position}"
                    box_size = int.from_bytes(large, "big")
                elif box_size == 0:
                    box_size = size - position  # "to the end of the file"
                if box_size < 8 or position + box_size > size:
                    return f"the {kind!r} box at byte {position} runs past the end of the file"
                kinds.append(kind.decode("latin-1"))
                position += box_size
    except OSError as exc:
        return f"the file could not be read: {exc}"
    if not kinds or kinds[0] != "ftyp":
        return "not an MP4: no ftyp box first"
    missing = [k for k in ("moov", "mdat") if k not in kinds]
    if missing:
        return f"not a playable MP4: no {' or '.join(missing)} box"
    return None


def _waited_long_enough(*, ended_at: datetime | None, room_expires_at: datetime | None) -> bool:
    now = datetime.now(UTC)
    if ended_at is not None and _aware(ended_at) + timedelta(hours=COLLECT_GIVE_UP_HOURS) < now:
        return True
    # The backstop for a bot that was killed and never wrote an end time. Once
    # the room has expired nobody can be in it, so nothing more is coming.
    return room_expires_at is not None and _aware(room_expires_at) < now


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes even for timezone-aware columns."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _mark_unavailable(interview_id: str, reason: str) -> None:
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            return
        interview.recording_status = RecordingStatus.UNAVAILABLE
        interview.recording_error = reason
        await session.commit()
    logger.warning(f"[{interview_id}] no recording: {reason}")


# -- deleting ----------------------------------------------------------------


async def remove_recording(interview_id: str, *, status: RecordingStatus, api_key: str) -> None:
    """Delete a recording for real, and confirm it is gone.

    Removes our file and Daily's copy if one is still there, then checks. This
    raises rather than reporting success on a file that is still on disk: a
    delete button that clears a row and leaves the bytes is the shape of failure
    this codebase has recorded six times, and it would be worse here than
    anywhere else, because what was deleted was promised to a candidate.
    """
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail=f"No interview '{interview_id}'.")
        path = stored_path(interview)
        remote_id = interview.recording_id

    if path is not None and path.exists():
        path.unlink()
        if path.exists():
            raise RuntimeError(f"{path} is still on disk after being deleted.")

    if remote_id:
        # Raises if Daily does not confirm. Deliberately not swallowed: this is
        # the half of the deletion we do not control, and silence about it would
        # leave a copy of somebody's interview at a third party after they were
        # told it was gone.
        await delete_recording(api_key=api_key, recording_id=remote_id)

    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            return
        interview.recording_status = status
        interview.recording_path = None
        interview.recording_bytes = None
        interview.recording_id = None
        interview.recording_expires_at = None
        interview.recording_deleted_at = datetime.now(UTC)
        await session.commit()

    logger.info(f"[{interview_id}] recording deleted ({status})")


# -- the sweep ---------------------------------------------------------------


async def run_sweep(*, api_key: str) -> dict[str, int]:
    """One pass: collect what has finished, delete what has aged out.

    Returns counts, which the loop logs. Never raises: a sweep that dies takes
    retention with it, and retention failing silently is how a ten day promise
    becomes a permanent archive.
    """
    counts = {"stored": 0, "waiting": 0, "busy": 0, "unavailable": 0, "error": 0, "expired": 0,
              "aftercall_expired": 0, "no_show": 0}
    now = datetime.now(UTC)

    async with get_sessionmaker()() as session:
        pending = list(
            await session.scalars(
                select(Interview.id).where(
                    Interview.recording_status == RecordingStatus.RECORDING
                )
            )
        )

    for interview_id in pending:
        try:
            counts[await collect_one(interview_id, api_key=api_key)] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{interview_id}] collecting the recording failed: {exc}")
            counts["error"] += 1

    async with get_sessionmaker()() as session:
        expired = list(
            await session.scalars(
                select(Interview.id).where(
                    Interview.recording_status.in_(
                        [RecordingStatus.RECORDING, RecordingStatus.STORED]
                    ),
                    Interview.recording_expires_at.isnot(None),
                    Interview.recording_expires_at <= now,
                )
            )
        )

    for interview_id in expired:
        try:
            await remove_recording(
                interview_id, status=RecordingStatus.EXPIRED, api_key=api_key
            )
            counts["expired"] += 1
        except Exception as exc:  # noqa: BLE001
            # Left as it is so the next sweep tries again. The retention promise
            # is not kept by giving up on it.
            logger.error(
                f"[{interview_id}] recording is past its retention window and "
                f"could NOT be deleted: {exc}. It will be retried."
            )
            counts["error"] += 1

    try:
        from live.phase4 import mark_no_shows

        counts["no_show"] = await mark_no_shows(now)  # phase 4 data (D-41.4), on the same clock
    except Exception as exc:  # noqa: BLE001
        logger.error(f"no-shows not marked, will run again: {exc!r}")
        counts["error"] += 1
    try:
        counts["aftercall_expired"] = await expire_aftercall_files(now)
    except Exception as exc:  # noqa: BLE001  (retried on the next sweep)
        logger.error(f"after-call files past retention could NOT be deleted: {exc}. It will be retried.")
        counts["error"] += 1
    return counts


#: The after-call checks' folders under the recordings folder (backend/aftercall/
#: settings.py): capture/<interview id>/ (the bot's candidate track and its
#: manifest) and aftercall/<interview id>/ (the checks' results). Same volume,
#: same retention as the recordings.
AFTERCALL_FOLDERS = ("capture", "aftercall")


async def expire_aftercall_files(now: datetime) -> int:
    """Delete the after-call folders of every interview past retention (from its
    start, else its creation, as the recordings), or of an interview that no
    longer exists. Returns how many folders were deleted, and checks they are gone."""
    import shutil

    root = recordings_root()
    folders = [d for name in AFTERCALL_FOLDERS if (root / name).is_dir()
               for d in (root / name).iterdir() if d.is_dir()]
    if not folders:
        return 0
    async with get_sessionmaker()() as session:
        rows = {i.id: i for i in await session.scalars(
            select(Interview).where(Interview.id.in_({d.name for d in folders})))}
    deleted = 0
    for folder in folders:
        interview = rows.get(folder.name)
        began = None if interview is None else _aware(interview.started_at or interview.created_at)
        if interview is not None and retention_deadline(began) > now:
            continue
        if not folder.resolve().is_relative_to(root.resolve()):
            continue  # never outside the recordings folder
        shutil.rmtree(folder, ignore_errors=True)  # a second sweep may have just deleted it
        if folder.exists():
            raise RuntimeError(f"{folder} is still on disk after being deleted.")
        deleted += 1
        logger.info(f"[{folder.name}] after-call files deleted ({folder.parent.name}): "
                    f"{'past retention' if interview is not None else 'no such interview'}")
    return deleted


async def sweep_forever() -> None:
    """The background loop. Started by the app's lifespan, cancelled by it."""
    while True:
        try:
            settings = Settings.load()
            counts = await run_sweep(api_key=settings.daily_api_key)
            if any(counts.values()):
                logger.info(f"recording sweep: {counts}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"recording sweep failed, will run again: {exc}")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def sweep_enabled() -> bool:
    """Whether expired recordings get deleted. On unless explicitly switched off.

    **Parsed the way the other three flags are parsed, which it was not.** This
    read `not os.environ.get(...)`, bare truthiness on the raw string, so
    `RECORDING_SWEEP_DISABLED=0` and `=false` both switched the sweep OFF: the
    string "0" is true to Python. An operator writing `false` to keep retention
    running would have turned it off, and the sweep is the only thing that
    deletes recordings Daily charges for.

    `_TRUTHY` is the shared vocabulary — "1", "true", "yes", "on" — and this is
    now the fourth flag reading it rather than the one flag that did not.
    Absent still means the sweep runs, which is the behaviour that matters:
    retention that has to be enabled is not retention.
    """
    return os.environ.get(SWEEP_DISABLED_ENV, "").strip().lower() not in _TRUTHY


# -- routes ------------------------------------------------------------------


async def serve_recording(interview_id: str, request: Request, *, viewer: str, route: str,
                          filename: str | None = None) -> FileResponse:
    """Serve one interview's recording from our own disk, and record the view.

    Every request is recorded (who, which interview, when, which bytes) before
    any byte is sent: a view that cannot be recorded is not served. Callers
    sign the request in first; this does no auth of its own.

    A `FileResponse` rather than a redirect to Daily, for two reasons. The file
    is ours by the time anyone can play it, and Daily's own links are presigned
    and short lived, so a page open for half an hour would stop working halfway
    through. Starlette answers range requests here, which is what makes seeking
    work at all.
    """
    from live.models import RecordingViewRecord

    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail=f"No interview '{interview_id}'.")
        path = stored_path(interview)
        status = interview.recording_status
        offset = interview.recording_offset_seconds

    if path is None or not path.exists():
        # The row and the disk disagree. Say which, rather than serving a 404
        # that reads like the interview does not exist.
        raise HTTPException(
            status_code=404,
            detail=(
                f"There is no recording file for this interview (it is "
                f"'{status}'). Nothing is being hidden: the file is not there."
            ),
        )

    byte_range = (request.headers.get("range") or "")[:64] or None
    async with get_sessionmaker()() as session:
        session.add(RecordingViewRecord(interview_id=interview_id, viewer=viewer,
                                        byte_range=byte_range, route=route))
        await session.commit()
    logger.info(f"[{interview_id}] recording viewed by {viewer} ({route}, range {byte_range or 'all'})")

    headers = {"Cache-Control": RECORDING_CACHE_CONTROL}
    if offset is not None:
        # file_second = at_seconds - offset (D-05); also in GET .../recording.
        headers["X-Recording-Offset-Seconds"] = f"{offset:g}"
    return FileResponse(path, media_type="video/mp4", filename=filename, headers=headers)


@router.get(
    "/interviews/{interview_id}/recording/video",
    dependencies=ADMIN_ONLY,
    include_in_schema=False,
)
async def stream_recording(interview_id: str, request: Request):
    """The old panel's player (/admin). The console uses
    GET /api/live/interviews/{id}/recording/media; both record every view."""
    return await serve_recording(interview_id, request, viewer="admin", route="panel",
                                 filename=f"{interview_id}.mp4")


@router.delete("/interviews/{interview_id}/recording", dependencies=ADMIN_ONLY)
async def delete_interview_recording(interview_id: str) -> dict:
    """Delete a recording before its retention window runs out.

    Unrecoverable, and the panel asks before calling this. The transcript, the
    report and the interview record are untouched: what goes is the video.
    """
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail=f"No interview '{interview_id}'.")
        if interview.recording_status not in (
            RecordingStatus.STORED,
            RecordingStatus.RECORDING,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"There is no recording to delete: this interview is "
                    f"'{interview.recording_status}'."
                ),
            )

    settings = Settings.load()
    try:
        await remove_recording(
            interview_id, status=RecordingStatus.DELETED, api_key=settings.daily_api_key
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # The row is left alone. Saying it is deleted when it is not is the one
        # outcome that is worse than failing.
        logger.error(f"[{interview_id}] recording deletion failed: {exc}")
        raise HTTPException(
            status_code=502,
            detail=(
                "The recording could not be deleted and is still there. Nothing "
                "has been changed. Try again, and if it keeps failing the file "
                "needs removing by hand."
            ),
        ) from exc

    return {"interview_id": interview_id, "recording_status": RecordingStatus.DELETED}
