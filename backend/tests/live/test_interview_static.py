"""/interview/: types, caching, precompressed variants, the report-only security
rule on /interview/ only, and the violation-report route's limits."""

import gzip
import json

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from live import csp_reports, interview_static

WASM = b"\x00asm\x01\x00\x00\x00" + bytes(range(256)) * 64
JS = b"console.log('app');" * 200


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/static.db")
    monkeypatch.setenv("RECORDING_SWEEP_DISABLED", "1")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    from app import db, main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "mediapipe" / "wasm").mkdir(parents=True)
    (dist / "mediapipe" / "models").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>candidate app</title>")
    (dist / "assets" / "index-abc123.js").write_bytes(JS)
    (dist / "assets" / "index-abc123.js.gz").write_bytes(gzip.compress(JS))
    (dist / "assets" / "index-abc123.js.br").write_bytes(b"BROTLI-" + JS[:20])  # stand-in bytes
    (dist / "mediapipe" / "wasm" / "vision.wasm").write_bytes(WASM)
    (dist / "mediapipe" / "wasm" / "vision.wasm.gz").write_bytes(gzip.compress(WASM))
    (dist / "mediapipe" / "models" / "face_landmarker.task").write_bytes(b"model" * 100)
    monkeypatch.setattr(main, "CANDIDATE_APP_DIST", dist)
    csp_reports.limiter.reset()
    csp_reports.recent.clear()
    await db.reset_engine()
    with TestClient(main.app) as c:
        yield c
    await db.reset_engine()


def get(client, path, encoding="identity", **headers):
    return client.get(path, headers={"Accept-Encoding": encoding, **headers})


# -- types, caching, compression -------------------------------------------------------


def test_wasm_is_served_as_application_wasm(client):
    r = get(client, "/interview/mediapipe/wasm/vision.wasm")
    assert r.status_code == 200 and r.headers["content-type"] == "application/wasm"
    assert r.content == WASM
    assert get(client, "/interview/mediapipe/models/face_landmarker.task").headers["content-type"] == \
        "application/octet-stream"


def test_hashed_assets_are_immutable_and_fixed_names_revalidate_with_an_etag(client):
    assert get(client, "/interview/assets/index-abc123.js").headers["cache-control"] == \
        "public, max-age=31536000, immutable"
    r = get(client, "/interview/mediapipe/models/face_landmarker.task")
    assert r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    again = get(client, "/interview/mediapipe/models/face_landmarker.task", **{"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert get(client, "/interview/").headers["cache-control"] == "no-cache"


def test_the_best_precompressed_variant_is_served(client):
    br = client.get("/interview/assets/index-abc123.js", headers={"Accept-Encoding": "gzip, br"})
    assert br.headers["content-encoding"] == "br" and br.headers["vary"] == "Accept-Encoding"
    assert br.headers["content-type"].startswith("text/javascript")
    gz = get(client, "/interview/mediapipe/wasm/vision.wasm", "gzip, br")  # no .br for this one
    assert gz.headers["content-encoding"] == "gzip" and gz.content == WASM  # client decodes it
    assert gz.headers["content-type"] == "application/wasm"
    plain = get(client, "/interview/assets/index-abc123.js", "identity")
    assert "content-encoding" not in plain.headers and plain.content == JS
    refused = get(client, "/interview/assets/index-abc123.js", "br;q=0, gzip")
    assert refused.headers["content-encoding"] == "gzip"


def test_a_compressed_file_cannot_be_asked_for_by_name(client):
    assert get(client, "/interview/assets/index-abc123.js.br").status_code == 404


# -- the security rule ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/interview/", "/interview/assets/index-abc123.js",
                                  "/interview/mediapipe/wasm/vision.wasm", "/interview/device-check",
                                  "/interview/config", "/interview/missing.js"])
def test_every_interview_response_carries_the_enforced_rule(client, path):
    # Enforcing since 2026-11-19: two real calls showed zero reports (C-68 steps 2-4, B-89).
    r = client.get(path, follow_redirects=False)
    assert r.headers.get("content-security-policy") == interview_static.POLICY
    assert "content-security-policy-report-only" not in r.headers


def test_the_redirect_carries_it_too(client):
    r = client.get("/interview", follow_redirects=False)
    assert r.status_code == 308 and r.headers.get("content-security-policy") == interview_static.POLICY


@pytest.mark.parametrize("path", ["/join", "/admin", "/health", "/api/live/interviews/x/events",
                                  "/interviews/x"])
def test_nothing_else_carries_any_security_rule(client, path):
    r = client.get(path)
    assert "content-security-policy-report-only" not in r.headers
    assert "content-security-policy" not in r.headers


def test_the_policy_allows_only_ourselves_wasm_and_dailys_documented_hosts():
    d = interview_static.DIRECTIVES
    assert d["default-src"] == "'self'"
    assert "'wasm-unsafe-eval'" in d["script-src"] and "'unsafe-eval'" not in d["script-src"].replace(
        "'wasm-unsafe-eval'", "")
    hosts = " ".join(d.values())
    assert "google" not in hosts.lower() and "unsafe-inline" not in hosts
    for daily in ("https://*.daily.co", "https://*.dailywebrtc.com", "https://*.dailywebrtc.net",
                  "wss://*.daily.co"):
        assert daily in d["connect-src"]
    assert d["report-uri"] == interview_static.REPORT_PATH


# -- the violation report route ------------------------------------------------------------


def _report(blocked="https://evil.example/x.js?token=abc#f", doc="http://127.0.0.1/interview/?secret=1"):
    return json.dumps({"csp-report": {"document-uri": doc, "referrer": "http://ref.example/?q=2",
                                      "effective-directive": "script-src-elem", "blocked-uri": blocked,
                                      "script-sample": "alert(1)", "original-policy": "x"}})


def test_a_report_is_logged_with_only_the_rule_and_the_stripped_address(client):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        r = client.post("/api/live/csp-report", content=_report(),
                        headers={"Content-Type": "application/csp-report"})
    finally:
        logger.remove(sink)
    assert r.status_code == 204
    assert list(csp_reports.recent) == [{"directive": "script-src-elem", "blocked": "https://evil.example/x.js"}]
    logged = "\n".join(lines)
    assert "directive=script-src-elem blocked=https://evil.example/x.js" in logged
    for never in ("token=abc", "secret", "ref.example", "alert(1)", "#f"):
        assert never not in logged


def test_the_reporting_api_format_is_read_too(client):
    body = json.dumps([{"type": "csp-violation", "body": {"effectiveDirective": "connect-src",
                                                         "blockedURL": "wss://u:p@x.example:8443/s?k=1"}}])
    assert client.post("/api/live/csp-report", content=body,
                       headers={"Content-Type": "application/reports+json"}).status_code == 204
    assert csp_reports.recent[-1] == {"directive": "connect-src", "blocked": "wss://x.example:8443/s"}


def test_size_cap_and_rate_limit(client):
    big = _report(blocked="https://x.example/" + "a" * (csp_reports.MAX_BYTES + 10))
    assert client.post("/api/live/csp-report", content=big).status_code == 413
    codes = [client.post("/api/live/csp-report", content=_report()).status_code
             for _ in range(csp_reports.BURST + 3)]
    assert codes.count(204) == csp_reports.BURST - 1  # the 413 above spent one
    assert codes[-1] == 429


def test_unreadable_reports_record_nothing(client):
    assert client.post("/api/live/csp-report", content=b"not json").status_code == 204
    assert list(csp_reports.recent) == []


def test_keywords_are_kept():
    assert csp_reports.strip("inline") == "inline" and csp_reports.strip("wasm-eval") == "wasm-eval"
