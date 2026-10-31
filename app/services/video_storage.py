"""Object storage for uploaded videos.

The backend never handles the bytes. A browser uploads straight to the bucket
with a presigned PUT, and the AI service downloads with a presigned GET. A
1 GB body through this API would hold a worker for minutes, and Render's proxy
cuts requests long before a slow farm connection finishes one.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

#: A worker downloads as soon as it claims a job; an hour covers a slow link.
DOWNLOAD_URL_TTL = timedelta(hours=1)


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    expires_at: datetime
    method: str = "PUT"
    #: Headers the client must send exactly, because they are part of the signature.
    headers: dict[str, str] = field(default_factory=dict)


class VideoStorage(Protocol):
    def presign_upload(self, key: str, content_type: str, size_bytes: int, expires_in: timedelta,
                       now: datetime) -> PresignedUpload: ...

    def presign_download(self, key: str) -> str: ...

    def object_size(self, key: str) -> int | None:
        """The stored size in bytes, or None when nothing has been uploaded under the key."""
        ...

    def delete(self, key: str) -> None: ...


def _client(settings: Settings, endpoint: str) -> "S3Client":
    assert settings.video_storage_access_key_id is not None  # noqa: S101 - checked by video_storage_enabled
    assert settings.video_storage_secret_access_key is not None  # noqa: S101
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.video_storage_region,
        aws_access_key_id=settings.video_storage_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.video_storage_secret_access_key.get_secret_value(),
        # Path style works on R2 and MinIO alike, and needs no wildcard DNS.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class S3VideoStorage:
    """Cloudflare R2, MinIO, or any S3-compatible bucket."""

    def __init__(self, settings: Settings) -> None:
        assert settings.video_storage_endpoint is not None  # noqa: S101 - only built when enabled
        assert settings.video_storage_bucket is not None  # noqa: S101
        internal = str(settings.video_storage_endpoint).rstrip("/")
        public = str(settings.video_storage_public_endpoint or settings.video_storage_endpoint).rstrip("/")
        self._bucket = settings.video_storage_bucket
        self._internal = _client(settings, internal)
        self._public = self._internal if public == internal else _client(settings, public)

    def presign_upload(self, key: str, content_type: str, size_bytes: int, expires_in: timedelta,
                       now: datetime) -> PresignedUpload:
        # ContentLength is signed, so the bucket refuses a body of any other
        # size; complete() checks it again because not every S3 clone enforces it.
        url = self._public.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type, "ContentLength": size_bytes},
            ExpiresIn=int(expires_in.total_seconds()),
        )
        return PresignedUpload(url=url, expires_at=now + expires_in, headers={"Content-Type": content_type})

    def presign_download(self, key: str) -> str:
        return self._public.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=int(DOWNLOAD_URL_TTL.total_seconds()),
        )

    def object_size(self, key: str) -> int | None:
        try:
            return self._internal.head_object(Bucket=self._bucket, Key=key)["ContentLength"]
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def delete(self, key: str) -> None:
        self._internal.delete_object(Bucket=self._bucket, Key=key)
