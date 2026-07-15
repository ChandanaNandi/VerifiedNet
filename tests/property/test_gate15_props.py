"""Gate 15 property tests: outcome determinism/totality/honesty under
Hypothesis, id stability + sensitivity, phase-order validity, paired
arithmetic, firewall key detection, build-twice structural equality."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.experiment import (
    EXPERIMENT_OUTCOMES,
    EXPERIMENT_PHASE_SEQUENCE,
    ExperimentPhaseLog,
    ExperimentPrimaryMetrics,
    build_success_policy,
    classify_experiment_outcome,
    success_policy_checks,
)

pytestmark = pytest.mark.property

_POLICY = build_success_policy()  # min 30 eligible test examples


def _metrics(base_correct: int, trained_correct: int, wins: int, losses: int,
             base_invalid: int, trained_invalid: int, base_abstention: int,
             trained_abstention: int, *, evaluated: int = 36,
             abstention: int = 24,
             unconfounded: bool = True) -> ExperimentPrimaryMetrics:
    return ExperimentPrimaryMetrics(
        eligible_test_examples=evaluated, base_test_correct=base_correct,
        trained_test_correct=trained_correct, test_evaluated=evaluated,
        test_base_incorrect_trained_correct=wins,
        test_base_correct_trained_incorrect=losses,
        test_predictions_differed=min(evaluated, wins + losses),
        base_invalid_predictions=base_invalid,
        trained_invalid_predictions=trained_invalid,
        abstention_count=abstention,
        base_abstention_correct=base_abstention,
        trained_abstention_correct=trained_abstention,
        comparison_unconfounded=unconfounded)


@st.composite
def metric_space(draw):
    evaluated = draw(st.integers(min_value=0, max_value=40))
    abstention = draw(st.integers(min_value=0, max_value=24))
    return _metrics(
        draw(st.integers(min_value=0, max_value=evaluated)),
        draw(st.integers(min_value=0, max_value=evaluated)),
        draw(st.integers(min_value=0, max_value=evaluated)),
        draw(st.integers(min_value=0, max_value=evaluated)),
        draw(st.integers(min_value=0, max_value=60)),
        draw(st.integers(min_value=0, max_value=60)),
        draw(st.integers(min_value=0, max_value=abstention)),
        draw(st.integers(min_value=0, max_value=abstention)),
        evaluated=evaluated, abstention=abstention,
        unconfounded=draw(st.booleans()))


@settings(max_examples=300, deadline=None)
@given(metrics=metric_space())
def test_outcome_is_total_deterministic_and_honest(metrics) -> None:
    outcome, reasons = classify_experiment_outcome(metrics, _POLICY)
    assert outcome in EXPERIMENT_OUTCOMES
    again, _ = classify_experiment_outcome(metrics, _POLICY)
    assert again == outcome  # deterministic
    assert reasons  # raw counts stay visible
    if not metrics.comparison_unconfounded:
        assert outcome == "experiment_failed"  # never a quality verdict
        return
    if metrics.eligible_test_examples < _POLICY.min_eligible_test_examples:
        assert outcome == "inconclusive"
        return
    # any regression prevents a pure-improvement classification
    if metrics.trained_test_correct < metrics.base_test_correct:
        assert outcome != "improved"
    # any increase in invalid outputs prevents improvement
    if metrics.trained_invalid_predictions > metrics.base_invalid_predictions:
        assert outcome != "improved"
    # any abstention regression prevents improvement
    if metrics.trained_abstention_correct < metrics.base_abstention_correct:
        assert outcome != "improved"
    # improvement additionally demands strictly net paired wins
    if metrics.test_base_incorrect_trained_correct \
            <= metrics.test_base_correct_trained_incorrect:
        assert outcome != "improved"
    if outcome == "improved":
        assert all(c.passed
                   for c in success_policy_checks(metrics, _POLICY))


def test_outcome_sensitivity_to_every_primary_dimension() -> None:
    baseline = _metrics(10, 14, 6, 2, 20, 12, 10, 10)
    assert classify_experiment_outcome(baseline, _POLICY)[0] == "improved"
    flipped = {
        "accuracy_regression": _metrics(14, 10, 2, 6, 20, 12, 10, 10),
        "invalid_increase": _metrics(10, 14, 6, 2, 12, 20, 10, 10),
        "abstention_regression": _metrics(10, 14, 6, 2, 20, 12, 10, 8),
        "net_losses": _metrics(10, 11, 2, 6, 20, 12, 10, 10),
        "confounded": _metrics(10, 14, 6, 2, 20, 12, 10, 10,
                               unconfounded=False),
        "underpowered": _metrics(5, 7, 3, 1, 20, 12, 10, 10, evaluated=18),
    }
    for name, metrics in flipped.items():
        outcome, _ = classify_experiment_outcome(metrics, _POLICY)
        assert outcome != "improved", name


def test_scientific_honesty_cases() -> None:
    # fewer invalid outputs with LOWER accuracy is mixed, never improved
    fewer_invalid_lower_accuracy = _metrics(14, 10, 2, 6, 30, 5, 10, 10)
    assert classify_experiment_outcome(
        fewer_invalid_lower_accuracy, _POLICY)[0] == "mixed"
    # one-dimension gains with an overall accuracy regression are not improved
    partial = _metrics(14, 12, 4, 6, 20, 20, 10, 12)
    assert classify_experiment_outcome(partial, _POLICY)[0] == "mixed"
    # a genuinely unmoved comparison is unchanged
    unmoved = _metrics(10, 10, 0, 0, 20, 20, 10, 10)
    assert classify_experiment_outcome(unmoved, _POLICY)[0] == "unchanged"
    # a failed (confounded) experiment is NOT an inconclusive quality verdict
    failed = _metrics(10, 14, 6, 2, 20, 12, 10, 10, unconfounded=False)
    assert classify_experiment_outcome(failed, _POLICY)[0] == \
        "experiment_failed"


@settings(max_examples=100, deadline=None)
@given(cut=st.integers(min_value=1, max_value=len(EXPERIMENT_PHASE_SEQUENCE)),
       data=st.data())
def test_phase_order_validity(cut: int, data) -> None:
    prefix = EXPERIMENT_PHASE_SEQUENCE[:cut]
    assert ExperimentPhaseLog(phases=prefix).phases == prefix  # every prefix
    permuted = data.draw(st.permutations(list(prefix)))
    if tuple(permuted) != prefix:
        with pytest.raises(Exception, match="prefix"):
            ExperimentPhaseLog(phases=tuple(permuted))


def test_experiment_and_result_id_stability_and_sensitivity(
    tmp_path, experiment_pipeline,
) -> None:
    from verifiednet.experiment import build_experiment_result

    ctx = experiment_pipeline(tmp_path)
    rebuilt = build_experiment_result(
        spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
        evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
        paired=ctx.paired, reliability=ctx.reliability,
        metrics=ctx.result.metrics)
    assert rebuilt == ctx.result  # stability
    qualified = build_experiment_result(
        spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
        evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
        paired=ctx.paired, reliability=ctx.reliability,
        metrics=ctx.result.metrics, qualifiers=("post_hoc_note",))
    assert qualified.experiment_result_id != ctx.result.experiment_result_id
    assert qualified.experiment_result_digest != \
        ctx.result.experiment_result_digest


def test_paired_count_arithmetic_across_partitions(
    tmp_path, experiment_pipeline,
) -> None:
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.experiment import compute_partition_paired_counts

    ctx = experiment_pipeline(tmp_path)
    per_partition = [
        compute_partition_paired_counts(ctx.base_run, ctx.trained_run,
                                        partitions=(partition,))
        for partition in (DatasetPartition.TRAIN, DatasetPartition.VALIDATION,
                          DatasetPartition.TEST, DatasetPartition.ABSTENTION)]
    combined = compute_partition_paired_counts(
        ctx.base_run, ctx.trained_run, partitions=None)
    assert combined.total == sum(c.total for c in per_partition)
    assert combined.predictions_differed == \
        sum(c.predictions_differed for c in per_partition)
    assert combined.base_invalid == sum(c.base_invalid for c in per_partition)
    for counts in (*per_partition, combined):  # quadrants always partition
        assert (counts.both_correct + counts.both_incorrect
                + counts.base_correct_trained_incorrect
                + counts.base_incorrect_trained_correct) == counts.total


def test_firewall_detects_any_injected_held_out_key(
    tmp_path, plan_pipeline,
) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.experiment import audit_test_firewall
    from verifiednet.orchestrator.catalog import case_by_id
    from verifiednet.orchestrator.expansion import expansion_topology
    from verifiednet.training import (
        build_training_corpus,
        diagnosis_input_template,
        diagnosis_target_template,
        diagnosis_training_policy,
    )

    ctx = plan_pipeline(
        tmp_path,
        accepted=[("ras-ref", "run-a"),
                  (case_by_id("ras-ref"), expansion_topology("2r-v2"),
                   "run-test-1")],
        rejected=["run-rej"])
    prepared = load_prepared(ctx.prepared_dir)
    task_id = diagnosis_task().task_id
    input_template = diagnosis_input_template(
        task_id=task_id,
        feature_policy_id=prepared.manifest.feature_policy_id)
    target_template = diagnosis_target_template(task_id=task_id)
    policy = diagnosis_training_policy(
        task_id=task_id, input_template=input_template,
        target_template=target_template)
    corpus = build_training_corpus(
        prepared, training_data_policy=policy,
        input_template=input_template, target_template=target_template)
    held_out = [e for e in prepared.examples
                if e.trace.partition is not DatasetPartition.TRAIN]
    assert held_out
    for token_kind in ("example_id", "group_id", "run_id"):
        token = getattr(held_out[0].trace, token_kind)
        audit = audit_test_firewall(
            prepared=prepared, training_corpus=corpus,
            training_side_payloads={
                "clean": b"{}",
                "poisoned": f'{{"leak": "{token}"}}'.encode()})
        assert audit.passed is False, token_kind
        assert any(c.rule == "no_held_out_identifier_in_poisoned"
                   and not c.passed for c in audit.checks)
    clean = audit_test_firewall(
        prepared=prepared, training_corpus=corpus,
        training_side_payloads={"clean": b"{}"})
    assert clean.passed is True


def test_build_twice_structural_equality(experiment_pipeline, tmp_path) -> None:
    import shutil

    shared = tmp_path / "shared"
    shared.mkdir()
    first = experiment_pipeline(shared)
    first_ids = (first.spec.experiment_id,
                 first.result.experiment_result_id,
                 first.written.experiment_digest, first.result.outcome)
    first_bytes = {
        p.name: p.read_bytes()
        for p in sorted(Path(str(first.written.root)).iterdir())
        if p.is_file()}
    shutil.rmtree(shared)
    shared.mkdir()
    second = experiment_pipeline(shared)
    assert (second.spec.experiment_id, second.result.experiment_result_id,
            second.written.experiment_digest,
            second.result.outcome) == first_ids
    second_bytes = {
        p.name: p.read_bytes()
        for p in sorted(Path(str(second.written.root)).iterdir())
        if p.is_file()}
    assert second_bytes == first_bytes  # identical artifact bytes
