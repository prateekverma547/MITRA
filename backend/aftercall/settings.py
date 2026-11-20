"""Settings for the after-call checks, read at the moment of use (never cached).

- AFTERCALL_ENABLED  the switch. OFF unless set to 1/true/yes/on. Nothing runs
                     on real candidates until the consent wording covers it.
- AFTERCALL_PAUSED   the same truthy words pause the runner between jobs and
                     between steps of a check; so does a file named PAUSED in
                     the after-call folder (pausing without a restart).
- AFTERCALL_MODELS_DIR  where the model files are (default: aftercall/models).
- Files live under the recordings folder (app.recordings.recordings_root(), the
  setting RECORDINGS_DIR), so the same volume and the same retention cover them:
    <root>/capture/<interview_id>/manifest.json   written by the bot (Session A)
    <root>/capture/<interview_id>/candidate.wav   the candidate's own track
    <root>/aftercall/<interview_id>/status.json   AfterCallStatus (this package)
    <root>/aftercall/<interview_id>/second_voice.json  SecondVoiceSignal, if any
"""

from __future__ import annotations

import os
from pathlib import Path

from shared.contracts import RECORDING_RETENTION_DAYS

ENABLED_ENV = "AFTERCALL_ENABLED"
PAUSED_ENV = "AFTERCALL_PAUSED"
MODELS_DIR_ENV = "AFTERCALL_MODELS_DIR"
_TRUE = {"1", "true", "yes", "on"}

#: How many times a failed check is tried before it stays failed.
MAX_ATTEMPTS = 3
#: The same promise as the recordings (shared/contracts.py). Enforced by the
#: recordings sweep (app.recordings.expire_aftercall_files), not by this package.
RETENTION_DAYS = RECORDING_RETENTION_DAYS
#: Seconds between the background loop's passes.
LOOP_SECONDS = 60.0
#: Seconds after which a lock left by a dead job is taken over.
STALE_LOCK_SECONDS = 3600.0

PACKAGE_DIR = Path(__file__).resolve().parent


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def enabled() -> bool:
    return _truthy(ENABLED_ENV)


def storage_root() -> Path:
    # Imported here so this module loads without the app (the job subprocess).
    from app.recordings import recordings_root

    return recordings_root()


def capture_root() -> Path:
    return storage_root() / "capture"


def aftercall_root() -> Path:
    return storage_root() / "aftercall"


def paused() -> bool:
    return _truthy(PAUSED_ENV) or (aftercall_root() / "PAUSED").exists()


def models_dir() -> Path:
    configured = os.environ.get(MODELS_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else PACKAGE_DIR / "models"
