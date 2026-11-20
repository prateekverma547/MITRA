"""Check Intelligence for one interview: the data behind the CheckIntel screen.

Read-only, assembled on request from what is already stored:

- the transcript (spoken times, item ids, cut replies);
- `session_metrics`: reply waits linked to turns (F-060), the coordinator's
  events (time notes, tool calls, withdrawal steps), silence prompts, realtime
  usage, text-model calls;
- the candidate's blueprint (the plan's areas and question ids);
- stored InterviewEvents, when the bot emits them: question labels, area
  changes, reply waits, costs.

Labels are the auditor's reading after the fact; a row the auditor has not
labelled says "not yet labelled", and the tiles that depend on labels are null
with a reason in `unfilled`. Nothing here is computed by a model.

The response shape is `CheckIntelReport` (shared/interview_contracts.py);
`build` validates against it, so the console's generated types are exact.

Skips and declines are notes on the row of the question they belong to (C-A9;
every other marker sits on the next question, the one it led to):
- from the coordinator's records once the call is over, with the auditor's
  entry paired in order (both are written in the same call): its question id
  and `after_turn`, the Mitra turn that was skipped or declined;
- from `skip` events while it runs. Declines have no live event (decided).
A marker goes on the row of its question: the `after_turn` row when its
question matches, else the latest row labelled with that question id at or
before the marker (which may be in an earlier area, or earlier in the call),
else the turn where it happened. A marker with no known question stays on the
turn where it happened and says "question not identified".

Design: track_i_notes/designs/designs.md, "CheckIntel".
"""

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from shared.contracts import InterviewBlueprint, Transcript
from shared.interview_contracts import CheckIntelReport, InterviewEvent, QuestionLabel

SCHEMA = "check_intel.v1"
NOT_LABELLED = "not yet labelled"
AUDITOR_NOTE = (
    "Labels are the auditor's reading after the fact. The realtime model's own "
    "reasoning is not visible."
)
FOLLOW_UP_CAP = 3
#: Percentile method, stated so a reader can reproduce the number: linear
#: interpolation between closest ranks (numpy's default, Excel's PERCENTILE.INC).
PERCENTILE_METHOD = "linear interpolation between closest ranks"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def _seconds(ms: float | None) -> float | None:
    return None if ms is None else round(ms / 1000, 3)


# -- waits ---------------------------------------------------------------------------


def _waits(metrics: dict, events: list[InterviewEvent]) -> tuple[dict[int, dict], dict]:
    """Reply waits keyed by the Mitra turn they preceded."""
    from_events = [e for e in events if e.kind.value == "reply_wait"]
    by_turn: dict[int, dict] = {}
    unlinked = 0
    if from_events:
        source = "events"
        for e in from_events:
            p = e.payload
            by_turn[p.turn_index] = {
                "detect_s": p.end_of_speech_detect_seconds,
                "model_s": p.model_to_first_sound_seconds,
                "total_s": p.total_seconds,
                "interrupted": p.interrupted,
            }
    else:
        source = "session_metrics"
        for w in metrics.get("turns") or []:
            reply = w.get("reply_turn_index")
            if reply is None:
                unlinked += 1
                continue
            by_turn[reply] = {
                "detect_s": _seconds(w.get("endpointing_ms")),
                "model_s": _seconds(w.get("generation_ms")),
                "total_s": _seconds(w.get("ttfa_ms")),
                "interrupted": bool(w.get("was_interrupted")),
            }
    totals = [w["total_s"] for w in by_turn.values() if w["total_s"] is not None]
    summary = {
        "median_s": percentile(totals, 0.5),
        "p95_s": percentile(totals, 0.95),
        "n": len(totals),
        "unlinked_waits": unlinked,
        "method": PERCENTILE_METHOD,
        "source": source if by_turn else None,
    }
    return by_turn, summary


# -- markers: time notes, tools, silence, area switches -----------------------------


def _auditor_entries(entries) -> list[dict]:
    """The auditor's skips or declines, in order: question id and the skipped turn."""
    return [{"question_id": e.get("question_id"), "after_turn": e.get("after_turn")} if isinstance(e, dict)
            else {"question_id": None, "after_turn": None} for e in entries or []]


