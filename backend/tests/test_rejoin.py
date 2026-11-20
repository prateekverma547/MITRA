"""Rejoin (phase 3 wave 1): the real coordinator, presence, silence ladder and live events
(validated against the contract), a stand-in realtime service that records what Mitra is
made to say, and the controller driven by replayed Daily events and Session C's signals.
Timings are scaled down (grace 0.6 s, counts from 0.2 s) so a scenario takes under a second."""

import asyncio
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.live_events import LiveEvents
from bot.presence import RoomPresence
from bot.realtime_coordinator import RealtimeCoordinator

#: Round 14: code no longer speaks; a private note tells Mitra and she speaks. `said`
#: records each note that came with a reply request (what she was asked to respond to).
WELCOME_BACK = "has just reconnected after a short drop"
REJOIN_LIMIT_GOODBYE = "connection has dropped several times"
from bot.rejoin import RejoinController
from shared.contracts import ConnectionRecord, InterviewBlueprint
from shared.interview_contracts import InterviewEvent

BP = InterviewBlueprint.model_validate(json.loads(
    (Path(__file__).resolve().parents[1] / "bot" / "fixtures" / "pm_senior.json").read_text()))
QUESTION = "What options did you weigh in the hardest prioritization call you have made?"
CANDIDATE = {"id": "p-1", "info": {"userId": "candidate:int_0123456789ab"}}


class Emitter:
    def __init__(self):
        self.events = []

    async def start(self):
        pass

    def emit(self, event):
        assert isinstance(event, InterviewEvent)
        self.events.append(event)
        return True

    async def close(self):
        return {}


class Service:
    """Records what the coordinator makes Mitra do."""

    def __init__(self):
        self.said, self.cancels, self.paused = [], 0, []
        self.reply_text = {"r1": f"Okay. Thanks. {QUESTION}"}
        self.reply_spoke, self.reply_of_call = {}, {}

    async def insert_note(self, text):
        self.notes = getattr(self, "notes", []) + [text]
        return f"n{len(self.notes)}"

    async def delete_note(self, item_id):
        pass

    async def request_reply(self):
        self.said.append(self.notes[-1])

    async def cancel_reply(self):
        self.cancels += 1

    def set_audio_input_paused(self, paused):
        self.paused.append(paused)

    def register_function(self, *a, **k):
        pass


class Worker:
    def __init__(self):
        self.ended = []

    async def queue_frames(self, frames):
        self.ended.extend(frames)


async def setup(grace=0.6, settle=0.05):
    """The interview clock (gaps, counting) is a hand-moved value, so the contract's real
    10 s rule applies; the grace window and settling run on real time, scaled down."""
    service, worker = Service(), Worker()
    coord = RealtimeCoordinator(blueprint=BP, llm=service)
    coord.attach(worker)
    live = LiveEvents("int_0123456789ab", clock_zero=time.time(), blueprint=BP, emitter=Emitter())
    await live.start()
    coord.attach_events(live)
    presence = RoomPresence(interview_over=lambda: coord.is_over, candidate_mark="candidate:int_0123456789ab")
    presence.observe_join(CANDIDATE)
    t = {"now": 100.0}
    rejoin = RejoinController(coordinator=coord, presence=presence, live=live, interview_id="int_0123456789ab",
                              clock=lambda: t["now"], grace_seconds=grace, settle_seconds=settle)
    return SimpleNamespace(service=service, worker=worker, coord=coord, live=live, presence=presence,
                           rejoin=rejoin, t=t)


def kinds(s):
    return [e.payload.kind for e in s.live._emitter.events if e.payload.kind in (
        "candidate_dropped", "candidate_returned", "rejoin_limit_reached")]


def payloads(s, kind):
    return [e.payload for e in s.live._emitter.events if e.payload.kind == kind]


async def daily_drop_and_back(s, gap, new_id="p-2"):
    s.presence.observe_leave({"id": s.presence_id if hasattr(s, "presence_id") else "p-1"})
    await s.rejoin.on_candidate_left("p-1")
    await asyncio.sleep(gap)
    s.presence.observe_join({"id": new_id, "info": {"userId": "candidate:int_0123456789ab"}})
    await s.rejoin.on_candidate_joined(new_id)
    await asyncio.sleep(0.15)


async def test_a_reload_pauses_mitra_then_welcomes_back_and_repeats_the_last_question():
    s = await setup()
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    assert s.presence.candidate_present is False and s.presence.rejoin_pending
    assert s.service.paused[-1] is True and s.coord.away
    await asyncio.sleep(0.3)
    s.t["now"] += 25                               # a 25 s cut on the interview clock
    assert s.service.said == [], "Mitra stays silent while waiting"
    s.presence.observe_join({"id": "p-2", "info": {"userId": "candidate:int_0123456789ab"}})
    await s.rejoin.on_candidate_joined("p-2")
    await asyncio.sleep(0.15)
    assert len(s.service.said) == 1 and WELCOME_BACK in s.service.said[0] and QUESTION in s.service.said[0]
    assert s.service.paused[-1] is False and not s.coord.away and s.presence.candidate_present
    [dropped] = payloads(s, "candidate_dropped")
    assert dropped.detected_by.value == "daily_left" and dropped.participant_id == "p-1"
    [back] = payloads(s, "candidate_returned")
    assert back.same_participant is False and back.counted and back.rejoin_number == 1
    assert s.live.invalid == 0
    record = ConnectionRecord.model_validate(s.rejoin.record())
    assert record.rejoin_count == 1 and record.drops[0].counted and record.drops[0].detected_by.value == "daily_left"
    assert not s.coord.is_over


