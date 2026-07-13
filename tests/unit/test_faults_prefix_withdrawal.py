"""Offline proof of the Gate 5.4 prefix-withdrawal wiring (no Docker).

The shared ``PrefixLabSim`` (tests/conftest.py) keeps the BGP session
Established at all times and only toggles the target's advertised loopback in
the peer's route table — the routing-intent family. Restoration re-advertises
with NO forced reset. Drives the REAL scenario + executor + provider + plans.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LifecyclePhase

pytestmark = pytest.mark.unit

TARGET_LOOPBACK = "10.255.0.1/32"


def test_full_prefix_withdrawal_slice_offline(
    tmp_path: Path, run_ctx: RunContext, prefix_sim_cls, build_prefix_scenario
) -> None:
    sim = prefix_sim_cls()
    scenario, ledger, _provider, _backend = build_prefix_scenario(sim, run_ctx, tmp_path)

    baseline_cfg = sim._running_config("router_a")
    pre = scenario.validate_preconditions()
    fault = scenario.inject()
    assert sim.advertised is False
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
    assert fault.parameter_name == "network"
    assert fault.before_value == f"{TARGET_LOOPBACK} advertised"
    assert fault.after_value == "withdrawn"
    # the signature of this family: NO forced reset, session never dropped
    assert restoration.forced_reset_used is False
    assert restoration.forced_reset_command == ""
    assert all(r.committable for r in (*pre, *onset, *recovery))
    # onset: route absent on the peer AND session Established (invariant)
    absent = [r for r in onset if r.check_id.startswith("route_absent")]
    established = [r for r in onset if r.check_id.startswith("bgp_established")]
    assert absent and absent[0].observed == ("false",)
    assert established and all(r.observed == ("Established",) for r in established)
    # byte-identical config restore
    assert sim._running_config("router_a") == baseline_cfg
    assert any("config_unchanged:router_a" in r.check_id for r in recovery)


def test_only_one_mutation_pair_no_clear(
    tmp_path: Path, run_ctx: RunContext, prefix_sim_cls, build_prefix_scenario
) -> None:
    # withdraw + restore = exactly two mutation pairs; no clear bgp shape exists
    # for this family.
    sim = prefix_sim_cls()
    scenario, _, _, backend = build_prefix_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    scenario.restore()
    scenario.verify_recovery()
    muts = [e for e in backend.transcript.entries if e.mode == "mutation"]  # type: ignore[attr-defined]
    pend = [e for e in muts if e.stage == "pending"]
    assert len(pend) == 2
    assert all(e.target == "router_a" for e in muts)
