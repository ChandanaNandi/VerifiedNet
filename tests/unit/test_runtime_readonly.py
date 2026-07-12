"""Unit tests for the executors' happy/denial paths with a fake runner.

Covers ReadOnlyExecutor plus the MutationExecutor happy path (write-ahead
ordering); failure paths live in tests/failure/test_runtime_failures.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.runtime import (
    CommandPolicy,
    ExecStatus,
    InMemoryTranscript,
    MutationCommandPolicy,
    MutationExecutor,
    RawResult,
    ReadOnlyExecutor,
    TargetPolicy,
)

pytestmark = pytest.mark.unit

SHOW_ARGV = ["vtysh", "-c", "show ip bgp summary json"]
MUTATE_ARGV = [
    "vtysh",
    "-c",
    "configure terminal",
    "-c",
    "router bgp 65001",
    "-c",
    "neighbor 172.30.0.2 remote-as 65999",
]


class FakeRunner:
    """Deterministic ProcessRunner; records calls, optionally advances the clock."""

    def __init__(
        self,
        result: RawResult,
        clock: object | None = None,
        advance_s: float = 0.0,
    ) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float, int]] = []
        self._clock = clock
        self._advance_s = advance_s

    def __call__(
        self, argv: Sequence[str], timeout_s: float, max_output_bytes: int
    ) -> RawResult:
        self.calls.append((tuple(argv), timeout_s, max_output_bytes))
        if self._clock is not None and self._advance_s:
            self._clock.advance(self._advance_s)  # type: ignore[attr-defined]
        return self.result


def ok_raw(stdout: str = "peers: 1") -> RawResult:
    return RawResult(0, stdout, "", False, False, False)


def make_readonly(
    runner: FakeRunner, run_ctx: RunContext, transcript: InMemoryTranscript | None = None
) -> ReadOnlyExecutor:
    return ReadOnlyExecutor(
        runner=runner,
        command_policy=CommandPolicy(allowed_binaries=frozenset({"vtysh"})),
        target_policy=TargetPolicy(allowed_targets=frozenset({"router_a", "router_b"})),
        transcript=transcript if transcript is not None else InMemoryTranscript(),
        run_ctx=run_ctx,
    )


def make_mutation(
    runner: FakeRunner, run_ctx: RunContext, transcript: InMemoryTranscript | None = None
) -> MutationExecutor:
    return MutationExecutor(
        runner=runner,
        command_policy=MutationCommandPolicy(
            allowed_binaries=frozenset({"vtysh"}),
            allowed_vtysh_prefixes=(
                ("configure terminal", "router bgp ", "neighbor "),
                ("clear bgp ",),
            ),
        ),
        target_policy=TargetPolicy(allowed_targets=frozenset({"router_a", "router_b"})),
        transcript=transcript if transcript is not None else InMemoryTranscript(),
        run_ctx=run_ctx,
    )


def test_ok_path_maps_exit_zero(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw("hello world"))
    result = make_readonly(runner, run_ctx).run("router_a", SHOW_ARGV, timeout_s=5.0)
    assert result.status is ExecStatus.OK
    assert result.exit_code == 0
    assert result.stdout == "hello world"
    assert result.stderr == ""
    assert result.truncated is False
    assert result.transcript_ok is True
    assert result.argv == tuple(SHOW_ARGV)
    assert result.target == "router_a"
    assert runner.calls == [(tuple(SHOW_ARGV), 5.0, 65536)]


def test_nonzero_exit(run_ctx: RunContext) -> None:
    runner = FakeRunner(RawResult(2, "", "boom", False, False, False))
    result = make_readonly(runner, run_ctx).run("router_a", SHOW_ARGV, timeout_s=5.0)
    assert result.status is ExecStatus.NONZERO_EXIT
    assert result.exit_code == 2
    assert result.stderr == "boom"


def test_seq_increments_via_run_context(run_ctx: RunContext) -> None:
    executor = make_readonly(FakeRunner(ok_raw()), run_ctx)
    first = executor.run("router_a", SHOW_ARGV, timeout_s=5.0)
    second = executor.run("router_a", SHOW_ARGV, timeout_s=5.0)
    assert (first.seq, second.seq) == (1, 2)


def test_duration_measured_with_injected_clock(run_ctx: RunContext, fake_clock: object) -> None:
    runner = FakeRunner(ok_raw(), clock=fake_clock, advance_s=1.5)
    result = make_readonly(runner, run_ctx).run("router_a", SHOW_ARGV, timeout_s=5.0)
    assert result.duration_s == pytest.approx(1.5)


def test_command_denial_does_not_execute_but_is_transcripted(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw())
    transcript = InMemoryTranscript()
    executor = make_readonly(runner, run_ctx, transcript)
    result = executor.run("router_a", ["vtysh", "-c", "configure terminal"], timeout_s=5.0)
    assert result.status is ExecStatus.DENIED_COMMAND
    assert result.exit_code is None
    assert result.detail != ""
    assert runner.calls == []
    assert len(transcript.entries) == 1
    entry = transcript.entries[0]
    assert entry.stage == "completed"
    assert entry.mode == "read"
    assert entry.status == "denied_command"
    assert entry.seq == result.seq == 1


def test_target_denial_does_not_execute(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw())
    transcript = InMemoryTranscript()
    executor = make_readonly(runner, run_ctx, transcript)
    result = executor.run("router_z", SHOW_ARGV, timeout_s=5.0)
    assert result.status is ExecStatus.DENIED_TARGET
    assert runner.calls == []
    assert transcript.entries[0].status == "denied_target"


def test_read_transcript_records_completed_entry(run_ctx: RunContext) -> None:
    transcript = InMemoryTranscript()
    make_readonly(FakeRunner(ok_raw()), run_ctx, transcript).run(
        "router_b", SHOW_ARGV, timeout_s=5.0
    )
    (entry,) = transcript.entries
    assert entry.stage == "completed"
    assert entry.status == "ok"
    assert entry.target == "router_b"
    assert entry.argv == tuple(SHOW_ARGV)


def test_mutation_happy_path_writes_pending_then_completed(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw("applied"))
    transcript = InMemoryTranscript()
    executor = make_mutation(runner, run_ctx, transcript)
    result = executor.run("router_a", MUTATE_ARGV, timeout_s=5.0)
    assert result.status is ExecStatus.OK
    assert result.transcript_ok is True
    assert runner.calls == [(tuple(MUTATE_ARGV), 5.0, 65536)]
    assert [(e.stage, e.status) for e in transcript.entries] == [
        ("pending", "pending"),
        ("completed", "ok"),
    ]
    assert all(e.mode == "mutation" and e.seq == result.seq for e in transcript.entries)


def test_mutation_denial_does_not_execute(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw())
    transcript = InMemoryTranscript()
    executor = make_mutation(runner, run_ctx, transcript)
    result = executor.run("router_a", SHOW_ARGV, timeout_s=5.0)
    assert result.status is ExecStatus.DENIED_COMMAND
    assert runner.calls == []
    (entry,) = transcript.entries
    assert (entry.stage, entry.status) == ("completed", "denied_command")
