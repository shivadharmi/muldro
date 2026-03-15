"""S3-backed artifact storage for documents, drafts, screenshots, etc."""

import logging

from ulid import ULID

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class ArtifactStore:
    """S3-backed artifact storage with metadata tracking.

    Artifacts are stored as:
      s3://{bucket}/artifacts/{user_id}/{artifact_type}/{artifact_id}
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._bucket = settings.s3_bucket

    async def store(
        self,
        user_id: str,
        artifact_type: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
        metadata: dict | None = None,
    ) -> str:
        """Store an artifact. Returns the S3 key."""
        artifact_id = f"art_{ULID()}"
        key = f"artifacts/{user_id}/{artifact_type}/{artifact_id}"

        if not self._bucket:
            logger.warning("S3 bucket not configured, artifact store is no-op")
            return key

        import aioboto3

        session = aioboto3.Session()
        kwargs = {"region_name": self._settings.s3_region}
        if self._settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self._settings.s3_endpoint_url

        async with session.client("s3", **kwargs) as s3:
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=mime_type,
                Metadata=metadata or {},
            )

        logger.info("Artifact stored: %s (%d bytes)", key, len(content))
        return key

    async def retrieve(self, key: str) -> bytes:
        """Retrieve an artifact by S3 key."""
        if not self._bucket:
            raise ValueError("S3 bucket not configured")

        import aioboto3

        session = aioboto3.Session()
        kwargs = {"region_name": self._settings.s3_region}
        if self._settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self._settings.s3_endpoint_url

        async with session.client("s3", **kwargs) as s3:
            resp = await s3.get_object(Bucket=self._bucket, Key=key)
            body = await resp["Body"].read()

        return body

    async def delete(self, key: str) -> None:
        """Delete an artifact."""
        if not self._bucket:
            return

        import aioboto3

        session = aioboto3.Session()
        kwargs = {"region_name": self._settings.s3_region}
        if self._settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self._settings.s3_endpoint_url

        async with session.client("s3", **kwargs) as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def get_presigned_url(self, key: str, ttl: int = 3600) -> str:
        """Get a presigned URL for an artifact."""
        if not self._bucket:
            return ""

        import aioboto3

        session = aioboto3.Session()
        kwargs = {"region_name": self._settings.s3_region}
        if self._settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self._settings.s3_endpoint_url

        async with session.client("s3", **kwargs) as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl,
            )
        return url

    async def list_artifacts(
        self, user_id: str, artifact_type: str | None = None, limit: int = 50
    ) -> list[dict]:
        """List artifacts for a user."""
        if not self._bucket:
            return []

        prefix = f"artifacts/{user_id}/"
        if artifact_type:
            prefix += f"{artifact_type}/"

        import aioboto3

        session = aioboto3.Session()
        kwargs = {"region_name": self._settings.s3_region}
        if self._settings.s3_endpoint_url:
            kwargs["endpoint_url"] = self._settings.s3_endpoint_url

        async with session.client("s3", **kwargs) as s3:
            resp = await s3.list_objects_v2(Bucket=self._bucket, Prefix=prefix, MaxKeys=limit)
            items = resp.get("Contents", [])

        return [
            {
                "key": item["Key"],
                "size": item["Size"],
                "last_modified": item["LastModified"].isoformat(),
            }
            for item in items
        ]
