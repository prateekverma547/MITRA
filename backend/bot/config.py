"""Environment configuration for the bot process.

Keys are read from the environment only — never hardcoded, never committed.
See `.env.example` at the repo root.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Repo root: backend/bot/config.py -> backend/bot -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env", override=False)


class MissingConfig(RuntimeError):
    """Raised when a required environment variable is absent.

    Raised eagerly at startup rather than mid-interview: a bot that dies four
    minutes into a candidate's session is far worse than one that never starts.
    """


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissingConfig(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


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
