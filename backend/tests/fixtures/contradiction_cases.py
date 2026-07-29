"""Labelled cases for judge precision on contradictions.

The judge over-fires. In one observed run it flagged "That is a good question, I
would have to think about it" as contradicting an earlier ownership claim. That
is harmless while the only consequence is a neutral clarifying question, but it
becomes serious in Milestone 4, where a contradiction in a feedback report is an
accusation about a real person's honesty.

Four categories, three of which must NOT be reported:

- **genuine**    — the two statements cannot both be true as stated.
- **hedge**      — a non-committal or "I'd have to think" reply. Says nothing,
                   so it contradicts nothing.
- **non_answer** — evasion or topic change. Absence of an answer is not a
                   conflicting answer.
- **revision**   — the candidate openly changes or refines their view. This is
                   candour, and treating it as a contradiction would punish
                   exactly the honesty an interview should reward.

Used by the tolerant behaviour suite; kept as data so cases can be added when
real interviews surface new false positives.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ContradictionCase:
    label: str
    category: str
    earlier_claim: str
    later_statement: str
    note: str

    @property
    def should_flag(self) -> bool:
        return self.category == "genuine"


CASES: list[ContradictionCase] = [
    # ---------------- genuine ----------------
    ContradictionCase(
        label="ownership_reversed",
        category="genuine",
        earlier_claim="I owned that pricing decision end to end and made the final call myself.",
        later_statement="Honestly the pricing change was forced on us by the CFO. I argued against it and lost.",
        note="Sole ownership and being overruled cannot both be true of the same decision.",
    ),
    ContradictionCase(
        label="timeline_conflict",
        category="genuine",
        earlier_claim="I led that platform migration for eighteen months.",
        later_statement="I joined that team about three months before the migration finished.",
        note="Durations are incompatible.",
    ),
    ContradictionCase(
        label="outcome_reversed",
        category="genuine",
        earlier_claim="The launch hit every target we set and retention went up.",
        later_statement="We never really moved retention with that launch.",
        note="Directly opposite claims about the same outcome.",
    ),
    ContradictionCase(
        label="team_size_conflict",
        category="genuine",
        earlier_claim="I was managing a team of about twenty-five engineers.",
        later_statement="It was just me and two other people on that product the whole time.",
        note="Same period, incompatible headcount.",
    ),
    # ---------------- hedge ----------------
    ContradictionCase(
        label="needs_to_think",
        category="hedge",
        earlier_claim="I owned that pricing decision end to end and made the final call myself.",
        later_statement="That is a good question. I would have to think about it.",
        note="The exact false positive observed in a real run. Asserts nothing.",
    ),
    ContradictionCase(
        label="cannot_recall",
        category="hedge",
        earlier_claim="We cut the reporting rebuild to make room for it.",
        later_statement="I genuinely can't remember the exact numbers on that one.",
        note="Failure to recall a detail is not a conflicting claim.",
    ),
    ContradictionCase(
        label="qualified_uncertainty",
        category="hedge",
        earlier_claim="Retention improved after the pricing change.",
        later_statement="I think it helped, though I couldn't swear the pricing change alone caused it.",
        note="Appropriate epistemic caution about causation, not a reversal.",
    ),
    # ---------------- non_answer ----------------
    ContradictionCase(
        label="deflect_to_team",
        category="non_answer",
        earlier_claim="I made the final call on that roadmap.",
        later_statement="It was really a team effort, everyone contributed a lot.",
        note="Vague and evasive, but not a claim that conflicts with having decided.",
    ),
    ContradictionCase(
        label="changes_subject",
        category="non_answer",
        earlier_claim="I ran the experiment that killed the feature.",
        later_statement="Actually, can I ask how long this interview is going to run?",
        note="Topic change. Nothing asserted.",
    ),
    # ---------------- revision ----------------
    ContradictionCase(
        label="openly_revises",
        category="revision",
        earlier_claim="Usage-based pricing was clearly the right call.",
        later_statement="Thinking about it more, I'd say it was right for revenue but we underestimated the support load, so I'm less sure it was clearly right.",
        note="Explicit, signposted revision. Candour, not conflict.",
    ),
    ContradictionCase(
        label="adds_nuance",
        category="revision",
        earlier_claim="I decided we would not build custom dashboards that year.",
        later_statement="To be fair, my director pushed back hard and I only held that line after we agreed a review date.",
        note="Refines the earlier claim with detail; does not negate it.",
    ),
    ContradictionCase(
        label="corrects_self_immediately",
        category="revision",
        earlier_claim="We shipped it in the third quarter.",
        later_statement="Sorry, I misspoke earlier — it was actually the fourth quarter.",
        note="Self-correction. Flagging it would punish the candidate for being accurate.",
    ),
]


def cases_by_category(category: str) -> list[ContradictionCase]:
    return [c for c in CASES if c.category == category]
