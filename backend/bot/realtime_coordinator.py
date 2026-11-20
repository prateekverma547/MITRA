"""The realtime interview's guarantees, and the one way code talks to Mitra.

Round 14 (Karandeep's decisions): one voice, the model's. Mitra makes every decision
that needs understanding, through tools; code keeps only what she cannot sense or do,
plus the guarantees. One owner per decision:

  Mitra (through her tools):
    - end_interview(reason): "withdrew" after the candidate confirmed they want to stop,
      or "completed" when the plan is covered; she says goodbye first.
    - move_on(): she has finished the current area; code shows her the next one.
    - every word she says, pacing, clarifying, repeating, declines, skips, follow-ups.
  Code (this file and its callers):
    - the clock: a few time notes, and the hard stop (never later than minute 58);
    - drops and rejoins (bot/rejoin.py), presence, the long-silence close (bot/silence.py),
      the End button: each ends by code, after a private note asks her to say goodbye;
    - carrying out an accepted end_interview: hang up once she stops speaking, with
      the silent backstop;
    - two light guards on end_interview, on facts code can check without reading words.

How code tells Mitra something: `tell()`. A short private note in her conversation,
then (when she should respond now) an ordinary reply request: her permanent
instructions, tone and warmth rules included, apply to every word. Never while she or
the candidate is speaking, except the hard stop and the End button (her reply is
cancelled first). During a drop nothing is sent until they return.

It is an **observer**, for the reason `bot/ending.py` documents: it must see
`BotStoppedSpeakingFrame`, which comes from the output transport. Not a dataclass:
observers live in a set and must stay hashable.
"""

import asyncio
import re
import time
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.llm_service import FunctionCallResultProperties

from bot.brain.refusal import declined_this_question
from bot.brain.repair import _normalise
from bot.brain.withdrawal import skipped_this_question
from bot.realtime_prompt import NOTE_PREFIX

END_INTERVIEW = "end_interview"
MOVE_ON = "move_on"
#: end_interview's reasons and the EndReason each records (shared/interview_contracts.py).
WITHDREW = "withdrew"
COMPLETED = "completed"
_REASON_RECORDED = {WITHDREW: "candidate_withdrew", COMPLETED: "interview_complete"}

_END_INTERVIEW_SCHEMA = FunctionSchema(
    name=END_INTERVIEW,
    description=(
        "End the interview. Call it only AFTER you have said a warm goodbye in your own words. "
        'reason "withdrew": the candidate said they want to stop, you asked them once whether '
        "they want to end the interview here, and they said yes. "
        'reason "completed": you have covered the plan (you are in the closing), or time is '
        "nearly up. Never call it in the middle of a question or while waiting for an answer."
    ),
    properties={"reason": {"type": "string", "enum": [WITHDREW, COMPLETED],
                           "description": '"withdrew" or "completed".'}},
    required=["reason"],
)

_MOVE_ON_SCHEMA = FunctionSchema(
    name=MOVE_ON,
    description=(
        "Call this when you have finished the current area and want to start the next one. "
        "The next area and its questions are then shown to you. Call it before saying "
        "anything about the next area; then give a short, warm transition and ask its first "
        "question."
    ),
    properties={},
    required=[],
)

REALTIME_TOOLS = ToolsSchema(standard_tools=[_END_INTERVIEW_SCHEMA, _MOVE_ON_SCHEMA])

#: If the goodbye was already over by the time the end was accepted, there will be no
#: BotStoppedSpeaking to end on. Hang up after this.
_ENDED_WHILE_SILENT_SECONDS = 2.0
#: How long to wait for a goodbye that never comes before hanging up anyway (the
#: silent backstop: no line is ever spoken in its place).
_GOODBYE_TIMEOUT_SECONDS = 20.0
#: After the greeting stops playing, wait this long before listening (the speaker tail).
_GREETING_TAIL_SECONDS = 0.3
#: Never stay deaf longer than this, even if the greeting never finishes.
_GREETING_MAX_SECONDS = 20.0
#: How long an accepted end_interview waits for its reply's record (response.done),
#: which follows the call's arguments within milliseconds.
_REPLY_RECORD_WAIT_SECONDS = 3.0
#: A goodbye that began while earlier audio still played: after a "stopped speaking",
#: wait this long for the goodbye's own audio to start before treating that stop as the
#: end of one continuous stretch (round 16 real calls: a 0.9 s gap cut the goodbye).
_GOODBYE_GAP_SECONDS = 1.5
#: How often a waiting note checks whether both are quiet.
_QUIET_POLL_SECONDS = 0.05
#: An urgent note waits at most this long for cancelled audio to stop playing.
_URGENT_DRAIN_SECONDS = 3.0

#: At the hard stop her reply is cancelled, a note asks for a short goodbye, and the
#: call is cut at this many seconds after the hard stop whatever is playing. Measured
#: Mitra replies ran 2.55 to 6.5 s (A-28); twelve seconds is about twice the longest.
FORCED_END_REPLY_CAP_SECONDS = 12.0

#: OpenAI ends a Realtime session at 60 minutes. The hard stop happens by this minute
#: whatever the plan says. Worst case after it: the 12 s cap, then the hang-up.
FORCED_END_BY_MINUTES = 58.0

#: The "completed" guard: time counts as nearly up from the closing's own planned
#: minutes before the booked end (the booked end itself capped at FORCED_END_BY_MINUTES),
#: so a 40-minute plan with a 2-minute closing accepts "completed" from minute 38. See
#: A_findings A-86 for why this threshold.
def nearly_up_seconds(blueprint) -> float:
    spec = blueprint.evaluation_spec
    booked = min(float(spec.duration_minutes), FORCED_END_BY_MINUTES)
    return max(0.0, booked - float(blueprint.closing_minutes)) * 60

#: Round 15 pacing (Karandeep: one bounded fix). Each area's ALLOWANCE is the smaller
#: of its planned minutes and its fair share of the time left once the closing's planned
#: minutes are set aside, so the closing always keeps its time. A concrete note comes at
#: NOTE_POINT of the allowance ("about one more question here, then move on"): round 14's
#: timelines show she moves about one exchange after a note (A-87), so a note at 80%
#: lands her move near the allowance's end. She decides; code does not move her for it.
NOTE_POINT = 0.8
#: An exchange before any is measured, and the most one may count for (minutes).
DEFAULT_EXCHANGE_MINUTES = 1.5
MAX_EXCHANGE_MINUTES = 4.0
#: Set aside on top of the closing's planned minutes (see `allowance`); at least one
#: measured exchange.
CLOSING_MARGIN_MINUTES = 2.0
#: What every later area keeps of its plan when an earlier one runs over (the backstop).
AREA_FLOOR = 0.5
#: The backstop's floor gap after the note, in minutes, when the allowance is tiny.
_BACKSTOP_AFTER_NOTE_MINUTES = 0.5

