"""Tests for observability middleware — metrics and tracing."""

from starlette.testclient import TestClient

from src.api.app import app
from src.middleware.observability import RequestMetrics


def test_metrics_snapshot_empty():
    """Should return empty metrics when no requests recorded."""
    RequestMetrics.reset()
    snapshot = RequestMetrics.snapshot()

    assert snapshot["total_requests"] == 0
    assert snapshot["total_errors"] == 0
    assert snapshot["endpoints"] == {}


def test_metrics_record_and_snapshot():
    """Should track request counts and latencies."""
    RequestMetrics.reset()

    RequestMetrics.record("/v1/health", 200, 5.0)
    RequestMetrics.record("/v1/health", 200, 10.0)
    RequestMetrics.record("/v1/health", 500, 100.0)
    RequestMetrics.record("/v1/approvals", 200, 20.0)

    snapshot = RequestMetrics.snapshot()

    assert snapshot["total_requests"] == 4
    assert snapshot["total_errors"] == 1
    assert snapshot["endpoints"]["/v1/health"]["requests"] == 3
    assert snapshot["endpoints"]["/v1/health"]["errors"] == 1
    assert snapshot["endpoints"]["/v1/approvals"]["requests"] == 1


def test_metrics_endpoint():
    """Should expose metrics via the /v1/system/metrics endpoint."""
    RequestMetrics.reset()
    client = TestClient(app)

    # Make a few requests first
    client.get("/v1/health")
    client.get("/v1/health")

    resp = client.get("/v1/system/metrics")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_requests"] >= 2
    assert "/v1/health" in data["endpoints"]


def test_tracing_adds_headers():
    """Should add X-Request-ID and X-Response-Time-Ms headers."""
    client = TestClient(app)

    resp = client.get("/v1/health")

    assert "x-request-id" in resp.headers
    assert "x-response-time-ms" in resp.headers
    assert float(resp.headers["x-response-time-ms"]) >= 0


def test_tracing_propagates_request_id():
    """Should propagate incoming X-Request-ID."""
    client = TestClient(app)

    resp = client.get("/v1/health", headers={"X-Request-ID": "test_req_123"})

    assert resp.headers["x-request-id"] == "test_req_123"
