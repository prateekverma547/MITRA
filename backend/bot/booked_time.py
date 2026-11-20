"""Run the interview to the booked length (joint A + C step).

The booking stores what the bot must run to (app/db.py: planned_duration_minutes,
planned_grace_minutes; live/booking.py trims them so the hard stop lands by
minute 58 on the realtime engine). The blueprint carries the plan's default
length. Here the blueprint is re-timed to the booking: the spec's duration and
grace become the booked ones, the opening and closing keep their minutes, and
every competency's budget is scaled by the same factor so they fill the rest.
An interview with no booking value keeps the blueprint as it is.

The coordinator's minute-58 cap (FORCED_END_BY_MINUTES) still applies on top.
"""

from shared.contracts import InterviewBlueprint


def apply_booked_timing(blueprint: InterviewBlueprint, *, planned_minutes: int | None,
                        grace_minutes: int | None) -> InterviewBlueprint:
    """A copy of `blueprint` timed to the booking, or the blueprint unchanged."""
    if planned_minutes is None:
        return blueprint
    spec = blueprint.evaluation_spec
    grace = spec.overrun_grace_minutes if grace_minutes is None else grace_minutes
    if planned_minutes == spec.duration_minutes and grace == spec.overrun_grace_minutes:
        return blueprint
    data = blueprint.model_dump(mode="json")
    data["evaluation_spec"]["duration_minutes"] = int(planned_minutes)
    data["evaluation_spec"]["overrun_grace_minutes"] = int(grace)
    available = planned_minutes - blueprint.opening_minutes - blueprint.closing_minutes
    total = sum(p.time_budget_minutes for p in blueprint.competency_plans)
    if total > 0 and available > 0:
        factor = available / total
        for plan in data["competency_plans"]:
            # Rounded down to a tenth, so the sum never exceeds the time available.
            plan["time_budget_minutes"] = int(plan["time_budget_minutes"] * factor * 10) / 10
    return InterviewBlueprint.model_validate(data)
