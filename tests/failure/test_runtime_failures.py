"""Failure-path tests for the runtime executors and default_runner.

The three real-process tests at the bottom exec only trivial local binaries
(sleep / echo / a nonexistent name) — no shell, no network, no services.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from verifiednet.common.errors import TranscriptWriteError
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
    default_runner,
)

pytestmark = pytest.mark.failure

SHOW_ARGV = ["vtysh", "-c", "show ip bgp summary json"]
CLEAR_ARGV = ["vtysh", "-c", "clear bgp 172.30.0.2"]


class FakeRunner:
    def __init__(self, result: RawResult) -> None:
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, argv: Sequence[str], timeout_s: float, max_output_bytes: int
    ) -> RawResult:
        self.calls.append(tuple(argv))
        return self.result


def make_readonly(
    runner: FakeRunner, run_ctx: RunContext, transcript: InMemoryTranscript
) -> ReadOnlyExecutor:
    return ReadOnlyExecutor(
        runner=runner,
        command_policy=CommandPolicy(allowed_binaries=frozenset({"vtysh"})),
        target_policy=TargetPolicy(allowed_targets=frozenset({"router_a"})),
        transcript=transcript,
        run_ctx=run_ctx,
    )


def make_mutation(
    runner: FakeRunner, run_ctx: RunContext, transcript: InMemoryTranscript
) -> MutationExecutor:
    return MutationExecutor(
        runner=runner,
        command_policy=MutationCommandPolicy(
            allowed_binaries=frozenset({"vtysh"}),
            allowed_vtysh_prefixes=(("clear bgp ",),),
        ),
        target_policy=TargetPolicy(allowed_targets=frozenset({"router_a"})),
        transcript=transcript,
        run_ctx=run_ctx,
    )


def test_timed_out_maps_to_timeout(run_ctx: RunContext) -> None:
    runner = FakeRunner(RawResult(None, "partial", "", True, False, False))
    result = make_readonly(runner, run_ctx, InMemoryTranscript()).run(
        "router_a", SHOW_ARGV, timeout_s=0.1
    )
    assert result.status is ExecStatus.TIMEOUT
    assert result.exit_code is None
    assert result.stdout == "partial"


def test_not_found_maps_to_target_not_found(run_ctx: RunContext) -> None:
    runner = FakeRunner(RawResult(None, "", "", False, True, False))
    result = make_readonly(runner, run_ctx, InMemoryTranscript()).run(
        "router_a", SHOW_ARGV, timeout_s=1.0
    )
    assert result.status is ExecStatus.TARGET_NOT_FOUND
    assert result.exit_code is None


def test_runner_truncation_flag_propagates(run_ctx: RunContext) -> None:
    runner = FakeRunner(RawResult(0, "x" * 16, "", False, False, True))
    result = make_readonly(runner, run_ctx, InMemoryTranscript()).run(
        "router_a", SHOW_ARGV, timeout_s=1.0
    )
    assert result.status is ExecStatus.OK
    assert result.truncated is True


def test_read_transcript_failure_sets_flag_without_raising(run_ctx: RunContext) -> None:
    runner = FakeRunner(RawResult(0, "ok", "", False, False, False))
    transcript = InMemoryTranscript(fail_after=0)
    result = make_readonly(runner, run_ctx, transcript).run(
        "router_a", SHOW_ARGV, timeout_s=1.0
    )
    assert result.status is ExecStatus.OK
    assert result.transcript_ok is False
    assert transcript.entries == ()


def test_mutation_write_ahead_failure_blocks_execution(run_ctx: RunContext) -> None:
    runner = FakeRunner(RawResult(0, "", "", False, False, False))
    transcript = InMemoryTranscript(fail_after=0)
    executor = make_mutation(runner, run_ctx, transcript)
    with pytest.raises(TranscriptWriteError):
        executor.run("router_a", CLEAR_ARGV, timeout_s=1.0)
    assert runner.calls == []
    assert transcript.entries == ()


def test_mutation_completion_transcript_failure_sets_flag(run_ctx: RunContext) -> None:
    runner = FakeRunner(RawResult(0, "cleared", "", False, False, False))
    transcript = InMemoryTranscript(fail_after=1)
    result = make_mutation(runner, run_ctx, transcript).run(
        "router_a", CLEAR_ARGV, timeout_s=1.0
    )
    assert result.status is ExecStatus.OK
    assert result.transcript_ok is False
    assert runner.calls == [tuple(CLEAR_ARGV)]
    (pending,) = transcript.entries
    assert (pending.stage, pending.status) == ("pending", "pending")


def test_default_runner_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        default_runner(["true"], 0.0, 1024)
    with pytest.raises(ValueError, match="timeout_s"):
        default_runner(["true"], -1.0, 1024)


def test_default_runner_rejects_str_argv() -> None:
    with pytest.raises(TypeError, match="sequence"):
        default_runner("true", 1.0, 1024)


def test_default_runner_rejects_nonpositive_max_output_bytes() -> None:
    with pytest.raises(ValueError, match="max_output_bytes"):
        default_runner(["true"], 1.0, 0)


# --- real-process tests (trivial local binaries only) -----------------------


def test_default_runner_real_timeout() -> None:
    raw = default_runner(["sleep", "2"], 0.05, 1024)
    assert raw.timed_out is True
    assert raw.not_found is False
    assert raw.exit_code is None


def test_default_runner_real_not_found() -> None:
    raw = default_runner(["verifiednet-no-such-binary-xyz"], 1.0, 1024)
    assert raw.not_found is True
    assert raw.timed_out is False
    assert raw.exit_code is None


def test_default_runner_real_truncation_caps_output() -> None:
    raw = default_runner(["echo", "x" * 200], 5.0, 16)
    assert raw.exit_code == 0
    assert raw.truncated is True
    assert len(raw.stdout.encode("utf-8")) <= 16
