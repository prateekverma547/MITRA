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
"""

from dataclasses import dataclass, field

from loguru import logger


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
    #: The most people in the room at once, besides the bot. Above one means
    #: somebody else was there.
    peak_others: int = 0

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

    def left(self, participant_id: str, reason: str = "") -> None:
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

        self.disconnects += 1
        logger.info(
            f"participant left mid-interview ({reason or 'no reason given'}); "
            f"{remaining} left in the room"
        )

    @property
    def candidate_present(self) -> bool:
        """False when the room is empty of anyone but the bot.

        The ladder uses this to hold rather than escalate. Someone who dropped
        out may be rejoining right now, and closing their interview while they
        reconnect would end it over a network blip.
        """
        return bool(self._present)

    @property
    def others_in_the_room(self) -> bool:
        """True when more than one person is in the call with the interviewer."""
        return len(self._present) > 1

    def summary(self) -> dict:
        return {
            "disconnects": self.disconnects,
            "peak_others": self.peak_others,
            "present_at_end": len(self._present),
        }


def attach(transport, presence: RoomPresence) -> RoomPresence:
    """Wire the transport's participant events into a presence tracker."""

    @transport.event_handler("on_participant_joined")
    async def _joined(_transport, participant):
        presence.joined((participant or {}).get("id", ""))

    @transport.event_handler("on_participant_left")
    async def _left(_transport, participant, reason=""):
        presence.left((participant or {}).get("id", ""), str(reason or ""))

    return presence
