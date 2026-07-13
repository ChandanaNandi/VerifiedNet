"""Gate 7 property tests: deterministic ids, metrics, order independence."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.datasets.features import DatasetFeatures, FeatureEvidenceRef, FeaturePolicy
from verifiednet.evaluation import (
    EvidenceRuleBaseline,
    compute_aggregate_metrics,
    compute_confusion,
    diagnosis_task,
    evaluate_prepared_corpus,
    ratio_str,
)

pytestmark = pytest.mark.property

_hex = st.integers(min_value=0, max_value=(1 << 64) - 1).map(lambda n: f"{n:064x}")
_backend = st.sampled_from(["frr_compose", "other_backend"])


@st.composite
def _features(draw: st.DrawFn) -> DatasetFeatures:
    has_onset = draw(st.booleans())
    return DatasetFeatures(
        feature_policy_id=FeaturePolicy().policy_id, topology_hash=draw(_hex),
        backend=draw(_backend),
        baseline_evidence=FeatureEvidenceRef(relative_path="evidence/baseline.json"),
        onset_evidence=(FeatureEvidenceRef(relative_path="evidence/onset.json")
                        if has_onset else None),
    )


@given(features=_features())
@settings(max_examples=200)
def test_prediction_is_deterministic(features: DatasetFeatures) -> None:
    baseline = EvidenceRuleBaseline(task=diagnosis_task(), default_fault_family="bgp_x")
    p1 = baseline.predict(features)
    p2 = baseline.predict(features)
    assert p1.prediction_id == p2.prediction_id
    assert canonical_json_bytes(p1) == canonical_json_bytes(p2)


@given(num=st.integers(0, 50), den=st.integers(0, 50))
@settings(max_examples=200)
def test_ratio_zero_denominator(num: int, den: int) -> None:
    result = ratio_str(num, den)
    if den == 0:
        assert result is None
    else:
        assert result is not None and len(result.split(".")[1]) == 6


@given(seed=st.integers(0, 6))
@settings(max_examples=7, deadline=None)
def test_task_and_baseline_ids_stable(seed: int) -> None:
    assert diagnosis_task().task_id == diagnosis_task().task_id
    fam = f"family-{seed}"
    a = EvidenceRuleBaseline(task=diagnosis_task(), default_fault_family=fam)
    b = EvidenceRuleBaseline(task=diagnosis_task(), default_fault_family=fam)
    assert a.spec.baseline_id == b.spec.baseline_id


def test_input_order_independence(tmp_path: Path, eval_pipeline) -> None:
    # Shuffling the prepared examples must not change the evaluation output.
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-rev", "run-b"),
                                            ("if-ref", "run-c"), ("pf-ref", "run-d")],
                        rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run_a = evaluate_prepared_corpus(ctx.loaded, baseline, task)

    reversed_loaded = dataclasses.replace(
        ctx.loaded, examples=tuple(reversed(ctx.loaded.examples)))
    run_b = evaluate_prepared_corpus(reversed_loaded, baseline, task)
    assert run_a.evaluation_id == run_b.evaluation_id
    assert canonical_json_bytes(run_a) == canonical_json_bytes(run_b)


def test_metric_and_confusion_recomputation(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-rev", "run-b")],
                        rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    assert compute_aggregate_metrics(run.records) == run.metrics
    assert compute_confusion(run.records) == run.confusion
    # confusion order independence: recomputing from reversed records is identical
    assert compute_confusion(tuple(reversed(run.records))) == run.confusion