#: The pacing guarantee (Karandeep): when an area passes this multiple of its planned
#: minutes, or its fair share of the time left (whichever is earlier), code moves to the
#: next area at the next quiet moment and tells her, so she makes the transition in her
#: own words. A guarantee like the hard stop, not a second decider. 1.5 since round 15
#: (2.0 in round 14 never fired: areas ran about 1.6x and the lag added up, A-87).
BACKSTOP_FACTOR = 1.5
NOTE_BACKSTOP = ("The previous area has used its share of the time, so the interview has now moved on to "
                 "{area}: you are in it now and its questions are shown, so do not call move_on for it. At "
                 "your next turn, make a short, warm transition in your own words and ask its first "
                 "question. {remaining}")
NOTE_BACKSTOP_TO_CLOSING = ("The last area has used its share of the time, so the interview has now moved on "
                           "to the closing: you are in it now. At your next turn, thank them warmly and say a "
                           "one-sentence goodbye with their first name, then call end_interview with reason "
                           "\"completed\".")

#: How an interview ended, recorded in session_metrics["ending"].
ENDING_COMPLETED = "completed"
ENDING_WITHDREW = "withdrew"
ENDING_FORCED = "forced_at_hard_stop"
ENDING_END_BUTTON = "candidate_ended_button"
ENDING_SILENCE = "silence_close"
ENDING_DID_NOT_RETURN = "candidate_did_not_return"
ENDING_REJOIN_LIMIT = "rejoin_limit_reached"
ENDING_HARD_STOP_DURING_DROP = "hard_stop_during_drop"

#: The private notes. Calm information; the words she says are her own. Each is one
#: of code's decisions (the clock, a drop, a silence, the End button) told to her.
NOTE_HALFWAY = ("About {minutes} minutes in: half of the booked {booked} minutes have passed. You are in "
                "{area}. {remaining}")
NOTE_TEN_LEFT = "About 10 minutes of the booked interview time are left. You are in {area}. {remaining}"
NOTE_THREE_LEFT = "About 3 minutes of the booked interview time are left. You are in {area}. {remaining}"
NOTE_BOOKED_OVER = ("The booked {booked} minutes are up. {unfinished}Wrap up now: thank them warmly, say goodbye "
                    "with their first name, then call end_interview with reason \"completed\". The interview "
                    "stops for good in about {grace} minutes.")
#: Track H, 2026-11-20 (product owner): a candidate who says they don't know to this
#: many questions in a row in one area has nothing more to give there; the next area
#: starts. Counted in code, because asked in the prompt she did it 1 time in 6.
DONT_KNOW_LIMIT = 2
#: An answer saying they don't know: short (a long answer that mentions not knowing
#: something is still an answer), and containing one of these, after the same
#: normalising as the repair detector (bot/brain/repair.py).
DONT_KNOW_PHRASES = ("i dont know", "i do not know", "i have no idea", "no idea", "i dont have any idea",
                     "not sure about", "i am not sure", "im not sure", "i dont really know")
DONT_KNOW_MAX_WORDS = 25
NOTE_DONT_KNOWS_MOVED = ("The candidate has said they don't know twice in a row, so the interview has moved on "
                         "to {area}: you are in it now and its questions are shown, so do not call move_on for "
                         "it. Say kindly that it is completely fine, then make a short, warm transition in your "
                         "own words and ask its first question.")
NOTE_DONT_KNOWS_MOVED_NEXT = ("The candidate has said they don't know twice in a row, so the interview has moved "
                              "on to {area}: you are in it now and its questions are shown, so do not call "
                              "move_on for it. At your next turn, make a short, warm transition in your own words "
                              "and ask its first question.")


#: Track H, 2026-11-20 (product owner): a topic the candidate offers a second time, that
#: the job uses, is followed for one or two questions and then the plan resumes. Spotted
#: and counted in code: asked in the prompt alone, she followed it 0 times in 9.
TOPIC_OFFER = re.compile(
    r"\b(?:i (?:really |do |also |actually )?know(?: a lot)?(?: about)?|ask me(?: some)?(?: questions)? (?:about|on)"
    r"|i have worked(?: a lot)? (?:on|with)|i am good at|im good at|i am strong in|im strong in)"
    r"\s+(?P<topic>[a-z0-9+#. -]{2,40}?)(?=\s+(?:well|very|better|a lot|and|but|so|instead|too)\b|[,.!?]|$)")
_TOPIC_STOP = {"it", "that", "this", "them", "about", "the", "a", "an", "some", "more", "things", "stuff", "anything",
               "everything", "something", "much", "lot", "really", "how", "what"}
NOTE_FOLLOW_TOPIC = ("The candidate has now offered {topic} more than once. If {topic} is relevant to this "
                     "role (something a {role} would use or need), follow it now: acknowledge it in a few words, "
                     "then make your very next question about {topic} itself, named in the question and aimed "
                     "at what one of the plan's goals needs to find out. After their answer you may ask one more about it; then go back to the plan's next "
                     "question. If it is not relevant to this role, acknowledge it briefly and carry on with "
                     "the plan.")


def offered_topic(text: str) -> str | None:
    """The topic a candidate offers instead ("I know about LLMs", "ask me about AWS"), or None."""
    match = TOPIC_OFFER.search(_normalise(text))
    if not match:
        return None
    words = [w for w in match.group("topic").split() if w not in _TOPIC_STOP]
    if not words or len(" ".join(words)) < 2:
        return None
    return " ".join(words[:4])


def _topic_key(topic: str) -> str:
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in topic.split())


def said_dont_know(text: str) -> bool:
    """A short answer that says they don't know ("I don't know about AWS, ask me about
    Google Cloud" counts; a long answer that mentions not knowing something does not)."""
    said = _normalise(text)
    return len(said.split()) <= DONT_KNOW_MAX_WORDS and any(p in said for p in DONT_KNOW_PHRASES)


#: Track H, 2026-11-20: "finish the thread", not "one more question", so a follow-up the
#: candidate's answer has opened is finished rather than dropped for the next goal.
NOTE_AREA_SOON = ("Time in this area ({area}) is nearly used: finish the thread you are on, with at "
                  "most one more question, then move on. {remaining}")
NOTE_HARD_STOP = ("Time is up: the interview has to end now. Say a warm goodbye with their first name "
                  "in ONE short sentence. Do not ask anything.")
NOTE_WELCOME_BACK = ("The candidate has just reconnected after a short drop in the connection. "
                     "The last question you asked was: \"{question}\". Welcome them back warmly and "
                     "ask it again (you may rephrase it).")
NOTE_WELCOME_BACK_NO_QUESTION = ("The candidate has just reconnected after a short drop in the "
                                 "connection. Welcome them back warmly and pick up where you left off.")
NOTE_REJOIN_LIMIT = ("The candidate's connection has dropped several times, so the interview has to "
                     "stop now. Kindly explain that the connection keeps dropping, thank them, and say "
                     "goodbye with their first name. Do not ask anything.")
NOTE_END_BUTTON = ("The candidate pressed End and confirmed that they want to finish now. Say a short, "
                   "warm goodbye with their first name. Do not ask anything.")
NOTE_QUIET = ("The candidate has been quiet for about {seconds} seconds since your last turn. They may "
              "be thinking. You decide whether to keep waiting or to say something gentle.")
