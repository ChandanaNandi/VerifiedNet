"""Gate 6.2 rejected projection: rejected runs are EVAL-ONLY abstention examples.

A rejected precondition run carries NO fault-family label, NO ground truth, and
NO onset/recovery evidence; it projects to an ``ABSTENTION`` example. Accepted
projection (Gate 6.1) is unchanged. Status-mismatched projection fails closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets import (
    DatasetExampleKind,
    compute_group_id,
    discover_verified_runs,
    project_accepted_run,
    project_rejected_run,
    project_verified_run,
)
from verifiednet.datasets.projection import (
    AcceptedProjectionError,
    RejectedProjectionError,
)
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.unit


def _accepted_library(tmp_path, run_catalog_case, catalog_sim_cls, specs) -> Path:
    out_root = tmp_path / "runs"
    for case_id, run_id in specs:
        run_catalog_case(case_by_id(case_id), out_root, tmp_path, run_id=run_id,
                         sim=catalog_sim_cls())
    return out_root


def test_rejected_run_projects_as_abstention(
    tmp_path: Path, make_rejected_prefix_inputs, write_indexed_run,
    run_catalog_case, catalog_sim_cls,
) -> None:
    out_root = _accepted_library(tmp_path, run_catalog_case, catalog_sim_cls,
                                 [("ras-ref", "run-acc")])
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)

    by = {d.loaded.run_id: project_verified_run(d)
          for d in discover_verified_runs(out_root)}
    rej = by["run-rej"]
    assert rej.example_kind is DatasetExampleKind.ABSTENTION
    assert rej.acceptance_status == "rejected"
    # No fault-family label leaks: only source facts are carried.
    assert rej.ground_truth_reference is None
    assert rej.onset_reference is None
    assert rej.recovery_reference is None
    assert rej.failed_phase == "precondition"
    assert rej.rejection_code  # a machine-readable source code, not a label
    # It still points at its authoritative artifacts (references only).
    assert rej.incident_reference.run_id == "run-rej"
    assert rej.baseline_reference.run_id == "run-rej"


def test_rejected_runs_of_same_scenario_share_group(
    tmp_path: Path, make_rejected_prefix_inputs, write_indexed_run,
) -> None:
    # Repeated-run grouping proof (Step 22) for the abstention side: two rejected
    # runs of the SAME scenario share ONE group_id but differ per run.
    out_root = tmp_path / "runs"
    write_indexed_run(make_rejected_prefix_inputs("run-r1"), out_root)
    write_indexed_run(make_rejected_prefix_inputs("run-r2"), out_root)

    examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
    e1, e2 = sorted(examples, key=lambda e: e.run_id)
    assert e1.group_id == e2.group_id
    assert e1.run_id != e2.run_id
    assert e1.run_digest != e2.run_digest
    assert e1.example_id != e2.example_id
    assert e1.example_kind is DatasetExampleKind.ABSTENTION


def test_rejected_group_distinct_from_accepted(
    tmp_path: Path, make_rejected_prefix_inputs, write_indexed_run,
    run_catalog_case, catalog_sim_cls,
) -> None:
    out_root = _accepted_library(tmp_path, run_catalog_case, catalog_sim_cls,
                                 [("ras-ref", "run-acc"), ("pf-ref", "run-pf")])
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)
    groups = {d.loaded.run_id: compute_group_id(d.loaded)
              for d in discover_verified_runs(out_root)}
    # The rejected impossible-prefix scenario is its own leakage group.
    assert groups["run-rej"] not in {groups["run-acc"], groups["run-pf"]}


def test_accepted_projection_intact(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    out_root = _accepted_library(tmp_path, run_catalog_case, catalog_sim_cls,
                                 [("ras-ref", "run-a")])
    (d,) = tuple(discover_verified_runs(out_root))
    ex = project_accepted_run(d)
    assert ex.example_kind is DatasetExampleKind.ACCEPTED_FAULT
    assert ex.acceptance_status == "accepted"
    assert ex.ground_truth_reference is not None
    assert ex.onset_reference is not None
    assert ex.recovery_reference is not None
    assert ex.rejection_code is None
    assert ex.failed_phase is None


def test_project_accepted_on_rejected_fails_closed(
    tmp_path: Path, make_rejected_prefix_inputs, write_indexed_run,
) -> None:
    out_root = tmp_path / "runs"
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)
    (d,) = tuple(discover_verified_runs(out_root))
    with pytest.raises(AcceptedProjectionError):
        project_accepted_run(d)


def test_project_rejected_on_accepted_fails_closed(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    out_root = _accepted_library(tmp_path, run_catalog_case, catalog_sim_cls,
                                 [("ras-ref", "run-a")])
    (d,) = tuple(discover_verified_runs(out_root))
    with pytest.raises(RejectedProjectionError):
        project_rejected_run(d)
