"""Tests for the central error boundary (src/errors.py + src/api/error_handlers.py).

Verifies the core invariant: no raw internal exception detail ever reaches a
client; every error response is the standard {error:{code,message,correlation_id}}
envelope; the correlation id is present and matches the X-Request-ID header.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from src.api.error_handlers import register_exception_handlers
from src.errors import (
    ExternalServiceError,
    MuldroError,
    NotFoundError,
    ValidationError,
    safe_error_event,
)
from src.middleware.observability import TracingMiddleware

# A secret-looking internal string we must never see in a response body.
SECRET = "postgres://admin:hunter2@db.internal:5432/muldro"


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(TracingMiddleware)

    @app.get("/boom")
    async def boom():
        raise ValueError(f"connection refused to {SECRET}")

    @app.get("/missing")
    async def missing():
        raise NotFoundError(f"user row {SECRET} not found")

    @app.get("/bad")
    async def bad():
        raise ValidationError("amount must be positive", safe_message="Amount must be positive.")

    @app.get("/upstream")
    async def upstream():
        raise ExternalServiceError(f"anthropic 529 from {SECRET}")

    @app.get("/http")
    async def http():
        raise HTTPException(status_code=404, detail="Memory abc not found")

    return app


def _client() -> TestClient:
    # raise_server_exceptions=False so the catch-all handler's 500 response is
    # returned rather than re-raised into the test.
    return TestClient(_make_app(), raise_server_exceptions=False)


def test_unhandled_exception_returns_generic_envelope_no_leak():
    resp = _client().get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"] == "Something went wrong. Please try again."
    assert body["error"]["correlation_id"]
    # The raw exception text / secret must NOT appear anywhere in the body.
    assert SECRET not in resp.text
    assert "connection refused" not in resp.text


def test_domain_error_exposes_only_safe_message():
    resp = _client().get("/missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "The requested resource was not found."
    assert SECRET not in resp.text


def test_validation_error_uses_controlled_safe_message():
    resp = _client().get("/bad")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Amount must be positive."
    assert SECRET not in resp.text


def test_external_service_error_maps_to_502_generic():
    resp = _client().get("/upstream")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_unavailable"
    assert SECRET not in resp.text


def test_http_exception_detail_is_preserved_as_message():
    resp = _client().get("/http")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "Memory abc not found"


def test_correlation_id_matches_request_header():
    resp = _client().get("/boom")
    assert resp.json()["error"]["correlation_id"] == resp.headers["x-request-id"]


def test_safe_error_event_sse_shape_no_leak():
    evt = safe_error_event(ValueError(f"boom {SECRET}"))
    assert evt["event"] == "error"
    assert evt["code"] == "internal_error"
    assert evt["message"] == "Something went wrong. Please try again."
    assert SECRET not in str(evt)
    assert evt["correlation_id"]


def test_safe_error_event_ws_shape_and_domain_passthrough():
    evt = safe_error_event(
        NotFoundError("internal detail"), correlation_id="err_fixed", channel="ws"
    )
    assert evt["status"] == "error"
    assert evt["code"] == "not_found"
    assert evt["message"] == "The requested resource was not found."
    assert evt["correlation_id"] == "err_fixed"


def test_muldro_error_internal_message_is_separate_from_safe():
    exc = MuldroError("dsn leak here", safe_message="oops")
    assert exc.internal_message == "dsn leak here"
    assert exc.safe_message == "oops"
