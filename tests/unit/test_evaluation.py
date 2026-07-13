"""Gate 7 unit tests: contracts, baselines, scoring, metrics, build/write/read."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.evaluation import (
    AbstentionPrediction,
    DiagnosisPrediction,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    NormalizationPolicy,
    OutcomeCategory,
    audit_evaluation_run,
    diagnosis_task,
    evaluate_prepared_corpus,
    ratio_str,
    read_evaluation,
    score,
    verify_evaluation,
    verify_prediction_id,
    write_evaluation,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c"),
        ("pf-ref", "run-d")]


def test_task_id_is_deterministic_and_content_derived() -> None:
    a = diagnosis_task()
    b = diagnosis_task()
    assert a.task_id == b.task_id
    assert a.task_id.startswith("task-")
    # a different normalization policy changes the task id
    c = diagnosis_task(normalization=NormalizationPolicy(casefold=False))
    assert c.task_id != a.task_id


def test_baseline_id_changes_with_rules() -> None:
    task = diagnosis_task()
    b1 = EvidenceRuleBaseline(task=task, default_fault_family="x")
    b2 = EvidenceRuleBaseline(task=task, default_fault_family="y")
    assert b1.spec.baseline_id != b2.spec.baseline_id
    assert b1.spec.baseline_id.startswith("baseline-")
    fixed = FixedPriorBaseline(task=task, fixed_fault_family="x")
    assert fixed.spec.baseline_id != b1.spec.baseline_id


def test_prediction_id_is_deterministic(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    feats = ctx.loaded.examples[0].features
    p1 = baseline.predict(feats)
    p2 = baseline.predict(feats)
    assert p1.prediction_id == p2.prediction_id
    assert verify_prediction_id(
        p1, baseline_id=baseline.spec.baseline_id, task_id=task.task_id,
        feature_policy_id=feats.feature_policy_id,
        feature_payload=feats.model_dump(mode="json"))


def test_evidence_rule_abstains_without_onset(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    for ex in ctx.loaded.examples:
        pred = baseline.predict(ex.features)
        if ex.features.onset_evidence is None:
            assert isinstance(pred, AbstentionPrediction)
            assert pred.reason_code == "no_onset_evidence"
        else:
            assert isinstance(pred, DiagnosisPrediction)
            assert pred.fault_family == "bgp_remote_as_mismatch"


def test_fixed_prior_never_abstains(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch")
    for ex in ctx.loaded.examples:
        assert isinstance(baseline.predict(ex.features), DiagnosisPrediction)


def test_scoring_semantics() -> None:
    norm = NormalizationPolicy()
    from verifiednet.datasets.features import AbstentionLabels, AcceptedLabels, LabelPolicy
    from verifiednet.datasets.models import ArtifactReference
    from verifiednet.evaluation import build_abstention_prediction, build_diagnosis_prediction

    ref = ArtifactReference(run_id="r", relative_path="incident.json")
    acc = AcceptedLabels(label_policy_id=LabelPolicy().policy_id, fault_family="bgp_x",
                         scenario_id="s", ground_truth_reference=ref, recovery_reference=ref)
    rej = AbstentionLabels(label_policy_id=LabelPolicy().policy_id,
                           rejection_code="precondition_failed", failed_phase="precondition")
    kw = dict(baseline_id="baseline-0000000000000000", task_id="task-0000000000000000",
              feature_policy_id="feat-0000000000000000", feature_payload={"a": 1})
    diag_right = build_diagnosis_prediction(fault_family="bgp_x", **kw)
    diag_wrong = build_diagnosis_prediction(fault_family="other", **kw)
    absten = build_abstention_prediction(reason_code="x", **kw)

    assert score(diag_right, acc, normalization=norm)[0] is OutcomeCategory.CORRECT_DIAGNOSIS
    assert score(diag_wrong, acc, normalization=norm)[0] is OutcomeCategory.INCORRECT_DIAGNOSIS
    assert score(absten, acc, normalization=norm)[0] is OutcomeCategory.ABSTAINED_ON_DIAGNOSIS
    assert score(absten, rej, normalization=norm)[0] is OutcomeCategory.CORRECT_ABSTENTION
    assert score(diag_right, rej, normalization=norm)[0] is \
        OutcomeCategory.FALSE_DIAGNOSIS_ON_REJECTED


def test_zero_denominator_is_none() -> None:
    assert ratio_str(0, 0) is None
    assert ratio_str(1, 2) == "0.500000"
    assert ratio_str(1, 1) == "1.000000"


def test_metrics_separate_accepted_and_abstention(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    assert run.metrics.corpus_counts.accepted == 4
    assert run.metrics.corpus_counts.abstention == 1
    assert run.metrics.abstention.count == 1
    assert run.metrics.abstention.correct == 1
    # abstention example is NOT in the accepted confusion matrix
    assert all(c.authoritative_class != "abstain" for c in run.confusion)
    assert audit_evaluation_run(run).passed


def test_write_verify_read_round_trip(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    written = write_evaluation(run, tmp_path / "evaluations")
    assert written.root.name == run.evaluation_id
    result = verify_evaluation(written.root)
    assert result.verified is True, result.failures

    from verifiednet.common.canonical import canonical_json_bytes
    back = read_evaluation(written.root)
    assert canonical_json_bytes(back) == canonical_json_bytes(run)


def test_records_carry_trace_identity(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    for r in run.records:
        assert r.example_id.startswith("ex-")
        assert r.group_id.startswith("grp-")
        assert r.run_id  # identity retained for audit, never fed to the baseline
