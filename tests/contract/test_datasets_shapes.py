"""Contract tests: Gate 6.2 dataset model shapes round-trip and stay frozen.

These pin the JSON contract of the split/assignment/leakage models: canonical
round-trip equality, frozen-ness, ``extra="forbid"``, versioning, and the
model-level invariants (kind<->status, kind<->partition, fail-closed audit).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.datasets.models import (
    SPLIT_BUCKET_COUNT,
    ArtifactReference,
    AssignedDatasetExample,
    DatasetExample,
    DatasetExampleKind,
    DatasetPartition,
    LeakageAuditResult,
    LeakageFinding,
    LeakageFindingCode,
    LeakageSeverity,
    SplitPolicy,
    StableScenarioIdentity,
)

pytestmark = pytest.mark.contract

_HEX64 = "a" * 64


def _identity() -> StableScenarioIdentity:
    return StableScenarioIdentity(
        template_id="bgp_remote_as_mismatch",
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        target_node="router_a",
        target_session="a-b",
        parameters={"wrong_asn": 65999, "target_node": "router_a"},
        topology_hash=_HEX64,
        backend="frr_compose",
    )


def _example(kind: DatasetExampleKind, run_id: str = "run-1") -> DatasetExample:
    accepted = kind is DatasetExampleKind.ACCEPTED_FAULT
    incident_ref = ArtifactReference(run_id=run_id, relative_path="incident.json")
    return DatasetExample(
        example_id="ex-0123456789abcdef",
        group_id="grp-0123456789abcdef",
        example_kind=kind,
        stable_identity=_identity(),
        run_id=run_id,
        run_digest=_HEX64,
        template_id="bgp_remote_as_mismatch",
        scenario_id="bgp-remote-as-mismatch-2r-0001",
        topology_hash=_HEX64,
        backend="frr_compose",
        acceptance_status="accepted" if accepted else "rejected",
        rejection_code=None if accepted else "precondition_failed",
        failed_phase=None if accepted else "precondition",
        incident_reference=incident_ref,
        ground_truth_reference=incident_ref if accepted else None,
        transcript_reference=ArtifactReference(run_id=run_id, relative_path="transcript.jsonl"),
        ledger_reference=ArtifactReference(run_id=run_id, relative_path="ledger.jsonl"),
        baseline_reference=ArtifactReference(run_id=run_id,
                                             relative_path="evidence/baseline.json"),
        onset_reference=(ArtifactReference(run_id=run_id, relative_path="evidence/onset.json")
                         if accepted else None),
        recovery_reference=(ArtifactReference(run_id=run_id,
                                              relative_path="evidence/recovery.json")
                            if accepted else None),
        code_commit="deadbeef",
        oracle_version="1",
        source_index_digest=_HEX64,
    )


def test_split_policy_round_trip_and_frozen() -> None:
    policy = SplitPolicy(salt="s", train_buckets=8000, validation_buckets=1000,
                         test_buckets=1000)
    again = SplitPolicy.model_validate_json(policy.model_dump_json())
    assert again == policy
    assert policy.train_buckets + policy.validation_buckets + policy.test_buckets \
        == SPLIT_BUCKET_COUNT
    with pytest.raises(ValidationError):
        policy.train_buckets = 1  # frozen
    with pytest.raises(ValidationError):
        SplitPolicy(salt="s", train_buckets=8000, validation_buckets=1000,
                    test_buckets=1000, extra="no")  # extra forbidden


def test_dataset_example_kind_status_invariants() -> None:
    acc = _example(DatasetExampleKind.ACCEPTED_FAULT)
    rej = _example(DatasetExampleKind.ABSTENTION, run_id="run-2")
    assert DatasetExample.model_validate_json(acc.model_dump_json()) == acc
    assert DatasetExample.model_validate_json(rej.model_dump_json()) == rej
    # accepted example must not carry rejection facts
    with pytest.raises(ValidationError):
        DatasetExample.model_validate(acc.model_dump() | {"rejection_code": "x"})
    # rejected example must not carry a ground-truth reference
    with pytest.raises(ValidationError):
        DatasetExample.model_validate(
            rej.model_dump() | {"ground_truth_reference": acc.incident_reference.model_dump()}
        )


def test_assigned_example_kind_partition_invariants() -> None:
    acc = _example(DatasetExampleKind.ACCEPTED_FAULT)
    rej = _example(DatasetExampleKind.ABSTENTION, run_id="run-2")
    # valid bindings
    AssignedDatasetExample(example=acc, partition=DatasetPartition.TRAIN,
                           split_policy_id="split-1")
    AssignedDatasetExample(example=rej, partition=DatasetPartition.ABSTENTION,
                           split_policy_id="abstention-v1")
    # abstention example may not be placed in a trainable split
    with pytest.raises(ValidationError):
        AssignedDatasetExample(example=rej, partition=DatasetPartition.TRAIN,
                               split_policy_id="split-1")
    # accepted example may not be placed in the abstention partition
    with pytest.raises(ValidationError):
        AssignedDatasetExample(example=acc, partition=DatasetPartition.ABSTENTION,
                               split_policy_id="split-1")


def test_leakage_audit_result_fail_closed_contract() -> None:
    err = LeakageFinding(code=LeakageFindingCode.DUPLICATE_EXAMPLE_ID,
                         severity=LeakageSeverity.ERROR, detail="d")
    with pytest.raises(ValidationError):
        LeakageAuditResult(passed=True, findings=(err,))
    failed = LeakageAuditResult(passed=False, findings=(err,))
    assert LeakageAuditResult.model_validate_json(failed.model_dump_json()) == failed
    assert failed.errors == (err,)
