"""Recordings in S3: the store, and the sweep, playback and deletion that use it.

The S3 client is faked. It behaves the way boto3 does for the calls we make, including
the "no such key" error code, so the tests exercise our logic rather than AWS. What
these tests cannot show is that a real bucket accepts the upload and the presigned
link. That is the drill in docs/OPERATIONS.md.
"""

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from app import recording_s3
from app.recording_s3 import RecordingStorageError, S3RecordingStore, configured_store
from tests.test_recordings import a_recording_file, client, make_interview  # noqa: F401


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], int] = {}
        self.uploads: list[tuple[str, str]] = []
        self.fail_upload = False
        self.stored_size_override: int | None = None

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        if self.fail_upload:
            raise OSError("connection reset")
        size = Path(filename).stat().st_size
        self.objects[(bucket, key)] = self.stored_size_override or size
        self.uploads.append((bucket, key))

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": self.objects[(Bucket, Key)]}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)  # S3 deletes are idempotent

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return (
            f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}"
        )


@pytest.fixture
def fake_s3():
    """Swap the S3 client for the fake, and restore the real function on the way out.

    Written with an explicit context manager rather than monkeypatch: a leftover patch
    here made a later, unrelated test read the fake bucket.
    """
    from unittest.mock import patch

    from app import recordings

    fake = FakeS3Client()
    store = S3RecordingStore(bucket="mitra-recordings", region="eu-west-1", client=fake)
    with patch.object(recording_s3, "configured_store", lambda: store), patch.object(
        recordings, "configured_store", lambda: store
    ):
        yield fake, store


# --- the store itself ----------------------------------------------------------


def test_an_upload_is_stored_and_its_size_is_checked(tmp_path, fake_s3):
    fake, store = fake_s3
    local = a_recording_file(tmp_path / "x.mp4")

    size = store.upload(local, store.key_for("int_1"))

    assert size == local.stat().st_size
    assert fake.uploads == [("mitra-recordings", "recordings/int_1.mp4")]


def test_an_upload_the_bucket_truncated_is_not_treated_as_stored(tmp_path, fake_s3):
    fake, store = fake_s3
    fake.stored_size_override = 10
    local = a_recording_file(tmp_path / "x.mp4")

    with pytest.raises(RecordingStorageError, match="not treating it as stored"):
        store.upload(local, store.key_for("int_1"))


def test_an_empty_file_is_never_uploaded(tmp_path, fake_s3):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    _, store = fake_s3

    with pytest.raises(RecordingStorageError, match="empty"):
        store.upload(empty, store.key_for("int_1"))


def test_exists_is_false_for_a_missing_object_and_true_after_upload(tmp_path, fake_s3):
    _, store = fake_s3
    key = store.key_for("int_1")
    assert store.exists(key) is False

    store.upload(a_recording_file(tmp_path / "x.mp4"), key)

    assert store.exists(key) is True


def test_a_real_storage_error_is_raised_rather_than_read_as_missing(fake_s3):
    """Only "no such key" means missing. A permissions or network error must not be
    mistaken for a recording that is gone, because that would mark it lost."""
    fake, store = fake_s3

    def denied(Bucket, Key):
        raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    fake.head_object = denied
    with pytest.raises(ClientError):
        store.exists("recordings/int_1.mp4")


def test_delete_removes_the_object_and_is_safe_to_repeat(tmp_path, fake_s3):
    _, store = fake_s3
    key = store.key_for("int_1")
    store.upload(a_recording_file(tmp_path / "x.mp4"), key)

    store.delete(key)
    store.delete(key)  # already gone: still fine

    assert store.exists(key) is False


def test_playback_is_a_presigned_link_that_expires(fake_s3):
    _, store = fake_s3
    url = store.playback_url("recordings/int_1.mp4")
    assert "recordings/int_1.mp4" in url
    assert f"X-Amz-Expires={recording_s3.PLAYBACK_SECONDS}" in url


def test_the_prefix_always_ends_in_a_slash():
    store = S3RecordingStore(bucket="b", prefix="recs", client=object())
    assert store.key_for("int_9") == "recs/int_9.mp4"


def test_no_bucket_means_local_storage(monkeypatch):
    monkeypatch.delenv("RECORDINGS_S3_BUCKET", raising=False)
    assert configured_store() is None


def test_a_bucket_turns_s3_on_with_the_documented_defaults(monkeypatch):
    monkeypatch.setenv("RECORDINGS_S3_BUCKET", "mitra-recordings")
    monkeypatch.delenv("RECORDINGS_S3_REGION", raising=False)
    monkeypatch.delenv("RECORDINGS_S3_PREFIX", raising=False)

    store = configured_store()

    assert store.bucket == "mitra-recordings"
    assert store.region == "us-east-1"
    assert store.prefix == "recordings/"


# --- the sweep, playback and deletion through the app ------------------------


