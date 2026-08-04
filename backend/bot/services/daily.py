"""Daily adapter — WebRTC transport plus room/token management.

**Vendor swap point.** Every concern lives here so a transport change touches one
file: the REST side (creating rooms, minting meeting tokens, fetching and
deleting recordings) and the Pipecat transport the bot joins with.

**On recording.** Daily composites the call in its own cloud and stores the
result in its own S3 bucket. There is no mode that writes to our disk while the
call runs, so a recording that is meant to live locally has to be pulled down
afterwards; `app/recordings.py` does that and then deletes Daily's copy. What
this module offers is the four operations that involves: turn recording on for a
room, start and stop it in the call, find the finished file, and delete it.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from loguru import logger
from pipecat.transports.daily.transport import DailyParams, DailyTransport

DAILY_API_URL = "https://api.daily.co/v1"

#: How long to wait for Daily to acknowledge a recording start before giving up
#: and running the interview without one. Short on purpose: this is competing
#: with a candidate sitting in a silent room waiting to be spoken to, and the
#: interview is the product while the recording is only evidence about it.
RECORDING_START_TIMEOUT_SECONDS = 8.0

#: Streaming download chunk. A forty-minute recording is tens of megabytes and
#: must never be held in memory in one piece.
_DOWNLOAD_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True)
class DailyRoom:
    """A created Daily room."""

    name: str
    url: str
    expires_at: int


async def create_room(
    *,
    api_key: str,
    expiry_seconds: int,
    privacy: str = "public",
) -> DailyRoom:
    """Create a short-lived Daily room via the REST API.

    Milestone 1 uses `privacy="public"` so the developer can join through
    Daily's prebuilt UI without minting a token. From Milestone 5 rooms must be
    private and candidates only ever reach them through a token our backend
    mints — see CLAUDE.md.

    `exp` is enforced by Daily; `eject_at_room_exp` makes sure participants are
    actually removed rather than lingering in an expired room.

    `enable_recording` grants the room permission to be recorded. It does not
    start anything: the bot calls `start_recording` once it has joined.
    """
    # Imported here so module import stays cheap for callers that only need the
    # transport (e.g. the bot subprocess).
    import time

    expires_at = int(time.time()) + expiry_seconds
    payload = {
        "privacy": privacy,
        "properties": {
            "exp": expires_at,
            "eject_at_room_exp": True,
            # The candidate's camera is on. It is a talking-head for a person to
            # review later, not something the interviewer watches: the bot does
            # not subscribe to video at all (see `build_transport`), so nothing
            # in this system decodes it. Quality is capped at capture time in the
            # browser rather than left at whatever the webcam offers.
            "start_video_off": False,
            "start_audio_off": False,
            "enable_prejoin_ui": False,
            # Permission to record, not an instruction to. Nothing starts until
            # the bot asks, which it does once it is in the room, so a room that
            # is booked and never used records nothing.
            "enable_recording": "cloud",
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{DAILY_API_URL}/rooms",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        ) as response:
            body = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Daily room creation failed ({response.status}): {body}")

    return DailyRoom(name=body["name"], url=body["url"], expires_at=expires_at)


async def create_meeting_token(
    *,
    api_key: str,
    room_name: str,
    expiry_seconds: int,
    is_owner: bool = False,
    user_name: str | None = None,
) -> str:
    """Mint a short-lived meeting token for a room.

    Not needed for a public Milestone 1 room, but the bot will need one as soon
    as rooms go private, and candidates will need one in Milestone 5.
    """
    import time

    properties: dict = {
        "room_name": room_name,
        "exp": int(time.time()) + expiry_seconds,
        "is_owner": is_owner,
    }
    if user_name:
        properties["user_name"] = user_name

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{DAILY_API_URL}/meeting-tokens",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"properties": properties},
        ) as response:
            body = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Daily token creation failed ({response.status}): {body}")

    return body["token"]


def build_transport(
    *,
    room_url: str,
    token: str | None,
    bot_name: str,
) -> DailyTransport:
    """The transport the bot joins the room with.

    Audio in and out only. Video output is off — the interviewer is a voice, and
    publishing an empty camera track wastes bandwidth on a 40-minute call.

    **The bot does not receive video either, and that is the whole reason turning
    the candidate's camera on is safe.** `video_in_enabled` is stated rather than
    left to its default, because it is load-bearing: a bot that subscribed would
    decode a video stream for forty minutes inside a container whose concurrency
    limit is already set by unmeasured CPU, and it would be spending that on a
    picture nothing looks at. The interviewer works from the transcript. Audio is
    what the assessment rests on and it must never queue behind video.

    Note: VAD and turn-taking are NOT configured here. In Pipecat 1.6.0 they
    live on the LLM user aggregator (`bot/turn_taking.py`), not on the
    transport.
    """
    return DailyTransport(
        room_url=room_url,
        token=token,
        bot_name=bot_name,
        params=DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            camera_out_enabled=False,
            video_in_enabled=False,
            # Daily's own transcription is off: our STT adapter is the single
            # source of transcript truth.
            transcription_enabled=False,
        ),
    )


# -- recording, in the call --------------------------------------------------
#
# Both of these swallow every failure by design. A recording is evidence about
# an interview and the interview is the product: an interview that runs without
# a recording is a diminished record, while an interview that dies because a
# recording could not start is a candidate's time thrown away. Neither returns a
# bare bool for success, because "it failed" without saying how is exactly the
# kind of report this codebase has been wrong about before.


async def start_recording(transport: DailyTransport) -> str | None:
    """Start recording the call. Returns an error to record, or None on success.

    Never raises. A returned string is meant to be written to the interview
    record and shown to whoever opens it, so it says what went wrong rather than
    just that something did.
    """
    try:
        _, error = await asyncio.wait_for(
            transport.start_recording(),
            timeout=RECORDING_START_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return (
            f"Daily did not acknowledge the recording within "
            f"{RECORDING_START_TIMEOUT_SECONDS:.0f}s, so the interview went ahead "
            f"without one."
        )
    except Exception as exc:  # noqa: BLE001
        return f"The recording could not be started: {exc}"

    if error is not None:
        return f"Daily refused to start the recording: {error}"
    return None


async def stop_recording(transport: DailyTransport) -> str | None:
    """Stop recording. Returns an error to log, or None.

    **Not the only thing that ends a recording, and deliberately so.** This runs
    in the bot's `finally` block, which does not run when a process is killed:
    a redeploy, an OOM kill, or capacity teardown all skip it. Daily closes the
    recording by itself when the last participant leaves the room, which was
    verified against the live API by leaving without calling this at all: the
    file came back `finished` within five seconds. So this is the tidy path, and
    the room emptying is the one that catches everything else.
    """
    try:
        error = await transport.stop_recording()
    except Exception as exc:  # noqa: BLE001
        return f"The recording could not be stopped cleanly: {exc}"
    return f"Daily reported a problem stopping the recording: {error}" if error else None


# -- recording, over REST ----------------------------------------------------


@dataclass(frozen=True)
class DailyRecording:
    """A finished recording sitting in Daily's storage."""

    id: str
    room_name: str
    #: Epoch seconds. Daily's own view of when the recording began.
    start_ts: int
    duration_seconds: int


