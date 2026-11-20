"""Downloading a finished recording from Daily's signed link must send the link byte for byte.

The link is a CloudFront URL whose signature covers the exact query string. aiohttp re-encodes a
plain URL string (`%2F` becomes `/`), and the signature then fails with 403 AccessDenied. This test
serves a signed-looking link that only answers when its query arrives unchanged, so it fails on the
old code and passes on the fix.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.services import daily

# A query string that aiohttp changes if it is not told to leave it alone.
SIGNED_QUERY = "Expires=1900000000&Key-Pair-Id=K1&Signature=ab%2Bc%2F%3D&dl=1"
PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + b"x" * 4096


@pytest.fixture
async def daily_server(monkeypatch):
    seen: list[str] = []

    async def access_link(request):
        base = f"http://{request.host}"
        return web.json_response({"download_link": f"{base}/file.mp4?{SIGNED_QUERY}"})

    async def file(request):
        seen.append(request.raw_path)
        if request.raw_path != f"/file.mp4?{SIGNED_QUERY}":
            # What CloudFront does with a signature it cannot verify.
            return web.Response(status=403, text="<Error><Code>AccessDenied</Code></Error>")
        return web.Response(body=PAYLOAD, content_type="video/mp4")

    app = web.Application()
    app.router.add_get("/recordings/{rid}/access-link", access_link)
    app.router.add_get("/file.mp4", file)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(daily, "DAILY_API_URL", f"http://{server.host}:{server.port}")
    yield seen
    await server.close()


async def test_the_signed_link_is_sent_exactly_as_daily_issued_it(daily_server, tmp_path):
    destination = tmp_path / "rec.mp4"

    written = await daily.download_recording(
        api_key="test", recording_id="rec-1", destination=destination
    )

    assert written == len(PAYLOAD)
    assert destination.read_bytes() == PAYLOAD
    assert daily_server == [f"/file.mp4?{SIGNED_QUERY}"]
