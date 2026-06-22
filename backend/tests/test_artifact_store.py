"""Tests for ArtifactStore S3/MinIO client configuration and fail-closed writes.

Regression: with no bucket configured, store() silently returned a fake key and
the POST /v1/artifacts handler responded 201 Created — but the content could
never be retrieved ("S3 bucket not configured" on GET). Local dev also could
not authenticate against MinIO because the client passed no credentials.
"""

import pytest

from src.services.artifact_store import ArtifactStore
from tests.conftest import make_mock_settings


def _settings(**s3):
    """make_mock_settings returns a MagicMock, so S3 fields must be set
    explicitly — otherwise unset attributes are truthy mocks."""
    base = dict(
        s3_bucket="jarvis-artifacts",
        s3_endpoint_url="",
        s3_region="ap-south-1",
        s3_access_key_id="",
        s3_secret_access_key="",
    )
    base.update(s3)
    return make_mock_settings(**base)


class TestClientKwargs:
    def test_includes_minio_endpoint_and_credentials_when_set(self):
        store = ArtifactStore(
            _settings(
                s3_endpoint_url="http://localhost:9000",
                s3_access_key_id="jarvis",
                s3_secret_access_key="jarvisdev",
            )
        )
        kw = store._client_kwargs()
        assert kw["region_name"] == "ap-south-1"
        assert kw["endpoint_url"] == "http://localhost:9000"
        assert kw["aws_access_key_id"] == "jarvis"
        assert kw["aws_secret_access_key"] == "jarvisdev"

    def test_omits_endpoint_and_credentials_when_unset(self):
        """Production (IAM role) path: only region, no explicit creds/endpoint."""
        store = ArtifactStore(_settings(s3_region="us-east-1"))
        kw = store._client_kwargs()
        assert kw == {"region_name": "us-east-1"}


class TestFailClosed:
    @pytest.mark.asyncio
    async def test_store_raises_when_bucket_unconfigured(self):
        """store() must not return a phantom key when there is nowhere to store —
        otherwise the artifact row + 201 response lie about a write that never
        happened."""
        store = ArtifactStore(_settings(s3_bucket=""))
        with pytest.raises(ValueError):
            await store.store("usr_1", "document", b"hello")
