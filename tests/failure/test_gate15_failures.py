"""Gate 15 failure tests: mismatched bindings, firewall breaches, phase
violations, second runs, tampered stores, dishonest outcomes — fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.experiment import (
    ControlledExperimentError,
    ExperimentPhase,
    advance_phase,
    audit_test_firewall,
    build_experiment_result,
    start_phase_log,
    verify_controlled_experiment,
    write_experiment_result,
)

pytestmark = pytest.mark.failure


def test_evaluation_cannot_start_before_checkpoint_verification() -> None:
    log = start_phase_log()
    log = advance_phase(log, ExperimentPhase.TRAINING_CORPUS_FINALIZED)
    log = advance_phase(log, ExperimentPhase.PLAN_AUTHORIZED)
    with pytest.raises(ControlledExperimentError, match="illegal"):
        advance_phase(log, ExperimentPhase.TEST_EVALUATION_STARTED)
    log = advance_phase(log, ExperimentPhase.TRAINING_COMPLETED)
    with pytest.raises(ControlledExperimentError, match="illegal"):
        advance_phase(log, ExperimentPhase.TEST_EVALUATION_STARTED)
    log = advance_phase(log, ExperimentPhase.CHECKPOINT_VERIFIED)
    advance_phase(log, ExperimentPhase.TEST_EVALUATION_STARTED)  # now legal


def test_wrong_corpus_binding_blocks_finalization(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    wrong = ctx.training.model_copy(
        update={"training_corpus_id": "traincorpus-" + "f" * 16})
    with pytest.raises(ControlledExperimentError,
                       match="training_matches_preregistration"):
        write_experiment_result(
            spec=ctx.spec, training=wrong, checkpoint=ctx.checkpoint,
            evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
            paired=ctx.paired, reliability=ctx.reliability,
            result=ctx.result,
            experiments_root=_fresh_preregistration(tmp_path, ctx))


def _fresh_preregistration(tmp_path: Path, ctx) -> Path:
    """A new experiments root carrying ONLY the preregistered spec."""
    import itertools

    from verifiednet.experiment import preregister_experiment

    for index in itertools.count():
        root = tmp_path / f"fresh-{index}"
        if not root.exists():
            break
    preregister_experiment(ctx.spec, root)
    return root


def test_foreign_checkpoint_lineage_blocks_finalization(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    foreign = ctx.checkpoint.model_copy(
        update={"real_execution_id": "rexec-" + "f" * 16})
    with pytest.raises(ControlledExperimentError,
                       match="checkpoint_binds_this_training"):
        write_experiment_result(
            spec=ctx.spec, training=ctx.training, checkpoint=foreign,
            evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
            paired=ctx.paired, reliability=ctx.reliability,
            result=ctx.result,
            experiments_root=_fresh_preregistration(tmp_path, ctx))


def test_benchmark_coverage_mismatch_blocks_finalization(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    missing_trained = ctx.benchmark_binding.model_copy(update={
        "ranking": tuple(
            row for row in ctx.benchmark_binding.ranking
            if row.predictor_identifier
            != ctx.evaluations.trained_baseline_id)})
    with pytest.raises(ControlledExperimentError,
                       match="benchmark_covers_both_model_predictors"):
        write_experiment_result(
            spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
            evaluations=ctx.evaluations, benchmark=missing_trained,
            paired=ctx.paired, reliability=ctx.reliability,
            result=ctx.result,
            experiments_root=_fresh_preregistration(tmp_path, ctx))


def test_paired_count_mismatch_blocks_finalization(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    counts = ctx.paired.counts_test
    if counts.predictions_identical > 0:  # keep every sum validator green
        inflated = counts.model_copy(update={
            "predictions_differed": counts.predictions_differed + 1,
            "predictions_identical": counts.predictions_identical - 1})
    else:
        assert counts.both_incorrect > 0
        inflated = counts.model_copy(update={
            "base_incorrect_trained_correct":
                counts.base_incorrect_trained_correct + 1,
            "both_incorrect": counts.both_incorrect - 1})
    tampered = ctx.paired.model_copy(update={"counts_test": inflated})
    with pytest.raises(ControlledExperimentError,
                       match="paired_counts_match_result_metrics"):
        write_experiment_result(
            spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
            evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
            paired=tampered, reliability=ctx.reliability, result=ctx.result,
            experiments_root=_fresh_preregistration(tmp_path, ctx))


def test_reliability_mismatch_blocks_finalization(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    swapped = ctx.reliability.model_copy(update={
        "base": ctx.reliability.trained, "trained": ctx.reliability.base})
    with pytest.raises(ControlledExperimentError,
                       match="reliability_counts_match_result_metrics"):
        write_experiment_result(
            spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
            evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
            paired=ctx.paired, reliability=swapped, result=ctx.result,
            experiments_root=_fresh_preregistration(tmp_path, ctx))


def test_modified_spec_after_preregistration_is_refused(
    tmp_path: Path, experiment_pipeline,
) -> None:
    from verifiednet.experiment import (
        build_success_policy,
        preregister_experiment,
    )

    ctx = experiment_pipeline(tmp_path)
    root = tmp_path / "tampered-root"
    preregister_experiment(ctx.spec, root)
    # a post-hoc "revised" spec (same experiment id cannot even be built —
    # rebuild with a changed field and try to finalize against the ORIGINAL
    # preregistration directory by renaming)
    revised = ctx.spec.model_copy(update={
        "hypothesis": "revised after seeing results",
        "success_policy": build_success_policy(min_eligible_test_examples=1)})
    spec_dir = root / ctx.spec.experiment_id
    from verifiednet.common.canonical import canonical_json_bytes

    (spec_dir / "experiment-spec.json").write_bytes(
        canonical_json_bytes(ctx.spec)[:-2] + b" }")  # byte-level tamper
    with pytest.raises(ControlledExperimentError, match="never modified"):
        write_experiment_result(
            spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
            evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
            paired=ctx.paired, reliability=ctx.reliability,
            result=ctx.result, experiments_root=root)
    del revised


def test_test_example_smuggled_into_training_is_detected(
    tmp_path: Path, plan_pipeline,
) -> None:
    """A training example whose trace CLAIMS train but whose source is a
    held-out test example: unbuildable by the Gate 10A builder, and caught
    fail-closed by the firewall audit if fabricated."""
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.evaluation import diagnosis_task
    from verifiednet.orchestrator.catalog import case_by_id
    from verifiednet.orchestrator.expansion import expansion_topology
    from verifiednet.training import (
        SupervisedTrainingExample,
        SupervisedTrainingInput,
        SupervisedTrainingTarget,
        TrainingCorpus,
        TrainingTraceMetadata,
        build_training_corpus,
        derive_training_corpus_id,
        derive_training_example_id,
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
    test_example = next(e for e in prepared.examples
                        if e.trace.partition is DatasetPartition.TEST)
    honest = corpus.examples[0]
    lying_trace = TrainingTraceMetadata(
        source_example_id=test_example.trace.example_id,  # the smuggled id
        source_group_id=test_example.trace.group_id,
        task_id=honest.trace.task_id,
        training_data_policy_id=honest.trace.training_data_policy_id,
        input_template_id=honest.trace.input_template_id,
        target_template_id=honest.trace.target_template_id,
        feature_policy_id=honest.trace.feature_policy_id,
        label_policy_id=honest.trace.label_policy_id,
        source_schema_version=honest.trace.source_schema_version)
    lying = SupervisedTrainingExample(
        training_example_id=derive_training_example_id(
            source_example_id=lying_trace.source_example_id,
            task_id=lying_trace.task_id,
            training_data_policy_id=lying_trace.training_data_policy_id,
            input_template_id=lying_trace.input_template_id,
            target_template_id=lying_trace.target_template_id,
            rendered_input=honest.input.text,
            rendered_target=honest.target.text),
        input=SupervisedTrainingInput(text=honest.input.text),
        target=SupervisedTrainingTarget(text=honest.target.text),
        trace=lying_trace)
    examples = tuple(sorted((*corpus.examples, lying),
                            key=lambda e: e.trace.source_example_id))
    poisoned = TrainingCorpus(
        training_corpus_id=derive_training_corpus_id(
            task_id=corpus.task_id,
            training_data_policy_id=policy.training_data_policy_id,
            input_template_id=input_template.input_template_id,
            target_template_id=target_template.target_template_id,
            training_example_ids=tuple(
                e.training_example_id for e in examples)),
        task_id=corpus.task_id, policy=policy,
        input_template=input_template, target_template=target_template,
        source_prepared_digest=corpus.source_prepared_digest,
        source_dataset_digest=corpus.source_dataset_digest,
        feature_policy_id=corpus.feature_policy_id,
        label_policy_id=corpus.label_policy_id, examples=examples)
    audit = audit_test_firewall(
        prepared=prepared, training_corpus=poisoned,
        training_side_payloads={"plan": b"{}"})
    assert audit.passed is False
    assert any(c.rule == "training_sources_are_train_accepted_only"
               and not c.passed for c in audit.checks)


def test_confounded_comparison_is_experiment_failed_never_improved(
    tmp_path: Path, experiment_pipeline,
) -> None:
    from verifiednet.experiment import extract_primary_metrics

    ctx = experiment_pipeline(tmp_path)
    confounded = extract_primary_metrics(
        ctx.base_run, ctx.trained_run, comparison_unconfounded=False)
    result = build_experiment_result(
        spec=ctx.spec, training=ctx.training, checkpoint=ctx.checkpoint,
        evaluations=ctx.evaluations, benchmark=ctx.benchmark_binding,
        paired=ctx.paired, reliability=ctx.reliability, metrics=confounded)
    assert result.outcome == "experiment_failed"


def test_store_tamper_and_unexpected_files_fail_closed(
    tmp_path: Path, experiment_pipeline,
) -> None:
    ctx = experiment_pipeline(tmp_path)
    root = Path(str(ctx.written.root))
    for name in ("experiment-spec.json", "training-binding.json",
                 "checkpoint-binding.json", "evaluation-bindings.json",
                 "benchmark-binding.json", "paired-summary.json",
                 "reliability-summary.json", "interpretation.json",
                 "manifest.json"):
        path = root / name
        original = path.read_bytes()
        position = len(original) // 2
        path.write_bytes(original[:position]
                         + bytes([original[position] ^ 0xFF])
                         + original[position + 1:])
        assert verify_controlled_experiment(root).verified is False, name
        path.write_bytes(original)
    assert verify_controlled_experiment(root).verified is True
    (root / "extra.json").write_bytes(b"{}")
    assert verify_controlled_experiment(root).verified is False
    (root / "extra.json").unlink()
    assert verify_controlled_experiment(root).verified is True
