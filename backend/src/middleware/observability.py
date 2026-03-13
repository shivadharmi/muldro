"""Observability middleware — request tracing, structured logging, metrics.

Adds correlation IDs to every request, tracks latency, and exposes
a metrics endpoint for monitoring.
"""

import logging
import time
from collections import defaultdict
from contextvars import ContextVar
from typing import ClassVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from ulid import ULID

logger = logging.getLogger(__name__)

# Context variable for request-scoped correlation ID
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Get the current request's correlation ID."""
    return correlation_id_var.get()


class RequestMetrics:
    """In-memory request metrics collector.

    Tracks request counts, latencies, and error rates per endpoint.
    Reset on server restart (suitable for v1; swap for Prometheus later).
    """

    _counts: ClassVar[dict[str, int]] = defaultdict(int)
    _errors: ClassVar[dict[str, int]] = defaultdict(int)
    _latencies: ClassVar[dict[str, list[float]]] = defaultdict(list)
    _max_latency_samples: ClassVar[int] = 1000

    @classmethod
    def record(cls, path: str, status_code: int, latency_ms: float) -> None:
        key = path
        cls._counts[key] += 1
        if status_code >= 400:
            cls._errors[key] += 1
        if len(cls._latencies[key]) < cls._max_latency_samples:
            cls._latencies[key].append(latency_ms)

    @classmethod
    def snapshot(cls) -> dict:
        """Return a snapshot of current metrics."""
        endpoints = {}
        for path in cls._counts:
            latencies = cls._latencies.get(path, [])
            avg_latency = sum(latencies) / len(latencies) if latencies else 0
            p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

            endpoints[path] = {
                "requests": cls._counts[path],
                "errors": cls._errors.get(path, 0),
                "avg_latency_ms": round(avg_latency, 2),
                "p95_latency_ms": round(p95_latency, 2),
            }
        return {
            "total_requests": sum(cls._counts.values()),
            "total_errors": sum(cls._errors.values()),
            "endpoints": endpoints,
        }

    @classmethod
    def reset(cls) -> None:
        cls._counts.clear()
        cls._errors.clear()
        cls._latencies.clear()


class TracingMiddleware(BaseHTTPMiddleware):
    """Add correlation IDs and track request metrics."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate or propagate correlation ID
        req_id = request.headers.get("X-Request-ID") or f"req_{ULID()}"
        correlation_id_var.set(req_id)

        start = time.monotonic()

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000
            path = request.url.path
            RequestMetrics.record(path, 500, elapsed_ms)
            logger.error(
                "request_error path=%s method=%s correlation_id=%s latency_ms=%.2f",
                path,
                request.method,
                req_id,
                elapsed_ms,
            )
            raise

        elapsed_ms = (time.monotonic() - start) * 1000
        path = request.url.path
        RequestMetrics.record(path, response.status_code, elapsed_ms)

        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"

        logger.info(
            "request path=%s method=%s status=%d latency_ms=%.2f correlation_id=%s",
            path,
            request.method,
            response.status_code,
            elapsed_ms,
            req_id,
        )

        return response
