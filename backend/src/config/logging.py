"""Structured JSON logging configuration.

Provides a JSON formatter that includes trace context (correlation_id)
from the request middleware and any extra fields passed via logger.info(..., extra={}).
"""

import json
import logging
from datetime import datetime, timezone


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

    Args:
        json_output: If True, use JSON formatter. Otherwise use human-readable format.
        level: Log level.
    """
    handler = logging.StreamHandler()

    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    logging.basicConfig(level=level, handlers=[handler], force=True)
