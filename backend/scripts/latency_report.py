"""Why is the first MITRA round slow? One row per MITRA round, oldest first.

Measurement only (2026-11-20). Joins what was already stored about each round
(reply waits split into end-of-speech detection and the model; the share of turns
where turn detection fell back to its timeout; MITRA's start-up) with what the
candidate's browser reported (hiring/diagnostics.py: face-check load time, main-
thread stalls, join timings, whether it was the tab's first call).

    cd backend && uv run python scripts/latency_report.py            # the last 12 rounds
    uv run python scripts/latency_report.py --all
"""

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SESSIONS = BACKEND / "sessions"


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _fmt(value, unit="s", digits=1):
    return "-" if value is None else f"{value:.{digits}f}{unit}"


def rows(db: Path, limit: int | None):
    con = sqlite3.connect(db)
    interviews = con.execute(
        "select id, started_at, transcript_zero_at from interviews "
        "where round = 'technical' and started_at is not null order by started_at"
    ).fetchall()
    if limit:
        interviews = interviews[-limit:]
    for iid, started, zero in interviews:
        waits = [json.loads(p)["payload"] for (p,) in con.execute(
            "select payload from interview_events where interview_id = ? and kind = 'reply_wait'", (iid,))]
        waits = [w for w in waits if not w.get("interrupted")]
        session = SESSIONS / f"{iid}.json"
        summary = (json.loads(session.read_text()).get("latency_summary") or {}) if session.exists() else {}
        diag_path = SESSIONS / "diagnostics" / f"{iid}.jsonl"
        diag = [json.loads(line) for line in diag_path.read_text().splitlines()] if diag_path.exists() else []
        last = diag[-1] if diag else {}
        start_up = None
        if zero:
            start_up = (datetime.fromisoformat(zero) - datetime.fromisoformat(started)).total_seconds()
        yield {
            "started": started[:16], "id": iid,
            "detect": _median([w.get("end_of_speech_detect_seconds") for w in waits]),
            "model": _median([w.get("model_to_first_sound_seconds") for w in waits]),
            "fallback": summary.get("fallback_share"),
            "start_up": start_up,
            "first_call": None if not last else last.get("earlier_calls_in_tab") == 0,
            "tab_age": last.get("tab_age_seconds_at_join"),
            "face_ms": last.get("face_check_load_ms"),
            "stalls": (last.get("long_tasks") or {}).get("first_minute_ms"),
            "stall_max": (last.get("long_tasks") or {}).get("max_ms"),
            "daily_join": last.get("daily_join_ms"),
            "echo": (last.get("mic") or {}).get("echo_cancellation"),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--db", default=str(BACKEND / "interviewer.db"))
    args = parser.parse_args(argv)
    head = (f"{'started (UTC)':16} {'interview':18} {'detect':>7} {'model':>6} {'fallback':>8} {'start-up':>8} | "
            f"{'tab':>5} {'tab age':>7} {'face load':>9} {'stalls 1st min':>14} {'worst stall':>11} {'daily join':>10} {'echo canc.':>10}")
    print(head)
    print("-" * len(head))
    for r in rows(Path(args.db), None if args.all else 12):
        tab = "-" if r["first_call"] is None else ("first" if r["first_call"] else "warm")
        fallback = "-" if r["fallback"] is None else f"{r['fallback']:.0%}"
        print(f"{r['started']:16} {r['id']:18} {_fmt(r['detect']):>7} {_fmt(r['model']):>6} "
              f"{fallback:>8} "
              f"{_fmt(r['start_up']):>8} | {tab:>5} {_fmt(r['tab_age']):>7} {_fmt(r['face_ms'], 'ms', 0):>9} "
              f"{_fmt(r['stalls'], 'ms', 0):>14} {_fmt(r['stall_max'], 'ms', 0):>11} {_fmt(r['daily_join'], 'ms', 0):>10} "
              f"{('-' if r['echo'] is None else str(r['echo'])):>10}")
    print("\ndetect = median wait for OpenAI to decide the candidate finished; model = median wait for its first sound;"
          "\nfallback = share of replies where turn detection waited for its timeout. Browser columns: from the room"
          "\n(hiring/diagnostics.py), empty for rounds before it existed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
