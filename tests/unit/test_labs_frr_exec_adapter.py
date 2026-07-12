"""Unit tests for the Compose project abstraction and the transport adapters.

The adapters preserve the Gate 3 security boundary while adding the logical vs
transport split: the *logical* command is what the runtime command policy
validates; the *transport* command (``docker compose … exec -T <svc> <logical>``)
is what the single process runner executes. Both are retained under one
deterministic ``command_id``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.compose_project import (
    ComposeProject,
    ServiceResolutionError,
    project_name_for_run,
)
from verifiednet.labs.frr.exec_adapter import (
    FrrMutationTransportAdapter,
    FrrReadOnlyTransportAdapter,
)
from verifiednet.runtime import (
    CommandPolicy,
    ExecStatus,
    InMemoryTranscript,
    MutationCommandPolicy,
    MutationExecutor,
    RawResult,
    ReadOnlyExecutor,
    TargetPolicy,
    bgp_remote_as_mutation_shapes,
)

pytestmark = pytest.mark.unit

SERVICES = ("router_a", "router_b")
COMPOSE_FILE = Path("/work/vnet/docker-compose.yml")
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
    """Deterministic ProcessRunner; records the argv it is asked to execute."""

    def __init__(self, result: RawResult) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float, int]] = []

    def __call__(
        self, argv: Sequence[str], timeout_s: float, max_output_bytes: int
    ) -> RawResult:
        self.calls.append((tuple(argv), timeout_s, max_output_bytes))
        return self.result


def ok_raw(stdout: str = "peers: 1") -> RawResult:
    return RawResult(0, stdout, "", False, False, False)


def make_project() -> ComposeProject:
    return ComposeProject.for_run("run-test-0001", COMPOSE_FILE, SERVICES)


# --- project name derivation ------------------------------------------------


def test_project_name_is_deterministic_and_prefixed() -> None:
    assert project_name_for_run("run-test-0001") == "vnet-run-test-0001"
    assert project_name_for_run("run-test-0001") == project_name_for_run("run-test-0001")


def test_project_name_sanitizes_invalid_chars() -> None:
    name = project_name_for_run("Run/Test:0001")
    assert name == "vnet-run-test-0001"
    assert all(c.islower() or c.isdigit() or c in "-_" for c in name)


def test_project_name_bounded_and_hash_suffixed_when_long() -> None:
    long_id = "run-" + ("a" * 200)
    name = project_name_for_run(long_id)
    assert len(name) <= 63
    assert name.startswith("vnet-")
    # deterministic under truncation
    assert project_name_for_run(long_id) == name
    # distinct long ids that share a truncated prefix stay distinct via the hash
    other = project_name_for_run("run-" + ("a" * 199) + "b")
    assert name != other


# --- argv builders ----------------------------------------------------------


def test_exec_argv_is_compose_exec_T_transport() -> None:
    project = make_project()
    argv = project.exec_argv("router_a", ("vtysh", "-c", "show version"))
    assert argv == [
        "docker",
        "compose",
        "-p",
        "vnet-run-test-0001",
        "-f",
        str(COMPOSE_FILE),
        "exec",
        "-T",
        "router_a",
        "vtysh",
        "-c",
        "show version",
    ]


def test_up_down_argv_have_no_shell_strings() -> None:
    project = make_project()
    assert project.up_argv()[-3:] == ["up", "-d", "--remove-orphans"]
    assert project.down_argv()[-3:] == ["down", "--volumes", "--remove-orphans"]


def test_ps_labels_argv_filters_by_project_label() -> None:
    project = make_project()
    argv = project.ps_labels_argv()
    assert "label=com.docker.compose.project=vnet-run-test-0001" in argv


def test_parse_ps_labels_and_resolution() -> None:
    stdout = "abc123\trouter_a\trunning\ndef456\trouter_b\trunning\n"
    rows = ComposeProject.parse_ps_labels(stdout)
    assert rows == (("abc123", "router_a", "running"), ("def456", "router_b", "running"))
    project = make_project()
    assert project.resolve_service_container(rows, "router_a") == "abc123"


def test_resolve_missing_service_raises() -> None:
    project = make_project()
    with pytest.raises(ServiceResolutionError):
        project.resolve_service_container((), "router_a")


def test_resolve_ambiguous_service_raises() -> None:
    project = make_project()
    rows = (("id1", "router_a", "running"), ("id2", "router_a", "running"))
    with pytest.raises(ServiceResolutionError):
        project.resolve_service_container(rows, "router_a")


def test_parse_ps_labels_rejects_malformed_row() -> None:
    with pytest.raises(ServiceResolutionError):
        ComposeProject.parse_ps_labels("only_one_field\n")


# --- read-only transport adapter -------------------------------------------


def make_read_adapter(
    runner: FakeRunner, run_ctx: RunContext, transcript: InMemoryTranscript
) -> FrrReadOnlyTransportAdapter:
    executor = ReadOnlyExecutor(
        runner=runner,
        command_policy=CommandPolicy(allowed_binaries=frozenset({"vtysh"})),
        target_policy=TargetPolicy(allowed_targets=frozenset(SERVICES)),
        transcript=transcript,
        run_ctx=run_ctx,
    )
    return FrrReadOnlyTransportAdapter(make_project(), executor, run_ctx)


def test_read_adapter_executes_transport_but_validates_logical(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw("hello"))
    transcript = InMemoryTranscript()
    adapter = make_read_adapter(runner, run_ctx, transcript)
    result = adapter.run("router_a", SHOW_ARGV, timeout_s=5.0)

    assert result.status is ExecStatus.OK
    # the runner was handed the compose-exec transport, not the bare logical argv
    executed_argv, timeout, _ = runner.calls[0]
    assert executed_argv[:2] == ("docker", "compose")
    assert executed_argv[-3:] == ("vtysh", "-c", "show ip bgp summary json")
    assert timeout == 5.0
    # the result's argv is the transport argv and matches the invocation
    assert result.invocation is not None
    assert result.argv == result.invocation.transport_argv
    assert result.invocation.logical_argv == tuple(SHOW_ARGV)
    assert result.invocation.target == "router_a"


def test_read_adapter_command_id_is_deterministic_content_hash() -> None:
    # Two independent adapters over the same run/op/target/logical produce the
    # same command_id — a content hash, never a random UUID.
    ctx1 = RunContext("run-test-0001")
    ctx2 = RunContext("run-test-0001")
    a1 = make_read_adapter(FakeRunner(ok_raw()), ctx1, InMemoryTranscript())
    a2 = make_read_adapter(FakeRunner(ok_raw()), ctx2, InMemoryTranscript())
    r1 = a1.run("router_a", SHOW_ARGV, timeout_s=5.0)
    r2 = a2.run("router_a", SHOW_ARGV, timeout_s=5.0)
    assert r1.invocation is not None and r2.invocation is not None
    assert r1.invocation.command_id == r2.invocation.command_id


def test_read_adapter_op_index_distinguishes_repeated_calls(run_ctx: RunContext) -> None:
    adapter = make_read_adapter(FakeRunner(ok_raw()), run_ctx, InMemoryTranscript())
    first = adapter.run("router_a", SHOW_ARGV, timeout_s=5.0)
    second = adapter.run("router_a", SHOW_ARGV, timeout_s=5.0)
    assert first.invocation is not None and second.invocation is not None
    # Same target/logical but different op index -> different command_id.
    assert first.invocation.command_id != second.invocation.command_id


def test_read_adapter_transcript_retains_invocation(run_ctx: RunContext) -> None:
    transcript = InMemoryTranscript()
    adapter = make_read_adapter(FakeRunner(ok_raw()), run_ctx, transcript)
    result = adapter.run("router_a", SHOW_ARGV, timeout_s=5.0)
    (entry,) = transcript.entries
    assert entry.invocation is not None
    assert entry.invocation.command_id == result.invocation.command_id  # type: ignore[union-attr]
    assert entry.argv == result.argv


def test_read_adapter_denies_forbidden_logical_command(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw())
    adapter = make_read_adapter(runner, run_ctx, InMemoryTranscript())
    result = adapter.run("router_a", ["vtysh", "-c", "configure terminal"], timeout_s=5.0)
    assert result.status is ExecStatus.DENIED_COMMAND
    # denial happens before any transport executes
    assert runner.calls == []


# --- mutation transport adapter (separate capability) ----------------------


def make_mutation_adapter(
    runner: FakeRunner, run_ctx: RunContext, transcript: InMemoryTranscript
) -> FrrMutationTransportAdapter:
    executor = MutationExecutor(
        runner=runner,
        command_policy=MutationCommandPolicy(
            allowed_binaries=frozenset({"vtysh"}),
            allowed_shapes=bgp_remote_as_mutation_shapes(),
        ),
        target_policy=TargetPolicy(allowed_targets=frozenset(SERVICES)),
        transcript=transcript,
        run_ctx=run_ctx,
    )
    return FrrMutationTransportAdapter(make_project(), executor, run_ctx)


def test_mutation_adapter_write_ahead_then_transport(run_ctx: RunContext) -> None:
    runner = FakeRunner(ok_raw("applied"))
    transcript = InMemoryTranscript()
    adapter = make_mutation_adapter(runner, run_ctx, transcript)
    result = adapter.run("router_a", MUTATE_ARGV, timeout_s=5.0)

    assert result.status is ExecStatus.OK
    executed_argv, _, _ = runner.calls[0]
    assert executed_argv[:2] == ("docker", "compose")
    # write-ahead pending then completed, both carrying the same invocation
    stages = [(e.stage, e.status) for e in transcript.entries]
    assert stages == [("pending", "pending"), ("completed", "ok")]
    ids = {e.invocation.command_id for e in transcript.entries if e.invocation}
    assert len(ids) == 1
