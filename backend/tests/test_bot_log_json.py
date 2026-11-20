"""C-132: the bot's logging honours LOG_JSON the way the web process does (app/logs.py).
Run as a real separate process, as the bot is, and its standard error read back."""

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = ("from loguru import logger; from bot.run_bot import configure_logging; "
          "configure_logging('INFO'); logger.info('bot line {}', 7); logger.debug('hidden')")


def run(env_value):
    env = {k: v for k, v in os.environ.items() if k != "LOG_JSON"}
    if env_value is not None:
        env["LOG_JSON"] = env_value
    out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=BACKEND, env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return [line for line in out.stderr.splitlines() if "bot line" in line or "hidden" in line]


def test_log_json_on_gives_one_json_object_per_line():
    for value in ("1", "true", "YES", "on"):
        [line] = run(value)
        record = json.loads(line)
        assert record["record"]["message"] == "bot line 7" and record["record"]["level"]["name"] == "INFO"


def test_log_json_off_or_unset_stays_plain_text():
    for value in (None, "", "0", "no"):
        [line] = run(value)
        assert "bot line 7" in line and not line.lstrip().startswith("{")
