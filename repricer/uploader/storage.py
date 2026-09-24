"""Where a generated feed is published for Kaspi to fetch.

Kaspi polls one fixed URL, so a feed is always written under the same name: the
new version replaces the old one in place.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

FEED_CONTENT_TYPE = "application/xml; charset=utf-8"


class FeedStorage(Protocol):
    def publish(self, filename: str, content: bytes) -> str:
        """Store the feed and return the URL to point Kaspi at."""


class S3Client(Protocol):
    """The part of a boto3 S3 client this module uses."""

    def put_object(self, **kwargs: Any) -> Any: ...


class LocalFeedStorage:
    """Writes the feed into a directory that nginx (or FastAPI) serves.

    The write is atomic: a temporary file in the same directory is renamed over
    the old feed, so Kaspi can never fetch a half-written catalogue.
    """

    def __init__(self, directory: Path | str, *, base_url: str | None = None) -> None:
        self._directory = Path(directory)
        self._base_url = base_url.rstrip("/") if base_url else None

    def publish(self, filename: str, content: bytes) -> str:
        check_filename(filename)
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._directory / filename
        handle = tempfile.NamedTemporaryFile(
            dir=self._directory, prefix=f".{filename}.", suffix=".tmp", delete=False
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        url = f"{self._base_url}/{filename}" if self._base_url else target.as_uri()
        logger.info("Feed written to {} ({} bytes), served at {}", target, len(content), url)
        return url


class S3FeedStorage:
    """Uploads the feed to any S3-compatible bucket, Supabase Storage included.

        client = boto3.client(
            "s3",
            endpoint_url="https://<project>.supabase.co/storage/v1/s3",
            aws_access_key_id=..., aws_secret_access_key=..., region_name="eu-central-1",
        )
        storage = S3FeedStorage(
            client, "feeds",
            public_base_url="https://<project>.supabase.co/storage/v1/object/public/feeds",
        )

    boto3 is not imported here: the client is passed in, so it stays an optional
    dependency and tests need no network.
    """

    def __init__(
        self,
        client: S3Client,
        bucket: str,
        *,
        prefix: str = "",
        public_base_url: str | None = None,
        # Kaspi re-reads the feed about once an hour; a cached copy would hide
        # price changes for as long as the CDN keeps it.
        cache_control: str = "no-cache",
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._cache_control = cache_control

    def publish(self, filename: str, content: bytes) -> str:
        check_filename(filename)
        key = f"{self._prefix}{filename}"
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content,
            ContentType=FEED_CONTENT_TYPE,
            CacheControl=self._cache_control,
        )
        url = f"{self._public_base_url}/{key}" if self._public_base_url else f"s3://{self._bucket}/{key}"
        logger.info("Feed uploaded to {} ({} bytes)", url, len(content))
        return url


def check_filename(filename: str) -> None:
    # Feed names are built from merchant IDs, which come from the database.
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ValueError(f"invalid feed filename {filename!r}")
