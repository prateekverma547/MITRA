"""Text-mode harness: drive the brain through a whole interview without audio.

This is the primary development vehicle for the brain (CLAUDE.md). It wraps
`plan_turn()` with a single non-streaming LLM call — the same object the voice
pipeline drives, with a different driver. In production, generation stays in the
streaming Pipecat pipeline; here we only need text.

The harness is also where off-path judgement is fulfilled. It drains
`pending_judgment_request()` after each turn and returns results through
`apply_judgment()`, exactly as the voice driver will — except synchronously,
which is what makes a faked judge produce a fully deterministic interview.

Nothing here imports Pipecat.
"""

import json
from dataclasses import dataclass, field
from typing import Protocol

from bot.brain.brain import InterviewBrain, Judgment, JudgmentRequest, TurnPlan
from shared.contracts import Contradiction, CoverageLevel, KeyClaim

# Wall-clock seconds a single exchange is assumed to consume. Injected rather
# than measured so a 40-minute interview runs instantly in tests.
DEFAULT_SECONDS_PER_TURN = 45.0


class Interviewer(Protocol):
    """Turns a TurnPlan into the interviewer's next utterance."""

    async def respond(self, plan: TurnPlan) -> str: ...


class Judge(Protocol):
    """Fulfils the brain's off-path judgement requests."""

    async def assess(self, request: JudgmentRequest) -> Judgment: ...


class Candidate(Protocol):
    """A scripted interviewee. Returning None ends the interview."""

    def reply(self, question: str, section_id: str) -> str | None: ...


# --------------------------------------------------------------------------
# Scripted candidates
# --------------------------------------------------------------------------


@dataclass
class ScriptedCandidate:
    """Replies from a fixed list, optionally keyed by section.

    `by_section` lets a script target a specific competency without having to
    predict how many turns earlier sections will take — which matters, because
    the number of turns per section is exactly what the brain decides.
    """

    replies: list[str] = field(default_factory=list)
    by_section: dict[str, list[str]] = field(default_factory=dict)
    default_reply: str = "I would need to think about that one."
    max_turns: int | None = None

    _used: int = field(default=0, init=False)
    _section_cursor: dict[str, int] = field(default_factory=dict, init=False)

    def reply(self, question: str, section_id: str) -> str | None:
        if self.max_turns is not None and self._used >= self.max_turns:
            return None
        self._used += 1

        if section_id in self.by_section:
            cursor = self._section_cursor.get(section_id, 0)
            bank = self.by_section[section_id]
            if cursor < len(bank):
                self._section_cursor[section_id] = cursor + 1
                return bank[cursor]
            return self.default_reply

        if self.replies:
            index = min(self._used - 1, len(self.replies) - 1)
            return self.replies[index]
        return self.default_reply


def contradicting_candidate() -> ScriptedCandidate:
    """A candidate who contradicts himself across sections.

    The contradiction is deliberately cross-section: he claims sole ownership of
    the pricing decision while discussing strategy, then in the stakeholder
    section says the decision was taken over his objection. Within-section
    context would never surface this — only carried claims can, which is
    precisely what this exercises.
    """
    return ScriptedCandidate(
        by_section={
            "opening": [
                "Sure. I have about eleven years in product, mostly B2B SaaS, "
                "the last four at a payments company as a senior PM."
            ],
            "product_strategy": [
                "The big one was our move to usage-based pricing. I owned that "
                "decision end to end and made the final call myself.",
                "I believed the market was shifting away from seat licences faster "
                "than our competitors thought. I pushed it through in one quarter.",
                "It worked. Revenue retention went up and I was proud of having "
                "driven it personally.",
            ],
            "prioritization": [
                "We cut the reporting rebuild to make room for it. That was painful "
                "but it was the right trade.",
                "I decided we would not build custom dashboards that year.",
            ],
            "stakeholders": [
                "Honestly the pricing change was forced on us by the CFO. I argued "
                "against it at the time and lost that argument.",
                "I was overruled, so we shipped it anyway and I had to get the team "
                "behind something I did not agree with.",
            ],
        },
        default_reply="That is a good question. I would have to think about it.",
    )


