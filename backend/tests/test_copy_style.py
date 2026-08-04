"""User-facing copy must not read as machine-written.

The em dash is the strongest tell that a model wrote something, and this product
is shown to people who are deciding whether to trust it with hiring. Two sources
feed the screen and both are covered here:

  - hardcoded copy in the panel and the candidate pages
  - text the models generate, which is most of what is actually read

Comments and docstrings are exempt. Nobody reads those on a projector.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DASHES = ("—", "–")  # em dash, en dash

FRONTEND = [
    REPO / "frontend" / "admin" / "admin.js",
    REPO / "frontend" / "admin" / "index.html",
    REPO / "frontend" / "candidate" / "index.html",
]


def offending_lines(path: Path) -> list[str]:
    found = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not any(d in line for d in DASHES):
            continue
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue  # a comment, never rendered
        found.append(f"{path.name}:{number}: {stripped[:90]}")
    return found


@pytest.mark.parametrize("path", FRONTEND, ids=lambda p: p.name)
def test_no_dashes_in_what_the_screen_shows(path):
    found = offending_lines(path)
    assert not found, "Dashes in user-facing copy:\n" + "\n".join(found)


def test_every_prose_prompt_forbids_dashes():
    """Most of the words on screen are model-written, so cleaning only the
    hardcoded copy leaves the tell exactly where people read it."""
    from blueprint.clarify import SYSTEM as clarify
    from blueprint.generate import SYSTEM as generate
    from blueprint.refine import SYSTEM as refine
    from bot.brain.prompting import VOICE_RULES
    from feedback.score import SYSTEM as score

    for name, prompt in [
        ("clarification chat", clarify),
        ("blueprint generation", generate),
        ("blueprint refinement", refine),
        ("feedback report", score),
        ("live interviewer", VOICE_RULES),
    ]:
        assert re.search(r"em dash", prompt, re.I), (
            f"The {name} prompt does not forbid em dashes, so its output will "
            f"contain them, and it is that output people read."
        )


def test_the_style_rule_lives_in_one_place():
    """Same reason the bot's name does: one voice, no drift between the panel,
    the interview and the report."""
    from shared.branding import PROSE_STYLE

    assert "em dash" in PROSE_STYLE.lower()


def test_the_panel_does_not_keep_its_own_copy_of_the_duration_rule():
    """The interview-length range is in the contract. The panel is served the
    numbers rather than typing them, the same way the product name is, so it
    cannot end up stating a rule the backend does not enforce."""
    from shared.contracts import MAX_DURATION_MINUTES, MIN_DURATION_MINUTES

    source = (REPO / "frontend" / "admin" / "admin.js").read_text()

    assert "{{DURATION_MIN}}" in source
    assert "{{DURATION_MAX}}" in source
    assert "const DURATION_MIN = 20" not in source
    assert "const DURATION_MAX = 90" not in source

    from app.main import _render_page

    rendered = _render_page("admin/admin.js")
    assert f"const DURATION_MIN = {MIN_DURATION_MINUTES};" in rendered
    assert f"const DURATION_MAX = {MAX_DURATION_MINUTES};" in rendered
    assert "{{DURATION_" not in rendered, "a token reached the browser unsubstituted"
