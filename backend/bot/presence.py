"""Who is actually in the room.

Daily tells us this outright, and until now nothing listened. Two things follow
from that silence.

The interviewer nudged into empty rooms. "Are you still there?" to somebody who
disconnected two minutes ago is not patience, it is talking to furniture, and
then the session closed itself blaming a silence that was really a dropped
call. Knowing they left turns a guess into a fact.

And a second person joining is certain knowledge rather than inference. Whether
that matters is the employer's judgement, not ours, so it is recorded and
mentioned once, never policed. Someone may well have a partner bringing them
tea.

Nothing here decides anything on its own. It answers "is the candidate in the
room", so the silence ladder can stop guessing.

**Who is the candidate (F-028 fix, option a).** Pipecat reports every
participant the same way, so a watcher joining used to restart the interview and
a watcher leaving used to end it. Now the candidate's meeting token carries a
Daily `user_id` of `candidate_user_id(interview_id)`, and only that participant
drives the interview. Everyone else is a watcher: logged and recorded in the
session metrics, never acted on. For tokens minted before the mark existed, the
first non-owner participant is taken as the candidate if nobody with the mark
has been seen (see `_classify`). The bot itself is the local participant and
never reported; it is also the only owner.
"""

import time
from dataclasses import dataclass, field
from enum import StrEnum

from loguru import logger

#: Prefix of the Daily `user_id` that marks this interview's candidate.
CANDIDATE_MARK_PREFIX = "candidate:"
#: Daily's limit on a meeting token's user_id ("The `user_id` has a limit of 36
#: characters", Daily create-meeting-token reference).
DAILY_USER_ID_MAX = 36


def candidate_user_id(interview_id: str) -> str:
    """The user_id put on the candidate's token, e.g. "candidate:int_0123456789ab".

    Interview ids are "int_" plus 12 hex characters (app/interviews.py), so the
    mark is 26 characters, inside Daily's 36.
    """
    mark = f"{CANDIDATE_MARK_PREFIX}{interview_id}"
    if len(mark) > DAILY_USER_ID_MAX:
        raise ValueError(f"candidate mark is {len(mark)} characters; Daily allows {DAILY_USER_ID_MAX}")
    return mark


class Role(StrEnum):
    CANDIDATE = "candidate"
    WATCHER = "watcher"


def _info(participant) -> dict:
    return (participant or {}).get("info") or {}


def participant_user_id(participant) -> str:
    """daily-python puts it in info.userId; daily-js style is user_id. Both read."""
    return str(_info(participant).get("userId") or (participant or {}).get("user_id") or "")


def participant_is_owner(participant) -> bool:
    info = _info(participant)
    return bool(info.get("isOwner", (participant or {}).get("owner", False)))


@dataclass
class Arrival:
    role: Role
    #: The first candidate connection of the session: start the interview.
    starts_interview: bool = False


@dataclass
class Departure:
    role: Role
    #: No candidate connection is left in the room.
    last_candidate_gone: bool = False
    #: The interview had already finished when they left (a natural end).
    interview_over: bool = False
    #: The Daily participant id that left (rejoin: a reload comes back as a new one).
    participant_id: str = ""


