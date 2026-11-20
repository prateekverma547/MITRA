"""Who is in an interview room right now, from Daily's presence API. Track H.

The interview's status says `in_progress` from the moment the candidate joins
until Mitra (or HR) closes it, and nothing in between records that the
candidate has left. Daily knows: `GET /rooms/{name}/presence` lists the
participants in the room at this moment. The screens use it to say what is
actually happening ("the candidate left the call, Mitra is closing") instead
of a status that is true but stale.

Daily's server-side list also holds hidden participants, which is why Track I
keeps every other reader off it (tests/live/test_watcher_counting.py). Here a
hidden watcher is skipped by its user_id, so the count is what the people in
the call see.

A fact for the screens only. It moves no candidate and changes no record, and
when Daily cannot be asked (no key, a timeout, an error) the answer is
`known: False` and the screens fall back to the stored status.
"""

import time
from dataclasses import dataclass

import httpx
from loguru import logger

from bot.config import Settings
from shared.branding import BOT_NAME

DAILY_API = "https://api.daily.co/v1"
TIMEOUT_SECONDS = 3.0
#: A screen polls every few seconds; one question to Daily per room per window.
CACHE_SECONDS = 4.0

#: The bot joins as "Interviewer" (app/interviews.py) or by its own name.
MITRA_NAMES = {"interviewer", BOT_NAME.lower()}
#: Track I marks the candidate's token with this user_id prefix.
CANDIDATE_MARK = "candidate:"
#: A hidden live-view watcher (Track I's live/watch.py). Daily's presence API
#: lists hidden participants too, while nobody in the call can see them, so
#: they are never counted here. Nobody watches in this product; this keeps it
#: true even if a watcher token is ever minted.
WATCHER_MARK = "watcher:"

_cache: dict[str, tuple[float, list[dict] | None]] = {}


@dataclass
class Presence:
    known: bool
    candidate: bool = False
    mitra: bool = False
    #: Everyone else: HR in an HR round, someone watching in a technical one.
    others: int = 0

    def as_dict(self) -> dict:
        return {"known": self.known, "candidate": self.candidate, "mitra": self.mitra, "others": self.others}


async def _fetch(room_name: str) -> list[dict] | None:
    key = Settings.load().daily_api_key
    if not key or not room_name:
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{DAILY_API}/rooms/{room_name}/presence", headers={"Authorization": f"Bearer {key}"}
            )
        if response.status_code != 200:
            logger.warning(f"Daily presence for {room_name}: HTTP {response.status_code}")
            return None
        data = response.json().get("data")
        return data if isinstance(data, list) else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(f"Daily presence for {room_name} failed: {exc}")
        return None


async def participants(room_name: str) -> list[dict] | None:
    """The room's participants now, or None when Daily could not be asked."""
    now = time.monotonic()
    hit = _cache.get(room_name)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    found = await _fetch(room_name)
    _cache[room_name] = (now, found)
    return found


def classify(people: list[dict] | None, candidate_name: str | None) -> Presence:
    if people is None:
        return Presence(known=False)
    seen = Presence(known=True)
    wanted = (candidate_name or "").strip().lower()
    for p in people:
        name = str(p.get("userName") or "").strip().lower()
        user_id = str(p.get("userId") or "")
        if user_id.startswith(WATCHER_MARK):
            continue
        if name in MITRA_NAMES:
            seen.mitra = True
        elif user_id.startswith(CANDIDATE_MARK) or (wanted and name == wanted):
            seen.candidate = True
        else:
            seen.others += 1
    return seen


async def in_room(room_name: str | None, candidate_name: str | None) -> Presence:
    return classify(await participants(room_name or ""), candidate_name)
