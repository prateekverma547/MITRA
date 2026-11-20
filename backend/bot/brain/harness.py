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


#: Phrases the interviewer uses to quote the candidate back at them before
#: asking. Everything after the LAST one is the part that carries the subject of
#: the question; everything before it is an acknowledgement of the previous
#: answer.
_QUOTING_CUES = ("you said", "you mentioned", "you told", "you described",
                 "you have said", "you gave", "you showed", "you used")


def ask_of(turn: str) -> str:
    """The part of an interviewer turn that carries what is being asked.

    **This exists because the acknowledgement is a trap.** A turn looks like
    "Okay. You said quantising to GPTQ 4-bit achieved 97.40 percent. You said
    bf16. Walk me through how that actually works." Matching the whole turn
    scores `quantising` and answers the wrong question, which is exactly the
    false positive that made the seed-question metric unsound: it scored the
    restatement rather than the question, and a section where one question was
    asked three times measured as three different questions.

    So the ask is taken from the LAST quote-back onward. Before it is what they
    already said; after it is what is wanted now.

    Falls back to the whole turn rather than to nothing, because a turn with no
    quote-back at all ("What Python libraries do you use most often?") is
    entirely ask.
    """
    lowered = (turn or "").lower()
    cut = max((lowered.rfind(cue) for cue in _QUOTING_CUES), default=-1)
    return lowered[cut:] if cut >= 0 else lowered


@dataclass
class Answer:
    """One thing the candidate can say, and what a question has to mention to
    get it.

    `triggers` are distinctive substrings, not a similarity score. That
    distinction is the whole reason this is safe: see `ScriptedCandidate.reply`.
    """

    triggers: tuple[str, ...]
    text: str


@dataclass
class ScriptedCandidate:
    """Replies from a fixed list, optionally keyed by section.

    `by_section` lets a script target a specific competency without having to
    predict how many turns earlier sections will take — which matters, because
    the number of turns per section is exactly what the brain decides.

    A bank may hold plain strings or `Answer`s. Strings are handed out in order,
    which is the original behaviour and is right for a candidate whose whole
    point is that it says the same useless thing whatever it is asked. `Answer`s
    are **selected by what the question was about**, which is what a candidate
    with real material has to do if any measurement over this harness is to mean
    anything.
    """

    replies: list[str] = field(default_factory=list)
    by_section: dict[str, list] = field(default_factory=dict)
    default_reply: str = "I would need to think about that one."
    max_turns: int | None = None

    _used: int = field(default=0, init=False)
    _section_cursor: dict[str, int] = field(default_factory=dict, init=False)

    def reply(self, question: str, section_id: str) -> str | None:
        if self.max_turns is not None and self._used >= self.max_turns:
            return None
        self._used += 1

        bank = self.by_section.get(section_id, [])
        answered_by_question = any(
            isinstance(a, Answer) for b in self.by_section.values() for a in b
        )
        if answered_by_question:
            # A section with no bank of its own is not a person with nothing to
            # say. The closing has none, and every question it asks is about
            # something covered earlier, so it searches the whole script.
            elsewhere = [a for sid, b in self.by_section.items() if sid != section_id
                         for a in b if isinstance(a, Answer)]
            return self._selected(question, [a for a in bank if isinstance(a, Answer)],
                                  elsewhere)
        if bank:
            cursor = self._section_cursor.get(section_id, 0)
            if cursor < len(bank):
                self._section_cursor[section_id] = cursor + 1
                return bank[cursor]
            return self.default_reply

        if self.replies:
            index = min(self._used - 1, len(self.replies) - 1)
            return self.replies[index]
        return self.default_reply

    def _selected(self, question: str, bank: list, elsewhere: list) -> str:
        """The answer whose subject the question actually named.

        **A pure function of the question**, deliberately. Nothing about which
        answers have already been given enters into it, which is what makes the
        one property the harness most needs true by construction: ask the same
        question twice and get the same answer twice. A cursor cannot do that,
        and without it the harness cannot see the repetition that four separate
        investigations this month were about.

        **Why keyword membership is safe here when similarity matching has
        misfired five times in this project.** Every one of those was a
        judgement over text nobody wrote on purpose: is this term explained, is
        this question about that topic, was this seed asked. Here both sides are
        controlled. The triggers are authored next to the answer they belong to,
        they are distinctive technical tokens rather than a score over prose, and
        there is no threshold to tune.

        More importantly it is **checkable**. Six stored runs give 150-odd real
        interviewer turns, so what this selects can be read against what a person
        would have answered before it is trusted. None of the five that misfired
        had ground truth to check against.

        And the failure mode is chosen. No trigger matched means the candidate
        says it does not know, which is a truthful thing for a person to say and
        is visible in the transcript. The alternative — handing over the next
        answer on the list — is how the harness came to answer questions nobody
        asked.
        """
        # An answer with no triggers is a declared fallback for its section:
        # what this person says when the question fits nothing they have. Only
        # the opening uses one, because "so, tell me about yourself" names
        # nothing and every phrasing of it deserves the same reply.
        declared = next((a for a in bank if not a.triggers), None)

        asked = ask_of(question)
        best, score = None, (0, 0)
        for answer in bank:
            matched = [t for t in answer.triggers if t in asked]
            # Count first, then specificity. "language metric" beats "metric",
            # which is what stops a vague trigger winning a tie against the
            # answer the question actually named.
            rank = (len(matched), max((len(t) for t in matched), default=0))
            if rank > score:
                best, score = answer, rank
        if best is None:
            # Nothing in the ask named anything this candidate has. Try the whole
            # turn before giving up: a question can be about the previous answer
            # without quoting it back.
            whole = (question or "").lower()
            for answer in bank:
                matched = [t for t in answer.triggers if t in whole]
                rank = (len(matched), max((len(t) for t in matched), default=0))
                if rank > score:
                    best, score = answer, rank
        if best is None:
            # **Then anywhere in the script, because a person does not forget a
            # thing when the topic changes.** Asked about LoRA inside the
            # evaluation topic, this candidate knows what LoRA is: they
            # explained it two minutes ago. Answering "I do not know" there
            # would punish the interviewer for a stale question by inventing a
            # gap the candidate does not have, and coverage would move for a
            # reason that has nothing to do with the interviewer.
            for answer in elsewhere:
                matched = [t for t in answer.triggers if t in asked]
                rank = (len(matched), max((len(t) for t in matched), default=0))
                if rank > score:
                    best, score = answer, rank
        if best is not None:
            return best.text
        return declared.text if declared is not None else self.default_reply


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


