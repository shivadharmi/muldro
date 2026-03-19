"""Structured logging configuration.

Provides a JSON formatter for production and a colored formatter for local
development that matches Uvicorn's default log style.
"""

import json
import logging
import re
import sys
import warnings
from datetime import datetime, timezone

# Third-party loggers that are too noisy at INFO level
_NOISY_LOGGERS = (
    "sqlalchemy",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "httpcore",
    "httpx",
    "aiosqlite",
    "asyncpg",
    "watchfiles",
    "multipart",
    "hpack",
    "h2",
)

# Regex to find sensitive query params in log messages
_SENSITIVE_QS_RE = re.compile(
    r"([\?&])(token|key|secret|password|auth)=([^\s&\"']+)", re.IGNORECASE
)


class _RedactFilter(logging.Filter):
    """Redact sensitive query parameters (token, key, secret, etc.) from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            # Uvicorn access logs pass args as a tuple; redact each string arg
            record.args = tuple(
                _SENSITIVE_QS_RE.sub(r"\1\2=[REDACTED]", str(a)) if isinstance(a, str) else a
                for a in record.args
            )
        record.msg = _SENSITIVE_QS_RE.sub(r"\1\2=[REDACTED]", str(record.msg))
        return True


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        from src.middleware.observability import correlation_id_var

        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Include correlation/request ID if present
        corr_id = correlation_id_var.get("")
        if corr_id:
            entry["correlation_id"] = corr_id

        # Include trace_id and other structured extras
        for key in (
            "trace_id",
            "trigger",
            "agent",
            "span_id",
            "duration_ms",
            "input_tokens",
            "output_tokens",
            "spans",
            "decision",
            "latency_ms",
            "event_id",
            "plan_id",
            "execution_id",
        ):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def configure_logging(*, json_output: bool = False, level: int = logging.INFO) -> None:
    """Configure root logging.

    Local dev uses Uvicorn's DefaultFormatter so all output looks identical.
    Production uses JSONFormatter for structured log aggregation.

    Args:
        json_output: If True, use JSON formatter (production). Otherwise Uvicorn-style colored.
        level: Log level for application loggers.
    """
    # Suppress websockets 16.x deprecation warning (uvicorn hasn't adapted yet)
    warnings.filterwarnings("ignore", message="remove second argument of ws_handler")

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_RedactFilter())

    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        from uvicorn.logging import DefaultFormatter

        handler.setFormatter(
            DefaultFormatter("%(levelprefix)s %(name)s: %(message)s", use_colors=True)
        )

    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Suppress noisy third-party loggers
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