def thin_answer_candidate() -> ScriptedCandidate:
    """Answers everything vaguely, to test whether the interviewer probes."""
    return ScriptedCandidate(
        replies=[
            "I have a lot of experience in product management, mostly B2B.",
            "We used a standard prioritization framework and it worked well.",
            "We aligned stakeholders and drove consensus across the org.",
            "We tracked the usual product metrics and iterated.",
            "We shipped a number of successful products over the years.",
            "I led through influence and built strong relationships.",
        ]
    )


def refusing_candidate() -> ScriptedCandidate:
    """Declines everything, as a real candidate did in a live session.

    That session exposed three bugs at once: the bot invented a strategy the
    candidate never described, thanked them for sharing it, and kept asking.
    """
    return ScriptedCandidate(
        replies=["No", "I don't want to.", "I don't want to.", "I don't want to."],
        default_reply="I don't want to.",
    )


def unheard_candidate() -> ScriptedCandidate:
    """Someone answering well through a connection that is dropping their words.

    The point of the script is that this person is **not** weak. Read the pieces
    together and there is a real answer underneath: a named product, a measured
    outcome, an owned decision. What reaches the transcript is wreckage.

    This is the case the report has to get right. Scored on the text alone the
    candidate looks incoherent, and that judgement would go into a hiring record
    about a real person whose only mistake was a cheap headset.
    """
    return ScriptedCandidate(
        replies=[
            "Sorry, could you say that again?",
            "So the",
            "we shipped a",
            "Sorry, you cut out there.",
            "retrieval assistant for support agents",
            "and it cut",
            "Can you repeat the question?",
            "handling time by about a third",
            "Uh",
            "the hard part was",
            "Sorry, I did not catch that.",
            "grounding every answer in ticket history",
        ],
        # Leave rather than repeat the last line forever, which made the tail of
        # every run look like a bot failure that was really a script artifact.
        max_turns=12,
    )


def withdrawing_candidate() -> ScriptedCandidate:
    """Someone who answers a little, then asks to stop.

    The interview must end because they said so, not four refusals later and not
    when they give up and close the tab themselves. The wording is deliberately
    plain: this is what a person actually says.
    """
    return ScriptedCandidate(
        replies=[
            "Hi, yes, I can hear you.",
            "I have been a product manager for about eight years.",
            "Sorry, I do not want to continue the interview.",
            "yes",
        ],
        max_turns=6,
    )


def off_topic_candidate() -> ScriptedCandidate:
    """Drifts off the role, to test the redirect."""
    return ScriptedCandidate(
        replies=[
            "Before we start, are you a real person or an AI?",
            "Interesting. What do you think about the job market at the moment?",
            "Do you know if this company allows fully remote work?",
            "Actually I am also interested in engineering management roles.",
        ]
    )


# --------------------------------------------------------------------------
# Fakes — the deterministic suite runs on these
# --------------------------------------------------------------------------


@dataclass
class FakeInterviewer:
    """Emits a predictable utterance so brain logic can be asserted exactly."""

    calls: list[TurnPlan] = field(default_factory=list)

    async def respond(self, plan: TurnPlan) -> str:
        self.calls.append(plan)
        return f"[{plan.section_id}] question {len(self.calls)}"


@dataclass
class FakeJudge:
    """Returns canned verdicts. Never calls a model.

    `verdicts` maps section_id -> coverage. `claims` maps section_id -> claim
    texts. `silent` makes the judge never answer, which is how we test that
    heuristics rule when a verdict has not arrived.
    """

    verdicts: dict[str, CoverageLevel] = field(default_factory=dict)
    claims: dict[str, list[str]] = field(default_factory=dict)
    contradictions: list[Contradiction] = field(default_factory=list)
    silent: bool = False
    seen: list[JudgmentRequest] = field(default_factory=list)

    async def assess(self, request: JudgmentRequest) -> Judgment | None:
        self.seen.append(request)
        if self.silent:
            return None

        turn_index = request.transcript[-1].index if request.transcript else 0
        return Judgment(
            section_id=request.section_id,
            coverage=self.verdicts.get(request.section_id),
            rationale="fake verdict",
            claims=[
                KeyClaim(text=text, section_id=request.section_id, turn_index=turn_index)
                for text in self.claims.get(request.section_id, [])
            ],
            contradictions=[
                c for c in self.contradictions if c.section_id == request.section_id
            ],
        )


# --------------------------------------------------------------------------
# Running an interview
# --------------------------------------------------------------------------