def jargon_candidate() -> ScriptedCandidate:
    """Confident technical terms with no mechanism behind any of them.

    **Not the same failure as `thin_answer_candidate`, and that is the point.**
    That one is vague, and vagueness is easy to hear. This one sounds specific:
    real terms, named systems, even a latency number. Nothing underneath says
    what any of it does. A human interviewer stops and asks what session
    management means here; the risk is that an interviewer hears a technical
    noun and treats it as evidence.

    Every reply below is lifted from real interviews in `interviewer.db`, joined
    across the consecutive utterances they arrived in and otherwise unedited:
    `int_25259a2d4f80` and `int_6cbc1b9baf54`, both Senior Product Manager,
    Payments. In both, the interviewer answered "we have implemented a session
    management concurrency" by asking what trade-off it involved, and never once
    asked what it was.

    The last reply is the tell, and it is verbatim: pushed on what the layer
    does, the candidate defines each term with itself. The script stays in that
    register however hard it is pushed, because an interviewer that only probes
    when probing is easy has not been tested.
    """
    return ScriptedCandidate(
        replies=[
            "I'm working on a payment flow which allows multiple users to do the "
            "payment at the same time.",
            "This is for fintech. We are catering those industries right now.",
            "The architecture involves allowing the multiple users to do the payment "
            "at the same time. In that particular architecture we have included "
            "session management and control, which allows multiple users to do the "
            "payment at the same time, resulting in the decrease in the payment "
            "failures when multiple users are using the same platform.",
            "To manage it, we have implemented a session management concurrency, "
            "which allows multiple users to make the payments at the same time.",
            "For this, what we have done is that we have taken the users and given "
            "them the unique ID. Unique IDs are mapped to in a hash table, and those "
            "hash table are responsible for doing the transit and resulting in a "
            "unit transaction values.",
            "The trade off was that making the architecture complex in nature and "
            "increasing the latency. Earlier the latency was one second, and now the "
            "latency is one point two.",
            "The concurrency allows the multiple users to access it concurrently, "
            "while the session management manages those people per sessions.",
        ],
        default_reply=(
            "That is handled by the session management and concurrency layer, which "
            "manages the sessions for the multiple users accessing the platform."
        ),
    )


