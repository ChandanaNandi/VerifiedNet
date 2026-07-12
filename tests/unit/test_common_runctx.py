"""Unit tests for RunContext: identity, sequence, clock, deterministic ids."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from verifiednet.common.runctx import RunContext

pytestmark = pytest.mark.unit

EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_seq_is_monotonic_from_one(run_ctx: RunContext) -> None:
    assert [run_ctx.next_seq() for _ in range(5)] == [1, 2, 3, 4, 5]


def test_now_returns_injected_clock_value(run_ctx: RunContext, fake_clock) -> None:
    assert run_ctx.now() == EPOCH
    fake_clock.advance(90)
    assert run_ctx.now() == datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC)


def test_naive_clock_rejected() -> None:
    ctx = RunContext("run-naive", clock=lambda: datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="naive"):
        ctx.now()


@pytest.mark.parametrize("bad", ["", "AB", "-lead", "x", "has space", "UPPER-case"])
def test_invalid_run_id_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid run_id"):
        RunContext(bad)


def test_content_id_deterministic_and_prefixed() -> None:
    a = RunContext("run-a").content_id("inc", {"k": 1})
    b = RunContext("run-b").content_id("inc", {"k": 1})
    assert a == b  # content-derived, not run-derived
    assert a.startswith("inc-")
    assert len(a) == len("inc-") + 16


def test_content_id_differs_for_different_payloads(run_ctx: RunContext) -> None:
    assert run_ctx.content_id("ev", {"k": 1}) != run_ctx.content_id("ev", {"k": 2})


def test_seq_id_deterministic_format(run_ctx: RunContext) -> None:
    assert run_ctx.seq_id("tr", 7) == "tr-run-test-0001-000007"
    assert run_ctx.seq_id("tr", 7) == run_ctx.seq_id("tr", 7)
