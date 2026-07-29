"""Daily adapter — WebRTC transport plus room/token management.

**Vendor swap point.** Both concerns live here so a transport change touches one
file: the REST side (creating rooms, minting meeting tokens) and the Pipecat
transport the bot joins with.
"""

from dataclasses import dataclass

import aiohttp
from pipecat.transports.daily.transport import DailyParams, DailyTransport

DAILY_API_URL = "https://api.daily.co/v1"


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
            "start_video_off": True,
            "start_audio_off": False,
            "enable_prejoin_ui": False,
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
            # Daily's own transcription is off: our STT adapter is the single
            # source of transcript truth.
            transcription_enabled=False,
        ),
    )