NOTE_AUDIO_CHECK = ("There has been no sound from the candidate for about {seconds} seconds. Their "
                    "microphone may not be reaching you.")
NOTE_SILENCE_CLOSE = ("There has been no sound from the candidate for about two minutes, so the "
                      "interview closes now. Tell them you haven't heard them for a while, that they "
                      "can rejoin if this was a technical problem, and thank them. Keep it short.")

#: What a refused end_interview tells her, calmly. Facts only.
REFUSE_WITHDREW = ("Not accepted: the candidate has not answered a question from you since they last "
                   "spoke about stopping. If they want to stop, ask them once whether they want to end "
                   "the interview here, and wait for their answer. Otherwise carry on calmly.")
REFUSE_COMPLETED = ("Not accepted: the plan is not finished yet ({reached} of {areas} areas reached) "
                    "and there is still time. Carry on calmly with the interview; do not mention this.")
#: The first move out of the opening. Round 18 (A-113) had her say "let's begin" here,
#: when the opening was a two-exchange warm-up. Track H, 2026-11-20 (product owner):
#: with an opening that is a real conversation about their work, "let's begin" in the
#: middle sounded as if the system had restarted. She now bridges in her own words:
#: from the overview they gave into more detail, naming where it goes. Described,
#: never quoted, so she does not copy a line. Given only here, never in her permanent
#: instructions (which every area keeps).
_BRIDGE_FROM_OPENING = (
    "lead from something specific they just told you into more detail: say, in your own words, that the "
    "conversation now goes deeper into their work and the topics this role involves, and name the first "
    "topic in plain words.")
_NOT_A_START = ("Never word it as the interview beginning or starting: you have been talking since hello, "
                "and an announced start sounds as if the conversation broke.")
MOVE_ON_FIRST = ("The getting-to-know part is done; from here the conversation goes into more detail. Your next "
                 "turn has three parts, in this order, and needs all three:\n"
                 "1. One short, genuine sentence about something specific they just told you, showing "
                 "curiosity about it (never praise, never repeat back what they said, never a bare okay).\n"
                 "2. From that, " + _BRIDGE_FROM_OPENING + "\n"
                 "3. This area's first question.\n" + _NOT_A_START)
#: The backstop's move out of the opening: the same bridge as MOVE_ON_FIRST.
NOTE_BACKSTOP_FROM_OPENING = ("The opening has used its share of the time, so the interview has now moved on to "
                              "{area}: you are in it now and its questions are shown, so do not call move_on for "
                              "it. At your next turn, " + _BRIDGE_FROM_OPENING
                              + " Then ask this area's first question. " + _NOT_A_START + " {remaining}")
MOVE_ON_NEXT = ("The interview is already under way. Open with a short, warm transition in your own "
                "words, then ask this area's first question. Never word it as the interview beginning, "
                "starting or starting over.")
#: Round 18 (A-114): the opening's "one more question" note comes no sooner than this
#: and only after the candidate has answered at least OPENING_NOTE_MIN_ANSWERS times.
OPENING_NOTE_FLOOR_SECONDS = 60.0
OPENING_NOTE_MIN_ANSWERS = 2
TOOL_SAY_GOODBYE = ("Accepted. Now say your warm goodbye with their first name, in one or two short "
                    "sentences; if you have already said goodbye, a brief \"Take care\" is enough. Do not "
                    "ask anything.")