def deflecting_candidate() -> ScriptedCandidate:
    """Deflects the SAME term twice, which `jargon_candidate` never forces.

    That one drifts across several terms, so an interviewer can look like it is
    probing while never returning to anything. This one is asked what session
    management does and answers by saying it manages sessions, and when asked
    again says it does concurrency control. The term is never explained and the
    candidate never refuses, so nothing in the refusal path fires and nothing
    stops the interview moving on except the interviewer deciding to stay.

    Taken from `int_b3d0a830156b` and `int_a96da5ddb0be`. In the first, the
    interviewer asked how WebRTC fitted the architecture, got "WebRTC is a live
    audio listener, which listens everything and send it to the 3rd party", said
    "Thanks for explaining that" and moved to an unrelated question.
    """
    return ScriptedCandidate(
        replies=[
            "Right now I'm working on a payment gateway, and how to make them "
            "reliable and consistent for multiple users at the same time.",
            "In that particular thing, what we have done is that we have built a "
            "payment gateway in which we allow multiple users to make the payment "
            "at the same time. For that we have used the technology like session "
            "and concurrency.",
            "I have introduced the layer of a session management concurrency, which "
            "allows the multiple users to make the payment at the same time without "
            "seeing each other transactions at the same time.",
            "The session management allows to do the concurrency control. As well as "
            "it does not allow the people to do the same transaction at the same "
            "time. Here the OLTP and OLAP works.",
            "Like, how you can do the concurrency control session. The concurrency "
            "allows the multiple users to access it concurrently, while the session "
            "management manages those people per sessions.",
        ],
        default_reply=(
            "As I said, the session management manages the sessions and the "
            "concurrency control allows the users to go concurrently."
        ),
    )


