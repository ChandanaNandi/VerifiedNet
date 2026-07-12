"""Unit tests for FRR collectors (Gate 3 Step 6).

FIXTURE PROVENANCE: the JSON fixtures under tests/fixtures/frr/ are
SOURCE-DERIVED (shaped from neuronoc-network-ops-assistant parser
expectations, MIT, commit 5f24447) and PROVISIONAL until Gate 4 re-records
them against a live FRR 8.4 lab.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from verifiednet.collectors.frr import (
    BgpSummaryCollector,
    InterfaceStateCollector,
    ReachabilityCollector,
    RoutePresenceCollector,
    RunningConfigCollector,
)
from verifiednet.common.runctx import RunContext
from verifiednet.runtime.results import ExecResult, ExecStatus

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "frr"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeExecutor:
    """Scripted ReadOnlyExec: maps argv -> queued response specs."""

    def __init__(self, target: str = "router_a") -> None:
        self._target = target
        self._scripts: dict[tuple[str, ...], deque[dict[str, Any]]] = {}
        self._seq = 0
        self.calls: list[tuple[str, tuple[str, ...], float]] = []

    def script(self, argv: Sequence[str], *specs: dict[str, Any]) -> None:
        self._scripts[tuple(argv)] = deque(specs)

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        self._seq += 1
        key = tuple(argv)
        self.calls.append((target, key, timeout_s))
        spec = self._scripts[key].popleft()
        status = spec.get("status", ExecStatus.OK)
        return ExecResult(
            status=status,
            target=target,
            argv=key,
            exit_code=spec.get("exit_code", 0),
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
            truncated=False,
            duration_s=0.01,
            seq=self._seq,
            transcript_ok=True,
            detail=spec.get("detail", ""),
        )


def _fresh_run_ctx() -> RunContext:
    return RunContext(
        "run-test-0001", clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )


BGP_ARGV = ("vtysh", "-c", "show ip bgp summary json")
IFACE_ARGV = ("vtysh", "-c", "show interface json")
ROUTE_ARGV = ("vtysh", "-c", "show ip route json")
CONFIG_ARGV = ("vtysh", "-c", "show running-config")
PING_ARGV = ("ping", "-c", "1", "-W", "2", "10.255.0.2")


def test_bgp_summary_established(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(BGP_ARGV, {"stdout": _fixture("bgp_summary_established.json")})
    record = BgpSummaryCollector(executor, "router_a", run_ctx).collect("baseline")
    assert record.normalized == {
        "bgp.local_as": "65001",
        "bgp.peer.172.30.0.2.remote_as": "65002",
        "bgp.peer.172.30.0.2.state": "Established",
    }
    assert list(record.normalized) == sorted(record.normalized)
    assert record.source.collector == "frr.bgp_summary"
    assert record.source.command == BGP_ARGV
    assert record.source.trusted is True
    assert record.source.transcript_seq == 1
    assert record.phase == "baseline"
    assert record.run_seq == 1


def test_bgp_summary_idle_wrong_as(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(BGP_ARGV, {"stdout": _fixture("bgp_summary_idle_wrong_as.json")})
    record = BgpSummaryCollector(executor, "router_a", run_ctx).collect("onset")
    assert record.normalized == {
        "bgp.local_as": "65001",
        "bgp.peer.172.30.0.2.remote_as": "65999",
        "bgp.peer.172.30.0.2.state": "Idle",
    }
    assert list(record.normalized) == sorted(record.normalized)


def test_evidence_id_content_derived() -> None:
    raw_established = _fixture("bgp_summary_established.json")
    raw_idle = _fixture("bgp_summary_idle_wrong_as.json")

    def collect(raw: str) -> str:
        executor = FakeExecutor()
        executor.script(BGP_ARGV, {"stdout": raw})
        collector = BgpSummaryCollector(executor, "router_a", _fresh_run_ctx())
        return collector.collect("baseline").evidence_id

    # same content -> same id across independent runs
    assert collect(raw_established) == collect(raw_established)
    # different raw payload -> different id (closcall _emit collision fix)
    assert collect(raw_established) != collect(raw_idle)
    assert collect(raw_established).startswith("ev-")


def test_interfaces_up(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(IFACE_ARGV, {"stdout": _fixture("interfaces_up.json")})
    record = InterfaceStateCollector(executor, "router_a", run_ctx).collect("baseline")
    assert record.normalized == {
        "iface.eth0.admin": "up",
        "iface.eth0.oper": "up",
        "iface.eth1.admin": "up",
        "iface.eth1.oper": "up",
        "iface.lo.admin": "up",
        "iface.lo.oper": "up",
    }
    assert list(record.normalized) == sorted(record.normalized)
    assert "iface._truncated" not in record.normalized


def test_reachability_all_probes_succeed(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    ok = {"exit_code": 0, "stdout": "1 packets transmitted, 1 received"}
    executor.script(PING_ARGV, ok, ok, ok)
    collector = ReachabilityCollector(executor, "router_a", run_ctx, dst_ip="10.255.0.2")
    record = collector.collect("baseline")
    assert record.normalized == {
        "ping.10.255.0.2.all_success": "true",
        "ping.10.255.0.2.probe_count": "3",
        "ping.10.255.0.2.success_count": "3",
    }
    assert list(record.normalized) == sorted(record.normalized)
    assert len(executor.calls) == 3
    assert "probe=1 exit=0" in record.raw_payload
    assert "probe=3 exit=0" in record.raw_payload


def test_reachability_two_of_three_is_not_success(run_ctx: RunContext) -> None:
    """3/3 rule (Gate 2.5 W8): partial reachability is a fault symptom."""
    executor = FakeExecutor()
    ok = {"exit_code": 0, "stdout": "1 received"}
    fail = {"status": ExecStatus.NONZERO_EXIT, "exit_code": 1, "stdout": "0 received"}
    executor.script(PING_ARGV, ok, fail, ok)
    collector = ReachabilityCollector(executor, "router_a", run_ctx, dst_ip="10.255.0.2")
    record = collector.collect("onset")
    assert record.normalized["ping.10.255.0.2.all_success"] == "false"
    assert record.normalized["ping.10.255.0.2.success_count"] == "2"
    assert record.normalized["ping.10.255.0.2.probe_count"] == "3"


def test_routes_present(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(ROUTE_ARGV, {"stdout": _fixture("routes_with_loopbacks.json")})
    collector = RoutePresenceCollector(
        executor, "router_a", run_ctx, prefixes=("10.255.0.1/32", "10.255.0.2/32")
    )
    record = collector.collect("baseline")
    assert record.normalized == {
        "route.10.255.0.1/32.present": "true",
        "route.10.255.0.1/32.protocols": "connected",
        "route.10.255.0.2/32.present": "true",
        "route.10.255.0.2/32.protocols": "bgp",
    }
    assert list(record.normalized) == sorted(record.normalized)


def test_routes_absent(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(ROUTE_ARGV, {"stdout": _fixture("routes_missing_loopback.json")})
    collector = RoutePresenceCollector(
        executor, "router_a", run_ctx, prefixes=("10.255.0.1/32", "10.255.0.2/32")
    )
    record = collector.collect("onset")
    assert record.normalized["route.10.255.0.2/32.present"] == "false"
    assert record.normalized["route.10.255.0.2/32.protocols"] == ""
    assert record.normalized["route.10.255.0.1/32.present"] == "true"


def test_running_config_hash(run_ctx: RunContext) -> None:
    config_text = "frr version 8.4.1\nhostname router_b\nrouter bgp 65002\n"
    executor = FakeExecutor()
    executor.script(CONFIG_ARGV, {"stdout": config_text})
    record = RunningConfigCollector(executor, "router_b", run_ctx).collect("baseline")
    expected = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    assert record.normalized == {"config.sha256": expected}
    assert record.raw_payload == config_text
    assert record.raw_sha256 == expected


def test_collectors_expose_names(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    assert BgpSummaryCollector(executor, "r", run_ctx).name == "frr.bgp_summary"
    assert InterfaceStateCollector(executor, "r", run_ctx).name == "frr.interfaces"
    assert (
        ReachabilityCollector(executor, "r", run_ctx, dst_ip="10.0.0.1").name
        == "frr.reachability"
    )
    assert (
        RoutePresenceCollector(executor, "r", run_ctx, prefixes=("10.0.0.1/32",)).name
        == "frr.routes"
    )
    assert RunningConfigCollector(executor, "r", run_ctx).name == "frr.running_config"
