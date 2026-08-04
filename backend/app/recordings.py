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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from loguru import logger
from sqlalchemy import select

from app.auth import require_admin
from app.db import Interview, RecordingStatus, get_sessionmaker
from bot.config import SESSIONS_DIR, Settings
from bot.services.daily import delete_recording, download_recording, find_recording
from shared.contracts import RECORDING_RETENTION_DAYS

router = APIRouter(tags=["recordings"])

#: Same auth as the rest of the panel, deliberately. Anyone with the admin
#: password can watch any recording, which is the access model this product has
#: today for transcripts and reports as well. Narrowing it is real work and it is
#: not this change.
ADMIN_ONLY = [Depends(require_admin)]

#: Recordings live under the directory that already holds local run artifacts,
#: which is gitignored. One subdirectory so they do not mix with the session
#: JSON files.
RECORDINGS_DIR = SESSIONS_DIR / "recordings"

#: How often the sweep runs. Nothing here is urgent: a recording that appears
#: five minutes later is fine, and a retention window measured in days does not
#: need a minute-accurate deletion.
SWEEP_INTERVAL_SECONDS = 5 * 60

#: How long after an interview ends to keep waiting for Daily to produce the
#: file before calling it lost. Generous, because compositing a long call is not
#: instant, and the cost of waiting is a panel that says "still being prepared"
#: while the cost of giving up early is a recording thrown away that had arrived.
COLLECT_GIVE_UP_HOURS = 2

#: Set this to switch the background loop off. The tests drive the sweep directly
#: so they can watch what it does; a loop running underneath them would make
#: calls to Daily nobody asked for.
SWEEP_DISABLED_ENV = "RECORDING_SWEEP_DISABLED"


def retention_deadline(from_time: datetime) -> datetime:
    return from_time + timedelta(days=RECORDING_RETENTION_DAYS)


def recording_file(interview_id: str) -> Path:
    return RECORDINGS_DIR / f"{interview_id}.mp4"


def stored_path(interview: Interview) -> Path | None:
    """The absolute path of a stored recording, or None if there is not one.

    The stored value is relative and is joined onto `SESSIONS_DIR` here. It is
    checked for containment before use: it comes out of the database, and a path
    from a database that is allowed to escape its directory is how a read of an
    interview record turns into a read of anything on the host.
    """
    if not interview.recording_path:
        return None
    candidate = (SESSIONS_DIR / interview.recording_path).resolve()
    root = SESSIONS_DIR.resolve()
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
        logger.warning(f"[{interview_id}] downloading the recording failed: {exc}")
        return "error"

    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            return "error"
        interview.recording_status = RecordingStatus.STORED
        interview.recording_id = found.id
        interview.recording_path = str(destination.relative_to(SESSIONS_DIR))
        interview.recording_bytes = written
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
    counts = {"stored": 0, "waiting": 0, "unavailable": 0, "error": 0, "expired": 0}
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

    return counts


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
    return not os.environ.get(SWEEP_DISABLED_ENV)


# -- routes ------------------------------------------------------------------


@router.get(
    "/interviews/{interview_id}/recording/video",
    dependencies=ADMIN_ONLY,
    include_in_schema=False,
)
async def stream_recording(interview_id: str):
    """Serve the recording from our own disk.

    A `FileResponse` rather than a redirect to Daily, for two reasons. The file
    is ours by the time anyone can play it, and Daily's own links are presigned
    and short lived, so a page open for half an hour would stop working halfway
    through. Starlette answers range requests here, which is what makes seeking
    work at all.
    """
    async with get_sessionmaker()() as session:
        interview = await session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail=f"No interview '{interview_id}'.")
        path = stored_path(interview)
        status = interview.recording_status

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

    return FileResponse(path, media_type="video/mp4", filename=f"{interview_id}.mp4")


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
