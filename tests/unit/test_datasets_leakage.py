"""Gate 6.2 leakage audit: independent re-derivation, fail-closed on ERROR."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.datasets import (
    AssignedDatasetExample,
    DatasetPartition,
    LeakageAuditResult,
    LeakageFinding,
    LeakageFindingCode,
    LeakageSeverity,
    SplitPolicy,
    assign_splits,
    audit_leakage,
    discover_verified_runs,
    project_verified_run,
    split_policy_id,
)
from verifiednet.orchestrator.catalog import case_by_id

pytestmark = pytest.mark.unit

_POLICY = SplitPolicy(salt="gate6", train_buckets=8000, validation_buckets=1000,
                      test_buckets=1000)


def _examples(tmp_path, run_catalog_case, catalog_sim_cls, specs):
    out_root = tmp_path / "runs"
    for case_id, run_id in specs:
        run_catalog_case(case_by_id(case_id), out_root, tmp_path, run_id=run_id,
                         sim=catalog_sim_cls())
    return [project_verified_run(d) for d in discover_verified_runs(out_root)]


def test_clean_library_passes(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
    make_rejected_prefix_inputs, write_indexed_run,
) -> None:
    out_root = tmp_path / "runs"
    for case_id, run_id in [("ras-ref", "run-a"), ("nr-rev", "run-b"),
                            ("if-ref", "run-c"), ("pf-ref", "run-d")]:
        run_catalog_case(case_by_id(case_id), out_root, tmp_path, run_id=run_id,
                         sim=catalog_sim_cls())
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)
    examples = [project_verified_run(d) for d in discover_verified_runs(out_root)]
    result = audit_leakage(assign_splits(examples=examples, policy=_POLICY))
    assert result.passed is True
    assert result.errors == ()


def test_group_spans_splits_is_error(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    # Two runs of one scenario forced into different partitions -> hard leak.
    e1, e2 = sorted(
        _examples(tmp_path, run_catalog_case, catalog_sim_cls,
                  [("ras-ref", "run-1"), ("ras-ref", "run-2")]),
        key=lambda e: e.run_id,
    )
    assert e1.group_id == e2.group_id
    pid = split_policy_id(_POLICY)
    assigned = (
        AssignedDatasetExample(example=e1, partition=DatasetPartition.TRAIN,
                               split_policy_id=pid),
        AssignedDatasetExample(example=e2, partition=DatasetPartition.TEST,
                               split_policy_id=pid),
    )
    result = audit_leakage(assigned)
    assert result.passed is False
    codes = {f.code for f in result.errors}
    assert LeakageFindingCode.GROUP_SPANS_SPLITS in codes


def test_duplicate_example_is_error(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    (e,) = _examples(tmp_path, run_catalog_case, catalog_sim_cls, [("ras-ref", "run-r")])
    pid = split_policy_id(_POLICY)
    dup = (
        AssignedDatasetExample(example=e, partition=DatasetPartition.TRAIN,
                               split_policy_id=pid),
        AssignedDatasetExample(example=e, partition=DatasetPartition.TRAIN,
                               split_policy_id=pid),
    )
    result = audit_leakage(dup)
    codes = {f.code for f in result.errors}
    assert LeakageFindingCode.DUPLICATE_EXAMPLE_ID in codes
    assert LeakageFindingCode.DUPLICATE_SOURCE_RUN in codes
    assert result.passed is False


def test_tampered_group_id_is_error(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    (e,) = _examples(tmp_path, run_catalog_case, catalog_sim_cls, [("ras-ref", "run-r")])
    forged = "grp-ffffffffffffffff"
    assert forged != e.group_id
    tampered = e.model_copy(update={"group_id": forged})  # no re-validation
    assigned = (AssignedDatasetExample(example=tampered,
                                       partition=DatasetPartition.TRAIN,
                                       split_policy_id=split_policy_id(_POLICY)),)
    result = audit_leakage(assigned)
    codes = {f.code for f in result.errors}
    assert LeakageFindingCode.GROUP_ID_MISMATCH in codes
    assert result.passed is False


def test_tampered_example_id_is_error(
    tmp_path: Path, run_catalog_case, catalog_sim_cls,
) -> None:
    (e,) = _examples(tmp_path, run_catalog_case, catalog_sim_cls, [("ras-ref", "run-r")])
    forged = "ex-ffffffffffffffff"
    assert forged != e.example_id
    tampered = e.model_copy(update={"example_id": forged})
    assigned = (AssignedDatasetExample(example=tampered,
                                       partition=DatasetPartition.TRAIN,
                                       split_policy_id=split_policy_id(_POLICY)),)
    result = audit_leakage(assigned)
    codes = {f.code for f in result.errors}
    assert LeakageFindingCode.EXAMPLE_ID_MISMATCH in codes


def test_invalid_abstention_assignment_defense_in_depth(
    tmp_path: Path, make_rejected_prefix_inputs, write_indexed_run,
) -> None:
    out_root = tmp_path / "runs"
    write_indexed_run(make_rejected_prefix_inputs("run-rej"), out_root)
    (d,) = tuple(discover_verified_runs(out_root))
    abstention = project_verified_run(d)
    # Bypass the model validator (model_construct) to simulate a corrupt binding:
    # an abstention example placed in a TRAIN split. The audit must still catch it.
    bad = AssignedDatasetExample.model_construct(
        example=abstention, partition=DatasetPartition.TRAIN, split_policy_id="x",
    )
    result = audit_leakage((bad,))
    codes = {f.code for f in result.errors}
    assert LeakageFindingCode.INVALID_ABSTENTION_ASSIGNMENT in codes
    assert result.passed is False


def test_audit_result_cannot_pass_with_error() -> None:
    err = LeakageFinding(code=LeakageFindingCode.GROUP_SPANS_SPLITS,
                         severity=LeakageSeverity.ERROR, detail="x")
    with pytest.raises(ValidationError):
        LeakageAuditResult(passed=True, findings=(err,))
    # But a passing result with only INFO findings is allowed.
    info = LeakageFinding(code=LeakageFindingCode.ORIENTATION_SIBLING,
                          severity=LeakageSeverity.INFO, detail="sibling")
    ok = LeakageAuditResult(passed=True, findings=(info,))
    assert ok.passed is True
    assert ok.errors == ()
