"""Access control for the admin panel: sign-in throttling and the session cookie.

Deterministic brain-logic style: no network, no model calls. The clock is
injected so the lockout can be walked through without sleeping.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import auth
from app.auth import (
    LOCKOUT_SECONDS,
    LOGIN_WINDOW_SECONDS,
    MAX_FAILED_LOGINS,
    LoginThrottle,
    client_key,
    is_https,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_request(headers: dict[str, str] | None = None, scheme: str = "http", host: str = "10.0.0.5") -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    raw_headers.append((b"host", b"mitra.example"))
    scope = {
        "type": "http",
        "scheme": scheme,
        "server": ("mitra.example", 443 if scheme == "https" else 80),
        "path": "/admin/login",
        "method": "POST",
        "headers": raw_headers,
        "client": (host, 50000),
    }
    return Request(scope)


# --- the throttle ------------------------------------------------------------


def test_a_client_may_try_until_the_failure_limit():
    clock = FakeClock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(MAX_FAILED_LOGINS - 1):
        throttle.record_failure("a")
    assert throttle.retry_after("a") == 0


def test_the_failure_that_reaches_the_limit_locks_the_client_out():
    clock = FakeClock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(MAX_FAILED_LOGINS):
        throttle.record_failure("a")
    assert throttle.retry_after("a") > 0
    assert throttle.retry_after("a") <= LOCKOUT_SECONDS + 1


def test_the_lockout_expires_by_itself():
    clock = FakeClock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(MAX_FAILED_LOGINS):
        throttle.record_failure("a")
    clock.advance(LOCKOUT_SECONDS + 1)
    assert throttle.retry_after("a") == 0


def test_failures_outside_the_window_do_not_count():
    clock = FakeClock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(MAX_FAILED_LOGINS - 1):
        throttle.record_failure("a")
    clock.advance(LOGIN_WINDOW_SECONDS + 1)
    throttle.record_failure("a")
    assert throttle.retry_after("a") == 0


def test_a_successful_sign_in_clears_the_count():
    clock = FakeClock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(MAX_FAILED_LOGINS - 1):
        throttle.record_failure("a")
    throttle.reset("a")
    throttle.record_failure("a")
    assert throttle.retry_after("a") == 0


def test_one_client_being_locked_does_not_lock_another():
    clock = FakeClock()
    throttle = LoginThrottle(clock=clock)
    for _ in range(MAX_FAILED_LOGINS):
        throttle.record_failure("attacker")
    assert throttle.retry_after("attacker") > 0
    assert throttle.retry_after("employer") == 0


# --- which address is counted ------------------------------------------------


def test_the_last_forwarded_hop_is_the_one_counted():
    # The client can put anything first; the proxy appends the address it saw.
    request = make_request({"X-Forwarded-For": "1.1.1.1, 203.0.113.9"})
    assert client_key(request) == "203.0.113.9"


def test_without_a_proxy_the_socket_address_is_used():
    assert client_key(make_request(host="198.51.100.4")) == "198.51.100.4"


# --- the Secure flag ---------------------------------------------------------


def test_plain_http_is_not_secure():
    assert is_https(make_request()) is False


def test_https_is_secure():
    assert is_https(make_request(scheme="https")) is True


def test_a_proxy_reporting_https_is_secure_even_though_the_app_sees_http():
    # Railway terminates TLS: the app's own scheme reads http.
    assert is_https(make_request({"X-Forwarded-Proto": "https"})) is True


def test_a_proxy_reporting_http_is_not_secure():
    assert is_https(make_request({"X-Forwarded-Proto": "http"})) is False


# --- the sign-in route, end to end ------------------------------------------


@pytest.fixture
def login_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-test")
    monkeypatch.setenv("DAILY_API_KEY", "daily-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password-0123456789")

    from app import main

    clock = FakeClock()
    monkeypatch.setattr(main, "LOGIN_THROTTLE", LoginThrottle(clock=clock))
    with TestClient(main.app) as client:
        client.clock = clock
        yield client


def test_a_wrong_password_is_refused(login_client):
    response = login_client.post("/admin/login", json={"password": "nope"})
    assert response.status_code == 401


def test_the_correct_password_signs_in(login_client):
    response = login_client.post("/admin/login", json={"password": "test-admin-password-0123456789"})
    assert response.status_code == 200
    assert auth.COOKIE_NAME in response.cookies


def test_repeated_wrong_passwords_lock_the_client_out(login_client):
    for _ in range(MAX_FAILED_LOGINS):
        assert login_client.post("/admin/login", json={"password": "nope"}).status_code == 401

    locked = login_client.post("/admin/login", json={"password": "nope"})
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


def test_the_right_password_is_also_refused_while_locked_out(login_client):
    # Otherwise the lockout would only slow guessing; a correct guess would still get in.
    for _ in range(MAX_FAILED_LOGINS):
        login_client.post("/admin/login", json={"password": "nope"})

    response = login_client.post("/admin/login", json={"password": "test-admin-password-0123456789"})
    assert response.status_code == 429


def test_the_lockout_lifts_after_the_wait(login_client):
    for _ in range(MAX_FAILED_LOGINS):
        login_client.post("/admin/login", json={"password": "nope"})
    login_client.clock.advance(LOCKOUT_SECONDS + 1)

    response = login_client.post("/admin/login", json={"password": "test-admin-password-0123456789"})
    assert response.status_code == 200


def test_the_session_cookie_is_httponly_and_lasts_as_long_as_the_session(login_client):
    response = login_client.post("/admin/login", json={"password": "test-admin-password-0123456789"})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert f"max-age={auth.SESSION_SECONDS}" in cookie


# --- password strength -------------------------------------------------------


def test_the_minimum_length_is_enforced_in_code_not_just_documented():
    assert auth.MIN_PASSWORD_LENGTH >= 16


@pytest.mark.parametrize("password", ["mitra-dev", "short", "x" * 15])
def test_a_short_admin_password_is_refused_and_never_unlocks(monkeypatch, password):
    monkeypatch.setenv("ADMIN_PASSWORD", password)
    with pytest.raises(auth.AdminNotConfigured, match="at least 16"):
        auth.check_password(password)


def test_a_password_at_the_minimum_length_is_accepted(monkeypatch):
    password = "x" * auth.MIN_PASSWORD_LENGTH
    monkeypatch.setenv("ADMIN_PASSWORD", password)
    assert auth.check_password(password) is True


def test_a_short_password_gives_a_503_on_login_not_a_500(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-test")
    monkeypatch.setenv("DAILY_API_KEY", "daily-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "mitra-dev")

    from app import main

    with TestClient(main.app) as client:
        response = client.post("/admin/login", json={"password": "mitra-dev"})
    assert response.status_code == 503
    assert "at least 16" in response.json()["detail"]


def test_a_refused_short_password_does_not_count_toward_the_lockout(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-test")
    monkeypatch.setenv("DAILY_API_KEY", "daily-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "mitra-dev")

    from app import main

    throttle = LoginThrottle(clock=FakeClock())
    monkeypatch.setattr(main, "LOGIN_THROTTLE", throttle)
    with TestClient(main.app) as client:
        for _ in range(MAX_FAILED_LOGINS + 2):
            client.post("/admin/login", json={"password": "mitra-dev"})
    assert throttle.retry_after("testclient") == 0
