"""Unit tests for the bounded BGP convergence helper (Gate 4 Step 2).

All tests are offline: a scripted executor returns canned ``show ip bgp summary
json`` payloads per poll, and time is a fake monotonic clock advanced by the
injected sleep. No blind sleeps, no Docker.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence

import pytest

from verifiednet.labs.frr.convergence import (
    ConvergenceReport,
    wait_for_bgp_established,
)
from verifiednet.labs.frr.topologies import two_router_frr_topology
from verifiednet.runtime.results import ExecResult, ExecStatus

pytestmark = pytest.mark.unit

BGP_ARGV = ("vtysh", "-c", "show ip bgp summary json")


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
B_EST = summary(65002, "172.30.0.1", "Established", 65001)
A_IDLE = summary(65001, "172.30.0.2", "Idle", 65002)
B_ACTIVE = summary(65002, "172.30.0.1", "Active", 65001)


class ScriptedExec:
    """Per-target queues of stdout payloads; repeats the last one when drained."""

    def __init__(self, scripts: dict[str, list[str]]) -> None:
        self._queues = {node: deque(payloads) for node, payloads in scripts.items()}
        self._last: dict[str, str] = {}
        self._seq = 0
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        self._seq += 1
        self.calls.append((target, tuple(argv)))
        queue = self._queues[target]
        stdout = queue.popleft() if queue else self._last[target]
        self._last[target] = stdout
        return ExecResult(
            status=ExecStatus.OK,
            target=target,
            argv=tuple(argv),
            exit_code=0,
            stdout=stdout,
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


def converge(executor: ScriptedExec, **kwargs: object) -> ConvergenceReport:
    clock = FakeMonotonic()
    return wait_for_bgp_established(
        executor,
        two_router_frr_topology(),
        monotonic=clock,
        sleep=clock.sleep,
        **kwargs,  # type: ignore[arg-type]
    )


def test_converges_with_two_consecutive_healthy_polls() -> None:
    executor = ScriptedExec({"router_a": [A_EST], "router_b": [B_EST]})
    report = converge(executor)
    assert report.converged is True
    assert report.attempts == 2  # two consecutive confirmations required
    assert report.last_states == {
        "router_a:172.30.0.2": "Established",
        "router_b:172.30.0.1": "Established",
    }


def test_consecutive_counter_resets_on_flap() -> None:
    # healthy, then a flap on B, then healthy again -> needs two MORE polls
    executor = ScriptedExec(
        {
            "router_a": [A_EST, A_EST, A_EST, A_EST],
            "router_b": [B_EST, B_ACTIVE, B_EST, B_EST],
        }
    )
    report = converge(executor)
    assert report.converged is True
    assert report.attempts == 4


def test_polls_only_the_bgp_summary_argv() -> None:
    executor = ScriptedExec({"router_a": [A_EST], "router_b": [B_EST]})
    converge(executor)
    assert {argv for _t, argv in executor.calls} == {BGP_ARGV}


def test_waits_through_initial_idle_states() -> None:
    executor = ScriptedExec(
        {"router_a": [A_IDLE, A_IDLE, A_EST], "router_b": [B_ACTIVE, B_EST, B_EST]}
    )
    report = converge(executor)
    assert report.converged is True
    assert report.attempts == 4
    assert report.elapsed_s > 0


def test_required_consecutive_must_be_positive() -> None:
    executor = ScriptedExec({"router_a": [A_EST], "router_b": [B_EST]})
    with pytest.raises(ValueError):
        converge(executor, required_consecutive=0)
