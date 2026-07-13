"""Gate 6.1 dataset projection: discovery, stable group_id, unique example_id.

A small verified run library is built OFFLINE via the catalog sim
(``run_accepted_case`` + ``CatalogLabSim``), then discovered and projected. The
authoritative runs are never mutated — projection is read-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.artifacts import verify_run_index
from verifiednet.artifacts.layout import (
    EVIDENCE_BASELINE_FILE,
    INCIDENT_FILE,
    LEDGER_FILE,
    TRANSCRIPT_FILE,
)
from verifiednet.datasets import (
    DatasetExample,
    compute_group_id,
    discover_verified_runs,
    project_verified_run,
)
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.unit


def _build_library(tmp_path: Path, run_catalog_case, catalog_sim_cls, specs) -> Path:
    """specs = list of (case_id, run_id); returns the shared index root."""
    out_root = tmp_path / "runs"
    for case_id, run_id in specs:
        run_catalog_case(case_by_id(case_id), out_root, tmp_path, run_id=run_id,
                         sim=catalog_sim_cls())
    return out_root


def test_discovery_and_projection_basic(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    out_root = _build_library(tmp_path, run_catalog_case, catalog_sim_cls, [
        ("ras-ref", "run-a"), ("nr-rev", "run-b"),
    ])
    discovered = list(discover_verified_runs(out_root))
    assert len(discovered) == 2
    examples = [project_verified_run(d) for d in discovered]
    assert all(isinstance(e, DatasetExample) for e in examples)

    by_run = {e.run_id: e for e in examples}
    a = by_run["run-a"]
    assert a.acceptance_status == "accepted"
    assert a.template_id == "bgp_remote_as_mismatch"
    assert a.ground_truth_reference is not None
    # references point at the authoritative artifacts, by (run_id, path)
    assert a.incident_reference.run_id == "run-a"
    assert a.incident_reference.relative_path == INCIDENT_FILE
    assert a.transcript_reference.relative_path == TRANSCRIPT_FILE
    assert a.ledger_reference.relative_path == LEDGER_FILE
    assert a.baseline_reference.relative_path == EVIDENCE_BASELINE_FILE
    # accepted runs have onset + recovery evidence references
    assert a.onset_reference is not None and a.recovery_reference is not None
    # run_digest carried verbatim from the verified run
    assert a.run_digest == discovered[0].loaded.run_digest


def test_group_id_stable_across_repeated_runs(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    # Two runs of the SAME case (different run_id) must share ONE group_id,
    # while their run_digests differ — the crux of leakage safety.
    out_root = _build_library(tmp_path, run_catalog_case, catalog_sim_cls, [
        ("ras-ref", "run-1"), ("ras-ref", "run-2"),
    ])
    examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
    e1, e2 = sorted(examples, key=lambda e: e.run_id)
    assert e1.group_id == e2.group_id       # same scenario -> same group
    assert e1.run_id != e2.run_id
    assert e1.run_digest != e2.run_digest   # different runs -> different digests
    assert e1.example_id != e2.example_id   # example_id is per-run unique


def test_group_id_distinguishes_orientation_and_parameters(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    out_root = _build_library(tmp_path, run_catalog_case, catalog_sim_cls, [
        ("ras-ref", "r-ref"),   # router_a, 65999
        ("ras-rev", "r-rev"),   # router_b, 65998  (orientation)
        ("ras-alt", "r-alt"),   # router_a, 65123  (parameter)
    ])
    g = {e.run_id: e.group_id for e in
         (project_verified_run(d) for d in discover_verified_runs(out_root))}
    assert g["r-ref"] != g["r-rev"]   # orientation is a distinct group
    assert g["r-ref"] != g["r-alt"]   # parameter variant is a distinct group
    assert g["r-rev"] != g["r-alt"]


def test_example_id_is_deterministic_and_unique(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    out_root = _build_library(tmp_path, run_catalog_case, catalog_sim_cls, [
        ("if-ref", "run-x"), ("pf-rev", "run-y"),
    ])
    discovered = list(discover_verified_runs(out_root))
    ids1 = {project_verified_run(d).example_id for d in discovered}
    ids2 = {project_verified_run(d).example_id for d in discovered}
    assert ids1 == ids2               # deterministic
    assert len(ids1) == 2             # unique per run


def test_group_id_uses_only_stable_identity(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    # compute_group_id must not depend on run_id / run_digest / incident_id.
    out_root = _build_library(tmp_path, run_catalog_case, catalog_sim_cls, [
        ("nr-ref", "run-p"), ("nr-ref", "run-q"),
    ])
    d1, d2 = sorted(discover_verified_runs(out_root), key=lambda d: d.loaded.run_id)
    assert compute_group_id(d1.loaded) == compute_group_id(d2.loaded)
    assert d1.loaded.run_id != d2.loaded.run_id
    assert d1.loaded.run_digest != d2.loaded.run_digest


def test_projection_is_read_only_reserved_fields_stay_none(
    tmp_path: Path, run_catalog_case, catalog_sim_cls
) -> None:
    # ADR-0018 §2: projection must never write dataset_group_id/dataset_split,
    # and the authoritative runs' digests must be unchanged after a build.
    out_root = _build_library(tmp_path, run_catalog_case, catalog_sim_cls, [
        ("ras-ref", "run-i"), ("pf-ref", "run-j"),
    ])
    before = {d.loaded.run_id: d.loaded.run_digest for d in discover_verified_runs(out_root)}

    for d in discover_verified_runs(out_root):
        example = project_verified_run(d)
        # the reserved fields on the authoritative record are untouched
        assert d.loaded.incident.dataset_group_id is None
        assert d.loaded.incident.dataset_split is None
        # the example carries its OWN group/split-free identity
        assert example.group_id.startswith("grp-")

    after = {d.loaded.run_id: d.loaded.run_digest for d in discover_verified_runs(out_root)}
    assert before == after                          # no run digest changed
    assert verify_run_index(out_root).verified is True  # index still verifies
