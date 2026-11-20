"""Hidden watchers are never counted as participants.

Session D found (D-04) that Daily's SERVER-side data counts a hidden watcher:
the REST presence endpoint lists it and a recording's `max_participants`
includes it. The clients' view does not (C-53): a hidden participant is not in
anyone's participants() and raises no joined or left event. So:

1. Nothing in our code may read Daily's server-side participant data (the
   presence endpoint, `max_participants`, the hidden count). A source scan
   enforces it, and is shown able to find such a read.
2. The one place that counts people in the call, the bot's RoomPresence, is fed
   by the client's events; if a watcher-shaped participant ever did reach it
   (a token minted without hasPresence false, say), it is classified a watcher
   and never counted.
3. The recording lookup keeps nothing about participants.
"""

import re
from dataclasses import fields
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
FRONTEND = BACKEND.parent / "frontend"

#: Reads of Daily's server-side participant data, in Python or TypeScript.
SERVER_SIDE_COUNTS = re.compile(
    r"""(/presence\b|max_participants|participantCounts\s*\(|participant_counts\s*\(|"""
    r"""["']participant-counts-updated["'])""")


def _code_lines(path: Path):
    """Lines that are code, not comments or docstrings. A docstring is entered
    on a line that starts with triple quotes and left on the next line that
    CONTAINS them, wherever they are (a closing at the end of a text line was
    missed once, and the scan then skipped the rest of the file; C-87)."""
    in_doc: str | None = None
    for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
        stripped = line.strip()
        if in_doc:
            if in_doc in stripped:
                in_doc = None
            continue
        quote = next((q for q in ('"""', "'''") if stripped.startswith(q)), None)
        if quote:
            if stripped.count(quote) < 2:
                in_doc = quote  # opens here, closes on a later line
            continue
        if stripped.startswith(("#", "//", "*", "/*")):
            continue
        yield number, line


def scan(paths) -> list[str]:
    found = []
    for path in paths:
        for number, line in _code_lines(path):
            if SERVER_SIDE_COUNTS.search(line):
                found.append(f"{path}:{number}: {line.strip()}")
    return found


def _sources() -> list[Path]:
    backend = [p for p in BACKEND.rglob("*.py")
               if not any(part in (".venv", "tests", "__pycache__") for part in p.parts)]
    frontend = [p for d in ("candidate-app/src", "console/src", "ui/src") if (FRONTEND / d).exists()
                for p in (FRONTEND / d).rglob("*") if p.suffix in (".ts", ".tsx")]
    return backend + frontend


#: The one reader allowed, with its reason. Track H's room status
#: (hiring/presence.py) must know whether the candidate is still in the call,
#: for the HR round and for the MITRA round's status page, and only Daily's
#: presence endpoint can say so from the server. It skips hidden watchers by
#: their `watcher:` user_id (tests/hiring/test_presence.py), so the concern
#: above does not apply to it.
ALLOWED = {BACKEND / "hiring" / "presence.py"}


def test_no_code_reads_dailys_server_side_participant_data():
    sources = _sources()
    assert len(sources) > 100  # the scan really covers the code
    assert scan([p for p in sources if p not in ALLOWED]) == []


def test_the_scan_finds_such_a_read_when_there_is_one(tmp_path):
    """Positive control: a check that finds nothing is unproven until shown able to find something."""
    planted = tmp_path / "planted.py"
    planted.write_text('"""Docs may say max_participants."""\n'
                       "# a comment about /presence is fine\n"
                       "def f():\n"
                       '    """A docstring over two lines,\n'
                       '    closing at the end of a text line, as most in this repo do."""\n'
                       "count = recording['max_participants']\n"
                       "r = await get(f'{DAILY_API_URL}/rooms/{room}/presence')\n")
    ts = tmp_path / "planted.ts"
    ts.write_text("// participantCounts() in a comment is fine\nconst n = call.participantCounts().hidden;\n"
                  "call.on('participant-counts-updated', f);\n")
    assert [line.split(": ", 1)[1] for line in scan([planted, ts])] == [
        "count = recording['max_participants']",
        "r = await get(f'{DAILY_API_URL}/rooms/{room}/presence')",
        "const n = call.participantCounts().hidden;",
        "call.on('participant-counts-updated', f);",
    ]


# -- the bot's own count -----------------------------------------------------------------------

CANDIDATE = {"id": "p-cand", "info": {"userId": "candidate:int_count000001", "isOwner": False}}
WATCHER = {"id": "p-watch", "info": {"userId": "watcher:admin", "userName": "Live view", "isOwner": False}}


def _presence():
    from bot.presence import RoomPresence, candidate_user_id

    return RoomPresence(candidate_mark=candidate_user_id("int_count000001"))


@pytest.mark.parametrize("order", [("candidate", "watcher"), ("watcher", "candidate")])
def test_a_watcher_reaching_the_bot_is_never_counted(order):
    presence = _presence()
    for who in order:
        presence.observe_join(CANDIDATE if who == "candidate" else WATCHER)
    assert presence.candidate_present
    assert presence.peak_others == 1 and not presence.others_in_the_room
    presence.observe_leave(WATCHER, "left")
    assert presence.disconnects == 0 and presence.candidate_present
    assert presence.summary()["present_at_end"] == 1


def test_a_watcher_alone_is_an_empty_room():
    presence = _presence()
    presence.observe_join(WATCHER)
    assert not presence.candidate_present and presence.peak_others == 0
    presence.observe_leave(WATCHER, "left")
    assert presence.disconnects == 0


# -- the recording lookup --------------------------------------------------------------------


async def test_the_recording_lookup_keeps_nothing_about_participants(monkeypatch):
    """Daily's recording object says max_participants 4 with a watcher in the
    room (D-04). Our lookup keeps id, room, start and duration only."""
    from bot.services import daily

    class Response:
        status = 200

        async def json(self):
            return {"data": [{"id": "rec1", "room_name": "r", "status": "finished", "start_ts": 1,
                              "duration": 98, "max_participants": 4}]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            return Response()

    monkeypatch.setattr(daily.aiohttp, "ClientSession", Session)
    found = await daily.find_recording(api_key="k", room_name="r")
    assert {f.name for f in fields(found)} == {"id", "room_name", "start_ts", "duration_seconds"}
