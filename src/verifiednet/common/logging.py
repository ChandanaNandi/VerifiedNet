"""Structured JSON logging.

Provenance: behavior modeled on closcall ``observability/logging.py`` (commit
d192bf3) — JSON formatter + configure entrypoint — REIMPLEMENTED FROM
SPECIFICATION (closcall license unresolved), extended with run/scenario/phase/
incident context fields per Gate 2 §13.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_CONTEXT_FIELDS = ("run_id", "scenario_id", "phase", "incident_id")


class JsonFormatter(logging.Formatter):
    """Format records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
