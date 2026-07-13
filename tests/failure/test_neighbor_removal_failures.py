"""Gate 5.2 neighbor-removal failure paths through the REAL wiring (offline).

Each failure must be loud and leave the ledger visibly at the failed phase; no
accepted state may be reachable unless recovery is fully verified. Uses the
shared ``NeighborLabSim`` (tests/conftest.py) with injectable failures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.common.errors import (
    InjectFailedError,
    PhaseTransitionError,
    RestoreFailedError,
)
from verifiednet.common.runctx import RunContext
from verifiednet.faults.ledger import LifecyclePhase

pytestmark = pytest.mark.failure


def _remove_cmd(cmds: list[str]) -> bool:
    return any(c.startswith("no neighbor") for c in cmds)


def _restore_cmd(cmds: list[str]) -> bool:
    return any(c.startswith("neighbor") and "remote-as" in c for c in cmds)


def test_remove_failure_leaves_ledger_injecting(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls(fail_command=_remove_cmd)
    scenario, ledger, _, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    with pytest.raises(InjectFailedError):
        scenario.inject()
    assert ledger.current is LifecyclePhase.INJECTING
    assert sim.neighbor_present is True  # nothing was actually removed
    # the mutation-failure recovery path stays open
    scenario.restore()
    assert ledger.current is LifecyclePhase.RESTORED


def test_restore_failure_leaves_ledger_restoring(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls(fail_command=_restore_cmd)
    scenario, ledger, _, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    with pytest.raises(RestoreFailedError):
        scenario.restore()
    assert ledger.current is LifecyclePhase.RESTORING
    assert sim.neighbor_present is False  # fault still live and visible


def test_missing_activation_yields_noncommittable_recovery(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    # A recreated-but-never-activated neighbor re-establishes the session but
    # exchanges no routes: the recovery ROUTE checks must not be committable,
    # so the acceptance gate (all-committable) refuses the run.
    sim = neighbor_sim_cls(ignore_activate=True)
    scenario, _ledger, _, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    scenario.restore()
    recovery = scenario.verify_recovery()
    route_results = [r for r in recovery if r.check_id.startswith("route_present")]
    assert route_results and not any(r.committable for r in route_results)
    # config hash also differs (activate line missing) — a second loud proof
    config_results = [r for r in recovery if "config_unchanged" in r.check_id]
    assert config_results and not any(r.committable for r in config_results)
    assert not all(r.committable for r in recovery)


def test_double_inject_refused(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls()
    scenario, _, _, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    with pytest.raises(PhaseTransitionError):
        scenario.inject()


def test_restore_after_restored_is_mutationless_noop(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls()
    scenario, _, _, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    first = scenario.restore()
    count_after_restore = len(sim.mutation_targets)
    second = scenario.restore()
    assert second is first
    assert len(sim.mutation_targets) == count_after_restore  # no extra commands


def test_peer_never_mutated_even_on_failures(
    tmp_path: Path, run_ctx: RunContext, neighbor_sim_cls: type, build_neighbor_scenario
) -> None:
    sim = neighbor_sim_cls(fail_command=_restore_cmd)
    scenario, _, _, _ = build_neighbor_scenario(sim, run_ctx, tmp_path)
    scenario.validate_preconditions()
    scenario.inject()
    scenario.verify_onset()
    with pytest.raises(RestoreFailedError):
        scenario.restore()
    assert set(sim.mutation_targets) <= {"router_a"}
