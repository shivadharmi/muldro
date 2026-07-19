"""Voyage embedding HTTP error handling (regression for the voyage-3-retired 400)."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.services.embedding_service import EmbeddingService
from tests.conftest import make_mock_settings

_URL = "https://ai.mongodb.com/v1/embeddings"


def _voyage_settings() -> MagicMock:
    s = make_mock_settings()
    s.voyage_api_key = "test-key"
    s.voyage_base_url = "https://ai.mongodb.com/v1"
    s.embedding_model = "voyage-3.5"
    return s


def _mock_response(status: int, body_text: str, json_data=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body_text
    resp.json.return_value = json_data or {}
    if status >= 400:
        req = httpx.Request("POST", _URL)
        http_resp = httpx.Response(status, text=body_text, request=req)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"Client error '{status}'", request=req, response=http_resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _patch_client(response: MagicMock) -> tuple:
    """Patch httpx.AsyncClient to yield a client whose .post returns ``response``."""
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    ctx = patch("src.services.embedding_service.httpx.AsyncClient")
    mock_cls = ctx.start()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


async def test_voyage_4xx_fails_fast_and_logs_body(caplog):
    """A 4xx (e.g. unsupported model) is permanent → return None WITHOUT retrying,
    and the response body must be logged so the cause is visible."""
    svc = EmbeddingService(_voyage_settings())
    body = '{"detail":"Model voyage-3 is not supported. Supported models are [...]."}'
    ctx, client = _patch_client(_mock_response(400, body))
    try:
        with caplog.at_level(logging.WARNING):
            result = await svc.embed_text("hello world")
    finally:
        ctx.stop()

    assert result is None
    assert client.post.await_count == 1, "4xx must not be retried"
    assert "not supported" in caplog.text, "response body must be logged"


async def test_voyage_success_returns_vector():
    svc = EmbeddingService(_voyage_settings())
    resp = _mock_response(200, "", {"data": [{"embedding": [0.1] * 1024}]})
    ctx, client = _patch_client(resp)
    try:
        result = await svc.embed_text("hello world")
    finally:
        ctx.stop()

    assert result is not None
    assert len(result) == 1024
    assert client.post.await_count == 1
