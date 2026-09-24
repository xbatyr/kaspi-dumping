from pathlib import Path
from typing import Any

import pytest

from repricer.uploader import FEED_CONTENT_TYPE, LocalFeedStorage, S3FeedStorage

FEED = b"<?xml version='1.0' encoding='utf-8'?><kaspi_catalog/>"
NAME = "kaspi-price-list-30123456.xml"


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"ETag": "fake"}


# --- Local directory ----------------------------------------------------------


def test_local_storage_writes_the_feed(tmp_path: Path) -> None:
    storage = LocalFeedStorage(tmp_path)

    url = storage.publish(NAME, FEED)

    assert (tmp_path / NAME).read_bytes() == FEED
    assert url == (tmp_path / NAME).as_uri()


def test_local_storage_creates_missing_directories(tmp_path: Path) -> None:
    storage = LocalFeedStorage(tmp_path / "feeds" / "kaspi")

    storage.publish(NAME, FEED)

    assert (tmp_path / "feeds" / "kaspi" / NAME).read_bytes() == FEED


def test_local_storage_returns_the_public_url_when_one_is_configured(tmp_path: Path) -> None:
    storage = LocalFeedStorage(tmp_path, base_url="https://feeds.example.kz/kaspi/")

    assert storage.publish(NAME, FEED) == f"https://feeds.example.kz/kaspi/{NAME}"


def test_republishing_replaces_the_feed_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    storage = LocalFeedStorage(tmp_path)
    storage.publish(NAME, FEED)

    storage.publish(NAME, b"<kaspi_catalog>newer</kaspi_catalog>")

    assert (tmp_path / NAME).read_bytes() == b"<kaspi_catalog>newer</kaspi_catalog>"
    # The write goes through a temporary file that must be gone afterwards, so
    # Kaspi never fetches a half-written catalogue.
    assert [path.name for path in tmp_path.iterdir()] == [NAME]


@pytest.mark.parametrize("filename", ["", ".", "..", "../secrets.xml", "nested/feed.xml", "a\\b.xml"])
def test_filenames_that_escape_the_directory_are_refused(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError):
        LocalFeedStorage(tmp_path).publish(filename, FEED)


# --- S3 and Supabase Storage --------------------------------------------------


def test_s3_storage_uploads_with_content_type_and_no_cache() -> None:
    client = FakeS3Client()

    S3FeedStorage(client, "feeds").publish(NAME, FEED)

    (call,) = client.calls
    assert call == {
        "Bucket": "feeds",
        "Key": NAME,
        "Body": FEED,
        "ContentType": FEED_CONTENT_TYPE,
        # Kaspi re-reads the same URL hourly; a cached copy would hide changes.
        "CacheControl": "no-cache",
    }


def test_s3_storage_applies_the_prefix_and_public_url() -> None:
    client = FakeS3Client()
    storage = S3FeedStorage(
        client,
        "feeds",
        prefix="/kaspi/",
        public_base_url="https://project.supabase.co/storage/v1/object/public/feeds/",
    )

    url = storage.publish(NAME, FEED)

    assert client.calls[0]["Key"] == f"kaspi/{NAME}"
    assert url == f"https://project.supabase.co/storage/v1/object/public/feeds/kaspi/{NAME}"


def test_s3_storage_falls_back_to_an_s3_uri() -> None:
    assert S3FeedStorage(FakeS3Client(), "feeds").publish(NAME, FEED) == f"s3://feeds/{NAME}"


def test_s3_storage_refuses_a_traversing_filename() -> None:
    client = FakeS3Client()

    with pytest.raises(ValueError):
        S3FeedStorage(client, "feeds").publish("../feed.xml", FEED)
    assert client.calls == []