@dataclass
class RoomPresence:
    """Live count of who is in the call besides the bot.

    Deliberately not a Pipecat observer: these are transport events, not frames,
    and the ladder needs a plain question answered rather than a frame stream.
    """

    #: Participant ids currently in the room, excluding the bot itself.
    _present: set[str] = field(default_factory=set)
    #: Ids seen at any point, so a rejoin is not counted as a fresh arrival.
    _seen: set[str] = field(default_factory=set)

    #: Answers "has the interview already finished?", supplied by the brain.
    #: Optional, and without it this counts every departure exactly as it used
    #: to, which keeps a transport that cannot report presence, and the text
    #: harness, working rather than broken. Same shape as the callables
    #: `SilenceEscalation` already takes for the same reason.
    interview_over: object = None

    disconnects: int = 0
    #: The most candidate connections at once (watchers excluded). Above one
    #: means a second tab or device.
    peak_others: int = 0

    #: This interview's candidate mark (candidate_user_id), or None when the
    #: bot runs without an interview id (scripts), which uses the fallback.
    candidate_mark: str | None = None
    #: How the candidate was recognised: "user_id" or "fallback_first_non_owner".
    candidate_identified_by: str | None = None
    #: Joins and leaves of everyone who is not the candidate.
    watcher_events: list = field(default_factory=list)
    #: The candidate dropped and the rejoin grace window is running (bot/rejoin.py),
    #: whether Daily still shows them or not (D-27: a silent cut under about 35 s is
    #: invisible to Daily). The ladder treats them as absent and never closes.
    rejoin_pending: bool = False
    _ever_candidate: bool = False
    _mark_seen: bool = False
    _fallback_ids: set = field(default_factory=set)
    _watchers: set = field(default_factory=set)
    _clock: float = field(default_factory=time.monotonic)

    def joined(self, participant_id: str) -> None:
        if not participant_id:
            return
        rejoining = participant_id in self._seen
        self._present.add(participant_id)
        self._seen.add(participant_id)
        self.peak_others = max(self.peak_others, len(self._present))
        logger.info(
            f"participant {'re' if rejoining else ''}joined; "
            f"{len(self._present)} in the room besides the interviewer"
        )

    def _already_over(self) -> bool:
        """Whether the interview had finished when somebody left.

        A bad reading must never invent a disconnect, so anything unexpected
        here falls back to counting the departure, which is the old behaviour
        and the safe direction: over-reporting a dropped call is recoverable by
        reading the transcript, and silencing a real one is not.
        """
        if self.interview_over is None:
            return False
        try:
            return bool(self.interview_over())
        except Exception:  # noqa: BLE001
            return False

    # -- the role layer: whole Daily participants, not ids -------------------

    def _classify(self, participant) -> Role:
        pid = (participant or {}).get("id", "")
        uid = participant_user_id(participant)
        if self.candidate_mark and uid == self.candidate_mark:
            if self.candidate_identified_by == "fallback_first_non_owner":
                # A marked candidate outranks a guess: the guessed participant
                # becomes a watcher from now on.
                for guessed in list(self._fallback_ids):
                    self._present.discard(guessed)
                    self._watchers.add(guessed)
                self._fallback_ids.clear()
                logger.warning("candidate mark seen after the fallback guess; guess demoted to watcher")
            self._mark_seen = True
            self.candidate_identified_by = "user_id"
            return Role.CANDIDATE
        if pid in self._fallback_ids:
            return Role.CANDIDATE
        if not uid and not participant_is_owner(participant) and not self._ever_candidate:
            # Compatibility: a token minted before the mark existed has no
            # user_id. If nobody with the mark has been seen, the first
            # non-owner human is the candidate. Later unmarked joins are watchers.
            self._fallback_ids.add(pid)
            self.candidate_identified_by = "fallback_first_non_owner"
            return Role.CANDIDATE
        return Role.WATCHER

    def _watcher_event(self, kind: str, participant, reason: str = "") -> None:
        event = {
            "kind": kind,
            "at_seconds": round(time.monotonic() - self._clock, 2),
            "participant_id": (participant or {}).get("id", ""),
            "has_user_id": bool(participant_user_id(participant)),
            "owner": participant_is_owner(participant),
        }
        if reason:
            event["reason"] = reason
        self.watcher_events.append(event)
        logger.info(f"watcher {kind} ({event['participant_id']}); the interview is not affected")

    def observe_join(self, participant) -> Arrival:
        """A Daily participant joined. Returns what the engine should do."""
        pid = (participant or {}).get("id", "")
        if not pid:
            return Arrival(role=Role.WATCHER)
        if self._classify(participant) is Role.WATCHER:
            self._watchers.add(pid)
            self._watcher_event("joined", participant)
            return Arrival(role=Role.WATCHER)
        first = not self._ever_candidate
        self._ever_candidate = True
        self.joined(pid)
        logger.info(
            f"candidate {'joined' if first else 'connected again (repeat join)'} "
            f"by {self.candidate_identified_by}"
        )
        return Arrival(role=Role.CANDIDATE, starts_interview=first)

    def observe_leave(self, participant, reason: str = "") -> Departure:
        """A Daily participant left. Returns what the engine should do."""
        pid = (participant or {}).get("id", "")
        if pid not in self._present:
            if pid:
                self._watchers.discard(pid)
                self._watcher_event("left", participant, reason)
            return Departure(role=Role.WATCHER)
        # Only the last candidate connection leaving is a disconnect; a second
        # tab closing while another is still connected is not.
        last = len(self._present) == 1
        self.left(pid, reason, count=last)
        return Departure(
            role=Role.CANDIDATE,
            last_candidate_gone=not self._present,
            interview_over=self._already_over(),
            participant_id=pid,
        )

    def left(self, participant_id: str, reason: str = "", *, count: bool = True) -> None:
        """Record a departure, and decide whether it was a fault.

        **An interview ends with the candidate leaving.** Counting every
        departure therefore recorded a disconnect on every single interview,
        and `disconnects > 0` is enough to mark a report degraded, so every
        report claimed a poor recording on the strength of a normal goodbye.
        Six of six stored interviews, no exceptions, no correct hits.

        What separates them is not presence. A candidate whose connection dies
        mid-answer and never returns looks identical at the end to one who hangs
        up after being thanked: one departure, empty room. The brain is what
        knows the difference, because it knows whether the interview had reached
        its close, so it is asked.

        Both directions matter. A departure before the interview finished is a
        real drop and is still counted, whether or not they come back, because a
        truncated interview is exactly when the report most needs to explain
        itself.
        """
        if participant_id not in self._present:
            return

        self._present.discard(participant_id)
        remaining = len(self._present)

        if self._already_over():
            logger.info(
                f"participant left after the interview finished "
                f"({reason or 'no reason given'}); not a disconnect"
            )
            return
        if not count:
            logger.info(f"one candidate connection closed, another remains ({reason or 'no reason given'})")
            return

        self.disconnects += 1
        logger.info(
            f"participant left mid-interview ({reason or 'no reason given'}); "
            f"{remaining} left in the room"
        )

    def is_candidate_connection(self, participant_id: str) -> bool:
        """This Daily participant id is one of the candidate's connections now (never a
        watcher). Used by the candidate audio capture (bot/capture.py)."""
        return participant_id in self._present

    @property
    def candidate_present(self) -> bool:
        """False when the room is empty of anyone but the bot.

        The ladder uses this to hold rather than escalate. Someone who dropped
        out may be rejoining right now, and closing their interview while they
        reconnect would end it over a network blip.
        """
        return bool(self._present) and not self.rejoin_pending

    @property
    def others_in_the_room(self) -> bool:
        """True when more than one person is in the call with the interviewer."""
        return len(self._present) > 1

    def summary(self) -> dict:
        return {
            "disconnects": self.disconnects,
            "peak_others": self.peak_others,
            "present_at_end": len(self._present),
            "candidate_identified_by": self.candidate_identified_by,
            "watcher_events": list(self.watcher_events),
        }


