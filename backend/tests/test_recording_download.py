"""The recording download (C-89, round 6): the signed link reaches aiohttp unchanged, and a
download whose size differs from Content-Length fails with nothing at the real path.
No network: a stand-in aiohttp session records what it was asked for."""

import pytest
from yarl import URL

from bot.services import daily

#: Shaped like Daily's signed S3 link: escapes that aiohttp/yarl would re-encode.
LINK = ("https://c.daily.co/rec/abc.mp4?X-Amz-Credential=AKIA%2F20261119%2Fus-west-2%2Fs3%2Faws4_request"
        "&X-Amz-Signature=ab%3D%2Bcd&response-content-disposition=attachment%3B%20filename%3D%22a.mp4%22")


class Response:
    def __init__(self, status, json_body=None, chunks=(), content_length=None, fail_after=None):
        self.status, self._json, self._chunks = status, json_body, list(chunks)
        self.content_length, self._fail_after = content_length, fail_after
        self.content = self

    async def json(self):
        return self._json

    async def iter_chunked(self, size):
        for i, c in enumerate(self._chunks):
            if self._fail_after is not None and i == self._fail_after:
                raise ConnectionResetError("connection dropped")
            yield c

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def stand_in(download_response):
    requested = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            requested.append(url)
            if "access-link" in str(url):
                return Response(200, {"download_link": LINK})
            return download_response

    return Session, requested


@pytest.fixture
def run(monkeypatch, tmp_path):
    async def go(download_response):
        session, requested = stand_in(download_response)
        monkeypatch.setattr(daily.aiohttp, "ClientSession", session)
        dest = tmp_path / "rec.mp4"
        try:
            result = await daily.download_recording(api_key="k", recording_id="r1", destination=dest)
        except RuntimeError as exc:
            result = exc
        return result, requested, dest
    return go


async def test_the_signed_link_reaches_aiohttp_exactly_as_daily_signed_it(run):
    result, requested, dest = await run(Response(200, chunks=[b"a" * 10, b"b" * 5], content_length=15))
    sent = requested[1]
    # A plain string would be parsed by aiohttp as URL(sent), re-encoding it: the 403.
    as_aiohttp_sees_it = sent if isinstance(sent, URL) else URL(sent)
    assert str(as_aiohttp_sees_it) == LINK
    assert result == 15 and dest.read_bytes() == b"a" * 10 + b"b" * 5


async def test_a_short_download_fails_and_leaves_nothing(run, tmp_path):
    result, _, dest = await run(Response(200, chunks=[b"a" * 10], content_length=15))
    assert isinstance(result, RuntimeError) and "incomplete (10 of 15 bytes)" in str(result)
    assert not dest.exists() and not list(tmp_path.glob("*.part"))


async def test_a_dropped_connection_leaves_no_partial_file(run, tmp_path):
    with pytest.raises(ConnectionResetError):
        session, _ = stand_in(Response(200, chunks=[b"a", b"b"], content_length=2, fail_after=1))
        import bot.services.daily as d
        original = d.aiohttp.ClientSession
        d.aiohttp.ClientSession = session
        try:
            await d.download_recording(api_key="k", recording_id="r1", destination=tmp_path / "rec.mp4")
        finally:
            d.aiohttp.ClientSession = original
    assert not list(tmp_path.iterdir())


async def test_a_403_is_still_a_failed_download(run):
    result, _, dest = await run(Response(403))
    assert isinstance(result, RuntimeError) and "(403)" in str(result) and not dest.exists()
