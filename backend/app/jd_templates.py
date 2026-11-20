"""Starting points for a job description, one file per role.

**Text only, and that boundary is the whole design.** A template fills the box
the employer would otherwise face empty; it does not decide what the interview
tests. That is the clarification chat's job, and it still runs in full: the
employer answers the same questions, and the `EvaluationSpec` with its
competencies, weights and dealbreakers is produced the same way it always was.
Shipping a pre-built spec would skip the one conversation that makes an interview
specific to this employer, and would do it invisibly.

**A file per role rather than rows in a table.** Adding a role is a commit and a
review, which is right for copy that goes in front of a paying customer, and it
means there is no migration, no admin screen to build, and no drift between
environments. The cost is that an employer cannot save their own template, which
is a feature nobody has asked for yet.

The text is a starting point and is always editable before it is used. A template
that could not be edited would be worse than none: it would put words the
employer does not mean into the document the whole interview is derived from.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Beside this module, so `COPY backend/ ./` in the Dockerfile carries them.
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "jd_templates"

#: Order shown to the employer. Explicit rather than alphabetical, because the
#: roles a services company fills constantly should be reachable first, and
#: because "Business Analyst" sorting above "Marketing Manager" is an accident
#: rather than a decision.
ORDER = (
    "business-analyst",
    "project-manager",
    "qa-engineer",
    "mobile-engineer",
    "marketing-manager",
    "social-media-content",
)


@dataclass(frozen=True)
class JobDescriptionTemplate:
    """One role's starting text, with the label the panel shows."""

    slug: str
    title: str
    text: str


def _title_of(text: str, slug: str) -> str:
    """The role name, taken from the file's own heading.

    Read from the document rather than kept in a second list here, so a template
    cannot end up labelled one thing in the panel and titled another in the text
    the employer then edits.
    """
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return slug.replace("-", " ").title()


@lru_cache(maxsize=1)
def templates() -> tuple[JobDescriptionTemplate, ...]:
    """Every template, in `ORDER`. Read once and cached, like the pages are.

    A slug in `ORDER` with no file is skipped rather than raised on: a missing
    template should cost the employer one option, not the whole screen on which
    they create a profile.
    """
    found = []
    for slug in ORDER:
        path = TEMPLATE_DIR / f"{slug}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        found.append(JobDescriptionTemplate(slug=slug, title=_title_of(text, slug), text=text))
    return tuple(found)
