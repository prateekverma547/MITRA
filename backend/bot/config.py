"""Environment configuration for the bot process.

Keys are read from the environment only — never hardcoded, never committed.
See `.env.example` at the repo root.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# Repo root: backend/bot/config.py -> backend/bot -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_FILE = REPO_ROOT / ".env"

load_dotenv(ENV_FILE, override=False)


def _warn_about_ignored_env_files() -> None:
    """Say so when a .env exists somewhere plausible and is not the one loaded.

    **The failure it replaces was silence.** The path above is derived from this
    file's own location, so a `.env` in `backend/` — which is where you would
    naturally put it, and where the server is actually run from — is read by
    nothing. No warning, no log line, just a 500 on first login and twenty
    minutes finding out why.

    Three places are checked, and only three. The repo root, because that is the
    one that works and its absence is worth saying out loud. `backend/`, because
    it is the working directory for `uv run` and for every script in this repo,
    so it is where a shell-completed path lands. And `backend/bot/`, because it
    is next to this file and next to `.env.example`'s nearest relative, which is
    where somebody debugging this reaches next. Anywhere further afield is a
    guess, and a warning that fires on guesses trains people to ignore warnings.

    **Which file is loaded does not change here.** Changing the path silently
    would break whatever is currently working on somebody else's machine, and
    this is a diagnostic, not a migration.
    """
    ignored = [
        candidate
        for candidate in (REPO_ROOT / "backend" / ".env", Path(__file__).with_name(".env"))
        if candidate.is_file()
    ]
    if ignored:
        for path in ignored:
            logger.warning(
                f"Ignoring {path}: configuration is only read from {ENV_FILE}. "
                f"Move it, or copy the keys across."
            )
    elif not ENV_FILE.is_file():
        logger.warning(
            f"No .env at {ENV_FILE}. Copy .env.example there and fill it in; "
            f"nothing else on this machine is read."
        )


_warn_about_ignored_env_files()


class MissingConfig(RuntimeError):
    """Raised when a required environment variable is absent.

    Raised eagerly at startup rather than mid-interview: a bot that dies four
    minutes into a candidate's session is far worse than one that never starts.
    """


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissingConfig(
            f"{name} is not set in this process's environment. Locally, put it in "
            f"the repo-root .env (copy .env.example). In Docker, pass it with "
            f"`docker run --env-file .env ...` or `-e {name}=...`. On Railway or "
            f"AWS, set it as a service variable. A .env file is never baked into "
            f"the image."
        )
    return value


def daily_api_key() -> str:
    """The one key the recording sweep and the recording delete route need.

    Separate from `Settings.load()` on purpose: those paths only talk to Daily, and
    requiring the OpenAI and ElevenLabs keys too meant a container missing an
    unrelated key could not clean up recordings at all.
    """
    return _required("DAILY_API_KEY")


# Model tiering (CLAUDE.md). Three roles, never collapsed into one.
#
# Live conversation must never use a reasoning model: it sits on the
# time-to-first-audio path, where thinking tokens are latency the candidate
# hears as dead air. Blueprint generation and feedback scoring are both
# off-path, so they get the reasoning tier — that is where the product's
# judgement actually lives.
DEFAULT_LLM_MODEL = "gpt-4.1-mini"  # live conversation
DEFAULT_BLUEPRINT_MODEL = "gpt-4.1"  # blueprint generation (M2)
DEFAULT_FEEDBACK_MODEL = "gpt-4.1"  # feedback scoring (M4)


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for a bot process."""

    openai_api_key: str
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    daily_api_key: str

    llm_model: str = DEFAULT_LLM_MODEL
    blueprint_model: str = DEFAULT_BLUEPRINT_MODEL
    feedback_model: str = DEFAULT_FEEDBACK_MODEL

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            openai_api_key=_required("OPENAI_API_KEY"),
            elevenlabs_api_key=_required("ELEVENLABS_API_KEY"),
            elevenlabs_voice_id=_required("ELEVENLABS_VOICE_ID"),
            daily_api_key=_required("DAILY_API_KEY"),
            llm_model=os.environ.get("OPENAI_LLM_MODEL") or DEFAULT_LLM_MODEL,
            blueprint_model=os.environ.get("OPENAI_BLUEPRINT_MODEL") or DEFAULT_BLUEPRINT_MODEL,
            feedback_model=os.environ.get("OPENAI_FEEDBACK_MODEL") or DEFAULT_FEEDBACK_MODEL,
        )


# Where local-run transcripts and metrics land. Gitignored.
SESSIONS_DIR = REPO_ROOT / "backend" / "sessions"


