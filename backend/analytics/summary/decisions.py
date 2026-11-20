"""Who decided: for each decision during an interview, its owner (Mitra or code), the
reason, the time and the record it came from (D_architecture 9.4).

Mitra's decisions are her tool calls (end_interview / finish_interview; move_on, which
Session A is adding now) and a withdrawal she confirmed (`by: "model"`). Code's are its
own rules: the hard stop, the End button, the rejoin and silence rules, a refused tool
call, a withdrawal confirmed by code or by the patterns, a technical end. The plan's HR
probes were decided before the interview by the plan refinement model.
Reads codes, times and ids only: no text from any record reaches a Decision.
"""

from __future__ import annotations

from analytics.summary.shapes import Decision

#: (owner, reason) of the ending, by end reason, when no more specific record says.
_ENDING_BY_REASON = {
    "candidate_ended_button": ("code", "candidate_pressed_end_confirmed"),
    "rejoin_limit_reached": ("code", "rejoin_limit"),
    "candidate_did_not_return": ("code", "did_not_return_in_grace"),
    "candidate_dropped": ("code", "connection_dropped"),
    "abandoned_by_candidate": ("code", "silence_rule"),
    "candidate_left_the_call": ("code", "candidate_left_the_call"),
    "failed_to_start": ("code", "failed_to_start"),
    "interrupted_by_restart": ("code", "interrupted_by_restart"),
    "session_ended": ("code", "session_ended"),
}
_BY = {"model": "mitra", "code": "code", "patterns": "code"}
#: end_interview's outcomes that END the interview (A-95, round 14). Its other outcomes,
#: "refused" (code's guards said no) and "after_end" (called once it was already over),
#: are decisions during the interview, never endings.
ACCEPTED_END = {"accepted_withdrew": "withdrew", "accepted_completed": "completed"}
NOT_AN_ENDING = ("refused", "after_end")


def _src_event(e) -> str:
    return f"event:{e['event_id']}"


def build(status: str, metrics: dict, events: list[dict], blueprint: dict | None) -> list[Decision]:
    """`events`: dicts with kind, event_id, at_seconds, payload (the inner payload)."""
    out: list[Decision] = []
    coord = metrics.get("coordinator_events") or []

    # -- before the interview: HR findings the plan refinement probed (C-114, A-78)
    hr = (blueprint or {}).get("hr_findings")
    if isinstance(hr, dict):
        for f in hr.get("findings") or []:
            out.append(Decision(phase="before_interview", owner="plan_refinement", action="hr_finding",
                                reason=f.get("outcome"), source="blueprint.hr_findings"))

    # -- during: area moves, withdrawal offers, silence and rejoin rules, refusals
    move_on_times = {round(c.get("at_seconds", -1), 1) for c in coord if c.get("kind") == "move_on"}
    for i, c in enumerate(coord):
        k = c.get("kind")
        if k == "move_on":
            out.append(Decision(phase="during_interview", owner="mitra", action="move_on", reason="area_finished",
                                at_seconds=c.get("at_seconds"), source=f"coordinator_events[{i}]"))
        elif k in ("move_on_refused", "end_interview_refused", "end_interview_after_end"):
            out.append(Decision(phase="during_interview", owner="code",
                                action="end_interview_after_end" if k == "end_interview_after_end" else k,
                                reason="already_over" if k == "end_interview_after_end" else "not_allowed_now",
                                at_seconds=c.get("at_seconds"), source=f"coordinator_events[{i}]"))
        elif k == "hard_stop_reached":
            out.append(Decision(phase="during_interview", owner="code", action="hard_stop", reason="time_limit",
                                at_seconds=c.get("at_seconds"), source=f"coordinator_events[{i}]"))
        elif k == "welcome_back":
            out.append(Decision(phase="during_interview", owner="code", action="welcome_back",
                                reason="candidate_returned", at_seconds=c.get("at_seconds"),
                                source=f"coordinator_events[{i}]"))
    for e in events:
        k, p = e["kind"], e["payload"]
        if k == "area_changed" and round(e["at_seconds"], 1) not in move_on_times and p.get("area_id") != "opening":
            out.append(Decision(phase="during_interview", owner="code", action="area_changed", reason="time_plan",
                                at_seconds=e["at_seconds"], source=_src_event(e)))
        elif k == "withdrawal" and p.get("stage") in ("offered", "declined"):
            out.append(Decision(phase="during_interview", owner=_BY.get(p.get("by"), "unknown"),
                                action=f"withdrawal_{p.get('stage')}", reason=p.get("by"),
                                at_seconds=e["at_seconds"], source=_src_event(e)))
        elif k == "silence_prompt":
            out.append(Decision(phase="during_interview", owner="code", action="silence_prompt",
                                reason=f"stage_{p.get('stage')}", at_seconds=e["at_seconds"], source=_src_event(e)))
        elif k == "tool_called" and p.get("tool") == "end_interview" and p.get("outcome") in NOT_AN_ENDING:
            action = "end_interview_refused" if p["outcome"] == "refused" else "end_interview_after_end"
            if not any(d.action == action and d.at_seconds is not None
                       and abs(d.at_seconds - e["at_seconds"]) < 1.0 for d in out):  # the coordinator's own record
                out.append(Decision(phase="during_interview", owner="code", action=action,
                                    reason="not_allowed_now" if p["outcome"] == "refused" else "already_over",
                                    at_seconds=e["at_seconds"], source=_src_event(e)))
        elif k == "rejoin_limit_reached":
            out.append(Decision(phase="during_interview", owner="code", action="rejoin_limit_reached",
                                reason="rejoin_limit", at_seconds=e["at_seconds"], source=_src_event(e)))
    end = ending_decision(status, metrics, events, coord)
    if end is not None:
        out.append(end)
    return out