async def test_a_silent_cut_is_noticed_by_status_readings_and_ends_on_resumed():
    s = await setup()
    # The grace window is measured from the drop's real start; with the scaled 0.6 s
    # window, the signal must carry "now" (a 3 s lead would use up the whole window).
    await s.rejoin.on_signal(SimpleNamespace(kind="dropped", at_seconds=s.t["now"], gap_seconds=None, seq=1))
    assert s.coord.away and s.presence.candidate_present is False, "Daily still shows them; readings say gone"
    assert payloads(s, "candidate_dropped") == [], "the server sends that drop's event, not the bot (C-121)"
    await asyncio.sleep(0.3)
    s.t["now"] += 17
    await s.rejoin.on_signal(SimpleNamespace(kind="resumed", at_seconds=s.t["now"], gap_seconds=20.0, seq=2))
    await asyncio.sleep(0.15)
    [back] = payloads(s, "candidate_returned")
    assert back.same_participant is True and back.counted
    assert WELCOME_BACK in s.service.said[-1]


async def test_a_short_drop_is_not_counted():
    s = await setup()
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    await asyncio.sleep(0.05)
    s.t["now"] += 4                                # 4 s: under the 10 s rule
    s.presence.observe_join(CANDIDATE)
    await s.rejoin.on_candidate_joined("p-1")
    await asyncio.sleep(0.15)
    [back] = payloads(s, "candidate_returned")
    assert back.counted is False and back.rejoin_number is None and back.same_participant is True
    assert s.rejoin.record()["rejoin_count"] == 0


async def test_not_back_within_the_grace_window_ends_silently():
    s = await setup(grace=0.3)
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    await asyncio.sleep(0.6)
    assert s.coord.is_over and s.coord.reason == "candidate_did_not_return"
    assert s.service.said == [], "nothing is said to an empty room"
    assert s.worker.ended, "the call ended"
    record = ConnectionRecord.model_validate(s.rejoin.record())
    assert record.drops[0].ended_seconds is None and not record.drops[0].counted


async def test_the_return_from_a_fourth_counted_drop_ends_with_the_kind_goodbye():
    s = await setup()
    ids = ["p-1", "p-2", "p-3", "p-4", "p-5"]
    for i in range(4):
        s.presence.observe_leave({"id": ids[i]})
        await s.rejoin.on_candidate_left(ids[i])
        await asyncio.sleep(0.25)
        s.t["now"] += 12                           # each a counted 12 s cut
        s.presence.observe_join({"id": ids[i + 1], "info": {"userId": "candidate:int_0123456789ab"}})
        await s.rejoin.on_candidate_joined(ids[i + 1])
        await asyncio.sleep(0.15)
    assert sum(1 for x in s.service.said if WELCOME_BACK in x) == 3
    assert REJOIN_LIMIT_GOODBYE in s.service.said[-1]
    assert s.coord.is_over and s.coord.reason == "rejoin_limit_reached"
    [limit] = payloads(s, "rejoin_limit_reached")
    assert limit.rejoins == 4 and limit.limit == 3
    record = ConnectionRecord.model_validate(s.rejoin.record())
    assert record.limit_reached and record.rejoin_count == 4 and s.live.invalid == 0


async def test_a_connection_that_flaps_on_return_is_one_drop():
    s = await setup(settle=0.2)
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    await asyncio.sleep(0.25)
    s.t["now"] += 15
    s.presence.observe_join({"id": "p-2", "info": {"userId": "candidate:int_0123456789ab"}})
    await s.rejoin.on_candidate_joined("p-2")
    await asyncio.sleep(0.05)                      # gone again before the return settles
    s.presence.observe_leave({"id": "p-2"})
    await s.rejoin.on_candidate_left("p-2")
    await asyncio.sleep(0.05)
    s.presence.observe_join({"id": "p-3", "info": {"userId": "candidate:int_0123456789ab"}})
    await s.rejoin.on_candidate_joined("p-3")
    await asyncio.sleep(0.35)
    assert len(payloads(s, "candidate_returned")) == 1 and len(s.rejoin.record()["drops"]) == 1
    assert sum(1 for x in s.service.said if WELCOME_BACK in x) == 1


async def test_the_hard_stop_during_a_drop_ends_silently():
    s = await setup(grace=5.0)
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    await s.coord._force_end()
    assert s.coord.is_over and s.coord.ending["ending"] == "hard_stop_during_drop" and s.service.said == []
    await asyncio.sleep(0.6)
    await s.rejoin.close()
    assert s.rejoin.record()["drops"][0]["ended_seconds"] is None


