"""Offline proof of the Gate 5.2 neighbor-removal wiring (no Docker).

The shared ``NeighborLabSim`` (tests/conftest.py) models the FRR behavior that
matters for this family: ``no neighbor`` removes the peer object (it disappears
from the BGP summary), the session drops on the peer side, routes withdraw, and
the running config loses the neighbor + activate lines. Restoration recreates
the neighbor; the sim's canonical config text returns byte-identical, so the
``config_unchanged`` recovery check proves hash equality exactly as live FRR
must. Drives the REAL scenario + REAL MutationExecutor + REAL evidence provider
with the REAL family phase plans.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LifecyclePhase
from verifiednet.schemas.evidence import Phase

pytestmark = pytest.mark.unit

PEER_IP = "172.30.0.2"
CORRECT_AS = 65002


def test_full_neighbor_removal_slice_offline(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls()
    scenario, ledger, _provider, backend = build_neighbor_scenario(sim, run_ctx, tmp_path)

    baseline_cfg = sim._running_config("router_a")
    pre = scenario.validate_preconditions()
    fault = scenario.inject()
    assert sim.neighbor_present is False  # object actually removed
    onset = scenario.verify_onset()
    restoration = scenario.restore()
    recovery = scenario.verify_recovery()

    assert [r.phase for r in ledger.records] == [
        LifecyclePhase.PRECHECKED,
        LifecyclePhase.INJECTING,
        LifecyclePhase.INJECTED,
        LifecyclePhase.ONSET_VERIFIED,
        LifecyclePhase.RESTORING,
        LifecyclePhase.RESTORED,
        LifecyclePhase.RECOVERY_VERIFIED,
    ]
    assert set(sim.mutation_targets) == {"router_a"}
    assert fault.parameter_name == "neighbor"
    assert fault.before_value == f"{PEER_IP} remote-as {CORRECT_AS}"
    assert fault.after_value == "removed"
    assert restoration.forced_reset_used is True
    # every verdict on the accepted path is committable
    assert all(r.committable for r in (*pre, *onset, *recovery))
    # byte-identical config restore (the sim's canonical serialization)
    assert sim._running_config("router_a") == baseline_cfg
    # the recovery check-set includes the byte-identical config proof
    assert any("config_unchanged:router_a" in r.check_id for r in recovery)
    # onset proved AFFIRMATIVE absence, not missing-metric INSUFFICIENT
    absent = [r for r in onset if r.check_id.startswith("bgp_peer_absent")]
    assert absent and absent[0].observed == ("false",)
    # transcript pairing: remove, restore, clear = 3 pairs, all on router_a
    entries = [e for e in backend.transcript.entries if e.mode == "mutation"]  # type: ignore[attr-defined]
    pend = [e for e in entries if e.stage == "pending"]
    done = [e for e in entries if e.stage == "completed"]
    assert len(pend) == len(done) == 3
    assert all(e.target == "router_a" for e in entries)


def test_onset_evidence_carries_affirmative_absence(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls()
    scenario, _, provider, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    bundle = provider(Phase.ONSET)[0]
    values = {
        key: value
        for record in bundle.records
        for key, value in record.normalized.items()
        if key == f"bgp.peer.{PEER_IP}.present"
    }
    assert values == {f"bgp.peer.{PEER_IP}.present": "false"}


def test_recovery_bundle_has_target_config_only(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    # config.sha256 must be observed from EXACTLY one node at recovery so the
    # byte-identical check sees a single, uncontradicted observation.
    sim = neighbor_sim_cls()
    _, _, provider, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    bundle = provider(Phase.RECOVERY)[0]
    config_targets = [
        record.source.target
        for record in bundle.records
        if "config.sha256" in record.normalized
    ]
    assert config_targets == ["router_a"]


def test_onset_bundle_has_peer_config_only(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls()
    _, _, provider, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    bundle = provider(Phase.ONSET)[0]
    config_targets = [
        record.source.target
        for record in bundle.records
        if "config.sha256" in record.normalized
    ]
    assert config_targets == ["router_b"]
