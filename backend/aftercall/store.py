"""Files of the after-call checks: the capture manifest (read), the status and the
signal (written), and a per-interview lock.

Retention is NOT here: the recordings sweep deletes `capture/<id>` and
`aftercall/<id>` 10 days from the interview's start in the database
(`app.recordings.expire_aftercall_files`, C-122, C-137). One rule, one clock.

Every write goes to a temporary file in the same folder and is moved into place,
so a reader never sees half a file and a crash never leaves one. Interview ids
are checked before they become folder names, and the WAV path from a manifest
must stay inside the capture folder: both come from outside this package.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from aftercall import settings
from shared.interview_contracts import AfterCallStatus, CaptureManifest, IntegrityEvent, SecondVoiceSignal

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BadInput(ValueError):
    """An id or a manifest that cannot be used safely."""


def safe_id(interview_id: str) -> str:
    if not _SAFE_ID.match(interview_id or ""):
        raise BadInput("interview id is not a plain identifier")
    return interview_id


def capture_dir(interview_id: str) -> Path:
    return settings.capture_root() / safe_id(interview_id)


def result_dir(interview_id: str) -> Path:
    return settings.aftercall_root() / safe_id(interview_id)


def manifest_path(interview_id: str) -> Path:
    return capture_dir(interview_id) / "manifest.json"


def status_path(interview_id: str) -> Path:
    return result_dir(interview_id) / "status.json"


def signal_path(interview_id: str) -> Path:
    return result_dir(interview_id) / "second_voice.json"


def events_path(interview_id: str) -> Path:
    return result_dir(interview_id) / "integrity_events.json"


def attempts_path(interview_id: str) -> Path:
    return result_dir(interview_id) / "attempts.json"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# -- the manifest (Session A writes it) ---------------------------------------------------------


def read_manifest(interview_id: str) -> CaptureManifest | None:
    p = manifest_path(interview_id)
    if not p.is_file():
        return None
    m = CaptureManifest.model_validate_json(p.read_text())
    if m.interview_id != interview_id:
        raise BadInput("the manifest names a different interview")
    return m


def audio_file(manifest: CaptureManifest) -> Path:
    """The candidate WAV's absolute path, only if it stays inside the capture folder."""
    root = settings.capture_root().resolve()
    p = (root / manifest.candidate_audio.relative_path).resolve()
    if root not in p.parents:
        raise BadInput("the audio path leaves the capture folder")
    return p


# -- status, signal, events, attempts ---------------------------------------------------------------


def read_status(interview_id: str) -> AfterCallStatus | None:
    p = status_path(interview_id)
    return AfterCallStatus.model_validate_json(p.read_text()) if p.is_file() else None


def write_status(status: AfterCallStatus) -> None:
    _write(status_path(status.interview_id), status.model_dump_json(indent=1))


def read_signal(interview_id: str) -> SecondVoiceSignal | None:
    p = signal_path(interview_id)
    return SecondVoiceSignal.model_validate_json(p.read_text()) if p.is_file() else None


def write_signal(signal: SecondVoiceSignal) -> None:
    _write(signal_path(signal.interview_id), signal.model_dump_json(indent=1))


def remove_signal(interview_id: str) -> None:
    signal_path(interview_id).unlink(missing_ok=True)
    events_path(interview_id).unlink(missing_ok=True)


def read_events(interview_id: str) -> list[IntegrityEvent]:
    p = events_path(interview_id)
    return [IntegrityEvent.model_validate(e) for e in json.loads(p.read_text())] if p.is_file() else []


def write_events(interview_id: str, events: list[IntegrityEvent]) -> None:
    _write(events_path(interview_id), json.dumps([e.model_dump(mode="json") for e in events], indent=1))


def read_attempts(interview_id: str) -> dict[str, int]:
    p = attempts_path(interview_id)
    return json.loads(p.read_text()) if p.is_file() else {}


def write_attempts(interview_id: str, attempts: dict[str, int]) -> None:
    _write(attempts_path(interview_id), json.dumps(attempts))


# -- one job per interview at a time ----------------------------------------------------------------


class Lock:
    """An exclusive lock file per interview. A lock older than STALE_LOCK_SECONDS,
    or held by a process that no longer exists, is taken over."""

    def __init__(self, interview_id: str):
        self.path = result_dir(interview_id) / ".lock"
        self.held = False

    def _stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
            pid = int(self.path.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return True
        if age > settings.STALE_LOCK_SECONDS:
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                return False
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()} {time.time()}")
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