#: Coordinator records shown as tool notes (C-A20). Mitra ends the interview with
#: end_interview (round 14, A-95): end_interview_completed and end_interview_withdrew
#: (accepted: they end it), end_interview_refused (code's guards said no),
#: end_interview_after_end (called once it was already over). withdrawal_*: the
#: pipeline engine's confirmation steps.
TOOL_RECORDS = ("end_interview", "withdrawal")
#: finish_interview_*: the ending tool before end_interview. Read only so interviews
#: recorded before round 14 still show it; nothing sends it now.
LEGACY_TOOL_RECORDS = ("finish_interview",)
#: Marker types placed on their own question's row rather than the next one.
OWN_QUESTION_TYPES = ("skip", "decline")
NOT_IDENTIFIED = "question not identified"


def _markers(metrics: dict, events: list[InterviewEvent]) -> list[dict]:
    markers: list[dict] = []
    coordinator = metrics.get("coordinator_events")
    silence = metrics.get("silence_events")
    auditor = metrics.get("auditor") if isinstance(metrics.get("auditor"), dict) else {}
    # The auditor's question ids for skips and declines, in the order they were
    # recorded: the coordinator writes its record and tells the auditor in the
    # same call (bot/realtime_coordinator.py _record_skip / _record_decline).
    skips, declines = _auditor_entries(auditor.get("skips")), _auditor_entries(auditor.get("declines"))
    for c in coordinator or []:
        kind = c.get("kind", "")
        if kind in ("question_skipped", "question_declined"):
            entries = skips if kind == "question_skipped" else declines
            entry = entries.pop(0) if entries else {"question_id": None, "after_turn": None}
            skipped = kind == "question_skipped"
            markers.append({"at_seconds": c.get("at_seconds"), "type": "skip" if skipped else "decline",
                            "text": "question skipped" if skipped else "question declined",
                            "question_id": entry["question_id"], "after_turn": entry["after_turn"],
                            "code": kind})
        elif kind == "note":
            markers.append({"at_seconds": c.get("at_seconds"), "type": "time_note",
                            "text": c.get("text", ""), "code": "time_note"})
        elif kind.startswith(TOOL_RECORDS) or kind.startswith(LEGACY_TOOL_RECORDS):
            markers.append({"at_seconds": c.get("at_seconds"), "type": "tool",
                            "text": kind.replace("_", " "), "code": kind})
        elif kind == "interview_over":
            markers.append({"at_seconds": c.get("at_seconds"), "type": "ended",
                            "text": f"interview over: {c.get('reason', '')}".strip(),
                            "code": c.get("reason") or None})
    for s in silence or []:
        markers.append({"at_seconds": s.get("at_seconds"), "type": "silence_prompt",
                        "text": f"silence prompt {s.get('stage')}: {s.get('action')}",
                        "code": s.get("action") or None})

    # Stored events fill what the metrics do not carry, and stand in for the
    # metrics' own lists when those are absent (an interview still running).
    for e in events:
        p, kind = e.payload, e.kind.value
        if kind == "area_changed":
            markers.append({"at_seconds": e.at_seconds, "type": "area_switch",
                            "text": f"area: {p.area_name}", "code": "area_changed"})
        elif kind == "error":
            markers.append({"at_seconds": e.at_seconds, "type": "error",
                            "text": f"{p.component}: {p.code}", "code": f"{p.component}.{p.code}"})
        elif kind == "time_note_sent" and coordinator is None:
            markers.append({"at_seconds": e.at_seconds, "type": "time_note", "text": p.text, "code": "time_note"})
        elif kind in ("tool_called", "withdrawal") and coordinator is None:
            text = f"{p.tool}: {p.outcome}" if kind == "tool_called" else f"withdrawal {p.stage}"
            code = f"{p.tool}.{p.outcome}" if kind == "tool_called" else f"withdrawal_{p.stage}"
            markers.append({"at_seconds": e.at_seconds, "type": "tool", "text": text, "code": code})
        elif kind == "skip" and coordinator is None:
            markers.append({"at_seconds": e.at_seconds, "type": "skip", "text": "question skipped",
                            "question_id": p.question_id, "after_turn": None, "code": "question_skipped"})
        elif kind == "silence_prompt" and silence is None:
            markers.append({"at_seconds": e.at_seconds, "type": "silence_prompt",
                            "text": f"silence prompt {p.stage}: {p.action}", "code": p.action or None})
    return sorted((m for m in markers if m["at_seconds"] is not None), key=lambda m: m["at_seconds"])


