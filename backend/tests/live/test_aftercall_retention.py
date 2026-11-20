"""Session D's after-call files follow the recordings' retention (wave 1).

Layout from backend/aftercall/settings.py: <recordings folder>/capture/<id>/
and <recordings folder>/aftercall/<id>/. The sweep deletes them 10 days after
the interview (as the recordings), or when the interview no longer exists;
it leaves everything younger alone and never reaches outside the folder.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import recordings
from tests.live.support import fresh_db, seed_interview

REPO = Path(__file__).resolve().parents[3]


async def test_after_call_folders_are_deleted_at_retention_and_young_ones_kept(tmp_path, monkeypatch):
    await fresh_db(tmp_path, monkeypatch, "aftercall.db")
    root = tmp_path / "recordings"
    monkeypatch.setenv(recordings.RECORDINGS_DIR_ENV, str(root))
    old, young = "int_after_old001", "int_after_young1"
    await seed_interview(old, status="completed", started_ago=11 * 86400, ended_ago=11 * 86400 - 1800)
    await seed_interview(young, candidate_id="c_young", status="completed", started_ago=3600, ended_ago=1800)
    for iid in (old, young, "int_after_gone01"):
        for name in recordings.AFTERCALL_FOLDERS:
            (root / name / iid).mkdir(parents=True)
            (root / name / iid / "candidate.wav").write_bytes(b"RIFF")
    (root / "outside.wav").write_bytes(b"keep")
    deleted = await recordings.expire_aftercall_files(datetime.now(UTC))
    assert deleted == 4  # old and the missing interview, in both folders
    for name in recordings.AFTERCALL_FOLDERS:
        assert not (root / name / old).exists() and not (root / name / "int_after_gone01").exists()
        assert (root / name / young / "candidate.wav").exists()
    assert (root / "outside.wav").exists()
    assert await recordings.expire_aftercall_files(datetime.now(UTC) + timedelta(days=1)) == 0


async def test_the_sweep_runs_it_and_counts_it(tmp_path, monkeypatch):
    await fresh_db(tmp_path, monkeypatch, "aftercall2.db")
    root = tmp_path / "recordings"
    monkeypatch.setenv(recordings.RECORDINGS_DIR_ENV, str(root))
    (root / "capture" / "int_after_gone02").mkdir(parents=True)
    counts = await recordings.run_sweep(api_key="unused")
    assert counts["aftercall_expired"] == 1 and not (root / "capture" / "int_after_gone02").exists()


def test_the_switch_is_documented_off():
    assert re.search(r"^AFTERCALL_ENABLED=$", (REPO / ".env.example").read_text(), re.M)
