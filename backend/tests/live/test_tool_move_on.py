"""move_on in the tool-call contract (C-156): Session A's realtime coordinator records
Mitra's move_on calls; as a `tool_called` event the contract refused them (tool was
end_interview | finish_interview), so they could never be stored.

The record below is real: Session A's coordinator in a realtime interview
(backend/sessions/int_0d55a3bd8e3a.json, coordinator_events). It goes through Session
A's own send path (bot.live_events.LiveEvents.send, which validates against the
contract and drops what fails) into the real emitter and the real store.
"""

import pytest

from bot.live_events import LiveEvents
from live.emit import EventEmitter
from live.store import all_events
from shared.interview_contracts import ToolCalled
from tests.live.support import fresh_db, seed_interview

IID = "int_moveon000001"
#: Real, as Session A's coordinator stored it (kind, time, the area entered, by whom).
REAL_MOVE_ON_RECORD = {"kind": "move_on", "at_seconds": 26.04, "area": 2, "by": "move_on"}


async def test_a_real_move_on_record_is_accepted_and_stored(tmp_path, monkeypatch):
    await fresh_db(tmp_path, monkeypatch, "move_on.db")
    await seed_interview(IID)
    emitter = EventEmitter(name=IID)
    live = LiveEvents(IID, clock_zero=0.0, emitter=emitter)
    await live.start()
    sent = live.send("ToolCalled", {"tool": REAL_MOVE_ON_RECORD["kind"], "outcome": "moved"},
                     source="coordinator", at_seconds=REAL_MOVE_ON_RECORD["at_seconds"])
    await live.close()
    assert sent is True and live.invalid == 0
    [stored] = [e for e in await all_events(IID) if e.kind.value == "tool_called"]
    assert (stored.payload.tool, stored.payload.outcome) == ("move_on", "moved")
    assert stored.at_seconds == 26.04 and stored.source.value == "coordinator"


@pytest.mark.parametrize("outcome", ["moved", "refused", "ignored"])
def test_move_on_takes_any_outcome_like_the_other_tools(outcome):
    assert ToolCalled(tool="move_on", outcome=outcome).tool == "move_on"


def test_an_unknown_tool_is_still_refused():
    with pytest.raises(ValueError):
        ToolCalled(tool="skip_area", outcome="moved")


def test_old_readers_word_move_on_and_never_read_it_as_an_ending():
    """As C-102: the readers of tool_called handle any tool name. Check Intelligence
    words it "move_on: moved"; Session D's summary only takes end_interview and
    finish_interview as Mitra's ending (analytics/summary/decisions.py)."""
    from analytics.summary import decisions
    from live import check_intel
    from live.emit import make_event

    event = make_event(IID, ToolCalled(tool="move_on", outcome="moved"), source="coordinator", at_seconds=26.04)
    data = check_intel.build(interview_id=IID, status="completed", started_at=None, ended_at=None,
                             transcript=[], session_metrics={}, blueprint=None, events=[event])
    texts = [n["text"] for n in data["after_last_turn"]] + [n["text"] for r in data["rows"] for n in r["notes"]]
    assert "move_on: moved" in texts
    dumped = [event.model_dump(mode="json")]
    decision = decisions.build("completed", {}, dumped, None)
    assert all(getattr(d, "action", None) != "move_on" for d in (decision if isinstance(decision, list) else [decision]))
