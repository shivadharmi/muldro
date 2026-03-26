"""Artifact storage service — async S3/MinIO operations for file uploads and downloads.

Wraps aioboto3 for async S3 operations. The `artifacts` table stores the
s3_key and s3_bucket references; this service handles the actual blob storage.
"""

import logging

import aioboto3

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class ArtifactStorageService:
    """Async S3/MinIO artifact storage."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = aioboto3.Session()

    def _client_kwargs(self) -> dict:
        kwargs: dict = {}
        if self._settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self._settings.s3_endpoint_url
        if self._settings.s3_region:
            kwargs["region_name"] = self._settings.s3_region
        return kwargs

    @property
    def _bucket(self) -> str:
        return self._settings.s3_bucket or "jarvis-artifacts"

    async def upload(
        self,
        artifact_id: str,
        data: bytes,
        mime_type: str = "application/octet-stream",
        *,
        s3_key: str | None = None,
        bucket: str | None = None,
    ) -> dict:
        """Upload artifact data to S3/MinIO.

        Returns dict with s3_key, s3_bucket, and size_bytes for DB persistence.
        """
        key = s3_key or f"artifacts/{artifact_id}"
        target_bucket = bucket or self._bucket

        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.put_object(
                Bucket=target_bucket,
                Key=key,
                Body=data,
                ContentType=mime_type,
            )

        logger.info(
            "Artifact uploaded: %s (%d bytes) to %s/%s",
            artifact_id,
            len(data),
            target_bucket,
            key,
        )
        return {
            "s3_key": key,
            "s3_bucket": target_bucket,
            "size_bytes": len(data),
        }

    async def download(
        self,
        s3_key: str,
        *,
        bucket: str | None = None,
    ) -> bytes:
        """Download artifact data from S3/MinIO."""
        target_bucket = bucket or self._bucket

        async with self._session.client("s3", **self._client_kwargs()) as s3:
            response = await s3.get_object(Bucket=target_bucket, Key=s3_key)
            body = await response["Body"].read()

        logger.debug("Artifact downloaded: %s/%s (%d bytes)", target_bucket, s3_key, len(body))
        return body

    async def delete(
        self,
        s3_key: str,
        *,
        bucket: str | None = None,
    ) -> None:
        """Delete an artifact from S3/MinIO."""
        target_bucket = bucket or self._bucket

        async with self._session.client("s3", **self._client_kwargs()) as s3:
            await s3.delete_object(Bucket=target_bucket, Key=s3_key)

        logger.info("Artifact deleted: %s/%s", target_bucket, s3_key)

    async def exists(
        self,
        s3_key: str,
        *,
        bucket: str | None = None,
    ) -> bool:
        """Check if an artifact exists in S3/MinIO."""
        target_bucket = bucket or self._bucket

        async with self._session.client("s3", **self._client_kwargs()) as s3:
            try:
                await s3.head_object(Bucket=target_bucket, Key=s3_key)
                return True
            except s3.exceptions.ClientError:
                return False