@dataclass
class CallbackOpportunity:
    """A turn where a contradiction was in the model's context.

    Pairs the surfaced contradiction with what the interviewer actually said, so
    the raise rate can be measured rather than assumed. The instruction to raise
    a callback is a soft behaviour — it needs a measured baseline before it can
    be trusted in real interviews.
    """

    earlier_claim: str
    later_statement: str
    bot_text: str

    #: Phrases that indicate the model actually reached back to the earlier
    #: claim rather than simply continuing the conversation.
    CALLBACK_MARKERS = (
        "earlier",
        "you mentioned",
        "you said",
        "fit together",
        "fit with",
        "a moment ago",
        "before you",
        "you told me",
    )

    @property
    def raised(self) -> bool:
        text = self.bot_text.lower()
        return any(marker in text for marker in self.CALLBACK_MARKERS)


@dataclass
class InterviewRun:
    """Everything a completed text-mode interview produced."""

    transcript: list[dict]
    outcomes: list[dict]
    section_order: list[str]
    turn_plans: list[TurnPlan]
    ended_because: str
    callback_opportunities: list[CallbackOpportunity] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        current = None
        for turn in self.transcript:
            if turn["section_id"] != current:
                current = turn["section_id"]
                lines.append(f"\n--- {current} ---")
            speaker = "INTERVIEWER" if turn["speaker"] == "interviewer" else "candidate"
            lines.append(f"{speaker}: {turn['text']}")
        return "\n".join(lines)


async def run_interview(
    brain: InterviewBrain,
    *,
    interviewer: Interviewer,
    candidate: Candidate,
    judge: Judge | None = None,
    seconds_per_turn: float = DEFAULT_SECONDS_PER_TURN,
    max_turns: int = 60,
    stop_after_section: str | None = None,
) -> InterviewRun:
    """Drive a full interview in text mode.

    `stop_after_section` ends the run once that section has been left, which
    keeps repeated-trial measurements cheap when the behaviour under test
    happens early.
    """
    turn_plans: list[TurnPlan] = []
    opportunities: list[CallbackOpportunity] = []
    now = 0.0
    ended_because = "candidate_left"

    for _ in range(max_turns):
        if brain.is_finished:
            ended_because = "brain_finished"
            break
        if stop_after_section and brain.current_section.id != stop_after_section:
            if any(p.section_id == stop_after_section for p in turn_plans):
                ended_because = "stop_after_section"
                break

        plan = brain.plan_turn()
        turn_plans.append(plan)

        # Contradictions are surfaced to the model at most once. Marking them
        # here means "the interviewer was given its one opportunity to raise
        # this" — the policy bounds re-raising, it does not guarantee the model
        # took the opening.
        surfaced = brain.unprobed_contradictions()

        bot_text = await interviewer.respond(plan)
        for contradiction in surfaced:
            opportunities.append(
                CallbackOpportunity(
                    earlier_claim=contradiction.earlier_claim,
                    later_statement=contradiction.later_statement,
                    bot_text=bot_text,
                )
            )
            brain.mark_contradiction_probed(contradiction)

        candidate_text = candidate.reply(bot_text, plan.section_id)

        now += seconds_per_turn
        brain.tick(now)
        brain.observe(bot_text=bot_text, candidate_text=candidate_text)

        if candidate_text is None:
            ended_because = "candidate_left"
            break

        # Off the critical path in production; synchronous here for determinism.
        if judge is not None:
            while (request := brain.pending_judgment_request()) is not None:
                judgment = await judge.assess(request)
                if judgment is not None:
                    brain.apply_judgment(judgment)
    else:
        ended_because = "max_turns"

    return InterviewRun(
        transcript=[
            {
                "index": t.index,
                "speaker": t.speaker,
                "text": t.text,
                "section_id": t.section_id,
                "at_seconds": t.at_seconds,
            }
            for t in brain.transcript
        ],
        outcomes=[o.model_dump(mode="json") for o in brain.outcomes()],
        section_order=[p.section_id for p in turn_plans],
        turn_plans=turn_plans,
        ended_because=ended_because,
        callback_opportunities=opportunities,
    )


def write_run(run: InterviewRun, path, *, label: str) -> None:
    """Persist a run for side-by-side model comparison."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "label": label,
                "ended_because": run.ended_because,
                "transcript": run.transcript,
                "outcomes": run.outcomes,
            },
            indent=2,
        )
    )
