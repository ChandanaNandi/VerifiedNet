"""Gate 6.2 guarantees: projection never mutates sources and never executes.

Two proofs:

* Byte-audit — every file under the verified run library is hashed before and
  after the FULL dataset pipeline (discover -> project -> split -> audit); the
  digests must be identical, and the reserved ``dataset_*`` fields on every
  authoritative IncidentRecord must stay ``None``.
* No-execution — with ``subprocess`` and the process runner sabotaged to raise,
  the same pipeline must complete: the dataset engine touches no lab, no Docker,
  no shell (ADR-0018).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from verifiednet.datasets import (
    SplitPolicy,
    assign_splits,
    audit_leakage,
    discover_verified_runs,
    project_verified_run,
)
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.failure

_POLICY = SplitPolicy(salt="gate6", train_buckets=8000, validation_buckets=1000,
                      test_buckets=1000)


def _fingerprint(root: Path) -> dict[str, str]:
    prints: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            prints[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return prints


def _build_mixed(tmp_path, run_catalog_case, catalog_sim_cls,
                 make_rejected_prefix_inputs, write_indexed_run) -> Path:
    out_root = tmp_path / "runs"
    for case_id, run_id in [("ras-ref", "run-a"), ("nr-ref", "run-b"),
                            ("pf-ref", "run-c")]:
        run_catalog_case(case_by_id(case_id), out_root, tmp_path, run_id=run_id,
                         sim=catalog_sim_cls())
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)
    return out_root


def _run_pipeline(out_root: Path) -> None:
    examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
    assigned = assign_splits(examples=examples, policy=_POLICY)
    result = audit_leakage(assigned)
    assert result.passed is True, result.findings


def test_projection_does_not_mutate_sources(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
    make_rejected_prefix_inputs, write_indexed_run,
) -> None:
    out_root = _build_mixed(tmp_path, run_catalog_case, catalog_sim_cls,
                            make_rejected_prefix_inputs, write_indexed_run)
    before = _fingerprint(out_root)

    _run_pipeline(out_root)
    # Reserved authoritative fields remain None after a full build.
    for d in discover_verified_runs(out_root):
        assert d.loaded.incident.dataset_group_id is None
        assert d.loaded.incident.dataset_split is None

    after = _fingerprint(out_root)
    assert before == after  # not a single source byte changed


def test_pipeline_executes_no_process(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
    make_rejected_prefix_inputs, write_indexed_run, monkeypatch,
) -> None:
    out_root = _build_mixed(tmp_path, run_catalog_case, catalog_sim_cls,
                            make_rejected_prefix_inputs, write_indexed_run)

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("dataset engine must not spawn a process")

    # Sabotage every plausible execution path AFTER the library is built.
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)

    _run_pipeline(out_root)  # completes -> no execution attempted
