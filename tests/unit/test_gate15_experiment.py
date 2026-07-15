"""Gate 15 unit tests: spec/policy identities, phase transitions, corpus
cap, metric extraction, firewall audit, and the experiment store round trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.experiment import (
    EXPERIMENT_PHASE_SEQUENCE,
    ControlledExperimentError,
    ExperimentPhase,
    advance_phase,
    audit_test_firewall,
    build_success_policy,
    cap_training_corpus,
    classify_experiment_outcome,
    corpus_distributions,
    extract_primary_metrics,
    read_controlled_experiment,
    start_phase_log,
    success_policy_checks,
    verify_controlled_experiment,
)

pytestmark = pytest.mark.unit


def test_success_policy_id_and_defaults() -> None:
    policy = build_success_policy()
    assert policy.success_policy_id.startswith("esucc-")
    assert policy.min_eligible_test_examples == 30
    assert policy.max_invalid_prediction_increase == 0
    assert build_success_policy() == policy  # deterministic


def test_phase_log_is_strictly_forward() -> None:
    log = start_phase_log()
    assert log.phases == (ExperimentPhase.PREREGISTERED,)
    for phase in EXPERIMENT_PHASE_SEQUENCE[1:]:
        with pytest.raises(ControlledExperimentError):  # cannot skip ahead
            advance_phase(log, EXPERIMENT_PHASE_SEQUENCE[
                (len(log.phases) + 1) % len(EXPERIMENT_PHASE_SEQUENCE)])
        log = advance_phase(log, phase)
    assert log.complete is True
    with pytest.raises(ControlledExperimentError):  # nothing after the end
        advance_phase(log, ExperimentPhase.RESULT_INTERPRETED)


def _build_corpus(prepared):
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.training import (
        build_training_corpus,
        diagnosis_input_template,
        diagnosis_target_template,
        diagnosis_training_policy,
    )

    task_id = diagnosis_task().task_id
    input_template = diagnosis_input_template(
        task_id=task_id,
        feature_policy_id=prepared.manifest.feature_policy_id)
    target_template = diagnosis_target_template(task_id=task_id)
    policy = diagnosis_training_policy(
        task_id=task_id, input_template=input_template,
        target_template=target_template)
    return build_training_corpus(
        prepared, training_data_policy=policy,
        input_template=input_template, target_template=target_template)


def test_cap_training_corpus_is_the_canonical_prefix(
    tmp_path: Path, plan_pipeline,
) -> None:
    from verifiednet.datasets import load_prepared

    ctx = plan_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b"),
                                            ("if-ref", "run-c")],
                        rejected=["run-rej"])
    corpus = _build_corpus(load_prepared(ctx.prepared_dir))
    capped = cap_training_corpus(corpus, max_example_count=2)
    assert capped.examples == corpus.examples[:2]
    assert capped.training_corpus_id != corpus.training_corpus_id
    # a cap covering everything is the identity operation
    assert cap_training_corpus(
        corpus, max_example_count=len(corpus.examples)) == corpus
    with pytest.raises(ControlledExperimentError):
        cap_training_corpus(corpus, max_example_count=0)


def test_corpus_distributions_resolve_against_prepared(
    tmp_path: Path, plan_pipeline,
) -> None:
    from verifiednet.datasets import load_prepared

    ctx = plan_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("ras-alt", "run-b"),
                                            ("nr-ref", "run-c")],
                        rejected=["run-rej"])
    prepared = load_prepared(ctx.prepared_dir)
    corpus = _build_corpus(prepared)
    families, topologies, groups = corpus_distributions(corpus, prepared)
    assert dict(families) == {"bgp_neighbor_removal": 1,
                              "bgp_remote_as_mismatch": 2}
    assert sum(count for _t, count in topologies) == 3
    assert groups == 3  # three distinct stable identities


def test_metric_extraction_and_classification_from_real_runs(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    metrics = ctx.result.metrics
    # the fixture prepared corpus: 2 held-out test examples, 1 abstention
    assert metrics.eligible_test_examples == 2
    assert metrics.test_evaluated == 2
    assert metrics.abstention_count == 1
    outcome, reasons = classify_experiment_outcome(
        metrics, ctx.success_policy)
    assert outcome == ctx.result.outcome
    assert any(reason.startswith("test_accuracy_base=")
               for reason in reasons)
    checks = success_policy_checks(metrics, ctx.success_policy)
    assert checks == ctx.result.success_checks
    assert {c.rule for c in checks} == {
        "min_eligible_test_examples", "comparison_unconfounded",
        "accepted_test_accuracy_increased", "net_paired_wins_positive",
        "invalid_predictions_not_increased",
        "abstention_accuracy_not_reduced"}


def test_extraction_refuses_misaligned_runs(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.evaluation import (
        FixedPriorBaseline,
        diagnosis_task,
        evaluate_prepared_corpus,
    )

    task = diagnosis_task()
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir(), right.mkdir()
    run_a = evaluate_prepared_corpus(
        eval_pipeline(left, accepted=[("ras-ref", "run-a")],
                      rejected=["run-rej"]).loaded,
        FixedPriorBaseline(task=task,
                           fixed_fault_family="bgp_remote_as_mismatch"),
        task)
    run_b = evaluate_prepared_corpus(
        eval_pipeline(right, accepted=[("nr-ref", "run-b")],
                      rejected=["run-rej"]).loaded,
        FixedPriorBaseline(task=task,
                           fixed_fault_family="bgp_remote_as_mismatch"),
        task)
    with pytest.raises(ControlledExperimentError, match="SAME prepared"):
        extract_primary_metrics(run_a, run_b, comparison_unconfounded=True)


def test_firewall_audit_passes_on_the_clean_offline_chain(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    trainctx = ctx.trainctx
    corpus = _build_corpus(ctx.prepared)
    payloads = {
        "training_plan": b"".join(
            p.read_bytes() for p in
            sorted(Path(str(trainctx.plan_dir)).rglob("*")) if p.is_file()),
        "authorization": b"".join(
            p.read_bytes() for p in
            sorted(Path(str(trainctx.auth_dir)).rglob("*")) if p.is_file()),
        "execution_and_checkpoint": b"".join(
            p.read_bytes() for p in
            sorted(Path(str(trainctx.output_root)).rglob("*.json"))
            if p.is_file()),
    }
    audit = audit_test_firewall(
        prepared=ctx.prepared, training_corpus=corpus,
        training_side_payloads=payloads)
    assert audit.passed is True, [c for c in audit.checks if not c.passed]
    assert audit.held_out_example_ids == 4  # 2 test + 1 validation + 1 abst
    assert audit.audit_id.startswith("fwaudit-")


def test_experiment_store_round_trip(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    verification = verify_controlled_experiment(ctx.written.root)
    assert verification.verified is True
    loaded = read_controlled_experiment(ctx.written.root)
    assert loaded.spec == ctx.spec
    assert loaded.result == ctx.result
    assert loaded.manifest.experiment_id == ctx.spec.experiment_id
    assert loaded.manifest.outcome == ctx.result.outcome
    assert loaded.result.experiment_result_id.startswith("expres-")
    assert loaded.result.experiment_result_digest.startswith("expresdig-")
    assert loaded.result.phases == EXPERIMENT_PHASE_SEQUENCE
    # exactly one execution and one checkpoint bound — by field shape
    assert isinstance(loaded.result.execution_id, str)
    assert isinstance(loaded.result.checkpoint_id, str)


def test_preregistration_is_required_and_immutable(
    tmp_path: Path, experiment_pipeline,
) -> None:
    from verifiednet.experiment import (
        preregister_experiment,
        write_experiment_result,
    )

    ctx = experiment_pipeline(tmp_path)
    # a second preregistration of the same experiment refuses
    with pytest.raises(ControlledExperimentError, match="already"):
        preregister_experiment(ctx.spec, ctx.experiments_root)
    # finalizing an experiment that was never preregistered refuses
    with pytest.raises(ControlledExperimentError, match="never preregistered"):
        write_experiment_result(
            spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
            evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
            paired=ctx.paired, reliability=ctx.reliability,
            result=ctx.result, experiments_root=tmp_path / "elsewhere")
    # a second finalization refuses (one authoritative result)
    with pytest.raises(ControlledExperimentError, match="already finalized"):
        write_experiment_result(
            spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
            evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
            paired=ctx.paired, reliability=ctx.reliability,
            result=ctx.result, experiments_root=ctx.experiments_root)
