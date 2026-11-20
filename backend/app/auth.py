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


#: Shortest password the panel will accept. The panel lists every CV and transcript
#: in the database, so this is the one credential guarding them. Sixteen random
#: characters is far beyond what a guessing attack can reach; a person-chosen
#: word is not. The lockout slows guessing, the length removes most of its reason.
MIN_PASSWORD_LENGTH = 16


def _password() -> str:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise AdminNotConfigured(
            "ADMIN_PASSWORD is not set, so the admin panel cannot be unlocked. "
            "Set it in the environment. There is deliberately no default: a "
            "known default password on a public URL is worse than no panel."
        )
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminNotConfigured(
            f"ADMIN_PASSWORD is {len(password)} characters; it must be at least "
            f"{MIN_PASSWORD_LENGTH}. Generate a longer one, for example with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(24))\"`."
        )
    return password


def _secret() -> bytes:
    """Signing key derived from the password.

    Deriving rather than storing a separate secret means changing the password
    invalidates every existing session, which is the behaviour you want.
    """
    return hashlib.sha256(_password().encode()).digest()


def _sign(expires_raw: str, token_id: str) -> str:
    return hmac.new(_secret(), f"{expires_raw}.{token_id}".encode(), hashlib.sha256).hexdigest()


def issue_token() -> str:
    """A signed, expiring session token: `expires.token_id.signature`.

    Signed rather than stored, so a restart does not log anyone out. The random
    token_id is what a sign-out revokes (see `revoke`), because the signature alone
    cannot say a token was ended early.
    """
    expires = str(int(time.time()) + SESSION_SECONDS)
    token_id = secrets.token_urlsafe(18)
    return f"{expires}.{token_id}.{_sign(expires, token_id)}"


def _parse(token: str | None) -> tuple[int, str, str] | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    expires_raw, token_id, signature = parts
    try:
        expires = int(expires_raw)
    except ValueError:
        return None
    return expires, token_id, signature


def token_is_valid(token: str | None) -> bool:
    """Signature and expiry only. A signed-out token still passes this; see `is_revoked`."""
    parsed = _parse(token)
    if parsed is None:
        return False
    expires, token_id, signature = parsed
    if expires < time.time():
        return False
    return hmac.compare_digest(signature, _sign(str(expires), token_id))


async def revoke(token: str | None) -> None:
    """End a session early. Only a genuine, unexpired token is recorded."""
    from sqlalchemy import delete

    from app.db import AdminRevocation, get_sessionmaker

    if not token_is_valid(token):
        return
    expires, token_id, _ = _parse(token)  # type: ignore[misc]
    async with get_sessionmaker()() as session:
        await session.merge(AdminRevocation(token_id=token_id, expires_at=expires))
        # Expired rows can never matter again, so keep the table from growing forever.
        await session.execute(delete(AdminRevocation).where(AdminRevocation.expires_at < time.time()))
        await session.commit()


async def is_revoked(token: str | None) -> bool:
    from sqlalchemy import select

    from app.db import AdminRevocation, get_sessionmaker

    parsed = _parse(token)
    if parsed is None:
        return False
    _, token_id, _ = parsed
    async with get_sessionmaker()() as session:
        found = await session.execute(select(AdminRevocation.token_id).where(AdminRevocation.token_id == token_id))
        return found.scalar_one_or_none() is not None


def check_password(supplied: str) -> bool:
    """Constant-time comparison, so a wrong password leaks nothing by timing."""
    return secrets.compare_digest((supplied or "").encode(), _password().encode())


#: Failed sign-ins allowed from one client inside the window before it is locked
#: out. Five is enough for a person who mistypes; it is nowhere near enough for
#: guessing a password.
MAX_FAILED_LOGINS = 5
LOGIN_WINDOW_SECONDS = 10* 60
LOCKOUT_SECONDS = 10 * 60

#: Stop the in-memory table growing without bound if many addresses try once each.
_THROTTLE_MAX_KEYS = 10000


class LoginThrottle:
    """Per-client lockout for the admin sign-in.

    In memory, which is correct for this deployment: replicas are pinned at 1, so
    there is one table and it is the one every attempt hits. A restart clears it,
    and that is an accepted gap rather than a reason to add a store.

    The clock is injectable so the tests can move time without sleeping.
    """

    def __init__(
        self,
        clock=time.monotonic,
        *,
        max_failures: int = MAX_FAILED_LOGINS,
        window_seconds: int = LOGIN_WINDOW_SECONDS,
        lockout_seconds: int = LOCKOUT_SECONDS,
    ) -> None:
        self._clock = clock
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, key: str) -> int:
        """Seconds until this client may try again, or 0 if it may try now."""
        now = self._clock()
        until = self._locked_until.get(key, 0.0)
        if until > now:
            return int(until - now) + 1
        self._locked_until.pop(key, None)
        return 0

    def record_failure(self, key: str) -> None:
        now = self._clock()
        if len(self._failures) >= _THROTTLE_MAX_KEYS and key not in self._failures:
            self._failures.clear()
        recent = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
        recent.append(now)
        if len(recent) >= self.max_failures:
            self._locked_until[key] = now + self.lockout_seconds
            self._failures.pop(key, None)
        else:
            self._failures[key] = recent

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


LOGIN_THROTTLE = LoginThrottle()

#: Candidate join and focus. Two counters, because each defends against a different
#: attacker. The per-meeting one stops guessing at a known meeting ID: an invitation
#: carries the ID, so the password is the only unknown, and five misses in ten minutes
#: is far too few to find it. The per-client one stops one address trying many meetings,
#: and is looser, because a whole office can share an address while interviewing.
CANDIDATE_MEETING_THROTTLE = LoginThrottle(max_failures=5)
CANDIDATE_CLIENT_THROTTLE = LoginThrottle(max_failures=30)


def client_key(request: Request) -> str:
    """The address the throttle counts against.

    Behind Railway's proxy every request arrives from the proxy, so the socket
    address is the same for everyone. The proxy appends the real client to
    `X-Forwarded-For`, so the last entry is the one it saw. Earlier entries came
    from the client and are not trusted.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    """True when the browser's connection is HTTPS, including behind a proxy.

    Railway terminates TLS before the app, so `request.url.scheme` reads `http`
    even when the browser is on https. Used for the cookie's Secure flag only.
    """
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.split(",")[-1].strip().lower() == "https"


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

    token = request.cookies.get(COOKIE_NAME)
    if not token_is_valid(token) or await is_revoked(token):
        raise HTTPException(status_code=401, detail="Not signed in.")