#: The one switch that turns interview recording on. Absent means off.
#:
#: **Off by default, and that is the safe direction.** Daily bills every recorded
#: minute and then bills again to store it, so a recording that starts by
#: accident costs money for as long as nobody notices. A recording that fails to
#: start costs a reviewer the ability to watch one interview back. Those are not
#: the same size of mistake.
#:
#: Everything the feature needs is still here: the storage path, the sweep,
#: retention, deletion, playback and the tests. This flag governs one thing only,
#: whether a recording is ever started, and it is deliberately not consulted
#: anywhere else. In particular **the sweep does not read it**: recordings made
#: before it was switched off still have to be collected from Daily and deleted
#: there, and a flag that stopped that would strand them and keep billing.
#:
#: It lives here rather than in `app/recordings.py`, next to the sweep's own
#: switch, because two separate processes have to agree on it: the bot subprocess
#: decides whether to record, and the web app decides which consent notice to
#: show. This module is the only configuration both of them already import, and
#: it stays free of FastAPI, which the bot has no reason to load.
RECORDING_ENABLED_ENV = "RECORDING_ENABLED"

#: What counts as on. Anything else, including "false", "0" and the empty string,
#: is off, so a half-set variable fails towards not spending money.
_TRUTHY = {"1", "true", "yes", "on"}


#: The one switch that turns the spoken acknowledgement on. Absent means off.
#:
#: **Off by default because it was built for a gap that did not exist.** The
#: twelve seconds it was meant to cover were an artefact of reading transcript
#: timestamps, which mark when a turn *finished*, so the interval between two of
#: them is the second one being spoken. Composition never exceeded 1943ms.
#:
#: Left in the code rather than deleted. It could be worth rebuilding if it can
#: be made to fire on what was actually just said and never over a candidate who
#: is still talking, and neither is true today: on a real interview it spoke
#: 0.2s after the candidate began a sentence, and elsewhere reflected back a
#: phrase ninety seconds out of date.
FILLER_ENABLED_ENV = "FILLER_ENABLED"


#: The one switch that turns term probing OFF. Absent means on.
#:
#: **The polarity is inverted against the two flags above, deliberately.** Those
#: guard features that are off until somebody decides to pay for them. This
#: guards a feature that has shipped in every measurement ever taken here, so
#: absent has to mean today's behaviour or the flag would silently change the
#: product.
#:
#: It exists because nobody knows what term probing is worth. It was added
#: because the interviewer never followed anything up — mechanism questions ran
#: at 2 in 39 real opportunities before it — and repetition and cross-section
#: drift arrived with it. Five attempts to separate the two have failed, four of
#: them on wrong diagnoses, and every one was argued without a baseline for the
#: product without the feature. This makes that baseline measurable.
#:
#: Not a deprecation. Nothing about term probing is removed and the default is
#: unchanged.
TERM_PROBING_DISABLED_ENV = "TERM_PROBING_DISABLED"


def term_probing_disabled() -> bool:
    """Whether to stop offering the interviewer a term to ask about.

    Read at the moment it is needed, like the two above, so a test or a
    comparison run can set it and the next call sees it.
    """
    return os.environ.get(TERM_PROBING_DISABLED_ENV, "").strip().lower() in _TRUTHY


def filler_enabled() -> bool:
    """Whether the interviewer speaks while the next question is composed.

    Read at the moment it is needed, like `recording_enabled`, so a test can set
    it and the next call sees it.
    """
    return os.environ.get(FILLER_ENABLED_ENV, "").strip().lower() in _TRUTHY


def recording_enabled() -> bool:
    """Whether interviews are recorded. Read at the moment it is needed.

    Not cached and not resolved at import, so a test can set it and the next
    call sees it, and so the consent notice and the bot can never be answering
    from two different snapshots of the environment.
    """
    return os.environ.get(RECORDING_ENABLED_ENV, "").strip().lower() in _TRUTHY


#: Lets the candidate's camera feed the hearing check: when lips are judged still
#: during speech that no words came back for, that speech is not counted as unheard.
#: The camera can only remove a "hearing lost" count, never add one.
#:
#: **Off by default.** Turning it on sends the candidate's video to the bot and
#: needs the candidate consent notice to say so first (see docs/DECISIONS.md, the
#: camera hearing check entry). Do not enable it in production before that notice
#: is updated.
CAMERA_HEARING_ENV = "CAMERA_HEARING_CHECK"


def camera_hearing_enabled() -> bool:
    """Whether the candidate's camera is used for the hearing check. Read when needed."""
    return os.environ.get(CAMERA_HEARING_ENV, "").strip().lower() in _TRUTHY
