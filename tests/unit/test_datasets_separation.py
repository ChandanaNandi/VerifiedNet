"""Gate 6.2 Part 4 separation: features/labels/trace, policies, narrow loaders."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets import (
    AbstentionLabels,
    AcceptedLabels,
    DatasetFeatures,
    DatasetPartition,
    FeaturePolicy,
    LabelPolicy,
    audit_separated_example,
    build_prepared,
    load_features,
    load_prepared,
    separate_dataset,
    verify_prepared,
    write_prepared,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c"),
        ("pf-ref", "run-d")]


def test_accepted_separation_shape(tmp_path: Path, separated_pipeline) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    by = {s.trace.run_id: s for s in ctx.separated}
    acc = by["run-a"]
    assert isinstance(acc.labels, AcceptedLabels)
    assert acc.trace.example_kind.value == "accepted_fault"
    assert acc.trace.partition in {DatasetPartition.TRAIN, DatasetPartition.VALIDATION,
                                   DatasetPartition.TEST}
    # features: allowlist only, onset present, no identity/label
    assert acc.features.onset_evidence is not None
    assert acc.features.baseline_evidence.relative_path == "evidence/baseline.json"
    dumped = acc.features.model_dump()
    assert set(dumped) == {"schema_version", "feature_policy_id", "topology_hash",
                           "backend", "baseline_evidence", "onset_evidence"}
    # labels carry the diagnosis target + authoritative references
    assert acc.labels.fault_family  # source template_id
    assert acc.labels.scenario_id
    assert acc.labels.ground_truth_reference.relative_path == "incident.json"
    # audit clean
    assert audit_separated_example(acc).passed


def test_abstention_separation_shape(tmp_path: Path, separated_pipeline) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    rej = next(s for s in ctx.separated if s.trace.run_id == "run-rej")
    assert isinstance(rej.labels, AbstentionLabels)
    assert rej.trace.partition is DatasetPartition.ABSTENTION
    assert rej.features.onset_evidence is None
    assert rej.labels.expected_outcome == "abstain"
    assert rej.labels.rejection_code
    assert rej.labels.failed_phase == "precondition"
    # no fault-family label smuggled into an abstention example
    assert not hasattr(rej.labels, "fault_family")
    assert audit_separated_example(rej).passed


def test_policy_ids_are_deterministic() -> None:
    assert FeaturePolicy().policy_id == FeaturePolicy().policy_id
    assert LabelPolicy().policy_id == LabelPolicy().policy_id
    assert FeaturePolicy().policy_id.startswith("feat-")
    assert LabelPolicy().policy_id.startswith("label-")
    # a different configuration changes the id
    assert FeaturePolicy(include_onset=False).policy_id != FeaturePolicy().policy_id


def test_transformation_is_ordered_and_order_independent(
    tmp_path: Path, separated_pipeline,
) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ids = [s.trace.example_id for s in ctx.separated]
    assert ids == sorted(ids)  # sorted by example_id
    reversed_sep = separate_dataset(
        tuple(reversed(ctx.loaded.examples)),
        feature_policy=ctx.feature_policy, label_policy=ctx.label_policy,
        dataset_version="v1", source_index_digest=ctx.source_index_digest,
    )
    assert [s.trace.example_id for s in reversed_sep] == ids


def test_model_facing_loader_returns_features_only(
    tmp_path: Path, separated_pipeline,
) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    prep = build_prepared(ctx.separated, feature_policy=ctx.feature_policy,
                          label_policy=ctx.label_policy, dataset_version="v1",
                          source_index_digest=ctx.source_index_digest,
                          source_dataset_digest=ctx.dataset.manifest.dataset_digest)
    write_prepared(prep, tmp_path / "prepared")
    feats = load_features(tmp_path / "prepared")
    total = sum(len(v) for v in feats.values())
    assert total == 5
    for items in feats.values():
        for it in items:
            assert isinstance(it, DatasetFeatures)  # ONLY features, never labels/trace


def test_evaluator_facing_loader_returns_all_layers(
    tmp_path: Path, separated_pipeline,
) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    prep = build_prepared(ctx.separated, feature_policy=ctx.feature_policy,
                          label_policy=ctx.label_policy, dataset_version="v1",
                          source_index_digest=ctx.source_index_digest,
                          source_dataset_digest=ctx.dataset.manifest.dataset_digest)
    written = write_prepared(prep, tmp_path / "prepared")
    assert verify_prepared(tmp_path / "prepared").verified is True
    loaded = load_prepared(tmp_path / "prepared")
    assert len(loaded.examples) == 5
    assert loaded.manifest.prepared_digest == written.prepared_digest
    # each reconstructed example has all three layers and passes the leak audit
    for s in loaded.examples:
        assert audit_separated_example(s).passed
    assert loaded.manifest.accepted_count == 4
    assert loaded.manifest.rejected_count == 1


def test_reproducibility_prepared_bytes(tmp_path: Path, separated_pipeline) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    kw = dict(feature_policy=ctx.feature_policy, label_policy=ctx.label_policy,
              dataset_version="v1", source_index_digest=ctx.source_index_digest,
              source_dataset_digest=ctx.dataset.manifest.dataset_digest)
    p1 = build_prepared(ctx.separated, **kw)
    p2 = build_prepared(ctx.separated, **kw)
    assert p1.output_files() == p2.output_files()
    assert p1.manifest == p2.manifest
    assert p1.manifest.prepared_digest == p2.manifest.prepared_digest
    write_prepared(p1, tmp_path / "d1")
    write_prepared(p2, tmp_path / "d2")
    from verifiednet.datasets import EXPECTED_PREPARED_FILES, PREPARED_MANIFEST_FILE
    for rel in sorted(EXPECTED_PREPARED_FILES | {PREPARED_MANIFEST_FILE}):
        assert (tmp_path / "d1" / rel).read_bytes() == (tmp_path / "d2" / rel).read_bytes()
