"""Local dev harness: create a Daily room, spawn the bot, print the join URL.

    uv run python scripts/dev_interview.py

Then open the printed URL in a browser to join via Daily's prebuilt UI and talk
to the bot. Ctrl-C stops the bot and dumps the transcript and latency summary.

This is Milestone 1 scaffolding only. From Milestone 3 the FastAPI backend
creates rooms and spawns bots; this script exists so the voice loop can be
exercised by ear without any of that.
"""

import argparse
import asyncio
import signal
import sys
import uuid
from pathlib import Path

# Make `bot` importable when running this file directly from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

from bot.config import MissingConfig, Settings  # noqa: E402
from bot.services.daily import create_room  # noqa: E402

ROOM_TTL_SECONDS = 60 * 60  # 1 hour — long enough for a full 40-minute session.

# Time allowed for the bot to load its models, verify credentials and join.
BOT_STARTUP_GRACE_SECONDS = 6.0


async def _wait_for_bot_startup(process) -> None:
    """Give the bot time to start, returning early if it exits first."""
    try:
        await asyncio.wait_for(process.wait(), timeout=BOT_STARTUP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        pass  # Still running, as expected.


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create a room and run one interview.")
    parser.add_argument(
        "--no-brain",
        action="store_true",
        help="Run the pre-wiring Milestone 1 path (static prompt). For the latency baseline.",
    )
    parser.add_argument(
        "--blueprint-id",
        default=None,
        help=(
            "Fixture name (pm_senior, sre_staff) or a cand_... id to interview a "
            "real candidate against their generated blueprint."
        ),
    )
    args = parser.parse_args()
    no_brain = args.no_brain
    blueprint_id = args.blueprint_id

    try:
        settings = Settings.load()
    except MissingConfig as exc:
        logger.error(str(exc))
        return 1

    session_id = f"dev-{uuid.uuid4().hex[:8]}"

    logger.info("Creating Daily room...")
    room = await create_room(
        api_key=settings.daily_api_key,
        expiry_seconds=ROOM_TTL_SECONDS,
        privacy="public",
    )

    bot_args = ["--room-url", room.url, "--session-id", session_id]
    if no_brain:
        bot_args.append("--no-brain")
    if blueprint_id:
        bot_args += ["--blueprint-id", blueprint_id]

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bot.run_bot",
        *bot_args,
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    # Let the bot verify credentials and join before we print anything, so the
    # join URL lands at the bottom of the terminal instead of scrolling away
    # under startup logs — and so we never advertise a room whose bot died.
    await _wait_for_bot_startup(process)
    if process.returncode is not None:
        logger.error("Bot exited during startup — see the error above. Not joining.")
        return process.returncode

    print("\n" + "=" * 72)
    print("  JOIN THE INTERVIEW HERE:")
    print(f"  {room.url}")
    print("=" * 72)
    print(f"  session: {session_id}")
    print(f"  mode:    {'M1 static prompt (baseline)' if no_brain else 'sectioned brain'}")
    print(f"  running: {blueprint_id or 'pm_senior (role-level fixture)'}")
    print("  Open the URL above in a browser, allow the mic, and start talking.")
    print("  Press Ctrl-C here to end the session and dump the transcript.")
    print("=" * 72 + "\n")

    # Forward Ctrl-C to the bot so it can write its artifacts before exiting,
    # rather than being killed out from under its transcript dump.
    loop = asyncio.get_running_loop()

    def request_stop():
        if process.returncode is None:
            logger.info("Stopping bot...")
            process.send_signal(signal.SIGINT)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_stop)

    return await process.wait()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