def ending_decision(status: str, metrics: dict, events: list[dict], coord: list[dict]) -> Decision | None:
    """Exactly one for an interview that ended in a call; None for one that never ran."""
    if status in ("scheduled", "in_progress", "no_show", "expired"):
        return None
    reason = metrics.get("ended_because")
    ending = (metrics.get("ending") or {}).get("ending") if isinstance(metrics.get("ending"), dict) else None
    # 1. Mitra's own end_interview / finish_interview call, accepted
    for i, c in enumerate(coord):
        if c.get("kind") in ("end_interview_withdrew", "end_interview_completed"):
            why = c["kind"].removeprefix("end_interview_")
            return Decision(phase="ending", owner="mitra", action="end_interview", reason=why,
                            at_seconds=c.get("at_seconds"), source=f"coordinator_events[{i}]")
    for e in events:
        tool, outcome = e["payload"].get("tool"), str(e["payload"].get("outcome", ""))
        if e["kind"] != "tool_called":
            continue
        # end_interview: only an accepted call ends it (A-95). finish_interview: older
        # interviews only (no longer sent), any outcome but a refusal.
        if (tool == "end_interview" and outcome in ACCEPTED_END) or \
                (tool == "finish_interview" and "refus" not in outcome):
            return Decision(phase="ending", owner="mitra", action=tool,
                            reason=ACCEPTED_END.get(outcome, outcome[:40]), at_seconds=e["at_seconds"],
                            source=_src_event(e))
    # 2. the candidate pressed End and confirmed: code ends it. Older bots stored this as
    #    candidate_withdrew (EndReason docstring), so the ending and the coordinator's
    #    end_button record decide, not only the end reason (found by the hand-check, D-80).
    for i, c in enumerate(coord):
        if c.get("kind") == "end_button":
            return Decision(phase="ending", owner="code", action="end", reason="candidate_pressed_end_confirmed",
                            at_seconds=c.get("at_seconds"), source=f"coordinator_events[{i}]")
    if ending == "candidate_ended_button" or reason == "candidate_ended_button":
        return Decision(phase="ending", owner="code", action="end", reason="candidate_pressed_end_confirmed",
                        at_seconds=next((e["at_seconds"] for e in events if e["kind"] == "interview_ended"), None),
                        source="session_metrics.ending" if ending == "candidate_ended_button"
                        else "session_metrics.ended_because")
    # 3. a confirmed withdrawal: Mitra's if she confirmed it, else code's
    for e in events:
        if e["kind"] == "withdrawal" and e["payload"].get("stage") == "confirmed" and reason == "candidate_withdrew":
            return Decision(phase="ending", owner=_BY.get(e["payload"].get("by"), "unknown"), action="end",
                            reason="candidate_withdrew_confirmed", at_seconds=e["at_seconds"], source=_src_event(e))
    # 4. the hard stop
    for i, c in enumerate(coord):
        if c.get("kind") == "hard_stop_reached":
            return Decision(phase="ending", owner="code", action="end", reason="time_limit",
                            at_seconds=c.get("at_seconds"), source=f"coordinator_events[{i}]")
    if ending == "forced_at_hard_stop":
        return Decision(phase="ending", owner="code", action="end", reason="time_limit",
                        source="session_metrics.ending")
    # 5. code's rules, by the end reason
    if reason in _ENDING_BY_REASON:
        owner, why = _ENDING_BY_REASON[reason]
        at = next((e["at_seconds"] for e in events if e["kind"] == "interview_ended"), None)
        return Decision(phase="ending", owner=owner, action="end", reason=why, at_seconds=at,
                        source="session_metrics.ended_because")
    if reason == "interview_complete":
        # No tool call recorded: the coordinator closed the plan (older records).
        at = next((e["at_seconds"] for e in events if e["kind"] == "interview_ended"), None)
        return Decision(phase="ending", owner="code", action="end", reason="plan_closed", at_seconds=at,
                        source="session_metrics.ended_because")
    return Decision(phase="ending", owner="unknown", action="end", reason=reason,
                    source="session_metrics.ended_because")