async def test_a_collected_recording_is_stored_in_s3_and_the_local_copy_removed(
    client, monkeypatch, fake_s3
):
    from app import db, recordings
    from bot.services.daily import DailyRecording

    fake, _ = fake_s3
    interview_id = await make_interview(recording_status=db.RecordingStatus.RECORDING)
    order: list[str] = []

    async def fake_find(*, api_key, room_name):
        return DailyRecording(id="daily-1", room_name=room_name, start_ts=0, duration_seconds=60)

    async def fake_download(*, api_key, recording_id, destination):
        order.append("download")
        a_recording_file(destination)
        return destination.stat().st_size

    async def fake_delete(*, api_key, recording_id):
        order.append("daily-delete")

    monkeypatch.setattr(recordings, "find_recording", fake_find)
    monkeypatch.setattr(recordings, "download_recording", fake_download)
    monkeypatch.setattr(recordings, "delete_recording", fake_delete)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["stored"] == 1
    assert order == ["download", "daily-delete"]
    assert fake.uploads == [("mitra-recordings", f"recordings/{interview_id}.mp4")]
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_store == "s3"
        assert interview.recording_path == f"recordings/{interview_id}.mp4"
    assert not recordings.recording_file(interview_id).exists()


async def test_a_failed_upload_keeps_dailys_copy_and_leaves_no_half_stored_row(
    client, monkeypatch, fake_s3
):
    from app import db, recordings
    from bot.services.daily import DailyRecording

    fake, _ = fake_s3
    fake.fail_upload = True
    interview_id = await make_interview(recording_status=db.RecordingStatus.RECORDING)
    deleted_from_daily: list[str] = []

    async def fake_find(*, api_key, room_name):
        return DailyRecording(id="daily-2", room_name=room_name, start_ts=0, duration_seconds=60)

    async def fake_download(*, api_key, recording_id, destination):
        a_recording_file(destination)
        return destination.stat().st_size

    async def fake_delete(*, api_key, recording_id):
        deleted_from_daily.append(recording_id)

    monkeypatch.setattr(recordings, "find_recording", fake_find)
    monkeypatch.setattr(recordings, "download_recording", fake_download)
    monkeypatch.setattr(recordings, "delete_recording", fake_delete)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["error"] == 1
    assert deleted_from_daily == []  # Daily keeps the only other copy
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.RECORDING
    assert not recordings.recording_file(interview_id).exists()


async def test_playback_redirects_to_a_presigned_link_without_serving_the_bytes(
    client, fake_s3
):
    from app import db

    fake, store = fake_s3
    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_store="s3",
        recording_path=store.key_for("int_rec1"),
    )
    fake.objects[("mitra-recordings", store.key_for(interview_id))] = 2048

    response = client.get(f"/interviews/{interview_id}/recording/video", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://mitra-recordings.s3.amazonaws.com/")
    assert response.headers["cache-control"] == "no-store"


async def test_a_recording_missing_from_s3_is_reported_not_redirected(client, fake_s3):
    from app import db

    _, store = fake_s3
    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_store="s3",
        recording_path=store.key_for("int_gone"),
    )

    response = client.get(f"/interviews/{interview_id}/recording/video", follow_redirects=False)

    assert response.status_code == 404
    assert "no recording in storage" in response.json()["detail"]


async def test_deleting_an_s3_recording_removes_the_object_and_confirms_it(client, fake_s3):
    from app import db, recordings

    fake, store = fake_s3
    interview_id = await make_interview(
        recording_status=db.RecordingStatus.STORED,
        recording_store="s3",
        recording_path=store.key_for("int_rec1"),
    )
    fake.objects[("mitra-recordings", store.key_for(interview_id))] = 2048

    await recordings.remove_recording(
        interview_id, status=db.RecordingStatus.DELETED, api_key="test"
    )

    assert ("mitra-recordings", store.key_for(interview_id)) not in fake.objects
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.DELETED
        assert interview.recording_path is None


async def test_a_deployed_instance_without_a_bucket_does_not_write_to_its_disk(
    client, monkeypatch
):
    """The container's disk is erased on deploy. Downloading there would lose the file
    while its row still said it was stored, so the sweep must refuse."""
    from app import db, recordings
    from bot.services.daily import DailyRecording

    monkeypatch.setattr(recordings, "configured_store", lambda: None)
    monkeypatch.setattr(recordings, "is_deployed", lambda: True)
    interview_id = await make_interview(recording_status=db.RecordingStatus.RECORDING)
    downloaded = []

    async def fake_find(*, api_key, room_name):
        return DailyRecording(id="daily-3", room_name=room_name, start_ts=0, duration_seconds=60)

    async def fake_download(*, api_key, recording_id, destination):
        downloaded.append(destination)
        return 1

    monkeypatch.setattr(recordings, "find_recording", fake_find)
    monkeypatch.setattr(recordings, "download_recording", fake_download)

    counts = await recordings.run_sweep(api_key="test")

    assert counts["error"] == 1
    assert downloaded == []
    async with db.get_sessionmaker()() as session:
        interview = await session.get(db.Interview, interview_id)
        assert interview.recording_status == db.RecordingStatus.RECORDING