def attach(transport, presence: RoomPresence, *, on_candidate_arrived=None,
           on_candidate_gone=None, on_change=None, on_candidate_rejoined=None) -> RoomPresence:
    """Wire the transport's participant events into presence and the engine.

    The engines no longer listen to Pipecat's `on_client_connected` /
    `on_client_disconnected`, which fire for every participant
    (pipecat/transports/daily/transport.py:3031,3043). They get exactly two
    callbacks instead:

    - `on_candidate_arrived(participant)`: the first candidate connection only.
      A repeat join (second tab, quick return) does not call it again.
    - `on_candidate_gone(departure)`: the last candidate connection has left.
    """

    @transport.event_handler("on_participant_joined")
    async def _joined(_transport, participant):
        arrival = presence.observe_join(participant or {})
        if on_change is not None:
            await on_change("joined", participant or {}, "")
        if arrival.starts_interview and on_candidate_arrived is not None:
            await on_candidate_arrived(participant)
        elif arrival.role is Role.CANDIDATE and not arrival.starts_interview and on_candidate_rejoined is not None:
            # A candidate connection after the first: a reload or a reconnect (rejoin).
            await on_candidate_rejoined(participant)

    @transport.event_handler("on_participant_left")
    async def _left(_transport, participant, reason=""):
        departure = presence.observe_leave(participant or {}, str(reason or ""))
        if on_change is not None:
            await on_change("left", participant or {}, str(reason or ""))
        if departure.last_candidate_gone and on_candidate_gone is not None:
            await on_candidate_gone(departure)

    return presence
