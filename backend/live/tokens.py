"""Short-lived, purpose-bound signed tokens.

Used for the proctoring intake (a candidate's browser posting integrity events
for its own interview) and for evidence links (a reviewer's player fetching
one interview's recording). A token names its purpose and its interview and
expires; verifying it for any other purpose or interview fails.

Format: `v1.<purpose>.<subject>.<expires>.<signature>`, HMAC-SHA256. Nothing in
it is secret except the signature; it identifies, it does not hide.

The key: derived from `SESSION_SECRET` when set (Track H's console secret,
present on a deployed instance after the merge), otherwise one random key per
process. A per-process key is enough today because the web process is the only
issuer and the only verifier, and a web restart ends every interview anyway
(the bots are its children). Tokens are never logged.
"""

import hashlib
import hmac
import os
import secrets
import time

_process_key: bytes | None = None


def _key() -> bytes:
    global _process_key
    configured = os.environ.get("SESSION_SECRET")
    if configured:
        # Domain-separated, so these tokens can never be read as a console session.
        return hmac.new(configured.encode(), b"mitra-purpose-tokens-v1", hashlib.sha256).digest()
    if _process_key is None:
        _process_key = secrets.token_bytes(32)
    return _process_key


def _sign(message: str) -> str:
    return hmac.new(_key(), message.encode(), hashlib.sha256).hexdigest()


def issue(purpose: str, subject: str, ttl_seconds: int, *, now: float | None = None) -> tuple[str, int]:
    """A token for `purpose` about `subject`. Returns (token, expires epoch seconds)."""
    if "." in purpose or "." in subject:
        raise ValueError("purpose and subject must not contain '.'")
    expires = int((now if now is not None else time.time()) + ttl_seconds)
    message = f"v1.{purpose}.{subject}.{expires}"
    return f"{message}.{_sign(message)}", expires


def verify(token: str | None, purpose: str, subject: str | None = None) -> str | None:
    """The subject of a valid, unexpired token for this purpose; otherwise None.

    When `subject` is given it must match too, which is how a token for one
    interview is refused for another.
    """
    if not token or token.count(".") != 4:
        return None
    version, got_purpose, got_subject, expires_raw, signature = token.split(".")
    message = f"{version}.{got_purpose}.{got_subject}.{expires_raw}"
    if not hmac.compare_digest(signature, _sign(message)):
        return None
    if version != "v1" or got_purpose != purpose:
        return None
    if subject is not None and got_subject != subject:
        return None
    try:
        if int(expires_raw) < time.time():
            return None
    except ValueError:
        return None
    return got_subject
