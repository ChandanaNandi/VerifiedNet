"""Unit tests for the structured JSON logging formatter."""

from __future__ import annotations

import json
import logging

import pytest

from verifiednet.common.logging import JsonFormatter, configure_logging

pytestmark = pytest.mark.unit


def _record(msg: str = "hello %s", args: tuple[object, ...] = ("world",)) -> logging.LogRecord:
    return logging.LogRecord(
        name="verifiednet.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_formatter_emits_valid_json_with_core_fields() -> None:
    out = JsonFormatter().format(_record())
    data = json.loads(out)
    assert data["level"] == "INFO"
    assert data["message"] == "hello world"
    assert data["logger"] == "verifiednet.test"
    assert data["ts"].endswith("Z")


def test_context_fields_included_when_present() -> None:
    record = _record()
    record.run_id = "run-test-0001"
    record.scenario_id = "bgp-remote-as-mismatch-2r-0001"
    record.phase = "onset"
    record.incident_id = "inc-abc"
    data = json.loads(JsonFormatter().format(record))
    assert data["run_id"] == "run-test-0001"
    assert data["scenario_id"] == "bgp-remote-as-mismatch-2r-0001"
    assert data["phase"] == "onset"
    assert data["incident_id"] == "inc-abc"


def test_context_fields_omitted_when_absent() -> None:
    data = json.loads(JsonFormatter().format(_record()))
    for field in ("run_id", "scenario_id", "phase", "incident_id"):
        assert field not in data


def test_exception_info_rendered() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(msg="failed", args=())
        record.exc_info = sys.exc_info()
    data = json.loads(JsonFormatter().format(record))
    assert "boom" in data["exc_info"]


def test_context_fields_flow_via_logging_extra() -> None:
    logger = logging.getLogger("verifiednet.extra-test")
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("injecting", extra={"run_id": "run-x", "phase": "inject"})
    finally:
        logger.removeHandler(handler)
    assert len(captured) == 1
    data = json.loads(JsonFormatter().format(captured[0]))
    assert data["run_id"] == "run-x"
    assert data["phase"] == "inject"


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    before = root.handlers[:]
    try:
        configure_logging("INFO")
        configure_logging("INFO")
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers = before
