"""Read the frontend's source, for the tests that inspect what the screens say and do.

There is no JavaScript harness. These tests read the source the same way the copy and contract
tests always have. The pages are plain HTML and JavaScript now, served as files with no build step,
so the source is the .js under frontend/static, not a compiled bundle.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
STATIC = FRONTEND / "static"
NOTICE = STATIC / "candidate" / "notice.js"


def _read_all(paths: list[Path]) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def panel_source() -> str:
    """Everything the employer panel is made of: its screens and the code they share."""
    return _read_all(sorted((STATIC / "admin").glob("*.js")) + sorted((STATIC / "shared").glob("*.js")))


def join_source() -> str:
    """Everything the candidate's join page is made of."""
    return _read_all(sorted((STATIC / "candidate").glob("*.js")) + sorted((STATIC / "shared").glob("*.js")))


def user_facing_files() -> list[Path]:
    """Every file whose text can reach a screen: the page scripts and the page entries.

    The vendored Daily bundle is a third party's code, not ours, so it is left out, the same way
    node_modules was.
    """
    files = [p for p in sorted(STATIC.rglob("*.js")) if "vendor" not in p.parts]
    files += [FRONTEND / "admin" / "index.html", FRONTEND / "candidate" / "index.html"]
    return files


def _notice_branches(src: str, function: str) -> tuple[str, str, str]:
    """Cut one exported notice function into its recording branch, its other branch and the copy after.

    The function writes the branch as a ternary on `recording`:

        recording
          ? [ ...recording items... ]
          : [ ...other items... ],
        ...copy both settings share...

    Returns (recording branch, other branch, shared copy), each as the source text it was cut from.
    """
    start = src.index(f"export function {function}")
    on_start = src.index("? [", start) + len("? [")
    off_marker = src.index("]\n        : [", on_start)
    off_start = off_marker + len("]\n        : [")
    off_end = src.index("],\n", off_start)
    common_start = off_end + len("],\n")
    common_end = src.index("    ),\n    ...extra", common_start) if function == "consentNotice" else src.index("    ),\n  );", common_start)
    return src[on_start:off_marker], src[off_start:off_end], src[common_start:common_end]


def consent_notice(recording: bool) -> str:
    """The consent notice as a candidate reads it, for one setting of the recording switch.

    The notice is written as a ternary on `recording`, the interview's own switch. Each branch is cut out of
    the source, and the copy after the ternary is common to both. A test asserting what a candidate
    is told checks the branch that the setting selects.
    """
    on_branch, off_branch, common = _notice_branches(NOTICE.read_text(encoding="utf-8"), "consentNotice")
    return (on_branch if recording else off_branch) + common


def device_notice(recording: bool) -> str:
    """The device-check notice as a candidate reads it, for one setting of the switch."""
    on_branch, off_branch, _ = _notice_branches(NOTICE.read_text(encoding="utf-8"), "deviceNotice")
    return on_branch if recording else off_branch
