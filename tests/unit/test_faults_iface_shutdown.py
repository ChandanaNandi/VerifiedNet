"""Offline proof of the Gate 5.3 interface-shutdown wiring (no Docker).

The shared ``IfaceLabSim`` (tests/conftest.py) reproduces the probe-verified FRR
behavior: admin-down drives oper-down, the target session leaves Established,
ping fails, the peer-loopback route withdraws, and a ``shutdown`` line appears
in the running config; ``no shutdown`` (+ clear) restores everything with a
byte-identical config. Drives the REAL scenario + executor + provider + plans.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LifecyclePhase

pytestmark = pytest.mark.unit


def test_full_iface_shutdown_slice_offline(
    tmp_path: Path, run_ctx: RunContext, iface_sim_cls, build_iface_scenario
) -> None:
    sim = iface_sim_cls()
    scenario, ledger, _provider, _backend = build_iface_scenario(sim, run_ctx, tmp_path)

    baseline_cfg = sim._running_config("router_a")
    pre = scenario.validate_preconditions()
    fault = scenario.inject()
    assert sim.eth1_up is False  # link actually down
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
    assert fault.parameter_name == "admin_state"
    assert (fault.before_value, fault.after_value) == ("up", "down")
    assert restoration.forced_reset_used is True
    assert all(r.committable for r in (*pre, *onset, *recovery))
    # both admin AND oper down proven at onset (the probe decision rule)
    admin = [r for r in onset if r.check_id.startswith("iface_admin_down")]
    oper = [r for r in onset if r.check_id.startswith("iface_oper_down")]
    assert admin and admin[0].observed == ("down",)
    assert oper and oper[0].observed == ("down",)
    # reachability affirmatively failed
    reach = [r for r in onset if r.check_id.startswith("reachability_fails")]
    assert reach and reach[0].observed == ("false",)
    # byte-identical config restore
    assert sim._running_config("router_a") == baseline_cfg
    assert any("config_unchanged:router_a" in r.check_id for r in recovery)


def test_onset_is_target_side_only(
    tmp_path: Path, run_ctx: RunContext, iface_sim_cls, build_iface_scenario
) -> None:
    # Probe-verified: the peer cannot observe the link loss during onset, so the
    # onset bundle must not carry peer BGP/route evidence — only its config.
    from verifiednet.schemas.evidence import Phase

    sim = iface_sim_cls()
    scenario, _, provider, _ = build_iface_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    bundle = provider(Phase.ONSET)[0]
    peer_metrics = {
        key
        for record in bundle.records
        if record.source.target == "router_b"
        for key in record.normalized
    }
    assert peer_metrics <= {"config.sha256"} or all(
        k == "config.sha256" for k in peer_metrics
    )
