"""Failure-path tests for BGP convergence: timeouts are typed, loud, informative.

Also proves the whole convergence path is mutation-free by construction: a run
that never converges leaves a transcript containing ONLY read-mode entries.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.compose_project import ComposeProject
from verifiednet.labs.frr.convergence import (
    BgpConvergenceTimeoutError,
    wait_for_bgp_established,
)
from verifiednet.labs.frr.exec_adapter import FrrReadOnlyTransportAdapter
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.runtime import (
    CommandPolicy,
    InMemoryTranscript,
    RawResult,
    ReadOnlyExecutor,
    TargetPolicy,
)
from verifiednet.runtime.results import ExecResult, ExecStatus

pytestmark = pytest.mark.failure


def summary(local_as: int, peer_ip: str, state: str, remote_as: int) -> str:
    return json.dumps(
        {
            "ipv4Unicast": {
                "as": local_as,
                "peers": {peer_ip: {"state": state, "remoteAs": remote_as}},
            }
        }
    )


A_EST = summary(65001, "172.30.0.2", "Established", 65002)
A_IDLE = summary(65001, "172.30.0.2", "Idle", 65002)
B_EST = summary(65002, "172.30.0.1", "Established", 65001)
B_IDLE = summary(65002, "172.30.0.1", "Idle", 65001)
NO_PEER = json.dumps({"ipv4Unicast": {"as": 65002, "peers": {}}})


class ConstantExec:
    """Always returns the same stdout per target."""

    def __init__(self, by_target: dict[str, str], status: ExecStatus = ExecStatus.OK) -> None:
        self._by_target = by_target
        self._status = status
        self._seq = 0

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        self._seq += 1
        return ExecResult(
            status=self._status,
            target=target,
            argv=tuple(argv),
            exit_code=0 if self._status is ExecStatus.OK else 1,
            stdout=self._by_target[target],
            stderr="",
            duration_s=0.01,
            seq=self._seq,
        )


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def run_wait(executor: object, timeout_s: float = 10.0) -> BgpConvergenceTimeoutError:
    clock = FakeMonotonic()
    with pytest.raises(BgpConvergenceTimeoutError) as excinfo:
        wait_for_bgp_established(
            executor,  # type: ignore[arg-type]
            two_router_frr_topology(),
            timeout_s=timeout_s,
            monotonic=clock,
            sleep=clock.sleep,
        )
    return excinfo.value


def test_timeout_reports_attempts_elapsed_and_last_states() -> None:
    error = run_wait(ConstantExec({"router_a": A_IDLE, "router_b": B_IDLE}))
    report = error.report
    assert report.converged is False
    assert report.attempts >= 10  # ~1 poll per simulated second
    assert report.elapsed_s >= 10.0
    assert report.last_states == {
        "router_a:172.30.0.2": "Idle",
        "router_b:172.30.0.1": "Idle",
    }
    assert "Idle" in str(error)


def test_one_side_established_is_not_convergence() -> None:
    error = run_wait(ConstantExec({"router_a": A_EST, "router_b": B_IDLE}))
    assert error.report.last_states["router_a:172.30.0.2"] == "Established"
    assert error.report.last_states["router_b:172.30.0.1"] == "Idle"


def test_missing_peer_in_live_json_is_visible_not_swallowed() -> None:
    error = run_wait(ConstantExec({"router_a": A_EST, "router_b": NO_PEER}))
    assert error.report.last_states["router_b:172.30.0.1"] == "missing-peer"


def test_malformed_json_is_recorded_as_parse_error_observation() -> None:
    error = run_wait(ConstantExec({"router_a": A_EST, "router_b": "not json"}))
    assert error.report.last_states["router_b:172.30.0.1"].startswith("parse-error:")


def test_non_ok_exec_status_is_recorded_not_raised() -> None:
    executor = ConstantExec(
        {"router_a": "", "router_b": ""}, status=ExecStatus.NONZERO_EXIT
    )
    error = run_wait(executor)
    assert error.report.last_states["router_a:172.30.0.2"] == "exec-nonzero_exit"


def test_failed_convergence_leaves_only_read_transcript_entries(tmp_path: object) -> None:
    """Zero mutation even when convergence fails — proven via a real transcript.

    The scripted runner always answers Idle; the executor is the REAL
    ReadOnlyExecutor behind the REAL transport adapter with a real transcript.
    """
    idle = A_IDLE

    def runner(argv: Sequence[str], timeout_s: float, max_output_bytes: int) -> RawResult:
        return RawResult(0, idle if "router_a" in argv else B_IDLE, "", False, False, False)

    run_ctx = RunContext("run-conv-fail-0001")
    transcript = InMemoryTranscript()
    executor = ReadOnlyExecutor(
        runner,
        CommandPolicy(allowed_binaries=frozenset({"vtysh", "ping"})),
        TargetPolicy(allowed_targets=frozenset({"router_a", "router_b"})),
        transcript,
        run_ctx,
    )
    project = ComposeProject.for_run("run-conv-fail-0001", "compose.yml", ("router_a", "router_b"))
    adapter = FrrReadOnlyTransportAdapter(project, executor, run_ctx)
    clock = FakeMonotonic()
    with pytest.raises(BgpConvergenceTimeoutError):
        wait_for_bgp_established(
            adapter,
            two_router_frr_topology(),
            timeout_s=5.0,
            monotonic=clock,
            sleep=clock.sleep,
        )
    assert len(transcript.entries) > 0
    assert all(entry.mode == "read" for entry in transcript.entries)
