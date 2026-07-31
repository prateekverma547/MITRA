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

    def left(self, participant_id: str, reason: str = "") -> None:
        if participant_id in self._present:
            self._present.discard(participant_id)
            self.disconnects += 1
            logger.info(
                f"participant left ({reason or 'no reason given'}); "
                f"{len(self._present)} left in the room"
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
