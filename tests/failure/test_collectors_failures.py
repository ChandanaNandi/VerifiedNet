"""Failure-path tests for FRR collectors: parse failures are LOUD (Gate 3 Step 6).

FIXTURE PROVENANCE: JSON shapes here and under tests/fixtures/frr/ are
SOURCE-DERIVED (from neuronoc-network-ops-assistant parser expectations, MIT,
commit 5f24447) and PROVISIONAL until Gate 4 re-records them live.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence
from typing import Any

import pytest

from verifiednet.collectors.frr import (
    BgpSummaryCollector,
    InterfaceStateCollector,
    ReachabilityCollector,
    RoutePresenceCollector,
    RunningConfigCollector,
)
from verifiednet.common.errors import ParserError
from verifiednet.common.runctx import RunContext
from verifiednet.runtime.results import ExecResult, ExecStatus

pytestmark = pytest.mark.failure

BGP_ARGV = ("vtysh", "-c", "show ip bgp summary json")
IFACE_ARGV = ("vtysh", "-c", "show interface json")
ROUTE_ARGV = ("vtysh", "-c", "show ip route json")
CONFIG_ARGV = ("vtysh", "-c", "show running-config")
PING_ARGV = ("ping", "-c", "1", "-W", "2", "10.255.0.2")


class FakeExecutor:
    """Scripted ReadOnlyExec: maps argv -> queued response specs."""

    def __init__(self) -> None:
        self._scripts: dict[tuple[str, ...], deque[dict[str, Any]]] = {}
        self._seq = 0

    def script(self, argv: Sequence[str], *specs: dict[str, Any]) -> None:
        self._scripts[tuple(argv)] = deque(specs)

    def run(self, target: str, argv: Sequence[str], timeout_s: float) -> ExecResult:
        self._seq += 1
        key = tuple(argv)
        spec = self._scripts[key].popleft()
        return ExecResult(
            status=spec.get("status", ExecStatus.OK),
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


def test_bgp_malformed_json_raises_parser_error(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(BGP_ARGV, {"stdout": "{ this is not json"})
    with pytest.raises(ParserError, match="malformed JSON"):
        BgpSummaryCollector(executor, "router_a", run_ctx).collect("baseline")


def test_bgp_empty_stdout_raises_parser_error(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(BGP_ARGV, {"stdout": ""})
    with pytest.raises(ParserError):
        BgpSummaryCollector(executor, "router_a", run_ctx).collect("baseline")


def test_bgp_missing_ipv4_unicast_raises_parser_error(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(BGP_ARGV, {"stdout": json.dumps({"ipv6Unicast": {}})})
    with pytest.raises(ParserError, match="ipv4Unicast"):
        BgpSummaryCollector(executor, "router_a", run_ctx).collect("baseline")


def test_interfaces_empty_stdout_raises_parser_error(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(IFACE_ARGV, {"stdout": ""})
    with pytest.raises(ParserError):
        InterfaceStateCollector(executor, "router_a", run_ctx).collect("baseline")


def test_interfaces_admin_up_missing_oper_still_raises(run_ctx: RunContext) -> None:
    """The Gate 4 live-format adaptation is NARROW: missing operationalStatus is
    tolerated only for admin-down entries; an admin-up interface without an
    operational status remains a loud parse failure."""
    executor = FakeExecutor()
    executor.script(
        IFACE_ARGV,
        {"stdout": json.dumps({"eth1": {"administrativeStatus": "up"}})},
    )
    with pytest.raises(ParserError, match="eth1"):
        InterfaceStateCollector(executor, "router_a", run_ctx).collect("baseline")


def test_non_ok_exec_status_raises_parser_error_for_parsing_collectors(
    run_ctx: RunContext,
) -> None:
    """NONZERO_EXIT cannot yield parseable evidence: loud failure, no fallback."""
    nonzero = {"status": ExecStatus.NONZERO_EXIT, "exit_code": 2, "stdout": "boom"}
    executor = FakeExecutor()
    executor.script(BGP_ARGV, dict(nonzero))
    executor.script(IFACE_ARGV, dict(nonzero))
    executor.script(ROUTE_ARGV, dict(nonzero))
    executor.script(CONFIG_ARGV, dict(nonzero))
    with pytest.raises(ParserError, match=r"nonzero_exit|NONZERO_EXIT"):
        BgpSummaryCollector(executor, "router_a", run_ctx).collect("baseline")
    with pytest.raises(ParserError, match=r"nonzero_exit|NONZERO_EXIT"):
        InterfaceStateCollector(executor, "router_a", run_ctx).collect("baseline")
    with pytest.raises(ParserError, match=r"nonzero_exit|NONZERO_EXIT"):
        RoutePresenceCollector(
            executor, "router_a", run_ctx, prefixes=("10.255.0.2/32",)
        ).collect("baseline")
    with pytest.raises(ParserError, match=r"nonzero_exit|NONZERO_EXIT"):
        RunningConfigCollector(executor, "router_a", run_ctx).collect("baseline")


def test_ping_denied_command_raises_parser_error(run_ctx: RunContext) -> None:
    """DENIED_* is policy misconfiguration — must be loud, never evidence."""
    executor = FakeExecutor()
    executor.script(
        PING_ARGV,
        {"status": ExecStatus.DENIED_COMMAND, "exit_code": None, "detail": "denied"},
    )
    collector = ReachabilityCollector(executor, "router_a", run_ctx, dst_ip="10.255.0.2")
    with pytest.raises(ParserError, match=r"denied_command|DENIED_COMMAND"):
        collector.collect("baseline")


def test_ping_timeout_counts_as_failed_probe_not_error(run_ctx: RunContext) -> None:
    """A timed-out probe is evidence (a failed ping), not a parse failure."""
    executor = FakeExecutor()
    ok = {"exit_code": 0, "stdout": "1 received"}
    timeout = {"status": ExecStatus.TIMEOUT, "exit_code": None, "stdout": ""}
    executor.script(PING_ARGV, ok, timeout, ok)
    collector = ReachabilityCollector(executor, "router_a", run_ctx, dst_ip="10.255.0.2")
    record = collector.collect("onset")
    assert record.normalized["ping.10.255.0.2.all_success"] == "false"
    assert record.normalized["ping.10.255.0.2.success_count"] == "2"
    assert "probe=2 exit=none" in record.raw_payload


def test_routes_malformed_entries_raise_parser_error(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(ROUTE_ARGV, {"stdout": json.dumps({"10.255.0.2/32": "not-a-list"})})
    with pytest.raises(ParserError, match="not a list"):
        RoutePresenceCollector(
            executor, "router_a", run_ctx, prefixes=("10.255.0.2/32",)
        ).collect("baseline")


def test_running_config_empty_stdout_raises_parser_error(run_ctx: RunContext) -> None:
    executor = FakeExecutor()
    executor.script(CONFIG_ARGV, {"stdout": "   \n"})
    with pytest.raises(ParserError, match="empty"):
        RunningConfigCollector(executor, "router_a", run_ctx).collect("baseline")


def test_interface_list_truncated_at_64_with_flag(run_ctx: RunContext) -> None:
    many = {
        f"eth{i:03d}": {"administrativeStatus": "up", "operationalStatus": "up"}
        for i in range(70)
    }
    executor = FakeExecutor()
    executor.script(IFACE_ARGV, {"stdout": json.dumps(many)})
    record = InterfaceStateCollector(executor, "router_a", run_ctx).collect("baseline")
    assert record.normalized["iface._truncated"] == "true"
    iface_keys = [k for k in record.normalized if not k.startswith("iface._")]
    assert len(iface_keys) == 64 * 2
    assert list(record.normalized) == sorted(record.normalized)
