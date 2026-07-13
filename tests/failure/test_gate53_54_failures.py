"""Gate 5.3 + 5.4 failure paths through the REAL wiring (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.common.errors import InjectFailedError, RestoreFailedError
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LifecyclePhase

pytestmark = pytest.mark.failure


# --- interface shutdown -------------------------------------------------------


def _shutdown_cmd(cmds: list[str]) -> bool:
    return cmds == ["configure terminal", "interface eth1", "shutdown"]


def _no_shutdown_cmd(cmds: list[str]) -> bool:
    return cmds == ["configure terminal", "interface eth1", "no shutdown"]


def test_iface_shutdown_failure_leaves_injecting(
    tmp_path: Path, run_ctx: RunContext, iface_sim_cls, build_iface_scenario
) -> None:
    sim = iface_sim_cls(fail_command=_shutdown_cmd)
    scenario, ledger, _, _ = build_iface_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    with pytest.raises(InjectFailedError):
        scenario.inject()
    assert ledger.current is LifecyclePhase.INJECTING
    assert sim.eth1_up is True  # link never went down


def test_iface_restore_failure_leaves_restoring(
    tmp_path: Path, run_ctx: RunContext, iface_sim_cls, build_iface_scenario
) -> None:
    sim = iface_sim_cls(fail_command=_no_shutdown_cmd)
    scenario, ledger, _, _ = build_iface_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    with pytest.raises(RestoreFailedError):
        scenario.restore()
    assert ledger.current is LifecyclePhase.RESTORING
    assert sim.eth1_up is False  # fault still live


# --- prefix withdrawal --------------------------------------------------------


def _withdraw_cmd(cmds: list[str]) -> bool:
    return any(c.startswith("no network") for c in cmds)


def _restore_cmd(cmds: list[str]) -> bool:
    return cmds and cmds[-1].startswith("network")


def test_prefix_withdraw_failure_leaves_injecting(
    tmp_path: Path, run_ctx: RunContext, prefix_sim_cls, build_prefix_scenario
) -> None:
    sim = prefix_sim_cls(fail_command=_withdraw_cmd)
    scenario, ledger, _, _ = build_prefix_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    with pytest.raises(InjectFailedError):
        scenario.inject()
    assert ledger.current is LifecyclePhase.INJECTING
    assert sim.advertised is True


def test_prefix_restore_failure_leaves_restoring(
    tmp_path: Path, run_ctx: RunContext, prefix_sim_cls, build_prefix_scenario
) -> None:
    sim = prefix_sim_cls(fail_command=_restore_cmd)
    scenario, ledger, _, _ = build_prefix_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    with pytest.raises(RestoreFailedError):
        scenario.restore()
    assert ledger.current is LifecyclePhase.RESTORING
    assert sim.advertised is False


def test_prefix_restore_after_restored_is_noop(
    tmp_path: Path, run_ctx: RunContext, prefix_sim_cls, build_prefix_scenario
) -> None:
    sim = prefix_sim_cls()
    scenario, _, _, _ = build_prefix_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    first = scenario.restore()
    n = len(sim.mutation_targets)
    second = scenario.restore()
    assert second is first
    assert len(sim.mutation_targets) == n
