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
