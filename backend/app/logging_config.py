"""Structured JSON logging and cross-request/job correlation-id propagation.

Every log line emitted while handling one HTTP request or processing one background job
carries the same correlation id (an incoming ``X-Request-Id`` header, a generated UUID, or
the job id for a Celery task), so an operator can grep one id and see the whole story for
that request or job -- no raw media, evidence, or personal data ever belongs in a log line.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

_correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Return the correlation id active for the current request or job, if any."""
    return _correlation_id_var.get()


def set_correlation_id(value: str | None) -> None:
    """Set the correlation id used by every log line emitted in the current context."""
    _correlation_id_var.set(value)


class _CorrelationJsonFormatter(logging.Formatter):
    """Render one structured JSON line per log record, including the active correlation id."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record to a single-line JSON object."""
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    """Configure every logger to emit structured JSON carrying the active correlation id.

    Replaces existing handlers on the root and uvicorn loggers so the app's own logging and
    uvicorn's access/error logging share one structured shape instead of mixed plain text.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_CorrelationJsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False
