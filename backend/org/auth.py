"""Sign-in, sessions, role guards and BU scoping for the console.

- **Passwords** are hashed with scrypt from the standard library: a memory-hard
  key derivation function, and no new dependency to audit.
- **Sessions** are a signed, expiring cookie carrying the user id. It is
  stateless, so a restart does not sign anyone out. The user row is still read
  on every request, so a deactivated account or a changed role takes effect on
  the next click rather than when the cookie expires.
- **The role comes from the account,** never from what the browser asks for.
- **BU scoping is a query condition** (`job_scope`). A BU head who edits a URL
  still gets nothing from another BU, because the rows are never selected.

The old `/admin` panel keeps its own shared-password guard (`app/auth.py`)
until cutover. The two do not share cookies.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import false, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.db import Job, get_sessionmaker, is_deployed
from org.models import User
from shared.stages import Role

COOKIE_NAME = "mitra_session"

#: A working day, the same as the old panel.
SESSION_SECONDS = 12 * 60 * 60

# scrypt cost. n=2^14, r=8 takes ~16MB and tens of milliseconds: slow enough to
# make guessing expensive, fast enough that signing in does not feel slow.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024

_local_secret: bytes | None = None


class SessionSecretMissing(RuntimeError):
    pass


# ---- passwords ------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), maxmem=_SCRYPT_MAXMEM, dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


#: Compared against when the email is unknown, so a wrong email costs the same
#: time as a wrong password and the response time does not reveal which it was.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def check_credentials_timing_safe(password: str, user: User | None) -> bool:
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, user.password_hash)


# ---- sessions -------------------------------------------------------------------


def _secret() -> bytes:
    """SESSION_SECRET signs every session.

    Required when deployed: without it there is no safe key. Locally a random
    one is made per process, so a restart signs you out, which is fine for
    development and says so in the log.
    """
    global _local_secret
    configured = os.environ.get("SESSION_SECRET")
    if configured:
        return hashlib.sha256(configured.encode()).digest()
    if is_deployed():
        raise SessionSecretMissing(
            "SESSION_SECRET is not set. Set it to a long random string on the "
            "service; sign-in is refused until then."
        )
    if _local_secret is None:
        _local_secret = secrets.token_bytes(32)
        logger.warning("SESSION_SECRET not set: using a per-process key; restarts sign everyone out")
    return _local_secret


def issue_session(user_id: str, *, now: float | None = None) -> str:
    expires = int((now if now is not None else time.time()) + SESSION_SECONDS)
    payload = f"{user_id}.{expires}"
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def read_session(token: str | None) -> str | None:
    """The user id in a valid, unexpired token; otherwise None."""
    if not token or token.count(".") < 2:
        return None
    payload, _, signature = token.rpartition(".")
    user_id, _, expires_raw = payload.rpartition(".")
    try:
        expires = int(expires_raw)
    except ValueError:
        return None
    if not user_id or expires < time.time():
        return None
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return user_id


def cookie_settings() -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": is_deployed(),
        "max_age": SESSION_SECONDS,
        "path": "/",
    }


# ---- guards ---------------------------------------------------------------------


def _guard(fn: Callable) -> Callable:
    """Mark a dependency as an access guard, for the route sweep test."""
    fn.__mitra_guard__ = True
    return fn


def is_guard(fn: Callable) -> bool:
    from app.auth import require_admin

    return fn is require_admin or getattr(fn, "__mitra_guard__", False)


@_guard
async def current_user(request: Request) -> User:
    """Dependency: the signed-in, active user, or 401."""
    try:
        user_id = read_session(request.cookies.get(COOKIE_NAME))
    except SessionSecretMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


def require_role(*roles: Role) -> Callable:
    """Dependency factory: the signed-in user, if their role is one of these."""
    allowed = frozenset(roles)

    @_guard
    async def guard(user: User = Depends(current_user)) -> User:
        if Role(user.role) not in allowed:
            raise HTTPException(status_code=403, detail="Your role cannot do this.")
        return user

    guard.allowed_roles = allowed
    return guard


# ---- BU scoping -----------------------------------------------------------------


def job_scope(user: User) -> ColumnElement[bool]:
    """Which jobs this user may see, as a WHERE condition.

    - BU head: their own BU's jobs, and nothing else. Jobs with no BU (made in
      the old /admin panel) are not theirs.
    - HR and org admin: every job in their organisation, plus jobs with no
      organisation, which is how old /admin jobs appear.
    """
    if Role(user.role) is Role.BU_HEAD:
        if not user.bu_id:
            # A BU head without a BU sees nothing rather than everything.
            return false()
        return Job.bu_id == user.bu_id
    return or_(Job.org_id == user.org_id, Job.org_id.is_(None))


def can_see_job(user: User, job: Job) -> bool:
    if Role(user.role) is Role.BU_HEAD:
        return bool(user.bu_id) and job.bu_id == user.bu_id
    return job.org_id in (user.org_id, None)


async def visible_job_or_404(user: User, job_id: str, session) -> Job:
    """Load a job the user may see. A job they may not see is a 404, not a 403:
    telling a BU head that a job exists in another BU is itself a leak."""
    job = (await session.execute(select(Job).where(Job.id == job_id, job_scope(user)))).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="No such job.")
    return job


@dataclass(frozen=True)
class SignedIn:
    id: str
    email: str
    name: str
    role: str
    bu_id: str | None
    org_id: str

    @classmethod
    def of(cls, user: User) -> "SignedIn":
        return cls(user.id, user.email, user.name, user.role, user.bu_id, user.org_id)
