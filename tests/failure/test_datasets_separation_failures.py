"""Gate 6.2 Part 4 separation failures: fail-closed on invalid data and leakage."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets import (
    DatasetFeatures,
    FeaturePolicy,
    LabelPolicy,
    build_prepared,
    load_features,
    load_prepared,
    separate_dataset,
    separate_example,
    verify_prepared,
    write_prepared,
)
from verifiednet.datasets.feature_leakage import (
    FeatureLeakageCode,
    audit_feature_payload,
    audit_separated_example,
)
from verifiednet.datasets.prepared import PREPARED_MANIFEST_FILE, PreparedError
from verifiednet.datasets.separation import SeparationError

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _accepted(ctx):
    return next(a for a in ctx.loaded.examples
               if a.example.acceptance_status == "accepted")


def _rejected(ctx):
    return next(a for a in ctx.loaded.examples
               if a.example.acceptance_status == "rejected")


def _sep_kwargs(ctx):
    return dict(feature_policy=ctx.feature_policy, label_policy=ctx.label_policy,
                dataset_version="v1", source_index_digest=ctx.source_index_digest)


def test_accepted_missing_ground_truth_fails(tmp_path: Path, separated_pipeline) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    a = _accepted(ctx)
    tampered = a.model_copy(update={
        "example": a.example.model_copy(update={"ground_truth_reference": None})
    })
    with pytest.raises(SeparationError):
        separate_example(tampered, **_sep_kwargs(ctx))


def test_accepted_missing_recovery_fails(tmp_path: Path, separated_pipeline) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    a = _accepted(ctx)
    tampered = a.model_copy(update={
        "example": a.example.model_copy(update={"recovery_reference": None})
    })
    with pytest.raises(SeparationError):
        separate_example(tampered, **_sep_kwargs(ctx))


def test_abstention_missing_rejection_facts_fails(
    tmp_path: Path, separated_pipeline,
) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    r = _rejected(ctx)
    tampered = r.model_copy(update={
        "example": r.example.model_copy(update={"rejection_code": None})
    })
    with pytest.raises(SeparationError):
        separate_example(tampered, **_sep_kwargs(ctx))


def test_build_prepared_rejects_mixed_feature_policy(
    tmp_path: Path, separated_pipeline,
) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    # separated under the default policy; build under a DIFFERENT feature policy.
    other = FeaturePolicy(include_onset=False)
    with pytest.raises(PreparedError):
        build_prepared(ctx.separated, feature_policy=other, label_policy=ctx.label_policy,
                       dataset_version="v1", source_index_digest=ctx.source_index_digest,
                       source_dataset_digest=ctx.dataset.manifest.dataset_digest)


def test_build_prepared_rejects_duplicate(tmp_path: Path, separated_pipeline) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    with pytest.raises(PreparedError):
        build_prepared((*ctx.separated, ctx.separated[0]),
                       feature_policy=ctx.feature_policy, label_policy=ctx.label_policy,
                       dataset_version="v1", source_index_digest=ctx.source_index_digest,
                       source_dataset_digest=ctx.dataset.manifest.dataset_digest)


def _write_prepared(tmp_path: Path, ctx) -> Path:
    prep = build_prepared(ctx.separated, feature_policy=ctx.feature_policy,
                          label_policy=ctx.label_policy, dataset_version="v1",
                          source_index_digest=ctx.source_index_digest,
                          source_dataset_digest=ctx.dataset.manifest.dataset_digest)
    write_prepared(prep, tmp_path / "prepared")
    return tmp_path / "prepared"


def test_corrupted_prepared_manifest_is_rejected(
    tmp_path: Path, separated_pipeline,
) -> None:
    root = _write_prepared(tmp_path, separated_pipeline(tmp_path, accepted=_ACC,
                                                        rejected=["run-rej"]))
    import json

    m = root / PREPARED_MANIFEST_FILE
    data = json.loads(m.read_text())
    data["prepared_digest"] = "0" * 64
    m.write_text(json.dumps(data))
    assert verify_prepared(root).verified is False
    with pytest.raises(PreparedError):
        load_prepared(root)


def test_corrupted_feature_file_is_rejected(tmp_path: Path, separated_pipeline) -> None:
    root = _write_prepared(tmp_path, separated_pipeline(tmp_path, accepted=_ACC,
                                                        rejected=["run-rej"]))
    victim = root / "features" / "train.jsonl"
    victim.write_bytes(victim.read_bytes() + b" ")
    result = verify_prepared(root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    with pytest.raises(PreparedError):
        load_features(root)


def test_missing_prepared_file_is_rejected(tmp_path: Path, separated_pipeline) -> None:
    root = _write_prepared(tmp_path, separated_pipeline(tmp_path, accepted=_ACC,
                                                        rejected=["run-rej"]))
    (root / "labels" / "test.jsonl").unlink()
    result = verify_prepared(root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)


def test_unsupported_prepared_version_does_not_parse(
    tmp_path: Path, separated_pipeline,
) -> None:
    root = _write_prepared(tmp_path, separated_pipeline(tmp_path, accepted=_ACC,
                                                        rejected=["run-rej"]))
    import json

    m = root / PREPARED_MANIFEST_FILE
    data = json.loads(m.read_text())
    data["prepared_version"] = 2
    m.write_text(json.dumps(data))
    result = verify_prepared(root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)


def test_layer_line_count_mismatch_is_rejected(
    tmp_path: Path, separated_pipeline,
) -> None:
    root = _write_prepared(tmp_path, separated_pipeline(tmp_path, accepted=_ACC,
                                                        rejected=["run-rej"]))
    # Drop a line from one labels file without touching the manifest hash? The
    # hash check catches it; to isolate reconstruction, tamper AND refresh nothing.
    victim = root / "labels" / "abstention.jsonl"
    victim.write_bytes(b"")  # now empty -> hash mismatch (caught) and count drift
    result = verify_prepared(root)
    assert result.verified is False


def test_deliberate_leakage_in_nested_feature_is_rejected() -> None:
    # Evaluator-only truth injected into a nested feature object is caught at any
    # depth, both by forbidden KEY and by forbidden VALUE.
    secret_digest = "d" * 64
    tampered = {
        "schema_version": 1,
        "backend": "frr_compose",
        "topology_hash": "a" * 64,
        "context": {"nested": {"ground_truth_reference": "incident.json",
                               "run_digest": secret_digest}},
    }
    findings = audit_feature_payload(tampered, forbidden_values=frozenset({secret_digest}))
    codes = {f.code for f in findings}
    assert FeatureLeakageCode.FORBIDDEN_FEATURE_KEY in codes
    assert FeatureLeakageCode.FORBIDDEN_FEATURE_VALUE in codes


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_model_construct_bypass_is_caught(tmp_path: Path, separated_pipeline) -> None:
    # Defense in depth: a model_construct-bypassed feature whose evidence field is
    # replaced with a leaky dict is still caught by the payload audit.
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    good = next(s for s in ctx.separated if s.trace.example_kind.value == "abstention")
    leaked = DatasetFeatures.model_construct(
        schema_version=1,
        feature_policy_id=good.features.feature_policy_id,
        topology_hash=good.features.topology_hash,
        backend=good.features.backend,
        baseline_evidence={"ground_truth_reference": good.trace.run_digest},
        onset_evidence=None,
    )
    payload = leaked.model_dump(mode="json")
    findings = audit_feature_payload(
        payload, forbidden_values=frozenset({good.trace.run_digest})
    )
    assert any(f.code is FeatureLeakageCode.FORBIDDEN_FEATURE_KEY for f in findings)


def test_separate_dataset_clean_pipeline_audits_pass(
    tmp_path: Path, separated_pipeline,
) -> None:
    ctx = separated_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    again = separate_dataset(ctx.loaded.examples, feature_policy=FeaturePolicy(),
                             label_policy=LabelPolicy(), dataset_version="v1",
                             source_index_digest=ctx.source_index_digest)
    for s in again:
        assert audit_separated_example(s).passed