async def find_recording(*, api_key: str, room_name: str) -> DailyRecording | None:
    """The finished recording for a room, or None if there is not one yet.

    **A finished recording is proof the call is over**, because Daily only
    composites once the session ends. That is why collection keys off this rather
    than off our own interview status: a bot that was killed never wrote a status,
    and a recording nobody knows to collect is one nobody deletes either.

    An in-progress recording returns None. So does a room that was never
    recorded. The caller distinguishes "not yet" from "never" by how long it has
    been waiting, not by asking here.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{DAILY_API_URL}/recordings",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"room_name": room_name, "limit": 10},
        ) as response:
            body = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Daily recording lookup failed ({response.status}): {body}")

    finished = [row for row in body.get("data", []) if row.get("status") == "finished"]
    if not finished:
        return None

    # One interview is one call is one recording. If a room somehow produced
    # several, the earliest is the one that starts where the transcript starts,
    # and the offset stored against the interview is only meaningful for that one.
    row = min(finished, key=lambda r: r.get("start_ts", 0))
    return DailyRecording(
        id=row["id"],
        room_name=row.get("room_name", room_name),
        start_ts=int(row.get("start_ts", 0)),
        duration_seconds=int(row.get("duration") or 0),
    )


async def download_recording(*, api_key: str, recording_id: str, destination: Path) -> int:
    """Stream a recording to disk. Returns the byte count written.

    Written to a `.part` file and renamed only once the whole body has arrived,
    so a download cut off halfway cannot leave something at the real path that
    looks like a recording and plays as a broken file.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{DAILY_API_URL}/recordings/{recording_id}/access-link",
            headers={"Authorization": f"Bearer {api_key}"},
        ) as response:
            body = await response.json()
            if response.status != 200:
                raise RuntimeError(f"Daily access link failed ({response.status}): {body}")
            url = body.get("download_link")
            if not url:
                raise RuntimeError("Daily returned an access link with no download URL.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        written = 0
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(f"Downloading the recording failed ({response.status}).")
            with partial.open("wb") as handle:
                async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_BYTES):
                    handle.write(chunk)
                    written += len(chunk)

    if written == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError("The recording downloaded as an empty file.")

    partial.replace(destination)
    return written


async def delete_recording(*, api_key: str, recording_id: str) -> None:
    """Delete a recording from Daily, and confirm it is actually gone.

    Raises if it is not. A delete that reports success while the file remains is
    the failure mode this whole change is written against, so the confirmation
    is not optional: Daily's own response has to say `deleted`, and a recording
    that was already gone counts, because the end state is what was asked for.
    """
    async with aiohttp.ClientSession() as session:
        async with session.delete(
            f"{DAILY_API_URL}/recordings/{recording_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        ) as response:
            body = await response.json()
            if response.status == 404:
                logger.info(f"recording {recording_id} was already gone from Daily")
                return
            if response.status != 200 or not body.get("deleted"):
                raise RuntimeError(
                    f"Daily did not confirm the deletion of {recording_id} "
                    f"({response.status}): {body}"
                )
