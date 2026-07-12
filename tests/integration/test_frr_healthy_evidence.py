"""Live integration: collect and validate the full healthy-state evidence set.

One live lab per module (module-scoped fixture): start, converge, then every
existing collector runs against the live backend through its read-only
executor — no collector bypasses its contract, no direct docker exec anywhere.
Evidence values are asserted against the approved healthy expectations.
Teardown + zero-resource verification runs even on failure.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import NamedTuple

import pytest

from verifiednet.collectors.frr import (
    BgpSummaryCollector,
    InterfaceStateCollector,
    ReachabilityCollector,
    RoutePresenceCollector,
    RunningConfigCollector,
)
from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.convergence import ConvergenceReport, wait_for_bgp_established
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.schemas.topology import TopologySpec

pytestmark = pytest.mark.integration

LOOPBACKS = {"router_a": "10.255.0.1/32", "router_b": "10.255.0.2/32"}
PEERS = {"router_a": "172.30.0.2", "router_b": "172.30.0.1"}
LOCAL_AS = {"router_a": "65001", "router_b": "65002"}
REMOTE_AS = {"router_a": "65002", "router_b": "65001"}


class LiveLab(NamedTuple):
    backend: FrrComposeBackend
    topology: TopologySpec
    run_ctx: RunContext
    report: ConvergenceReport


@pytest.fixture(scope="module")
def live_lab(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[LiveLab]:
    run_id = f"it-evidence-{int(time.time())}"
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_ctx = RunContext(run_id)
    backend = FrrComposeBackend(
        topology, run_ctx, work_dir=tmp_path_factory.mktemp("evidence-lab")
    )
    try:
        backend.start()
        report = wait_for_bgp_established(backend.readonly_executor, topology)
        yield LiveLab(backend, topology, run_ctx, report)
    finally:
        backend.stop()


def test_bgp_convergence_metrics_are_bounded(live_lab: LiveLab) -> None:
    assert live_lab.report.converged is True
    assert live_lab.report.attempts >= 2
    assert 0.0 < live_lab.report.elapsed_s < 60.0
    assert set(live_lab.report.last_states.values()) == {"Established"}


@pytest.mark.parametrize("node", ["router_a", "router_b"])
def test_bgp_summary_matches_healthy_expectations(live_lab: LiveLab, node: str) -> None:
    collector = BgpSummaryCollector(
        live_lab.backend.readonly_executor, node, live_lab.run_ctx
    )
    record = collector.collect("baseline")
    peer = PEERS[node]
    assert record.normalized["bgp.local_as"] == LOCAL_AS[node]
    assert record.normalized[f"bgp.peer.{peer}.state"] == "Established"
    assert record.normalized[f"bgp.peer.{peer}.remote_as"] == REMOTE_AS[node]
    assert record.source.trusted is True
    assert record.raw_payload  # raw JSON retained verbatim


@pytest.mark.parametrize("node", ["router_a", "router_b"])
def test_link_interface_is_up_with_approved_name(live_lab: LiveLab, node: str) -> None:
    collector = InterfaceStateCollector(
        live_lab.backend.readonly_executor, node, live_lab.run_ctx
    )
    record = collector.collect("baseline")
    assert record.normalized["iface.eth1.admin"] == "up"
    assert record.normalized["iface.eth1.oper"] == "up"
    assert record.normalized["iface.lo.admin"] == "up"


@pytest.mark.parametrize("node", ["router_a", "router_b"])
def test_both_loopback_routes_present(live_lab: LiveLab, node: str) -> None:
    own = LOOPBACKS[node]
    other = LOOPBACKS["router_b" if node == "router_a" else "router_a"]
    collector = RoutePresenceCollector(
        live_lab.backend.readonly_executor,
        node,
        live_lab.run_ctx,
        prefixes=(own, other),
    )
    record = collector.collect("baseline")
    assert record.normalized[f"route.{own}.present"] == "true"
    assert "connected" in record.normalized[f"route.{own}.protocols"]
    assert record.normalized[f"route.{other}.present"] == "true"
    assert record.normalized[f"route.{other}.protocols"] == "bgp"


@pytest.mark.parametrize("node", ["router_a", "router_b"])
def test_link_reachability_three_of_three(live_lab: LiveLab, node: str) -> None:
    peer = PEERS[node]
    collector = ReachabilityCollector(
        live_lab.backend.readonly_executor, node, live_lab.run_ctx, dst_ip=peer
    )
    record = collector.collect("baseline")
    assert record.normalized[f"ping.{peer}.probe_count"] == "3"
    assert record.normalized[f"ping.{peer}.success_count"] == "3"
    assert record.normalized[f"ping.{peer}.all_success"] == "true"


@pytest.mark.parametrize("node", ["router_a", "router_b"])
def test_running_config_hash_is_real(live_lab: LiveLab, node: str) -> None:
    collector = RunningConfigCollector(
        live_lab.backend.readonly_executor, node, live_lab.run_ctx
    )
    record = collector.collect("baseline")
    digest = record.normalized["config.sha256"]
    assert len(digest) == 64 and int(digest, 16) >= 0
    assert f"router bgp {LOCAL_AS[node]}" in record.raw_payload


def test_transcript_contains_reads_only(live_lab: LiveLab) -> None:
    entries = live_lab.backend.transcript.entries  # type: ignore[attr-defined]
    assert len(entries) > 0
    modes = {entry.mode for entry in entries}
    assert modes == {"read"}

# NOTE: zero-resource cleanup verification lives in
# test_frr_configured_lab.py::test_configured_lab_full_lifecycle — a second
# concurrent lab here would collide with the module-scoped lab's link subnet
# (Docker rejects two networks on the same address pool).
