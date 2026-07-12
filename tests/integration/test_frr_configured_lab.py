"""Live integration: the CONFIGURED two-router lab boots, converges, cleans up.

Full lifecycle against a real Docker daemon and the approved pinned image:
start (with generated config delivered read-only), health, real BGP
convergence, transcript purity (read-only), verified teardown with independent
zero-resource checks. Teardown runs in ``finally`` even when assertions fail.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.labs.frr.backend import FrrComposeBackend
from verifiednet.labs.frr.convergence import wait_for_bgp_established
from verifiednet.labs.frr.topologies import PINNED_FRR_IMAGE, two_router_frr_topology
from verifiednet.runtime.results import ExecStatus

pytestmark = pytest.mark.integration


def test_configured_lab_full_lifecycle(
    tmp_path: Path,
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    run_id = unique_run_id("it-lab")
    topology = two_router_frr_topology(image_ref=PINNED_FRR_IMAGE)
    run_ctx = RunContext(run_id)
    backend = FrrComposeBackend(topology, run_ctx, work_dir=tmp_path)
    project = backend.project_name
    try:
        backend.start()

        # rendered per-run build artifacts exist (configs live in the build dir)
        assert (tmp_path / "docker-compose.yml").is_file()
        assert (tmp_path / "daemons").is_file()
        assert (tmp_path / "router_a" / "frr.conf").is_file()
        assert (tmp_path / "router_b" / "frr.conf").is_file()

        # both services healthy (running + answering read-only vtysh)
        assert backend.health_check() is True

        # real BGP convergence, bounded, two consecutive confirmations
        report = wait_for_bgp_established(backend.readonly_executor, topology)
        assert report.converged is True
        assert report.attempts >= 2
        assert set(report.last_states.values()) == {"Established"}

        # the generated configuration was actually applied inside the routers
        result = backend.execute_readonly(
            "router_a", ["vtysh", "-c", "show running-config"], 10.0
        )
        assert result.status is ExecStatus.OK
        assert "router bgp 65001" in result.stdout
        assert "neighbor 172.30.0.2 remote-as 65002" in result.stdout
        result_b = backend.execute_readonly(
            "router_b", ["vtysh", "-c", "show running-config"], 10.0
        )
        assert "router bgp 65002" in result_b.stdout

        # transcript purity: every entry is read-mode; zero mutation entries
        entries = backend.transcript.entries  # type: ignore[attr-defined]
        assert len(entries) > 0
        assert all(entry.mode == "read" for entry in entries)
    finally:
        backend.stop()

    # independent host-side proof of zero remaining resources
    assert project_containers(project) == []
    assert project_networks(project) == []