async def test_the_end_button_during_a_drop_ends_it_and_stops_the_grace_window():
    s = await setup(grace=0.4)
    await s.rejoin.on_signal(SimpleNamespace(kind="dropped", at_seconds=s.t["now"], gap_seconds=None, seq=1))
    await s.coord.end_by_candidate_button()
    await asyncio.sleep(0.6)
    assert s.coord.reason == "candidate_ended_button", "not overwritten by did_not_return"


async def test_a_return_while_mitra_is_mid_reply_cancels_the_reply_first():
    s = await setup()
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    cancels_at_drop = s.service.cancels
    s.coord._response_active = True                # a reply started meanwhile
    s.presence.observe_join({"id": "p-2", "info": {"userId": "candidate:int_0123456789ab"}})
    await s.rejoin.on_candidate_joined("p-2")
    await asyncio.sleep(0.15)
    assert s.service.cancels == cancels_at_drop + 1 and WELCOME_BACK in s.service.said[-1]


async def test_a_reply_playing_when_they_drop_is_cancelled():
    s = await setup()
    s.coord._bot_speaking = True
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    assert s.service.cancels == 1 and s.service.paused[-1] is True


async def test_the_silence_ladder_never_closes_during_a_drop():
    from bot.silence import SilenceEscalation

    s = await setup(grace=5.0)
    closed = []
    ladder = SilenceEscalation(session_id="t", presence=s.presence, interview_over=lambda: s.coord.is_over,
                               notify=lambda stage, seconds: None,
                               close=lambda reason: closed.append(reason))
    injected = []

    async def inject(frame):
        injected.append(frame)

    ladder._inject = inject
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    for _ in range(6):                             # far past the ladder's close threshold
        await ladder.handle_idle(None)
    assert closed == [] and not any("EndWorkerFrame" in type(f).__name__ for f in injected)
    assert any(e["action"] == "waiting_for_rejoin" for e in ladder.events)


def test_the_engine_no_longer_ends_at_the_first_participant_left():
    from bot import run_realtime

    src = inspect.getsource(run_realtime)
    gone = src[src.index("async def candidate_gone"):src.index("async def participant_changed")]
    assert "coordinator.candidate_dropped()" not in gone
    assert "await rejoin.on_candidate_left(" in gone
    assert "on_candidate_rejoined=" in src and "rejoin.start()" in src and '"connection": rejoin.record()' in src


async def test_a_returned_drops_gap_ends_at_the_resumed_signals_own_time():
    """B-104 (1): the gap ends when the first reading came back (signal.at_seconds),
    not on the bot's clock after polling and the settle. Replayed: readings stop at
    100 s, come back at 120 s; the bot reads the "resumed" signal 2.8 s later (a poll
    and a slow loop), then settles. The server's gap is 20 s; the bot's must be within
    about 1 s of it, and the counting rule is applied to that gap."""
    s = await setup(settle=0.05)
    await s.rejoin.on_signal(SimpleNamespace(kind="dropped", at_seconds=100.0, gap_seconds=None, seq=1))
    s.t["now"] = 122.8                                  # the bot reads the signal late
    await s.rejoin.on_signal(SimpleNamespace(kind="resumed", at_seconds=120.0, gap_seconds=20.0, seq=2))
    s.t["now"] = 124.4                                  # ...and the settle passes
    await asyncio.sleep(0.15)
    [back] = payloads(s, "candidate_returned")
    assert abs(back.gap_seconds - 20.0) <= 1.0, back.gap_seconds
    assert s.rejoin.drops[-1]["ended_seconds"] == 120.0


async def test_a_9_second_cut_read_late_is_still_not_counted():
    """The 10 s rule depends on the gap: a 9 s cut whose resumed signal is read 2.8 s
    late must stay uncounted."""
    s = await setup(settle=0.05)
    await s.rejoin.on_signal(SimpleNamespace(kind="dropped", at_seconds=100.0, gap_seconds=None, seq=1))
    s.t["now"] = 111.8
    await s.rejoin.on_signal(SimpleNamespace(kind="resumed", at_seconds=109.0, gap_seconds=9.0, seq=2))
    await asyncio.sleep(0.15)
    [back] = payloads(s, "candidate_returned")
    assert back.counted is False and back.gap_seconds == 9.0


async def test_a_reload_gap_ends_at_the_join_not_after_the_settle():
    s = await setup(settle=0.05)
    s.presence.observe_leave(CANDIDATE)
    await s.rejoin.on_candidate_left("p-1")
    s.t["now"] += 15
    joined_at = s.t["now"]
    s.presence.observe_join({"id": "p-2", "info": {"userId": "candidate:int_0123456789ab"}})
    await s.rejoin.on_candidate_joined("p-2")
    s.t["now"] += 1.5                                    # the settle passes on the clock
    await asyncio.sleep(0.15)
    assert s.rejoin.drops[-1]["ended_seconds"] == round(joined_at, 2)
