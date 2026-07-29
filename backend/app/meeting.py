"""Meeting credentials a candidate can be given and can actually type.

These get emailed, pasted into chat, and sometimes read out loud. So they avoid
characters that are ambiguous when spoken or misread when typed: no 0/O, no
1/l/I, no 5/S. A candidate mistyping their own credentials at the start of an
interview is a bad first minute.
"""

import secrets

#: Digits only, grouped — easy to read aloud and unambiguous in any font.
MEETING_ID_GROUPS = 3
MEETING_ID_GROUP_SIZE = 3

#: Pairs that get confused when a password is read aloud or typed from a
#: screenshot. Only one member of each pair may appear in the alphabet.
CONFUSABLE_PAIRS = (
    ("0", "o"),
    ("1", "l"),
    ("1", "i"),
    ("5", "s"),
    ("2", "z"),
    ("9", "g"),
)

#: Lowercase only, with one member of every confusable pair removed. Entropy is
#: still ~5 bits per character, which is ample for a single-use credential to a
#: room that expires on its own.
PASSWORD_ALPHABET = "abcdefhjkmnpqrtuvwxy34678"
PASSWORD_LENGTH = 8


def new_meeting_id() -> str:
    """A spoken-friendly identifier, e.g. '428-193-756'."""
    groups = [
        "".join(secrets.choice("0123456789") for _ in range(MEETING_ID_GROUP_SIZE))
        for _ in range(MEETING_ID_GROUPS)
    ]
    return "-".join(groups)


def new_password() -> str:
    """A short single-use password for one interview."""
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def normalise_meeting_id(value: str) -> str:
    """Accept what a candidate actually types.

    People paste with spaces, drop the hyphens, or add stray punctuation. All of
    those should work — refusing a correct ID over formatting is a pointless way
    to start someone's interview.
    """
    digits = "".join(c for c in (value or "") if c.isdigit())
    if len(digits) != MEETING_ID_GROUPS * MEETING_ID_GROUP_SIZE:
        return ""
    return "-".join(
        digits[i : i + MEETING_ID_GROUP_SIZE]
        for i in range(0, len(digits), MEETING_ID_GROUP_SIZE)
    )


def passwords_match(supplied: str, expected: str) -> bool:
    """Compare in constant time, case-insensitively.

    Case-insensitive because the alphabet has no uppercase and a candidate's
    keyboard or autocorrect may capitalise the first letter.
    """
    return secrets.compare_digest(
        (supplied or "").strip().lower(), (expected or "").strip().lower()
    )