def _own_question_row(rows: list[dict], marker: dict) -> dict | None:
    """The row of the question a skip or decline belongs to (C-A9)."""
    at, qid, after_turn = marker["at_seconds"], marker["question_id"], marker.get("after_turn")
    by_index = {r["turn_index"]: r for r in rows}
    asked = by_index.get(after_turn) if after_turn is not None else None
    if qid is not None:
        if asked is not None and asked["question_id"] == qid:
            return asked
        labelled = [r for r in rows if r["question_id"] == qid and r["spoken_start"] <= at]
        if labelled:
            return labelled[-1]
    if asked is not None:
        return asked
    # The turn where it happened: the last question asked before it.
    before = [r for r in rows if r["spoken_start"] <= at]
    return before[-1] if before else None


def _stored_labels(metrics: dict, blueprint: dict | None) -> dict[int, Any]:
    """The auditor's labels as it stored them (session_metrics.auditor.labels),
    shaped like a live label. Only values the contract knows; the area number
    becomes the area id (1 opening, 2.. the plan's areas in order, last closing)."""
    from types import SimpleNamespace

    raw = ((metrics.get("auditor") or {}).get("labels") or {}) if isinstance(metrics.get("auditor"), dict) else {}
    plans = [p.get("competency_id") for p in (blueprint or {}).get("competency_plans") or []]
    known = {label.value for label in QuestionLabel}
    out = {}
    for key, record in raw.items():
        if not isinstance(record, dict) or record.get("label") not in known or not str(key).isdigit():
            continue
        area = record.get("area")
        area_id = ("opening" if area == 1 else plans[area - 2] if isinstance(area, int) and 2 <= area < len(plans) + 2
                   else "closing" if isinstance(area, int) and area >= len(plans) + 2 else None)
        out[int(key)] = SimpleNamespace(label=QuestionLabel(record["label"]), question_id=record.get("question_id"),
                                        follow_up_number=record.get("follow_up_number"), area_id=area_id)
    return out


# -- plan and cost ------------------------------------------------------------------


def _plan(blueprint: dict | None) -> list[dict] | None:
    if not blueprint:
        return None
    try:
        plans = InterviewBlueprint.model_validate(blueprint).competency_plans
        return [{"area_id": p.competency_id, "name": p.name, "budget_minutes": p.time_budget_minutes,
                 "question_ids": list(p.question_ids)} for p in plans]
    except ValidationError:
        return [{"area_id": p.get("competency_id"), "name": p.get("name"),
                 "budget_minutes": p.get("time_budget_minutes"), "question_ids": []}
                for p in blueprint.get("competency_plans") or []]


def _cost(metrics: dict, events: list[InterviewEvent], cleaned: dict | None = None) -> dict:
    usage = metrics.get("usage") or {}
    realtime = usage.get("estimated_usd")
    by_job: dict[str, float] = {}
    unknown_price = 0
    for call in metrics.get("text_model_calls") or []:
        if call.get("cost_usd") is None:
            unknown_price += 1
            continue
        job = call.get("job") or "text_model"
        by_job[job] = round(by_job.get(job, 0.0) + call["cost_usd"], 6)
    # Phase 4 (D-40): the auditor's and the cleaner's calls are stored apart from
    # text_model_calls (only scoring is there), so they are added here, each as
    # its own job and never twice.
    auditor = metrics.get("auditor") if isinstance(metrics.get("auditor"), dict) else {}
    auditor_usd = (auditor.get("stats") or {}).get("usd")
    if isinstance(auditor_usd, (int, float)):
        by_job["auditor"] = round(by_job.get("auditor", 0.0) + auditor_usd, 6)
    cleaned = cleaned or metrics.get("cleaned_transcript") or {}
    if isinstance(cleaned, dict) and isinstance(cleaned.get("usd"), (int, float)):
        by_job["cleaning"] = round(by_job.get("cleaning", 0.0) + cleaned["usd"], 6)
    source = "session_metrics"
    if realtime is None and not by_job:
        costs = [e.payload for e in events if e.kind.value == "cost_update"]
        if costs:
            source = "events"
            rt = [c.usd for c in costs if c.component == "realtime" and c.usd is not None]
            realtime = round(sum(rt), 6) if rt else None
            for c in costs:
                if c.component == "text_model":
                    if c.usd is None:
                        unknown_price += 1
                    else:
                        job = c.job or "text_model"
                        by_job[job] = round(by_job.get(job, 0.0) + c.usd, 6)
    report = by_job.get("feedback")
    parts = [v for v in (realtime, *by_job.values()) if v is not None]
    return {
        "realtime_usd": realtime,
        "realtime_is_estimate": realtime is not None,
        "report_usd": report,
        "text_model_usd_by_job": by_job,
        "calls_with_unknown_price": unknown_price,
        "total_usd": round(sum(parts), 6) if parts else None,
        "source": source,
    }


