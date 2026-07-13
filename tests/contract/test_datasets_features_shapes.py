"""Contract tests: Gate 6.2 Part 4 separation models round-trip and stay frozen."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from verifiednet.datasets.features import (
    FEATURE_ALLOWLIST_V1,
    AbstentionLabels,
    AcceptedLabels,
    DatasetFeatures,
    DatasetLabels,
    DatasetTraceMetadata,
    FeatureEvidenceRef,
    FeaturePolicy,
    LabelPolicy,
    SeparatedDatasetExample,
)
from verifiednet.datasets.models import ArtifactReference, DatasetExampleKind, DatasetPartition

pytestmark = pytest.mark.contract

_INCIDENT = ArtifactReference(run_id="run-a", relative_path="incident.json")
_RECOVERY = ArtifactReference(run_id="run-a", relative_path="evidence/recovery.json")
_LABELS_ADAPTER = TypeAdapter(DatasetLabels)


def _features(onset: bool = True) -> DatasetFeatures:
    return DatasetFeatures(
        feature_policy_id=FeaturePolicy().policy_id,
        topology_hash="a" * 64,
        backend="frr_compose",
        baseline_evidence=FeatureEvidenceRef(relative_path="evidence/baseline.json"),
        onset_evidence=(FeatureEvidenceRef(relative_path="evidence/onset.json")
                        if onset else None),
    )


def _accepted_labels() -> AcceptedLabels:
    return AcceptedLabels(
        label_policy_id=LabelPolicy().policy_id, fault_family="bgp_remote_as_mismatch",
        scenario_id="s1", ground_truth_reference=_INCIDENT, recovery_reference=_RECOVERY,
    )


def _abstention_labels() -> AbstentionLabels:
    return AbstentionLabels(
        label_policy_id=LabelPolicy().policy_id,
        rejection_code="precondition_failed", failed_phase="precondition",
    )


def _trace(kind: DatasetExampleKind, partition: DatasetPartition) -> DatasetTraceMetadata:
    return DatasetTraceMetadata(
        example_id="ex-0123456789abcdef", group_id="grp-0123456789abcdef",
        run_id="run-a", run_digest="b" * 64, example_kind=kind, partition=partition,
        split_policy_id="split-1", dataset_version="v1", source_index_digest="c" * 64,
        example_schema_version=1, incident_reference=_INCIDENT,
    )


def test_features_frozen_and_extra_forbid() -> None:
    f = _features()
    assert DatasetFeatures.model_validate_json(f.model_dump_json()) == f
    with pytest.raises(ValidationError):
        f.backend = "x"  # frozen
    with pytest.raises(ValidationError):
        DatasetFeatures.model_validate(f.model_dump() | {"ground_truth_reference": {}})


def test_features_expose_no_label_fields() -> None:
    fields = set(DatasetFeatures.model_fields)
    for forbidden in ("fault_family", "ground_truth_reference", "scenario_id",
                      "rejection_code", "failed_phase", "example_id", "group_id",
                      "partition", "split_policy_id", "run_id", "run_digest"):
        assert forbidden not in fields
    # the model's evidence/context fields are exactly the v1 allowlist
    allow = {n for n in DatasetFeatures.model_fields
             if n not in ("schema_version", "feature_policy_id")}
    assert allow == {"topology_hash", "backend", "baseline_evidence", "onset_evidence"}
    # the allowlist constant maps onto the same permitted names
    assert set(FEATURE_ALLOWLIST_V1) <= (allow | {"baseline_evidence", "onset_evidence"})


def test_labels_discriminated_union_round_trip() -> None:
    acc = _accepted_labels()
    rej = _abstention_labels()
    assert _LABELS_ADAPTER.validate_json(_LABELS_ADAPTER.dump_json(acc)) == acc
    assert _LABELS_ADAPTER.validate_json(_LABELS_ADAPTER.dump_json(rej)) == rej
    assert acc.kind == "accepted_fault"
    assert rej.kind == "abstention"


def test_separated_requires_matching_kind() -> None:
    # valid accepted
    SeparatedDatasetExample(
        features=_features(True), labels=_accepted_labels(),
        trace=_trace(DatasetExampleKind.ACCEPTED_FAULT, DatasetPartition.TRAIN))
    # valid abstention
    SeparatedDatasetExample(
        features=_features(False), labels=_abstention_labels(),
        trace=_trace(DatasetExampleKind.ABSTENTION, DatasetPartition.ABSTENTION))
    # accepted trace with abstention labels -> rejected
    with pytest.raises(ValidationError):
        SeparatedDatasetExample(
            features=_features(True), labels=_abstention_labels(),
            trace=_trace(DatasetExampleKind.ACCEPTED_FAULT, DatasetPartition.TRAIN))
    # abstention trace with accepted labels -> rejected
    with pytest.raises(ValidationError):
        SeparatedDatasetExample(
            features=_features(False), labels=_accepted_labels(),
            trace=_trace(DatasetExampleKind.ABSTENTION, DatasetPartition.ABSTENTION))
    # accepted in abstention partition -> rejected
    with pytest.raises(ValidationError):
        SeparatedDatasetExample(
            features=_features(True), labels=_accepted_labels(),
            trace=_trace(DatasetExampleKind.ACCEPTED_FAULT, DatasetPartition.ABSTENTION))
    # accepted with no onset feature -> rejected
    with pytest.raises(ValidationError):
        SeparatedDatasetExample(
            features=_features(False), labels=_accepted_labels(),
            trace=_trace(DatasetExampleKind.ACCEPTED_FAULT, DatasetPartition.TRAIN))
    # abstention carrying onset feature -> rejected
    with pytest.raises(ValidationError):
        SeparatedDatasetExample(
            features=_features(True), labels=_abstention_labels(),
            trace=_trace(DatasetExampleKind.ABSTENTION, DatasetPartition.ABSTENTION))


def test_schema_versions_are_required_exact() -> None:
    f = _features()
    with pytest.raises(ValidationError):
        DatasetFeatures.model_validate(f.model_dump() | {"schema_version": 2})


def test_policy_ids_stable_and_config_sensitive() -> None:
    assert FeaturePolicy().policy_id == FeaturePolicy().policy_id
    assert LabelPolicy().policy_id == LabelPolicy().policy_id
    assert FeaturePolicy(include_onset=False).policy_id != FeaturePolicy().policy_id
    # the allowlist is locked to the canonical v1 set
    with pytest.raises(ValidationError):
        FeaturePolicy(allowed_fields=("backend",))


def test_trace_metadata_is_separate_type() -> None:
    t = _trace(DatasetExampleKind.ACCEPTED_FAULT, DatasetPartition.TRAIN)
    assert DatasetTraceMetadata.model_validate_json(t.model_dump_json()) == t
    # trace carries identity that features never do
    assert "example_id" in DatasetTraceMetadata.model_fields
    assert "example_id" not in DatasetFeatures.model_fields