def competent_candidate() -> ScriptedCandidate:
    """Someone who answers well, in the ordinary vocabulary of their field.

    **Every other scripted candidate is a bad candidate.** Vague, deflecting, or
    both, and every one of them emits an unexplained term in every single answer.
    So the section's own questions and the term block never compete, and two
    changes this week were measured against scenarios that could not detect what
    they changed. The case that matters most for a hiring tool is the candidate
    who is good, and nothing tested it.

    What makes this one different, and all four properties are load-bearing:

    - **It uses field vocabulary naturally.** "LLM", "fine tuning", "agent",
      "evaluator". These are the words of the job, not claims that need proof,
      and a turn spent asking a senior engineer to define one is a turn wasted.
    - **It also names specifics.** Sarvam-M 24B, bf16 LoRA, GPTQ 4-bit at
      14.2 GB, 97.08 against 97.40 on 308 held-out examples, KL divergence,
      ml.g7e.2xlarge. These are the things worth probing, and the things that
      separate someone who did the work from someone who read about it.
    - **Most answers are substantive**, and a few genuinely are not, because a
      candidate who is perfect at everything is its own kind of unrealistic.
      Deployment is where this one runs out: the real one said "I don't know"
      three times and so does this.
    - **It answers the question asked.** No deflection. If the interviewer
      spends the section on vocabulary, that is the interviewer's choice and not
      the candidate forcing it.

    Every answer is taken from `int_7ffd1be01f56`, a real Senior AI/ML Engineer
    interview, joined across the utterances it arrived in and otherwise unedited
    apart from repairing what the transcription mangled: the numbers came
    through as "97.40 against 90.708 on 3 0 8 exit example" and are restored to
    what they plainly were.

    The test of whether this is right: probing "LLM" should read as a wasted
    turn, and probing "GPTQ 4-bit" should read as a good question. Both are
    available from the same script.
    """
    return ScriptedCandidate(
        by_section={
            "opening": [
                Answer(
                    (),
                    "Right now I'm building an AI interviewer. It's a voice agent. It "
                    "reads a candidate CV, holds a twenty minute conversation, and "
                    "produces a scored report with quotes from the transcript backing "
                    "every score."
                ),
            ],
            "applied_llm_depth": [
                # **No trigger here may be a word the interviewer says on its
                # own account.** This one carried "interviewer", "working on"
                # and "building", and the greeting is "I'm Mitra, an AI
                # interviewer" while the opening question the prompt models is
                # "tell me what you're working on at the moment". So the
                # greeting selected this answer, the opening's own answer was
                # unreachable in every run, and it counted against the
                # interviewer as material it had failed to reach. A trigger has
                # to name what the answer is ABOUT, not the shape of the
                # question that would ask for it.
                Answer(
                    ("agent", "evaluator", "two systems"),
                    "The bet was that the interview process is slow and an AI "
                    "interviewer saves everyone's time. There are two systems: the "
                    "agent that takes the interview, and the evaluator that reads the "
                    "transcript and decides whether the candidate is good enough."
                ),
                Answer(
                    ("gpt-4o", "gpt 4o", "sarvam", "24b", "classifier", "replac", "routing decision", "health-tech"),
                    "For the health-tech classifier we replaced GPT-4o with a "
                    "fine-tuned Sarvam-M 24B. It takes the caller's utterance and picks "
                    "which tool to call, so the output is a routing decision rather "
                    "than prose."
                ),
                Answer(
                    ("lora", "sagemaker", "g7e", "jinja", "mistral", "adapter", "training setup", "trained it", "fine-tun", "fine tun"),
                    "We trained it with bf16 LoRA on a SageMaker ml.g7e.2xlarge, on a "
                    "Mistral-format jinja template. LoRA freezes the base weights and "
                    "trains small low rank matrices beside them, which is why it fits "
                    "where full fine-tuning a 24B model does not, and the adapter is "
                    "enough when you are teaching it one decision."
                ),
                Answer(
                    # "tradeoff"/"trade-off" removed: they name the shape of a question,
                    # not what this answer is about, and the depth ramp now says
                    # "ask for the trade-off they made" in so many words.
                    ("quantis", "gptq", "4-bit", "four-bit", "14.2"),
                    "Then we quantised it to GPTQ 4-bit, which rounds the weights to four "
                    "bit integers with a scale per group, and took it to 14.2 GB so it "
                    "fits alongside the other services on the box. The tradeoff we were "
                    "watching for was whether four-bit weights moved the routing "
                    "decisions, and we checked that before shipping it."
                ),
                Answer(
                    # Four removed for the same reason. "cost" and "bet behind" are how
                    # the interviewer phrases a question about anything; "control"
                    # sits inside "version control" and "concurrency control"; and
                    # "why not" is not a subject at all.
                    ("per-call", "own the model", "owning", "cost and control"),
                    "The reason for owning the model at all was cost and control. "
                    "GPT-4o was a per-call charge on every routing decision, and the "
                    "decision is narrow enough that a 24B model taught on our own pairs "
                    "does it as well."
                ),
            ],
            "evaluation_reasoning": [
                Answer(
                    ("metric", "accuracy rather", "language metric", "why accuracy", "measure decision", "held-out example", "reflect", "real-world"),
                    "Accuracy on each held-out example: does the fine-tune choose the "
                    "same tool as the label. We used accuracy rather than a language "
                    "metric because the output is a routing decision, not prose. "
                    "Getting the words slightly different does not matter. Getting the "
                    "tool wrong matters completely."
                ),
                Answer(
                    ("97.08", "baseline", "bf16"),
                    "97.08 percent on the bf16 baseline. bf16 is a sixteen bit float "
                    "that keeps fp32's exponent range, so you get the dynamic range at "
                    "half the memory. Same evaluation set, same prompt, same parsing."
                ),
                Answer(
                    ("97.40", "308", "inside the noise", "quantised model", "flat", "statistical"),
                    "Same set again after quantising: 97.40 against 97.08 on 308 "
                    "held-out examples. So the quantised model was very slightly ahead, "
                    "which on 308 examples is well inside the noise."
                ),
                Answer(
                    ("divergence", "distribution"),
                    "For distributions I used KL divergence. You have a ground truth "
                    "distribution and you compare how far each model's output "
                    "distribution sits from it. The model with the lower divergence is "
                    "the one closer to the ground truth."
                ),
                Answer(
                    ("significan", "spread", "rerun", "noise", "real difference", "third of a percent", "establish"),
                    "The set is 308 held-out examples, which is small enough that I "
                    "would not read a third of a percent as a real difference. To call "
                    "a change real on that set I would want it to move by more than the "
                    "spread I see between reruns."
                ),
            ],
            "ml_service_deployment": [
                Answer(
                    ("livekit", "systemd", "ubuntu", "deployment process", "put it up", "put up", "weights", "in pieces", "six voice agent", "six production", "typical deployment"),
                    "Six voice agents on LiveKit Agents with OpenAI Realtime, on a "
                    "shared Ubuntu VM under systemd. LiveKit Agents holds the audio session "
                    "and systemd starts each agent and restarts it if it dies. You "
                    "check the GPU is free, then put it up in pieces rather than in "
                    "one go: weights first, then the scripts, then the rest."
                ),
                Answer(
                    ("upload", "loading", "load", "inference", "into memory", "training time"),
                    "At training time it means uploading the model files. At inference "
                    "it means loading them into memory on the VM."
                ),
                Answer(
                    ("restart", "gpu", "shared", "unit", "memory spike", "monitor"),
                    "The six agents run under systemd on one box, so restarting one is "
                    "a unit restart and the others keep serving. That is also the risk: "
                    "they share a GPU, so a job that takes more memory than expected is "
                    "felt by all of them."
                ),
                Answer(
                    ("otel", "tempo", "prometheus", "loki", "grafana", "langfuse", "observability", "tracing", "logs"),
                    "For observability we have OTel Collector into Tempo for traces, "
                    "Prometheus for metrics, Loki for logs and Grafana over the top, "
                    "with Langfuse for the LLM calls specifically."
                ),
                # **A gap has no subject, so it cannot carry triggers.** These
                # were two answers gated on "docker, container, kubernetes" and
                # "version control, rollback, ci/cd". Across 133 real
                # interviewer questions not one contained any of those words, so
                # neither ever fired, and this topic read as the worst in the
                # table for a reason that was the fixture's.
                #
                # The rule written above is that a trigger names what the answer
                # is ABOUT. "I don't know" is about nothing; its subject lives
                # entirely in the question. Putting the question's subject into
                # the answer's triggers is that rule upside down, and it turned
                # the one genuine gap this candidate has into the one thing the
                # interview could never find.
                #
                # So it is the topic's declared fallback instead: what this
                # person says when a question here names nothing they have. Two
                # near-identical answers collapse to one, which also makes every
                # topic five answers rather than this one alone having six.
                #
                # **Still genuinely unanswerable.** They cannot describe Docker,
                # version control or a rollback, and nothing here lets them. What
                # changed is that the interviewer can reach the gap, not that the
                # candidate found an answer.
                Answer(
                    (),
                    "I don't know about that one, so I can't tell you."
                ),
            ],
            "client_communication": [
                Answer(
                    ("hindi", "urdu", "language id", "phonetic", "confusion between"),
                    "On the language ID prototype I told them plainly that Hindi and "
                    "Urdu were being confused, and that the confusion was the model "
                    "hearing two languages that share most of their phonetics rather "
                    "than a bug we could fix by training longer."
                ),
                Answer(
                    ("prong", "at chance", "confusion matrix", "recommend", "stop"),
                    "For the prong-open detector I said it was performing at chance, "
                    "which means it was not detecting anything. I showed them the confusion "
                    "matrix, the grid of what it predicted against what was actually "
                    "true, so they could see the misses themselves. I recommended we "
                    "stop rather than spend more on it."
                ),
                Answer(
                    (),
                    "I try to give them the number and what it would cost to move it, "
                    "rather than an adjective. Ninety-seven percent on the routing "
                    "decision means about three calls in a hundred go to the wrong "
                    "tool, and that lands better than saying it works well."
                ),
                Answer(
                    ("inside the noise", "no change", "quantised", "deck", "win", "slightly ahead"),
                    "When the quantised model came back very slightly ahead of the "
                    "full-precision one, I said explicitly that the difference was "
                    "inside the noise on 308 examples and they should read it as no "
                    "change, not as an improvement. Otherwise it goes into a deck as a "
                    "win we did not get."
                ),
                Answer(
                    ("guardrail", "safety", "prompt-level", "booking", "unsafe", "not reliable"),
                    "The hard one was telling them the safety rules in the prompt were "
                    "not reliable. I did not want them to hear that the agent was "
                    "unsafe and stop the project, so I showed them the specific "
                    "bookings that got through and what moving the rules into code "
                    "would prevent."
                ),
            ],
            "python_ml_basics": [
                Answer(
                    ("librar", "framework", "pytorch", "transformers", "peft", "packages", "use most"),
                    "Mostly PyTorch, transformers and peft for the fine-tuning side, "
                    "and plain Python for the agent glue."
                ),
                Answer(
                    ("guardrail", "python code", "booking", "refuse", "enforce", "rule"),
                    "The guardrails for the medical booking agent were the interesting "
                    "one. They were prompt-level rules and they leaked, so I moved them "
                    "into Python: the agent proposes a booking and the code refuses it "
                    "if it violates a rule, rather than asking the model not to."
                ),
                Answer(
                    ("test", "determinist", "leak", "cases", "unit"),
                    "The rule functions are ordinary Python and they are the part I "
                    "test properly, because they are deterministic. I have the cases "
                    "that used to leak written down as tests, so if someone loosens a "
                    "rule it fails rather than reaching a patient."
                ),
                Answer(
                    ("hold out", "held-out", "pars", "evaluation script", "validat", "before training"),
                    "For the fine-tune I hold out a set before training and never look "
                    "at it, and the evaluation script parses the model output the same "
                    "way the production path does. If the two parse differently you "
                    "measure something the users never see."
                ),
                Answer(
                    ("pandas", "data handling", "template", "jinja", "training data"),
                    "Data handling is pandas and plain dictionaries mostly. The "
                    "training data for the classifier was input and output pairs in the "
                    "Mistral jinja template, so most of the work was getting the "
                    "template exactly right rather than anything clever."
                ),
            ],
            "voice_audio_exposure": [
                Answer(
                    ("voxlingua", "ecapa", "whisper", "fleurs", "language identification", "language id", "benchmark", "embedding"),
                    "The language ID prototype used VoxLingua107 ECAPA for the language "
                    "embedding and Whisper-small for the transcript, benchmarked on "
                    "FLEURS across seven Indian languages."
                ),
                Answer(
                    ("stutter", "drive-thru", "drive thru", "vad", "silence", "debug", "diagnos"),
                    "The stutter in the drive-thru agent turned out to be silence "
                    "detection: the VAD was cutting the turn while the customer was "
                    "still mid-sentence, so the agent answered half an order."
                ),
                Answer(
                    ("endpoint", "transcription", "layer", "the fix", "turn was"),
                    "The fix was on the endpointing side rather than the transcription "
                    "side. The words were coming through correctly, the turn was just "
                    "being called over too early, and those are two different layers."
                ),
                Answer(
                    ("realtime", "real-time", "speech to speech", "speech-to-speech", "transcript", "audit"),
                    "On the six agents we are on OpenAI Realtime, so speech to speech "
                    "rather than a pipeline. You lose the transcript as the thing you "
                    "can audit, which for the health-tech one mattered enough that we "
                    "kept the separate classifier rather than trusting the voice model "
                    "with the routing."
                ),
                Answer(
                    ("latency", "dead air", "first audio", "model choice", "constraint"),
                    "Latency is the constraint that shapes everything. Anything that "
                    "has to finish before the first audio goes out is heard as dead "
                    "air, so the model choice for the live turn is not the same "
                    "decision as the model choice for anything offline."
                ),
            ],
        },
        # Reached only when the question named nothing this person has, which
        # under question-conditioned selection is a real event rather than a
        # bank running dry. So it is now a truthful "I do not know", where under
        # the old cursor it had to be neutral filler.
        default_reply=(
            "I have not done that one, so I would only be guessing."
        ),
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
            # Verbatim from the session where none of this was detected.
            "I don't want to continue this interview. Just end this interview.",
            "Just end in the interview.",
            "No, just end the interview.",
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