# -- assembly -----------------------------------------------------------------------


def _ending_detail(stored) -> dict:
    """session_metrics["ending"] as the realtime engine stores it ({"ending": ...,
    "hang_up_by": ...}); anything else (missing, not a dict, not a string) is None,
    never an error. Cut to 64 characters, as the live interview_ended event is."""
    stored = stored if isinstance(stored, dict) else {}
    return {key: (value[:64] if isinstance(value, str) and value else None)
            for key in ("ending", "hang_up_by") for value in [stored.get(key)]}


def build(
    *,
    interview_id: str,
    status: str,
    started_at: datetime | None,
    ended_at: datetime | None,
    transcript: dict | None,
    session_metrics: dict | None,
    blueprint: dict | None,
    events: list[InterviewEvent],
    cleaned_transcript: dict | None = None,
) -> dict[str, Any]:
    metrics = session_metrics or {}
    turns_raw = []
    duration = None
    if transcript:
        try:
            parsed = Transcript.model_validate(transcript)
            duration = parsed.duration_seconds
            turns_raw = [
                {"index": t.index, "speaker": t.speaker.value, "text": t.text,
                 "at_seconds": t.at_seconds, "spoken_start": t.spoken_start,
                 "cut": t.cut, "item_id": t.item_id, "section_id": t.section_id}
                for t in parsed.interview_turns()
            ]
        except ValidationError:
            turns_raw = []

    waits, wait_summary = _waits(metrics, events)
    labels = {e.payload.turn_index: e.payload for e in events if e.kind.value == "question_labelled"}
    sources = {index: "live" for index in labels}
    # C-125: a turn whose live label never reached the store (the event was
    # refused, B-90 A1) takes the auditor's own stored label, marked "stored".
    for index, stored in _stored_labels(metrics, blueprint).items():
        if index not in labels:
            labels[index], sources[index] = stored, "stored"
    # C-A1: the area each turn was spoken in, when the bot sent it with the turn.
    turn_areas = {e.payload.turn_index: e.payload.area_id for e in events
                  if e.kind.value == "transcript_turn" and getattr(e.payload, "area_id", None)}
    area_events = sorted((e for e in events if e.kind.value == "area_changed"), key=lambda e: e.at_seconds)
    markers = _markers(metrics, events)

    rows = []
    mitra = [t for t in turns_raw if t["speaker"] == "interviewer"]
    led_to = [m for m in markers if m["type"] not in OWN_QUESTION_TYPES]
    own_question = [m for m in markers if m["type"] in OWN_QUESTION_TYPES]
    marker_i = 0
    for turn in mitra:
        when = turn["spoken_start"]
        notes = []
        # A marker belongs to the next question asked at or after it: that is
        # the question it led to. Skips and declines are placed below.
        while marker_i < len(led_to) and led_to[marker_i]["at_seconds"] <= when:
            notes.append(led_to[marker_i])
            marker_i += 1
        if turn["cut"]:
            notes.append({"at_seconds": when, "type": "cut", "text": "reply cut: heard only in part",
                          "code": "reply_cut"})
        label = labels.get(turn["index"])
        # The opening and closing are areas of their own: a closing turn whose
        # label carries no area must not fall back to the last competency (B-56).
        if label and label.label in (QuestionLabel.OPENING, QuestionLabel.CLOSING):
            area = label.label.value
        else:
            area = turn_areas.get(turn["index"]) or (label.area_id if label and label.area_id else None)
        if area is None:
            current = [e for e in area_events if e.at_seconds <= when]
            area = current[-1].payload.area_id if current else turn["section_id"]
        rows.append({
            "turn_index": turn["index"],
            "at_seconds": turn["at_seconds"],
            "spoken_start": when,
            "text": turn["text"],
            "item_id": turn["item_id"],
            "label": label.label.value if label else NOT_LABELLED,
            "label_source": sources.get(turn["index"]),
            "question_id": label.question_id if label else None,
            "follow_up_number": label.follow_up_number if label else None,
            "area_id": area,
            "wait": waits.get(turn["index"]),
            "notes": notes,
        })
    after_last = led_to[marker_i:]
    for marker in own_question:
        target = _own_question_row(rows, marker)
        note = {k: marker[k] for k in ("at_seconds", "type", "text", "question_id")}
        if marker["question_id"] is None:
            note["text"] = f"{marker['text']}: {NOT_IDENTIFIED}"
        if target is None:
            after_last.append(note)  # before any question was asked: nothing to sit on
        else:
            target["notes"].append(note)
            target["notes"].sort(key=lambda n: n["at_seconds"])

    plan = _plan(blueprint)
    unfilled = []
    skips_and_declines = {
        "skips": sum(1 for m in markers if m["type"] == "skip"),
        "declines": sum(1 for m in markers if m["type"] == "decline"),
        "easier_variants": (sum(1 for l in labels.values() if l.label is QuestionLabel.EASIER_VARIANT)
                            if labels else None),
    }
    if labels:
        off_plan = sum(1 for l in labels.values() if l.label.value == "off_plan")
        per_question: dict[str, int] = {}
        for l in labels.values():
            if l.question_id:
                per_question[l.question_id] = max(per_question.get(l.question_id, 0),
                                                  l.follow_up_number or 0)
        asked = sorted({l.question_id for l in labels.values() if l.question_id})
        total = sum(len(a["question_ids"]) for a in plan) if plan else None
        follow_ups = {"per_question": per_question, "cap": FOLLOW_UP_CAP,
                      "over_cap": sorted(q for q, n in per_question.items() if n > FOLLOW_UP_CAP)}
        coverage = {"asked_question_ids": asked, "asked": len(asked), "plan_questions": total}
    else:
        off_plan = follow_ups = coverage = None
        no_labels = "Mitra's questions have not been labelled for this interview."
        unfilled += [
            {"field": "rows[].label, question_id, follow_up_number", "code": "no_auditor_labels",
             "reason": f"{no_labels} Which plan question each one was is not known."},
            {"field": "tiles.off_plan_count", "code": "no_auditor_labels", "reason": no_labels},
            {"field": "tiles.follow_ups", "code": "no_auditor_labels", "reason": no_labels},
            {"field": "tiles.coverage.asked", "code": "no_auditor_labels",
             "reason": f"{no_labels} The plan's areas and questions are shown."},
            {"field": "tiles.skips_and_declines.easier_variants", "code": "no_auditor_labels", "reason": no_labels},
        ]
    if not area_events and not any(r["area_id"] for r in rows):
        unfilled.append({"field": "rows[].area_id", "code": "no_area_per_turn",
                         "reason": "Which area each question belongs to was not recorded for this interview."})
    if not waits:
        unfilled.append({"field": "rows[].wait, tiles.reply_wait, chart", "code": "no_reply_waits",
                         "reason": "How long the candidate waited for each reply was not recorded."})
    if plan is None:
        unfilled.append({"field": "plan", "code": "no_plan", "reason": "There is no interview plan for this candidate."})
    cost = _cost(metrics, events, cleaned_transcript)
    if cost["report_usd"] is None:
        unfilled.append({"field": "tiles.cost.report_usd", "code": "no_report_cost",
                         "reason": "The cost of writing the report was not recorded."})
    # The plan's generation and refinement costs ARE stored with the candidate since
    # C-143 (and split, C-158); they belong to the job summary (analytics/summary),
    # not to one interview, so nothing is said about them here (C-166).

    report = {
        "schema": SCHEMA,
        "interview": {
            "interview_id": interview_id,
            "status": status,
            "started_at": started_at.isoformat() if started_at else None,
            "ended_at": ended_at.isoformat() if ended_at else None,
            "duration_seconds": duration,
            "engine": metrics.get("engine"),
            "realtime_model": metrics.get("realtime_model"),
            "ended_because": metrics.get("ended_because"),
            **_ending_detail(metrics.get("ending")),
        },
        "labels_note": AUDITOR_NOTE,
        "tiles": {
            "reply_wait": wait_summary,
            "off_plan_count": off_plan,
            "follow_ups": follow_ups,
            "coverage": {"plan_areas": len(plan) if plan else None, **(coverage or {})},
            "cost": cost,
            "skips_and_declines": skips_and_declines,
        },
        "chart": [
            {"turn_index": t, **w} for t, w in sorted(waits.items())
        ],
        "plan": plan,
        "rows": rows,
        "after_last_turn": after_last,
        "events_stored": len(events),
        "unfilled": unfilled,
        # Wave 1: how the plan was refined with the HR findings (None: not refined).
        "hr_findings": (blueprint or {}).get("hr_findings"),
    }
    # Validated against the contract, and every field present, as the console's
    # generated types expect (frontend/ui/contracts/export_schema.py).
    return CheckIntelReport.model_validate(report).model_dump(mode="json", by_alias=True)
