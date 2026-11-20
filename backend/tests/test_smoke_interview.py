"""The integration smoke test (scripts/smoke_interview.py) runs clean, and its
registry takes other sessions' stages.

The smoke runs as a subprocess: it sets its own environment (a throwaway
database, made-up secrets) and must not share this test process's state.
"""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_the_smoke_interview_passes_every_stage():
    run = subprocess.run([sys.executable, "scripts/smoke_interview.py"], cwd=BACKEND, capture_output=True,
                         text=True, timeout=180)
    lines = [l for l in run.stdout.splitlines() if l.startswith(("PASS", "FAIL", "SKIP", "OK", "FAILED"))]
    assert run.returncode == 0, "\n".join(lines) or run.stderr[-2000:]
    assert lines[-1].startswith("OK:") and not any(l.startswith("FAIL") for l in lines)
    for name in ("seed", "auditor_sections", "events_and_relay", "cleaned_transcript", "scoring", "check_intel",
                 "interview_routes", "recording", "notes", "rejoin", "proctoring", "leak_scan"):
        assert any(l.startswith("PASS") and f" {name} " in l for l in lines), name


def test_a_plugin_stage_is_discovered_and_runs_before_the_leak_scan(tmp_path, monkeypatch):
    sys.path.insert(0, str(BACKEND))
    import importlib

    smoke = importlib.import_module("scripts.smoke_interview")
    plugin = tmp_path / "aftercall" / "smoke_stage.py"
    plugin.parent.mkdir()
    plugin.write_text("from scripts.smoke_interview import stage\n\n"
                      "@stage('d_after_call', owner='D')\n"
                      "def after_call(ctx):\n    ctx.expect(True, 'never')\n")
    ignored = tmp_path / "node_modules" / "x" / "smoke_stage.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("raise SystemExit('must not be imported')\n")
    monkeypatch.setattr(smoke, "STAGES", list(smoke.STAGES))
    found = smoke.discover_plugins((tmp_path,))
    assert found == [str(plugin)]
    names = [s.name for s in smoke.STAGES]
    assert "d_after_call" in names and [s for s in smoke.STAGES if s.name == "d_after_call"][0].owner == "D"
    ordered = [s for s in smoke.STAGES if not s.last] + [s for s in smoke.STAGES if s.last]
    assert ordered[-1].name == "leak_scan" and ordered[-2].name == "d_after_call"


def test_a_stage_name_cannot_be_registered_twice(monkeypatch):
    sys.path.insert(0, str(BACKEND))
    import importlib

    import pytest

    smoke = importlib.import_module("scripts.smoke_interview")
    monkeypatch.setattr(smoke, "STAGES", list(smoke.STAGES))
    with pytest.raises(ValueError):
        smoke.stage("seed")(lambda ctx: None)


def test_ci_runs_the_smoke_test_in_the_backend_job():
    import yaml

    jobs = yaml.safe_load((BACKEND.parent / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    assert "uv run python scripts/smoke_interview.py" in [s.get("run") for s in jobs["backend-tests"]["steps"]]



def test_the_hr_hook_stage_skips_with_its_reason_until_the_real_function_exists():
    from live import hr_hook

    run = subprocess.run([sys.executable, "scripts/smoke_interview.py"], cwd=BACKEND, capture_output=True,
                         text=True, timeout=180)
    [line] = [l for l in run.stdout.splitlines() if " hr_findings_hook " in l]
    if hr_hook.refinement_function() is None:
        assert line.startswith("SKIP") and "does not exist yet" in line  # never a silent PASS
    import re

    summary = run.stdout.splitlines()[-1]
    skipped = int(re.search(r"(\d+) skipped", summary).group(1))
    assert skipped >= (1 if line.startswith("SKIP") else 0)  # a skip is counted in the summary, not hidden
