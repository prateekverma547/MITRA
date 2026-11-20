"""Run the guardrail from CLAUDE.md: gpt-4.1-mini vs gpt-4.1, side by side.

    uv run python scripts/compare_models.py
    uv run python scripts/compare_models.py --scenarios thin_answer contradiction

Drives identical scripted candidates through the brain on both models and writes
both transcripts to `backend/sessions/model_comparison/` for reading.

The decision this informs is explicitly a judgement, not a metric: if mini
complies lazily — accepts hollow answers, drifts off role, softens the redirect —
the live default goes back to gpt-4.1. Probing quality outranks speed and cost.
So this script reports signals and prints transcripts; it does not declare a
winner.
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.blueprint_source import load_blueprint  # noqa: E402
from bot.brain.brain import (  # noqa: E402
    MAX_CLOSING_TURNS,
    MAX_REPAIR_ATTEMPTS,
    SECONDS_PER_COMPETENCY_TURN,
    InterviewBrain,
)
from bot.brain.drivers import OpenAIInterviewer, OpenAIJudge  # noqa: E402
from bot.brain.harness import (  # noqa: E402
    competent_candidate,
    contradicting_candidate,
    deflecting_candidate,
    jargon_candidate,
    off_topic_candidate,
    run_interview,
    thin_answer_candidate,
    unheard_candidate,
    write_run,
)
from bot.brain.state import BrainConfig, SectionKind  # noqa: E402
from bot.config import MissingConfig, Settings  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "sessions" / "model_comparison"

SCENARIOS = {
    "thin_answer": (
        thin_answer_candidate,
        "Every answer is vague. Does the interviewer push for specifics, or accept it?",
    ),
    "contradiction": (
        contradicting_candidate,
        "Claims sole ownership of a decision, then says it was forced on him. "
        "Cross-section — only carried claims can surface it.",
    ),
    "jargon": (
        jargon_candidate,
        "Confident technical terms with nothing behind them, taken verbatim from "
        "two real interviews. Does the interviewer ask what the term means, or "
        "accept it and ask about its trade-offs?",
    ),
    "deflecting": (
        deflecting_candidate,
        "Deflects the SAME term twice. Asked what session management does, says "
        "it manages sessions. Does the interviewer stay on it, or accept it?",
    ),
    "off_topic": (
        off_topic_candidate,
        "Repeatedly steers away from the role. Does the redirect hold?",
    ),
    "repair": (
        unheard_candidate,
        "Keeps asking for the question again. Does the interviewer return to "
        "the SAME question, or quietly abandon it and ask something else?",
    ),
    "competent": (
        competent_candidate,
        "Answers well, in ordinary field vocabulary, with real specifics. Does "
        "the interviewer spend its turns on the field's words, or on this "
        "person's work? Every other scenario here is a bad candidate.",
    ),
}

#: Which fixture a scenario runs against. Absent means the default.
#:
#: The scenarios above are all Senior Product Manager, because that is the
#: fixture the brain has always defaulted to. `competent` cannot be: its answers
#: are an ML engineer's, and against a PM blueprint every one of them would be
#: off-topic, which measures the redirect rather than the thing in question.
#:
#: It also needs a blueprint that carries `claims_to_verify`, and the two
#: existing fixtures carry none — which is why the specific-terms signal had
#: nothing to score against and could report a number for a run in which half
#: its inputs were empty.
SCENARIO_BLUEPRINT = {
    "competent": "ml_senior",
}

#: Screened on latency first, then measured on quality. Excluded and why:
#: gpt-5-mini and gpt-5-nano are superseded by the 5.4 pair and were the two
#: worst on consistency (1111ms and 840ms of spread against 172ms); gpt-5.6-luna
#: was no better than the baseline and costs more than gpt-5.4-nano.
MODELS = ["gpt-4.1-mini", "gpt-4.1", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4.1-nano"]


def signals(run, brain) -> dict:
    """Cheap, honest indicators. Not a score — the transcripts are the evidence."""
    interviewer_turns = [t["text"] for t in run.transcript if t["speaker"] == "interviewer"]
    joined = " ".join(interviewer_turns).lower()
    role = brain.blueprint.role_title.lower()

    return {
        "interviewer_turns": len(interviewer_turns),
        "avg_words": round(
            sum(len(t.split()) for t in interviewer_turns) / max(1, len(interviewer_turns)), 1
        ),
        "questions_asked": sum(t.count("?") for t in interviewer_turns),
        "sections_reached": len({t["section_id"] for t in run.transcript}),
        "names_role": role in joined,
        "contradictions_found": len(brain.contradictions),
        "claims_carried": len(brain.carried_claims),
        "coverage": {
            o["section_id"]: o["coverage"]
            for o in run.outcomes
            if o["kind"] == "competency"
        },
        "shortfalls": [o["section_id"] for o in run.outcomes if o["coverage_shortfall"]],
        # Added when the repair and register blocks landed: the guardrail is
        # about instruction adherence under a heavier prompt, and these are the
        # instructions that made it heavier.
        "repairs_requested": brain.repairs_requested,
        "returned_to_the_question": _returned_to_the_question(run),
        "field_words_used": _field_words_used(joined, brain.blueprint),
        # Added with the depth-ramp change. See `_probe_shape`.
        **_probe_shape(run, brain),
        # Added when the interviewer turned out to be speaking half the words in
        # the interview. See `_talk_length`.
        **_talk_length(run, brain),
        # Added when a communication section turned into a technical one. See
        # `_section_coverage`.
        **_section_coverage(run, brain),
        # Added when the reordering landed. See `_specific_terms`.
        **_specific_terms(run, brain),
        # Added with the acknowledgement change. See `_acknowledgement`.
        **_acknowledgement(run),
    }


#: How a reply that heard the candidate opens. Their own words are not on this
#: list on purpose: repeating them back is what the change removed.
_ACK_OPENER = re.compile(
    r"^\W*(?:okay|ok|right|thanks|thank you|got it|no problem|that'?s fine|"
    r"sure|of course|sorry|understood|alright|all right|fair enough)\b",
    re.IGNORECASE,
)

#: A verdict on the answer. The rule the acknowledgement change must not break.
_PRAISE = re.compile(
    r"\b(?:great|excellent|interesting|makes sense|helpful|impressive|nice|"
    r"well done|fantastic|good (?:answer|point|example|question)|"
    r"clear (?:answer|explanation|example)|that'?s clear|good to hear)\b",
    re.IGNORECASE,
)


def _acknowledgement(run) -> dict:
    """Did the interviewer show it heard them, and did it slip into praise doing so?

    Live in `int_39845b4313d4` the candidate's answers were followed straight
    by the next question, and it read as not listening. `acknowledged` is the
    share of replies to a candidate that open with an acknowledgement;
    `praise` counts verdicts anywhere, which must stay at zero; `stacked`
    counts turns with two question marks, the failure the prompt was measured
    against.
    """
    replies, opened, praise, stacked = 0, 0, 0, 0
    previous = None
    for turn in run.transcript:
        if turn["speaker"] == "interviewer":
            text = turn["text"]
            if previous == "candidate":
                replies += 1
                opened += bool(_ACK_OPENER.match(text))
            praise += len(_PRAISE.findall(text))
            stacked += text.count("?") > 1
        previous = turn["speaker"]
    return {
        "acknowledged": round(opened / replies, 2) if replies else 0.0,
        "praise": praise,
        "stacked": stacked,
    }


#: A digit anywhere. "GPTQ 4-bit", "Sarvam-M 24B", "97.08 percent",
#: "ml.g7e.2xlarge". A version or a quantity is the clearest evidence that a
#: term names one particular thing.
_HAS_DIGIT = re.compile(r"\d")

#: A capital inside a word: LiveKit, SageMaker, VoxLingua, PyTorch. Product
#: names take this shape and categories almost never do.
_INTERNAL_CAPS = re.compile(r"[a-z][A-Z]")


def _names_something_particular(term: str) -> bool:
    """A proxy, and a deliberately conservative one. See `_specific_terms`."""
    return bool(_HAS_DIGIT.search(term) or _INTERNAL_CAPS.search(term))


def _as_spoken(term: str, run) -> str:
    """The term with the capitals it was said with.

    The brain keys its raise counts by `term.lower()`, so reading them directly
    turns "LiveKit Agents" into "livekit agents" and the internal-capital rule
    can never fire — which it did not, silently, until the first baseline was
    read. Recovered from the transcript rather than by changing what the brain
    stores, because this is a measurement problem and the brain is not on trial.
    """
    import re as _re

    pattern = _re.compile(_re.escape(term), _re.IGNORECASE)
    for turn in run.transcript:
        found = pattern.search(turn["text"])
        if found:
            return found.group(0)
    return term


def _specific_terms(run, brain) -> dict:
    """Were the probes spent on this person's work, or on the field's words?

    The interviewer asked a senior ML engineer to define "LLM" and "fine
    tuning" while the choice of Sarvam-M over GPT-4o and the GPTQ tradeoff sat
    unused in the same prompt. Nothing measured that, because a term probe looks
    identical either way: it quotes the candidate and asks what the thing does.

    **The previous version of this signal was inverted, and was reported rather
    than shipped.** It counted a term as specific when it shared a word with the
    seed questions or the CV claims. Scored against the real interview it called
    "agent", "LLM do the reasoning" and "fine tuning" specific and "KL
    divergence" generic — exactly backwards, because the seed questions are
    written in the field's vocabulary too, so overlapping with them is evidence
    of nothing.

    **The honest answer to what was asked is that this cannot be decided
    reliably in code.** The question is whether a term names one particular
    thing that could be checked or a category of things, and that is semantic:
    "LLM" and "ECAPA" are the same shape, the same length and the same
    capitalisation, and only knowing what they refer to separates them. Doing it
    properly needs a model call. This does not make one, because the signal runs
    fifteen times per comparison and a judgement that costs money per read stops
    being a cheap indicator.

    So this is a proxy, and here is exactly what it does. A term counts as
    naming something particular if it carries a digit or an internal capital.
    Nothing else counts.

    **What it gets wrong, in one direction on purpose.** It misses named methods
    and single proper nouns: "KL divergence", "GPTQ", "ECAPA", "FLEURS",
    "Whisper-small", "systemd", "Grafana" all read as generic when a person
    reading them would call every one of them specific. So the count understates
    how well the interviewer did.

    That direction is chosen rather than tolerated. A false "specific" hides the
    failure this exists to catch, which is what the last version did. A false
    "generic" overstates the waste and sends a reader to the transcript, where
    the answer is. The rule that would fix the misses is "any capitalised
    token", and it cannot be used: it fires on "LLM", "VAD", "LoRA" and "API",
    which are the field vocabulary the signal is meant to detect.

    Which is why **the terms themselves are reported next to the count**. The
    count is the cheap indicator; the strings are the evidence, and reading four
    of them takes a person about five seconds.

    Counted from the terms the interviewer actually put to the candidate, which
    is what `mark_term_raised` records, rather than from everything the judge
    reported. A term the judge raised and the interviewer never asked about cost
    nobody a turn.
    """
    # Through the brain's own accessor. This read was `brain._term_raises`,
    # then a comprehension over `section.term_raises`, and it broke the day the
    # state moved and would have broken again. What it wants is the question,
    # not whichever field currently answers it.
    probed = [_as_spoken(t, run) for t in brain.terms_asked_about()]
    if not probed:
        return {"probe_terms_specific": "n/a", "probe_terms": "none"}

    particular = sum(1 for t in probed if _names_something_particular(t))
    return {
        "probe_terms_specific": f"{particular}/{len(probed)}",
        # Verbatim, for reading. The proxy above is not trusted to settle this.
        "probe_terms": "; ".join(sorted(probed)),
    }


#: Words naming the PEOPLE an answer is addressed to. Not the act: "explain"
#: appears in a technical competency ("able to explain the architecture") and a
#: communication one ("able to explain to non-technical stakeholders") alike, so
#: matching on the act would fire on both.
AUDIENCE_WORDS = (
    "stakeholder", "non-technical", "nontechnical", "partner", "audience",
    "executive", "cross-functional", "business team", "operations team",
)

#: A question about the act of communicating, rather than about how a thing works.
_COMMS_CUES = (
    "explain that to", "explain it to", "explain this to", "how did you explain",
    "did they follow", "did they understand", "how did you put that",
    "who did you", "what did they", "how did they react", "convince",
    "non-technical", "stakeholder", "audience",
)
#: Asking how the THING works, in a section that is supposed to be about how
#: they explained it to somebody. Narrower than the probe-shape set below on
#: purpose: this asks "did the interviewer go technical in a communication
#: topic", not "was this a mechanism question".
#:
#: **It was called `_MECHANISM_CUES` and so was the set 120 lines below it.**
#: Python resolves module globals at call time, so both this name and that one
#: pointed at whichever was defined last, and `_section_coverage` has been
#: running against a cue list it was not written for since the day the second
#: one was added.
_MECHANICS_CUES = (
    "walk me through how", "how that actually works", "what does it actually do",
    "how does that work", "what does that do",
)


def _is_about_talking_to_people(target_depth: str) -> bool:
    text = (target_depth or "").lower()
    return any(word in text for word in AUDIENCE_WORDS)


def _section_coverage(run, brain) -> dict:
    """Did the section ask its own questions, and about its own subject?

    Neither number existed, and between them they are what would have caught a
    communication section spending five of six turns on the mechanics of terms
    inside an analogy. One of its four seed questions was asked; the employer's
    stated dealbreaker for the role went unassessed.

    **Seed overlap is a diagnostic and not a target, and the name says so.** The
    prompt tells the interviewer to adapt these rather than read them out, so an
    exact match would count nothing; three shared content words is a rough proxy.
    Hand-checked against 144 seed/section/run pairs it found 45 where a person
    found 55, and three of its 45 were false positives caused by matching the
    acknowledgement rather than the question. Do not set a bar on it.
    """
    asked_seeds = total_seeds = 0
    comms = mech = 0
    for section in brain.sections:
        if section.kind.value != "competency":
            continue
        said = [t["text"] for t in run.transcript
                if t["speaker"] == "interviewer" and t.get("section_id") == section.id]
        # One turn answers at most one seed. Without that, a single question
        # that happens to share "non-technical stakeholders" with two seeds
        # counts as having asked both, which reads 2 of 4 where the transcript
        # plainly shows 1.
        spoken = [set(_content_words(t)) for t in said]
        taken: set[int] = set()
        for seed in section.seed_questions:
            total_seeds += 1
            wanted = set(_content_words(seed))
            best, best_overlap = None, 0
            for index, words in enumerate(spoken):
                if index in taken:
                    continue
                overlap = len(wanted & words)
                if overlap > best_overlap:
                    best, best_overlap = index, overlap
            if best is not None and best_overlap >= 3:
                taken.add(best)
                asked_seeds += 1
        if _is_about_talking_to_people(section.target_depth):
            for t in said:
                low = t.lower()
                if any(c in low for c in _COMMS_CUES):
                    comms += 1
                elif any(c in low for c in _MECHANICS_CUES):
                    mech += 1

    return {
        # Renamed from `seeds_asked`, which read as a target. It is not one:
        # `shared/contracts.py` calls seed questions "Openers. The brain adapts
        # and follows up; it does not read these verbatim", the prompt offers
        # them as "questions you may draw on", and no consumer treats an unasked
        # one as a gap — the judge scores against `target_depth` and the report
        # against the spec. It is also unsound as a count: hand-checked at 55 of
        # 144 against 45 measured, an 18% undercount, and its false positives
        # score the acknowledgement rather than the question, so a topic where
        # one question was asked three times measured as three of four. Kept as
        # a rough diagnostic, never as a bar.
        "seed_overlap_rough": f"{asked_seeds}/{total_seeds}" if total_seeds else "n/a",
        "comms_vs_mechanism": f"{comms} vs {mech}" if (comms or mech) else "n/a",
    }


#: What a single spoken turn may run to before it stops being a question and
#: starts being a monologue. The stated rule was fifteen words per *sentence*
#: with two or three sentences allowed, which permits forty-five; this is the
#: number the turn is actually judged against.
TURN_WORD_LIMIT = 20


def _talk_length(run, brain) -> dict:
    """How much the interviewer says, and how much of the conversation it takes.

    The share is the one that matters. In an interview the candidate should be
    doing most of the talking, and on a real call this read 48 percent. The
    candidate said it felt long, and this rather than latency is what they were
    feeling: composition never exceeded 1943ms and the twelve-second gap that
    was suspected turned out not to exist.

    The opening is excluded. It is scripted, it is meant to be a few sentences
    of greeting and orientation, and including it flatters nothing either way.
    """
    opening_ids = {s.id for s in brain.sections if s.kind.value == "opening"}
    said = [
        t["text"].split()
        for t in run.transcript
        if t["speaker"] == "interviewer" and t.get("section_id") not in opening_ids
    ]
    heard = [
        t["text"].split()
        for t in run.transcript
        if t["speaker"] == "candidate" and t.get("section_id") not in opening_ids
    ]
    if not said:
        return {"words_median": "n/a", "words_max": "n/a",
                "words_over_limit": "n/a", "interviewer_share": "n/a"}

    lengths = sorted(len(w) for w in said)
    interviewer_words = sum(lengths)
    candidate_words = sum(len(w) for w in heard)
    total = interviewer_words + candidate_words
    over = sum(1 for n in lengths if n > TURN_WORD_LIMIT)
    return {
        "words_median": lengths[len(lengths) // 2],
        "words_max": lengths[-1],
        "words_over_limit": f"{over}/{len(lengths)}",
        "interviewer_share": f"{interviewer_words / total * 100:.0f}%" if total else "n/a",
    }


#: Asking what a thing IS or HOW IT WORKS. The point of the change these
#: measure: none of these accept a term as given.
_MECHANISM_CUES = (
    "what do you mean", "what does that mean", "what does it mean",
    "what does that actually", "how does that work", "how does it work",
    "how that works", "how that actually works", "walk me through how",
    # **The escalated probe, missing since this set was written.** The term
    # block's second ask is "You have said X twice now. What does it actually
    # do?", and none of the cues above match it: "what does that actually" is
    # not "what does it actually". So every second ask, the one the whole term
    # machinery exists to produce, was invisible to this signal. Nineteen of
    # nineteen in the ten-run arm current when this was found.
    "what does it actually do", "what does that do",
    "explain how", "what exactly is", "what exactly does", "how exactly",
    "what is that", "what does the", "in your system", "under the hood",
)

#: The four ways the depth ramp already offered to go deep. Every one of them
#: takes the candidate's term at face value and asks about something attached
#: to it, which is why quoting the term back is not evidence of probing it.
_MENU_CUES = (
    "trade-off", "trade off", "tradeoff", "the bet behind", "bet was behind",
    "how did you measure", "how do you measure", "what metric", "which metric",
    "what specific metrics", "went wrong", "would you defend",
)


def _probe_shape(run, brain) -> dict:
    """Does a question open up the last answer, or move on from it?

    The failure this was built to catch looks like probing and is not. Against
    "we have implemented a session management concurrency", the interviewer said
    *"What was the trade-off you considered when implementing session management
    concurrency for that payment gateway?"* — it quotes the term back, so any
    check for "did it reference what they said" scores it as a follow-up. It
    then accepts the term as real and asks about something hanging off it.

    So quoting is necessary and not sufficient. Three counts per opportunity,
    where an opportunity is an interviewer turn inside a competency section that
    follows a candidate answer:

      mechanism  echoes a word from the answer AND asks what it is or how it
                 works. The number that should move.
      menu       asks for a trade-off, a metric, a bet, or what went wrong.
      new_topic  introduces a word that is in the seed questions and was not in
                 the answer. Advancing the plan.

    They do not have to sum to the opportunity count: a turn can be none of the
    three. Reported as counts rather than a verdict, like everything else here.
    """
    seed_words = {
        word
        for section in brain.sections
        for question in section.seed_questions
        for word in _content_words(question)
    }

    turns = run.transcript
    counts = {"mechanism": 0, "menu": 0, "new_topic": 0}
    opportunities = 0

    for i, turn in enumerate(turns):
        if turn["speaker"] != "interviewer":
            continue
        section = next(
            (s for s in brain.sections if s.id == turn.get("section_id")), None
        )
        if section is None or section.kind.value != "competency":
            continue

        # Everything the candidate said since the interviewer last spoke. Real
        # answers arrive as several utterances, so one turn back is not enough.
        answer: list[str] = []
        for previous in reversed(turns[:i]):
            if previous["speaker"] == "interviewer":
                break
            answer.append(previous["text"])
        if not answer:
            continue

        opportunities += 1
        said = set(_content_words(" ".join(answer)))
        asked = turn["text"].lower()
        echoed = said & set(_content_words(turn["text"]))

        if echoed and any(cue in asked for cue in _MECHANISM_CUES):
            counts["mechanism"] += 1
        elif any(cue in asked for cue in _MENU_CUES):
            counts["menu"] += 1
        elif (set(_content_words(turn["text"])) & seed_words) - said:
            counts["new_topic"] += 1

    if not opportunities:
        return {"probe_mechanism": "n/a", "probe_menu": "n/a", "probe_new_topic": "n/a"}
    return {
        "probe_mechanism": f"{counts['mechanism']}/{opportunities}",
        "probe_menu": f"{counts['menu']}/{opportunities}",
        "probe_new_topic": f"{counts['new_topic']}/{opportunities}",
    }


def _returned_to_the_question(run) -> str:
    """After a repair, did the next question ask for the same thing?

    Reported as a fraction rather than a verdict. Judging "same thing" properly
    needs a human reading the transcript, which is what the files are for.
    """
    from bot.brain.repair import RepairKind, classify

    turns = run.transcript
    asked_again = 0
    opportunities = 0
    for i, turn in enumerate(turns):
        if turn["speaker"] == "candidate" and classify(turn["text"]) is RepairKind.REPEAT:
            before = next(
                (t["text"] for t in reversed(turns[:i]) if t["speaker"] == "interviewer"), ""
            )
            after = next(
                (t["text"] for t in turns[i + 1:] if t["speaker"] == "interviewer"), ""
            )
            if not before or not after:
                continue
            opportunities += 1
            shared = set(_content_words(before)) & set(_content_words(after))
            if len(shared) >= 3:
                asked_again += 1
    return f"{asked_again}/{opportunities}" if opportunities else "n/a"


def _content_words(text: str) -> list[str]:
    import re

    stop = {
        "the", "a", "an", "and", "or", "you", "your", "that", "this", "was",
        "were", "did", "do", "can", "could", "would", "about", "with", "for",
        "what", "how", "why", "tell", "me", "of", "to", "in", "on", "it", "is",
        "sorry", "i", "my", "we", "they", "at", "as", "so", "but", "if", "not",
    }
    words = re.sub(r"[^a-z ]+", " ", text.lower()).split()
    return [w for w in words if w not in stop and len(w) > 2]


def _field_words_used(joined: str, blueprint) -> str:
    """How much of the borrowed vocabulary the interviewer actually reached for."""
    register = getattr(blueprint, "domain_language", None)
    if register is None or not register.vocabulary:
        return "n/a"
    used = sum(1 for term in register.vocabulary if term.lower() in joined)
    return f"{used}/{len(register.vocabulary)}"


def spread(values: list) -> str:
    """Report the range, never a single number.

    Three runs of the same code path at temperature 0.6 read 4, then 1, then 6
    on the same signal. One run per model measures the sampler, not the change.
    """
    if not values:
        return "n/a"
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return "[" + "  ".join(str(v) for v in values) + "]"
    lo, hi = min(nums), max(nums)
    body = " ".join(f"{n:g}" for n in nums)
    return f"[{body}]" + ("" if lo == hi else f"  range {hi - lo:g}")


def _runaway_bound(brain: InterviewBrain) -> int:
    """The most turns a correctly-behaving interview could possibly take.

    **Production has no such cap.** This exists only so a brain that stops
    advancing cannot sit in a loop burning API credit, so the one thing it must
    never do is bind on a normal run — and it was doing exactly that. It was a
    flat 60, chosen when a turn cost 40 seconds. At the measured 22 a full
    `pm_senior` interview needs 84 turns, so every deflecting run for the last
    two tasks ended on the cap rather than finishing: its topics could not get
    longer, its numbers sat still by construction, and the scenario that guards
    deflection being caught measured nothing at all.

    Derived rather than guessed, and from the brain's own numbers: a section
    cannot exceed its own ceiling, so the sum of the ceilings is what a correct
    interview costs. Repairs are the one thing that adds turns without spending
    one — deliberately, so the depth ramp does not advance on a question nobody
    heard — and they are capped per question, so a fixed allowance of
    `MAX_REPAIR_ATTEMPTS` per section covers them.

    That is 61 for `ml_senior` against a 45-turn interview, and 100 for
    `pm_senior` against 84. Loose enough never to bind, tight enough that a
    brain looping on one section still trips it.
    """
    ceilings = 0
    for section in brain.sections:
        if section.kind == SectionKind.COMPETENCY:
            ceilings += brain._turn_bounds(section)[1]
        elif section.kind == SectionKind.OPENING:
            ceilings += brain.config.opening_ceiling_turns
        else:
            ceilings += MAX_CLOSING_TURNS
    return ceilings + MAX_REPAIR_ATTEMPTS * len(brain.sections)


async def run_one(
    scenario: str,
    model: str,
    settings: Settings,
    run_index: int = 0,
    reverse_sections: bool = False,
) -> tuple:
    blueprint = load_blueprint(blueprint_id=SCENARIO_BLUEPRINT.get(scenario))
    if reverse_sections:
        # **Competency order reversed, and nothing else.** Every plan keeps its
        # own budget, weight, seeds and target depth; only its slot moves.
        #
        # This exists because a fixture can confound position with weight and
        # nothing in a single ordering can tell them apart. `ml_senior`'s weights
        # descend monotonically with position, so "later sections reach less"
        # and "lower-weighted sections reach less" are the same observation seen
        # twice. Running it reversed separates them, and the first time it was
        # run it overturned a finding four investigations had built on: the last
        # slot reached the most and the first slot reached nothing.
        blueprint = blueprint.model_copy(deep=True)
        blueprint.competency_plans = list(reversed(blueprint.competency_plans))
    # The ceiling is left unpinned so the script exercises what production does:
    # derived from each section's own budget. It used to pin 6, and then 3, both
    # of which measured a bound the deployed bot does not use.
    brain = InterviewBrain(blueprint, config=BrainConfig(floor_turns=2))
    candidate = SCENARIOS[scenario][0]()

    interviewer = OpenAIInterviewer(api_key=settings.openai_api_key, model=model)
    judge = OpenAIJudge(api_key=settings.openai_api_key, model=settings.blueprint_model)
    run = await run_interview(
        brain,
        interviewer=interviewer,
        candidate=candidate,
        # Judge held at the reasoning tier for every run, so the only variable
        # is the live conversation model.
        judge=judge,
        # **Taken from the brain's own constant so the two cannot drift.** This
        # was a hardcoded 40 against a measured 22, and nothing connected them.
        # The brain derives a section's turn ceiling from `budget / 22`, so at 40
        # the harness exhausted every budget before any ceiling could bind: a
        # 180-second section got five turns where production gives eight, and a
        # 150-second one got four where production gives six. Every coverage
        # number measured here was taken on sections at roughly 60% of their
        # real length, and one extra turn was measured to nearly double the
        # material a section reaches.
        #
        # A flat constant is still the wrong shape, and this only makes it the
        # right flat constant. Real turn length varies with how long the
        # candidate speaks, and this scripted candidate's answers run from 26 to
        # 46 words, so a section of short answers is charged the same as a
        # section of long ones. Charging by words actually spoken would be
        # closer; it is not built here because an instrument change and a
        # fixture change in the same run cannot be attributed.
        seconds_per_turn=SECONDS_PER_COMPETENCY_TURN,
        max_turns=_runaway_bound(brain),
    )
    suffix = "" if run_index == 0 else f"__run{run_index + 1}"
    if reverse_sections:
        suffix += "__reversed"
    write_run(run, OUTPUT_DIR / f"{scenario}__{model}{suffix}.json",
              label=f"{scenario} / {model} / run {run_index + 1}")
    return run, brain, interviewer, judge


async def main() -> int:
    parser = argparse.ArgumentParser(description="Compare live-conversation models.")
    parser.add_argument("--scenarios", nargs="*", default=list(SCENARIOS), choices=list(SCENARIOS))
    parser.add_argument("--models", nargs="*", default=MODELS)
    parser.add_argument("--print-transcripts", action="store_true")
    parser.add_argument("--runs", type=int, default=3,
                        help="Runs per model. One tells you nothing at temperature 0.6.")
    parser.add_argument("--reverse-sections", action="store_true",
                        help="Run the competencies in reverse order, everything else "
                             "identical. Separates a section's position from its weight, "
                             "which a fixture ordered by weight cannot do.")
    args = parser.parse_args()

    try:
        settings = Settings.load()
    except MissingConfig as exc:
        print(exc)
        return 1

    for scenario in args.scenarios:
        print("\n" + "=" * 78)
        print(f"SCENARIO: {scenario} — {SCENARIOS[scenario][1]}")
        print("=" * 78)

        for model in args.models:
            collected: list[dict] = []
            for index in range(args.runs):
                run, brain, interviewer, judge = await run_one(
                    scenario, model, settings, index,
                    reverse_sections=args.reverse_sections,
                )
                stats = signals(run, brain)
                stats["live_prompt_tokens"] = interviewer.prompt_tokens
                stats["live_output_tokens"] = interviewer.completion_tokens
                stats["live_cached_tokens"] = interviewer.cached_tokens
                stats["live_calls"] = interviewer.calls
                stats["judge_prompt_tokens"] = judge.prompt_tokens
                stats["judge_output_tokens"] = judge.completion_tokens
                stats["judge_cached_tokens"] = judge.cached_tokens
                stats["judge_calls"] = judge.calls
                collected.append(stats)
                if args.print_transcripts:
                    print(run.render())

            print(f"\n  {model}  ({args.runs} run{'s' if args.runs != 1 else ''})")
            for key in collected[0]:
                print(f"    {key:22} {spread([c[key] for c in collected])}")

    print(f"\nTranscripts written to {OUTPUT_DIR}")
    print("Read them. The signals above are indicators, not a verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
