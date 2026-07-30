"""Single-admin authentication for the admin panel.

One shared password, one session cookie. Multi-tenant auth is explicitly out of
scope for the POC (CLAUDE.md), and pretending otherwise would mean building user
accounts, invitations and roles for a system with one user.

What this *is* for: the admin panel lists every job description, every CV, and
every interview transcript in the database. On a public Railway URL that cannot
be open to anyone who guesses the path.

The candidate side stays unauthenticated — candidates have no account, and their
credential is the meeting ID and password on the interview itself.
"""

import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request

COOKIE_NAME = "mitra_admin"

#: Sessions last a working day. Long enough not to be irritating, short enough
#: that a forgotten open tab is not a standing invitation.
SESSION_SECONDS = 12 * 60 * 60


class AdminNotConfigured(RuntimeError):
    """Raised when ADMIN_PASSWORD is missing."""


def _password() -> str:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise AdminNotConfigured(
            "ADMIN_PASSWORD is not set, so the admin panel cannot be unlocked. "
            "Set it in the environment. There is deliberately no default: a "
            "known default password on a public URL is worse than no panel."
        )
    return password


def _secret() -> bytes:
    """Signing key derived from the password.

    Deriving rather than storing a separate secret means changing the password
    invalidates every existing session, which is the behaviour you want.
    """
    return hashlib.sha256(_password().encode()).digest()


def issue_token() -> str:
    """A signed, expiring session token. Stateless, so restarts do not log you out."""
    expires = int(time.time()) + SESSION_SECONDS
    payload = str(expires).encode()
    signature = hmac.new(_secret(), payload, hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def token_is_valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expires_raw, _, signature = token.partition(".")
    try:
        expires = int(expires_raw)
    except ValueError:
        return False
    if expires < time.time():
        return False
    expected = hmac.new(_secret(), expires_raw.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def check_password(supplied: str) -> bool:
    """Constant-time comparison, so a wrong password leaks nothing by timing."""
    return secrets.compare_digest((supplied or "").encode(), _password().encode())


async def require_admin(request: Request) -> None:
    """Dependency guarding every admin route.

    A missing ADMIN_PASSWORD is a 503 rather than a 401: the problem is the
    deployment's, not the person's, and telling them to try a different password
    would send them chasing something they cannot fix.
    """
    try:
        _password()
    except AdminNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not token_is_valid(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Not signed in.")