class RealtimeCoordinator(BaseObserver):
    """Clock notes, tools, code's endings and hanging up, for the realtime engine."""

    def __init__(self, *, blueprint, llm, session_id: str = "", area_instruction=None):
        super().__init__()
        self._blueprint = blueprint
        self._llm = llm
        self._session_id = session_id
        self._worker = None
        #: area number -> full instructions (realtime_prompt.build_area_instruction).
        self._area_instruction = area_instruction
        #: 1 = opening, 2.. = competencies, last = closing (realtime_prompt numbering).
        self._area = 1
        #: (area, entered at, left at) on the interview clock: pacing, for the record.
        self._area_log: list[dict] = [{"area": 1, "entered_seconds": 0.0, "left_seconds": None}]
        self._areas_over_noted: set[int] = set()
        #: One area move at a time, whoever asks (her move_on or the backstop).
        self._area_lock = asyncio.Lock()
        self._backstop_task: asyncio.Task | None = None
        self._backstop_moves = 0
        self._note_tasks: set = set()
        #: When each candidate turn began (interview clock): the exchange length.
        self._candidate_turn_times: list[float] = []
        #: The area shown when her current reply began (what a move_on in it refers to).
        self._reply_area: int | None = None
        #: Wall time of her first move into the plan (area 2): turns before it are
        #: the small talk, stored as pre-interview (bot/persistence.py).
        self.plan_started_wall: float | None = None

        self._started_at: float | None = None
        self._clock_task: asyncio.Task | None = None
        self._time_notes_sent: set[str] = set()
        self._last_time_note_id: str | None = None
        self._note_generation = None
        self._note_lock = asyncio.Lock()
        self._events: list[dict] = []
        self._seen: set = set()

        self._bot_speaking = False
        self._candidate_speaking = False
        self._response_active = False
        #: (area, consecutive "I don't know" answers in it): DONT_KNOW_LIMIT ends the area.
        self._dont_knows: tuple[int, int] = (0, 0)
        self._dont_know_tasks: set[asyncio.Task] = set()
        #: Topics the candidate offered instead of answering: key -> times, and the ones followed.
        self._topic_offers: dict[str, int] = {}
        self._topics_followed: set[str] = set()
        #: Who spoke, in order, for the "withdrew" guard: "C" (the candidate) and
        #: ("M", response id) for each of her replies that spoke.
        self._flow: list = []
        #: Live: the candidate's speech is known from OpenAI's VAD frames, which come
        #: promptly; transcripts arrive up to seconds later and are then not used for
        #: the flow (a late transcript must never look like a fresh answer).
        self._vad_seen = False

        self._over = False
        self._reason = "interview_complete"
        self._awaiting_goodbye = False
        #: A reply started after the ending was decided (see LLMFullResponseEndFrame).
        self._goodbye_reply_started = False
        #: The goodbye reply: whether audio was playing when it began, and how many times
        #: the bot started speaking since (its own audio starting).
        self._goodbye_began_while_speaking = False
        self._goodbye_audio_starts = 0
        self._gap_task: asyncio.Task | None = None
        self._end_on_next_stop = False
        self._ended = False
        self._greeting_done = False
        self._force_task: asyncio.Task | None = None
        self._away = False
        self._live = None
        self._auditor = None
        self._ending: str | None = None
        self._hang_up_by: str | None = None
        #: Accepted end_interview calls whose reply had not spoken (she was asked to
        #: say goodbye in the next reply).
        self._ended_without_goodbye = 0

    # -- wiring -----------------------------------------------------------

    def attach(self, worker) -> None:
        self._worker = worker

    def attach_auditor(self, auditor) -> None:
        """Tell the auditor (bot/auditor.py) about skips and declines. Record only."""
        self._auditor = auditor

    def attach_events(self, live) -> None:
        self._live = live
        live.area_now = lambda: self._area  # C-A1: each turn carries its area

    def tool_handlers(self) -> dict:
        return {END_INTERVIEW: self._handle_end_interview, MOVE_ON: self._handle_move_on}

    def register_tools(self) -> None:
        # Never cancelled by the candidate speaking: an accepted ending must finish
        # its record, and a move_on must finish its swap.
        for name, handler in self.tool_handlers().items():
            self._llm.register_function(name, handler, cancel_on_interruption=False)

    def start(self) -> None:
        """The interview clock starts when the greeting does."""
        if self._started_at is not None:
            return
        self._started_at = time.monotonic()
        self._clock_task = asyncio.create_task(self._run_clock())
        # Deaf until the greeting is over (a candidate's "hello? hello?" restarted it
        # three times, found live). Audio handling, not a decision about her words.
        self._llm.set_audio_input_paused(True)
        asyncio.create_task(self._start_listening_after(_GREETING_MAX_SECONDS))
        spec = self._blueprint.evaluation_spec
        if spec.duration_minutes + spec.overrun_grace_minutes >= 60:
            logger.warning(f"[{self._session_id}] booked time plus grace reaches OpenAI's 60-minute "
                           f"session cap; the hard stop is at minute {FORCED_END_BY_MINUTES:g}")

    async def _start_listening_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self._greeting_done and seconds == _GREETING_MAX_SECONDS:
            return
        self._greeting_done = True
        self._llm.set_audio_input_paused(False)
        self._record("listening", after_seconds=seconds)

    async def stop(self) -> None:
        for task in (self._clock_task, self._force_task, self._backstop_task, *self._note_tasks):
            if task and not task.done():
                task.cancel()

    # -- state the rest of the bot reads ---------------------------------

    @property
    def elapsed_seconds(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    @property
    def is_over(self) -> bool:
        return self._over

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    @property
    def current_area(self) -> int:
        return self._area

    @property
    def away(self) -> bool:
        return self._away

    @property
    def ending(self) -> dict:
        """For session_metrics: how the interview ended, what hung up, the pacing."""
        return {"ending": self._ending, "hang_up_by": self._hang_up_by,
                "skips": sum(1 for e in self._events if e["kind"] == "question_skipped"),
                "areas": self.area_log(),
                "notes": sum(1 for e in self._events if e["kind"] == "note"),
                "backstop_moves": self._backstop_moves,
                "ended_without_goodbye": self._ended_without_goodbye,
                "end_interview_refused": sum(1 for e in self._events if e["kind"] == "end_interview_refused")}

    def area_log(self) -> list[dict]:
        return [dict(a) for a in self._area_log]

    def _record(self, kind: str, **fields) -> None:
        event = {"kind": kind, "at_seconds": round(self.elapsed_seconds, 2), **fields}
        self._events.append(event)
        if self._live is not None:
            self._live.coordinator_record(kind)
        logger.info(f"[{self._session_id}] coordinator {kind}: {fields}")

    def _closing_area(self) -> int:
        return len(self._blueprint.competency_plans) + 2

    def _area_name(self, area: int) -> str:
        if area <= 1:
            return "the opening"
        if area >= self._closing_area():
            return "the closing"
        return self._blueprint.competency_plans[area - 2].name

    def _area_minutes(self, area: int) -> float:
        if area <= 1:
            return float(self._blueprint.opening_minutes)
        if area >= self._closing_area():
            return float(self._blueprint.closing_minutes)
        return float(self._blueprint.competency_plans[area - 2].time_budget_minutes)

    # -- the one way code talks to Mitra ----------------------------------------

    def _quiet(self) -> bool:
        return not (self._response_active or self._bot_speaking or self._candidate_speaking)

    async def _cancel_reply(self) -> None:
        if self._response_active or self._bot_speaking:
            cancel = getattr(self._llm, "cancel_reply", None)
            if cancel is not None:
                await cancel()
            self._response_active = False

    async def tell(self, text: str, *, kind: str, reply: bool = True, urgent: bool = False) -> bool:
        """A private note, then (reply=True) a reply request with no instructions of its
        own, so her permanent instructions apply. Waits until neither she nor the
        candidate is speaking; urgent (the hard stop, the End button) cancels her reply
        instead. Nothing is sent while the candidate is away. Returns whether it was sent."""
        async with self._note_lock:
            if urgent:
                await self._cancel_reply()
                # Cancelling stops the generation; audio already sent may still be
                # playing. Its BotStoppedSpeaking must come before the note's reply
                # starts, or it would hang up on the old reply, not the goodbye.
                loop = asyncio.get_running_loop()
                deadline = loop.time() + _URGENT_DRAIN_SECONDS
                while self._bot_speaking and loop.time() < deadline:
                    await asyncio.sleep(_QUIET_POLL_SECONDS)
            else:
                while not self._quiet():
                    if self._away or (self._over and kind not in _ENDING_NOTES):
                        break
                    await asyncio.sleep(_QUIET_POLL_SECONDS)
            if self._away:
                self._record("note_held", note_kind=kind)  # a drop: nothing until they return
                return False
            if self._over and kind not in _ENDING_NOTES:
                return False
            generation = getattr(self._llm, "connection_generation", None)
            note_id = await self._llm.insert_note(f"{NOTE_PREFIX} {text}")
            if kind == "time":
                # The newest time note is the truth; an older one beside it would only
                # contradict it.
                previous = self._last_time_note_id
                if previous and generation == self._note_generation:
                    await self._llm.delete_note(previous)
                self._last_time_note_id, self._note_generation = note_id, generation
                if self._live is not None:
                    self._live.time_note(text)
            self._record("note", note_kind=kind, text=text, reply=reply)
            if reply:
                await self._llm.request_reply()
            return True

    # -- the clock: time notes and the hard stop -----------------------------------

    def _time_marks(self) -> list[tuple[float, str, str]]:
        """(seconds, name, note text or "" for the hard stop), in order."""
        spec = self._blueprint.evaluation_spec
        booked = min(float(spec.duration_minutes), FORCED_END_BY_MINUTES)
        hard = min(float(spec.hard_stop_minutes), FORCED_END_BY_MINUTES)
        grace = max(0, round(hard - booked))
        marks = [(booked / 2 * 60, "halfway", NOTE_HALFWAY)]
        if booked >= 20:
            marks.append(((booked - 10) * 60, "ten_left", NOTE_TEN_LEFT))
        if booked >= 8:
            marks.append(((booked - 3) * 60, "three_left", NOTE_THREE_LEFT))
        if hard > booked:
            marks.append((booked * 60, "booked_over", NOTE_BOOKED_OVER.replace("{grace}", str(grace))))
        marks.append((hard * 60, "hard_stop", ""))
        return sorted(marks, key=lambda m: m[0])

    async def check_time_now(self, *, wait: bool = True) -> None:
        """Send whatever the clock owes now: due time notes, an area at its planned end,
        the backstop, the hard stop. Called every second by the clock with wait=False:
        a note waits for a quiet moment, and the clock must never wait with it (found
        in round 14: a candidate talking held the clock, and so the hard stop). Tests
        and the harness call it with wait=True to see the note sent."""
        if self._over:
            return
        now = self.elapsed_seconds
        due = [m for m in self._time_marks() if m[0] <= now and m[1] not in self._time_notes_sent]
        for _, name, text in due:
            self._time_notes_sent.add(name)
            if name == "hard_stop":
                if self._force_task is None:
                    self._force_task = asyncio.create_task(self._force_end())
                return
        if due:
            # Only the latest owed note: three notes in a row would be noise.
            _, name, text = due[-1]
            spec = self._blueprint.evaluation_spec
            await self._send(self.tell(text.format(minutes=int(now // 60), booked=int(spec.duration_minutes),
                                                   area=self._area_name(self._area),
                                                   remaining=self._remaining_text(now),
                                                   unfinished=self._unfinished_text()),
                                       kind="time", reply=False), wait)
        else:
            await self._check_area_over(now, wait)
        # Every tick, whatever note was due (round 15: a due time note used to skip it).
        self._check_backstop(now)

    def allowance(self, area: int, entered_seconds: float) -> tuple[float, float]:
        """(note at, backstop at), in minutes in the area. The allowance is the smaller of
        the area's plan and its share of the booked time left once the closing's planned
        minutes are set aside (in proportion to the plan); the note at NOTE_POINT of it.
        The backstop at BACKSTOP_FACTOR x the plan, but never so late that the areas after
        it cannot keep AREA_FLOOR of their plans and the closing its minutes; always a
        little after the note."""
        plan = self._area_minutes(area)
        spec = self._blueprint.evaluation_spec
        booked = min(float(spec.duration_minutes), FORCED_END_BY_MINUTES)
        # The closing's planned minutes plus CLOSING_MARGIN are set aside: a move lands on
        # an exchange boundary, so each area overshoots its limit a little (round 15's dry
        # run: without the margin the closing was reached only at the booked end).
        # One exchange (measured): a move, hers or the backstop's, takes effect about one
        # exchange after it is due, because her reply starts as the candidate stops.
        exchange = self.exchange_minutes()
        margin = max(CLOSING_MARGIN_MINUTES, exchange)
        left = booked - float(self._blueprint.closing_minutes) - margin - entered_seconds / 60
        # In proportion to the plan (the employer's weighting): on schedule the share is
        # the plan; behind schedule every remaining area shrinks alike.
        planned_left = sum(self._area_minutes(a) for a in range(area, self._closing_area()))
        share = left * plan / planned_left if planned_left > 0 else plan
        allowance = max(0.5, min(plan, share))
        # She moves about one exchange after the note (round 14's timelines, A-87): the
        # note comes one measured exchange before the allowance ends, or at NOTE_POINT of
        # it when exchanges are short.
        note_at = max(0.0, min(NOTE_POINT * allowance, allowance - exchange))
        # The backstop may let an area run over (to 1.5x its plan) only while every area
        # after it keeps AREA_FLOOR of its plan (at least one exchange) and the closing its
        # minutes plus the margin; it fires one exchange early for the lag.
        floors_after = sum(max(AREA_FLOOR * self._area_minutes(a), exchange)
                           for a in range(area + 1, self._closing_area()))
        backstop_at = max(note_at + _BACKSTOP_AFTER_NOTE_MINUTES,
                          min(BACKSTOP_FACTOR * plan, left - floors_after) - exchange)
        return note_at, backstop_at

    def exchange_minutes(self) -> float:
        """The interview's typical exchange, measured: the median time between the
        candidate's last few turns (DEFAULT_EXCHANGE_MINUTES before there are any)."""
        times = self._candidate_turn_times[-6:]
        gaps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
        if not gaps:
            return DEFAULT_EXCHANGE_MINUTES
        return min(MAX_EXCHANGE_MINUTES, gaps[len(gaps) // 2] / 60)

    def _check_backstop(self, now: float) -> None:
        area = self._area
        # Not the opening: firing one exchange early cut the small talk to one exchange
        # (round 15, A-96); its note one exchange before its end is enough.
        if area <= 1 or area >= self._closing_area() or self._force_task is not None:
            return
        if self._backstop_task is not None and not self._backstop_task.done():
            return
        entered = self._area_log[-1]["entered_seconds"]
        if (now - entered) / 60 >= self.allowance(area, entered)[1]:
            self._backstop_task = asyncio.create_task(self._backstop(area))

    async def _backstop(self, area: int) -> None:
        """Move from `area` to the next at the next quiet moment: never while she or the
        candidate is speaking, never during a drop (waits for the return), never once
        the interview is ending (the hard stop wins)."""
        try:
            while True:
                if self._over or self._force_task is not None or self._area != area:
                    return
                if not self._away and self._quiet():
                    break
                await asyncio.sleep(_QUIET_POLL_SECONDS)
            moved = await self._move_from(area, by="backstop")
            if moved:
                self._backstop_moves += 1
                now = self.elapsed_seconds
                note = NOTE_BACKSTOP_FROM_OPENING if area <= 1 else NOTE_BACKSTOP
                text = (NOTE_BACKSTOP_TO_CLOSING if self._area >= self._closing_area() else
                        note.format(area=self._area_name(self._area), remaining=self._remaining_text(now)))
                await self.tell(text, kind="backstop", reply=False)
        except asyncio.CancelledError:
            pass

    async def _send(self, note, wait: bool) -> None:
        if wait:
            await note
        else:
            task = asyncio.create_task(note)
            self._note_tasks.add(task)
            task.add_done_callback(self._note_tasks.discard)

    async def _check_area_over(self, now: float, wait: bool = True) -> None:
        """The concrete note at NOTE_POINT of the area's allowance, once per area."""
        area = self._area
        if area >= self._closing_area() or area in self._areas_over_noted:
            return
        entered = self._area_log[-1]["entered_seconds"]
        if (now - entered) / 60 < self.allowance(area, entered)[0]:
            return
        if area <= 1 and (now - entered < OPENING_NOTE_FLOOR_SECONDS
                          or len(self._candidate_turn_times) < OPENING_NOTE_MIN_ANSWERS):
            # A-114: on short bookings the opening's note came at about 20 s, before
            # the small talk had happened. Checked again on the next tick.
            return
        self._areas_over_noted.add(area)
        await self._send(self.tell(NOTE_AREA_SOON.format(area=self._area_name(area),
                                                         remaining=self._remaining_text(now, after_this=True)),
                                   kind="time", reply=False), wait)

    def _areas_left(self) -> int:
        """Competency areas after the current one (the closing not counted)."""
        return max(0, self._closing_area() - 1 - self._area)

    def _remaining_text(self, now: float, *, after_this: bool = False) -> str:
        """Concrete pacing information: what is left and how much time each part has."""
        spec = self._blueprint.evaluation_spec
        booked = min(float(spec.duration_minutes), FORCED_END_BY_MINUTES)
        left = max(0.0, booked - now / 60)
        areas = self._areas_left()
        if self._area >= self._closing_area():
            return f"This is the closing; about {round(left)} minutes of booked time are left."
        if areas == 0:
            return f"Only the closing remains after this area; about {round(left)} minutes of booked time are left."
        each = max(0.0, left - float(self._blueprint.closing_minutes)) / (areas + (1 if after_this else 0))
        which = "after this one" if after_this else "after this area"
        return (f"{areas} more area(s) remain {which}, about {max(1, round(each))} minute(s) each, then the "
                f"closing (about {round(left)} minutes of booked time left in all).")

    def _unfinished_text(self) -> str:
        areas = self._areas_left()
        if self._area >= self._closing_area():
            return ""
        if areas:
            return f"The plan is unfinished: {areas} area(s) were not reached. "
        return "The plan is unfinished: the closing was not reached. "

    async def _run_clock(self) -> None:
        try:
            while not self._over:
                await self.check_time_now(wait=False)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _force_end(self) -> None:
        """The hard stop: her reply is cancelled, a note asks for a short goodbye, the
        call ends when it has played, and is cut at FORCED_END_REPLY_CAP_SECONDS."""
        try:
            if self._over:
                return
            self._record("hard_stop_reached")
            if self._away:
                await self.end_silently("interview_complete", ENDING_HARD_STOP_DURING_DROP)
                return
            self._mark_over("interview_complete", ending=ENDING_FORCED)
            self._llm.set_audio_input_paused(True)
            await self.tell(NOTE_HARD_STOP, kind="hard_stop", urgent=True)
            await asyncio.sleep(FORCED_END_REPLY_CAP_SECONDS)
            if not self._ended:
                self._record("hard_stop_cap_cut")
                await self._cancel_reply()
                await self._end_call(by="hard_stop_cap")
        except asyncio.CancelledError:
            pass

    # -- areas: Mitra moves on (move_on) ----------------------------------------------

    async def _handle_move_on(self, params) -> None:
        """She has finished the current area: show her the next one."""
        if self._over:
            await params.result_callback({"moved": False}, properties=FunctionCallResultProperties(run_llm=False))
            return
        if self._area >= self._closing_area():
            self._record("move_on_refused", area=self._area)
            await params.result_callback({
                "moved": False,
                "why": "This is already the closing, the last part. When you have finished, say a warm "
                       "goodbye and call end_interview with reason completed."})
            return
        # She means the area she was shown when this reply began; if the backstop has
        # moved on since, her call is a duplicate and is ignored.
        shown = self._reply_area if self._reply_area is not None else self._area
        if not await self._move_from(shown, by="move_on"):
            await params.result_callback({"moved": False, "now_in": self._area_name(self._area),
                                          "why": "The next area is already shown."})
            return
        await params.result_callback(self.move_on_result(from_area=shown, now=self.elapsed_seconds))

    def move_on_result(self, *, from_area: int, now: float) -> dict:
        """What an accepted move_on tells her (the area she is now in is already set)."""
        return {"moved": True, "now_in": self._area_name(self._area),
                "time": f"About {round(now / 60)} minutes have passed. {self._remaining_text(now)}",
                "next": MOVE_ON_FIRST if from_area <= 1 else MOVE_ON_NEXT}

    async def _move_from(self, area: int, *, by: str) -> bool:
        """The ONE way areas change (her move_on and the backstop): one at a time, and a
        move asked for from an area she has already left is a duplicate, ignored."""
        async with self._area_lock:
            if self._area != area or area >= self._closing_area() or self._over:
                # A duplicate move_on is her tool call, ignored (sent live as ToolCalled);
                # a duplicate backstop move is code's, recorded only.
                self._record("area_move_ignored" if by == "move_on" else "backstop_move_ignored",
                             area=area, by=by, now_in=self._area)
                return False
            await self._enter_area(area + 1, by=by)
            return True

    async def _enter_area(self, area: int, *, by: str = "move_on") -> None:
        if self.plan_started_wall is None and area >= 2:
            self.plan_started_wall = time.time()
        now = round(self.elapsed_seconds, 2)
        self._area_log[-1]["left_seconds"] = now
        self._area_log.append({"area": area, "entered_seconds": now, "left_seconds": None})
        self._area = area
        if self._live is not None:
            self._live.area_changed(area)
        if self._area_instruction is not None:
            # Inside her tool call, before the reply that will use it.
            await self._llm.update_instructions(self._area_instruction(area))
        self._record("move_on" if by == "move_on" else "area_backstop", area=area, by=by)

    # -- end_interview: Mitra ends, code carries it out --------------------------------

    def _collapsed_flow(self, call_reply) -> list[str]:
        flow = [x for x in self._flow if not (isinstance(x, tuple) and x[1] == call_reply)]
        collapsed: list[str] = []
        for x in flow:
            who = "C" if x == "C" else "M"
            if not collapsed or collapsed[-1] != who:
                collapsed.append(who)
        return collapsed

    def _withdrew_refusal(self, call_reply) -> str | None:
        """She asked, they answered: after the candidate's previous turn she spoke, and
        the candidate has replied since. Her own replies after that answer (her goodbye)
        do not undo it (found on the real route in round 14: the goodbye came first and
        the call a reply later, and was refused)."""
        collapsed = self._collapsed_flow(call_reply)
        while collapsed and collapsed[-1] == "M":
            collapsed.pop()
        if len(collapsed) >= 3 and collapsed[-3:] == ["C", "M", "C"]:
            return None
        return REFUSE_WITHDREW

    def _spoke_since_candidate(self, call_reply) -> bool:
        """She has already spoken (her goodbye) since the candidate's last turn, in a
        reply before the one carrying this call."""
        collapsed = self._collapsed_flow(call_reply)
        return bool(collapsed) and collapsed[-1] == "M"

    def _completed_refusal(self) -> str | None:
        if self._area >= self._closing_area() or self.elapsed_seconds >= nearly_up_seconds(self._blueprint):
            return None
        reached = max(0, min(self._area, self._closing_area()) - 1)
        return REFUSE_COMPLETED.format(reached=reached, areas=self._closing_area() - 1)

    async def _reply_spoke(self, reply_id) -> bool:
        """Whether the reply that carried the call spoke (its response.done record)."""
        spoke = getattr(self._llm, "reply_spoke", None)
        if spoke is None:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _REPLY_RECORD_WAIT_SECONDS
        while loop.time() < deadline:
            if reply_id is not None and reply_id in spoke:
                return bool(spoke[reply_id])
            await asyncio.sleep(0.02)
        return bool(list(spoke.values())[-1]) if (reply_id is None and spoke) else False

    async def _handle_end_interview(self, params) -> None:
        arguments: dict[str, Any] = params.arguments or {}
        reason = str(arguments.get("reason", "")).strip().lower()
        call_reply = (getattr(self._llm, "reply_of_call", None) or {}).get(getattr(params, "tool_call_id", None))
        if self._over:
            # One of code's endings is under way (a note asked for her goodbye). If she
            # answered it with the tool alone, no goodbye was said: she is asked for it
            # now (found in round 14: a long-silence close ended in silence).
            spoke = self._ended or await self._reply_spoke(call_reply)
            self._record("end_interview_after_end", reason=reason, spoke_in_call_reply=spoke)
            if spoke:
                await params.result_callback({"accepted": True},
                                             properties=FunctionCallResultProperties(run_llm=False))
            else:
                self._ended_without_goodbye += 1
                await params.result_callback({"accepted": True, "next": TOOL_SAY_GOODBYE})
            return
        if reason not in _REASON_RECORDED:
            refusal = 'Not accepted: reason must be "withdrew" or "completed". Carry on calmly.'
        elif reason == WITHDREW:
            refusal = self._withdrew_refusal(call_reply)
        else:
            refusal = self._completed_refusal()
        if refusal:
            self._record("end_interview_refused", reason=reason, why=refusal)
            await params.result_callback({"accepted": False, "why": refusal})
            return
        spoke = await self._reply_spoke(call_reply) or self._spoke_since_candidate(call_reply)
        if self._over:  # a code ending won the race meanwhile
            await params.result_callback({"accepted": True}, properties=FunctionCallResultProperties(run_llm=False))
            return
        ending = ENDING_WITHDREW if reason == WITHDREW else ENDING_COMPLETED
        self._record(f"end_interview_{reason}", spoke_in_call_reply=spoke)
        if not spoke:
            self._ended_without_goodbye += 1
        # Round 16 (A-97): the call ends only after the reply that FOLLOWS the accepted
        # call, her goodbye, has played. Whatever the call's own reply said ("I'll wrap up
        # now" ended two real calls with no goodbye), it is never the last thing heard.
        self._mark_over(_REASON_RECORDED[reason], ending=ending)
        await params.result_callback({"accepted": True, "next": TOOL_SAY_GOODBYE})

    # -- code's own endings ---------------------------------------------------------

    def _mark_over(self, reason: str, *, ending: str) -> None:
        """Over: the next reply that STARTS from now and speaks is the goodbye; the call
        ends once it has played (or at the silent backstop)."""
        if self._over:
            return
        self._over = True
        self._reason = reason
        if self._ending is None:
            self._ending = ending
        self._record("interview_over", reason=reason, ending=self._ending)
        self._awaiting_goodbye = True
        asyncio.create_task(self._end_after(_GOODBYE_TIMEOUT_SECONDS))

    async def _end_with_goodbye_note(self, note: str, *, kind: str, reason: str, ending: str,
                                     urgent: bool = False) -> None:
        """One of code's endings: mark it over, then a note asks for her goodbye; the
        call ends when that goodbye has played (or at the silent backstop)."""
        if self._over:
            return
        self._mark_over(reason, ending=ending)
        self._llm.set_audio_input_paused(True)
        await self.tell(note, kind=kind, urgent=urgent)

    async def end_rejoin_limit(self) -> None:
        """A counted drop past the limit, on their return (bot/rejoin.py decides)."""
        await self._end_with_goodbye_note(NOTE_REJOIN_LIMIT, kind="rejoin_limit", reason=ENDING_REJOIN_LIMIT,
                                          ending=ENDING_REJOIN_LIMIT)

    async def end_by_candidate_button(self) -> None:
        """The candidate pressed End and confirmed it in the app: her reply is cancelled
        (they asked to finish now), then a note asks for her goodbye."""
        if self._over:
            self._record("end_button_after_end")
            return
        self._record("end_button")
        await self._end_with_goodbye_note(NOTE_END_BUTTON, kind="end_button", reason=ENDING_END_BUTTON,
                                          ending=ENDING_END_BUTTON, urgent=True)

    async def close_for_silence(self, reason: str = "abandoned_by_candidate") -> None:
        """The long-silence close (bot/silence.py decides when)."""
        await self._end_with_goodbye_note(NOTE_SILENCE_CLOSE, kind="silence_close", reason=reason,
                                          ending=ENDING_SILENCE)

    async def quiet_note(self, stage: int, dead_air_seconds: float) -> None:
        """The silence ladder's first two steps: she is told, and decides."""
        if self._over:
            return
        text = NOTE_QUIET if stage == 1 else NOTE_AUDIO_CHECK
        await self.tell(text.format(seconds=int(round(dead_air_seconds))), kind=f"quiet_{stage}")

    async def end_silently(self, reason: str, ending: str) -> None:
        """End with nothing said: the candidate is not there to hear it (not back in
        the grace window, or the hard stop during a drop)."""
        if self._over:
            return
        self._over = True
        self._reason = reason
        self._ending = ending
        self._record("interview_over", reason=reason, ending=ending)
        await self._end_call(by="no_one_to_hear")

    async def _end_unless_audio_starts(self) -> None:
        starts = self._goodbye_audio_starts
        await asyncio.sleep(_GOODBYE_GAP_SECONDS)
        if self._goodbye_audio_starts == starts and not self._bot_speaking:
            await self._end_call()

    async def _end_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self._end_on_next_stop and self._bot_speaking:
            return  # BotStoppedSpeaking will end it
        await self._end_call(by="goodbye_timeout" if seconds >= _GOODBYE_TIMEOUT_SECONDS else "silent_timer")

    async def _end_call(self, by: str = "bot_stopped_speaking") -> None:
        if self._ended:
            return
        self._ended = True
        self._hang_up_by = by
        self._record("hang_up", by=by, ending=self._ending)
        if self._clock_task:
            self._clock_task.cancel()
        logger.info(f"[{self._session_id}] interview over ({self._reason}); ending the call")
        if self._worker is None:
            logger.warning(f"[{self._session_id}] no worker attached; cannot end the call")
            return
        await self._worker.queue_frames([EndWorkerFrame(reason=self._reason)])

    # -- rejoin (the decisions are bot/rejoin.py's) ------------------------------------

    async def candidate_away(self, away: bool) -> None:
        """Away: her reply is cancelled and input paused; nothing is said until they are
        back. Back: input resumes; the welcome back is `welcome_back`'s note."""
        if self._over:
            return
        self._away = away
        self._record("candidate_away" if away else "candidate_back")
        if away:
            await self._cancel_reply()
            self._llm.set_audio_input_paused(True)
        else:
            self._llm.set_audio_input_paused(False)

    def last_question(self) -> str | None:
        """The last question she asked: the last sentence ending in "?" of her latest
        reply that has one (information for the welcome-back note)."""
        texts = getattr(self._llm, "reply_text", None) or {}
        for text in reversed(list(texts.values())):
            questions = [q.strip() for q in re.findall(r"[^.?!]*\?", text or "") if q.strip()]
            if questions:
                return questions[-1]
        return None

    async def welcome_back(self) -> None:
        if self._over:
            return
        await self._cancel_reply()
        question = self.last_question()
        self._record("welcome_back", question=question)
        text = NOTE_WELCOME_BACK.format(question=question) if question else NOTE_WELCOME_BACK_NO_QUESTION
        await self.tell(text, kind="welcome_back")

    # -- records only (the auditor reads them; nothing here acts on the conversation) --

    def _on_candidate_turn_record(self, text: str) -> None:
        if self._over:
            return
        if skipped_this_question(text):
            self._record_skip(text)
        elif declined_this_question(text):
            self._record_decline(text)

    async def _on_candidate_turn(self, text: str) -> None:
        """Every finished candidate utterance: the flow (when VAD is not available, as
        in text mode) and the skip and decline records."""
        if not self._vad_seen:
            self._candidate_spoke()
        self._on_candidate_turn_record(text)
        if not self._follow_offered_topic(text):
            self._count_dont_know(text)

    def _follow_offered_topic(self, text: str) -> bool:
        """A relevant topic offered the second time: she follows it for one or two questions."""
        topic = offered_topic(text)
        if topic is None or self._over or self._area < 2 or self._area >= self._closing_area():
            return False
        key = _topic_key(topic)
        self._topic_offers[key] = self._topic_offers.get(key, 0) + 1
        if self._topic_offers[key] < 2 or key in self._topics_followed:
            return False
        # Relevance is hers to judge (a plan's words miss "LLMs" when it says "Llama");
        # counting and timing are code's.
        self._topics_followed.add(key)
        self._dont_knows = (self._area, 0)
        self._record("topic_followed", area=self._area, topic=topic)
        role = self._blueprint.role_title or self._blueprint.evaluation_spec.role_title or "this role"
        task = asyncio.create_task(self._steer(NOTE_FOLLOW_TOPIC.format(topic=topic, role=role), kind="topic"))
        self._dont_know_tasks.add(task)
        task.add_done_callback(self._dont_know_tasks.discard)
        return True

    async def _steer(self, text: str, *, kind: str) -> None:
        """A note that should shape her very next reply: replaces it if she has not started
        speaking, else waits for the next quiet moment."""
        try:
            if not self._bot_speaking:
                await self._cancel_reply()
            await self.tell(text, kind=kind)
        except asyncio.CancelledError:
            pass

    def _count_dont_know(self, text: str) -> None:
        """Two "I don't know" answers in a row in one competency area move the interview on."""
        area = self._area
        if self._over or area < 2 or area + 1 >= self._closing_area():
            return  # not in a competency area, or the next area would be the closing
        if not said_dont_know(text):
            self._dont_knows = (area, 0)
            return
        count = self._dont_knows[1] + 1 if self._dont_knows[0] == area else 1
        self._dont_knows = (area, count)
        if count < DONT_KNOW_LIMIT:
            return
        self._dont_knows = (area, 0)
        self._record("dont_knows_move", area=area, said=text)
        task = asyncio.create_task(self._move_after_dont_knows(area))
        self._dont_know_tasks.add(task)
        task.add_done_callback(self._dont_know_tasks.discard)

    async def _move_after_dont_knows(self, area: int) -> None:
        """Before she has started speaking (the transcript usually comes first): her
        reply is replaced, and she says it is fine and opens the next area. Already
        speaking: she finishes, and the next area starts at the next quiet moment."""
        try:
            speaking_already = self._bot_speaking
            if not speaking_already:
                await self._cancel_reply()
            else:
                while not self._quiet():
                    if self._over or self._area != area:
                        return
                    await asyncio.sleep(_QUIET_POLL_SECONDS)
            if not await self._move_from(area, by="dont_know"):
                return
            name = self._area_name(self._area)
            if speaking_already:
                await self.tell(NOTE_DONT_KNOWS_MOVED_NEXT.format(area=name), kind="dont_knows", reply=False)
            else:
                await self.tell(NOTE_DONT_KNOWS_MOVED.format(area=name), kind="dont_knows")
        except asyncio.CancelledError:
            pass

    def _candidate_spoke(self) -> None:
        if not self._flow or self._flow[-1] != "C":
            self._flow.append("C")
            self._candidate_turn_times.append(self.elapsed_seconds)

    def _record_skip(self, said: str) -> None:
        names = {1: "Opening", self._closing_area(): "Closing"}
        names.update({i + 2: p.name for i, p in enumerate(self._blueprint.competency_plans)})
        self._record("question_skipped", source="coordinator",
                     payload={"area": self._area, "area_name": names.get(self._area), "said": said})
        if self._live is not None:
            area, qid = (self._auditor.placement_now() if self._auditor is not None else (self._area, None))
            self._live.skip(area, question_id=qid)
        if self._auditor is not None:
            self._auditor.note_skip(self._area)

    def _record_decline(self, said: str) -> None:
        names = {1: "Opening", self._closing_area(): "Closing"}
        names.update({i + 2: p.name for i, p in enumerate(self._blueprint.competency_plans)})
        self._record("question_declined", source="coordinator",
                     payload={"area": self._area, "area_name": names.get(self._area), "said": said})
        if self._auditor is not None:
            self._auditor.note_decline(self._area)

    # -- observing -------------------------------------------------------

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if not isinstance(frame, (TranscriptionFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
                                  BotStartedSpeakingFrame, BotStoppedSpeakingFrame, UserStartedSpeakingFrame,
                                  UserStoppedSpeakingFrame)):
            return
        if frame.id in self._seen:  # the same frame is pushed by more than one processor
            return
        self._seen.add(frame.id)

        if isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                await self._on_candidate_turn(frame.text.strip())
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._response_active = False
            spoke = getattr(self._llm, "reply_spoke", None) or {}
            latest_spoke = False
            if spoke:
                latest = list(spoke)[-1]
                latest_spoke = bool(spoke[latest])
                if latest_spoke and ("M", latest) not in self._flow:
                    self._flow.append(("M", latest))
            if self._goodbye_reply_started:
                # The reply after the ending began has finished generating: if it spoke, it
                # is the goodbye, and the call ends once ITS audio has played (round 16: not
                # on a stop that belongs to the line before it); a tool-only reply is not.
                self._goodbye_reply_started = False
                if latest_spoke:
                    self._awaiting_goodbye = False
                    self._end_on_next_stop = True
                    if not self._bot_speaking and self._goodbye_audio_starts > 0:
                        # Its audio already started and stopped: nothing more will play.
                        asyncio.create_task(self._end_after(_ENDED_WHILE_SILENT_SECONDS))
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._response_active = True
            self._reply_area = self._area
            if self._awaiting_goodbye:
                self._goodbye_reply_started = True
                self._goodbye_began_while_speaking = self._bot_speaking
                self._goodbye_audio_starts = 0
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._vad_seen = True
            self._candidate_speaking = True
            self._candidate_spoke()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._candidate_speaking = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            if self._goodbye_reply_started or self._end_on_next_stop:
                self._goodbye_audio_starts += 1
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if not self._greeting_done and self._started_at is not None:
                self._greeting_done = True
                asyncio.create_task(self._start_listening_after(_GREETING_TAIL_SECONDS))
            if self._end_on_next_stop:
                if self._goodbye_audio_starts > 0 or not self._goodbye_began_while_speaking:
                    await self._end_call()
                elif self._gap_task is None or self._gap_task.done():
                    # This stop may be the gap after the line before the goodbye: wait for
                    # the goodbye's own audio to start; if none starts, it was one stretch.
                    self._gap_task = asyncio.create_task(self._end_unless_audio_starts())


#: Notes that belong to an ending already decided, so they are sent although it is over.
_ENDING_NOTES = {"hard_stop", "rejoin_limit", "end_button", "silence_close"}
