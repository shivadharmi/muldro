"""Central FastAPI exception handlers — the system's error boundary.

Every error response (REST) flows through one of these handlers and comes out
as the standard envelope from ``src.errors``:

    {"error": {"code": ..., "message": ..., "correlation_id": ...}}

Guarantees:
- A raw, unhandled exception NEVER reaches the client. The catch-all
  ``Exception`` handler logs the full traceback server-side (against the
  request correlation id) and returns a generic message.
- ``MuldroError`` subclasses expose only their declared client-safe message.
- ``HTTPException.detail`` is developer-authored controlled text and is passed
  through as the message (callers must not put ``str(e)`` in it — domain errors
  exist for that).

Register once in ``create_app`` via ``register_exception_handlers(app)``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.errors import ErrorBody, ErrorResponse, MuldroError, error_body, new_correlation_id
from src.middleware.observability import get_correlation_id

logger = logging.getLogger(__name__)

# HTTP status → stable error code for responses that don't carry a MuldroError.
_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    502: "upstream_unavailable",
    503: "service_unavailable",
}


def _correlation_id() -> str:
    return get_correlation_id() or new_correlation_id()


def _json(status_code: int, body: ErrorBody, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=body).model_dump(),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the four handlers that together close the error boundary."""

    @app.exception_handler(MuldroError)
    async def _handle_muldro_error(request: Request, exc: MuldroError) -> JSONResponse:
        cid = _correlation_id()
        # Full internal detail goes to logs only, tagged with the correlation id.
        logger.warning(
            "domain_error code=%s status=%d correlation_id=%s detail=%s",
            exc.code,
            exc.http_status,
            cid,
            exc.internal_message,
        )
        return _json(exc.http_status, error_body(exc, cid))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        cid = _correlation_id()
        # Request-shape errors. We log the structured detail but return a generic
        # message — echoing raw validation detail can reflect submitted values.
        logger.info("request_validation_error correlation_id=%s detail=%s", cid, exc.errors())
        body = ErrorBody(
            code="validation_error", message="The request was invalid.", correlation_id=cid
        )
        return _json(422, body)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        cid = _correlation_id()
        # HTTPException.detail is controlled, developer-authored text (e.g.
        # "Memory abc not found") — safe to surface. Non-string details collapse
        # to a generic message rather than leaking a serialized object.
        message = exc.detail if isinstance(exc.detail, str) and exc.detail else "Request failed."
        code = _STATUS_CODES.get(exc.status_code, "error")
        headers = getattr(exc, "headers", None)
        body = ErrorBody(code=code, message=message, correlation_id=cid)
        return _json(exc.status_code, body, headers=headers)

    @app.exception_handler(Exception)
    async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        cid = _correlation_id()
        # The guarantee: full traceback server-side, generic message to client.
        logger.error(
            "unhandled_error correlation_id=%s path=%s",
            cid,
            request.url.path,
            exc_info=exc,
        )
        body = ErrorBody(
            code="internal_error",
            message="Something went wrong. Please try again.",
            correlation_id=cid,
        )
        # This 500 unwinds past TracingMiddleware (which injects x-request-id),
        # so set the correlation header here to keep 500s correlatable.
        return _json(500, body, headers={"x-request-id": cid})
