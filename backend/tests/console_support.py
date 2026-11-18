"""Shared setup for console tests: an app with the demo organisation seeded.

Import `org_client` into a test module to use it as a fixture, and
`sign_in(client, email)` to act as one of the seeded people (org/seed.py).
"""

import pytest
from fastapi.testclient import TestClient

PASSWORD = "demo-password-1"


@pytest.fixture
async def org_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/org.db")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    for name in ("OPENAI_API_KEY", "DAILY_API_KEY"):
        monkeypatch.setenv(name, "test")

    from app import db, main
    from org.seed import seed_demo_org

    await db.reset_engine()
    with TestClient(main.app) as client:
        async with db.get_sessionmaker()() as session:
            demo = await seed_demo_org(session, password=PASSWORD)
            await session.commit()
        client.demo = demo
        yield client
    await db.reset_engine()


def sign_in(client, email: str) -> None:
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
