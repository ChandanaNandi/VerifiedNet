"""Optional Gate 15 operational experiment: the REAL preregistered,
one-run, matched base-versus-trained controlled experiment on registered
evaluation corpus v3. Never runs in offline CI.

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE15=1``
AND the approved base-model snapshot dir AND the v3 registration +
prepared-chain dirs AND the persisted readiness assessment AND the
``training-hf`` extras AND an output artifact root. Strict offline mode is
enforced (network sabotaged; HF offline env forced by the engine/backend).

The test asserts STRUCTURAL and SCIENTIFIC-PROCESS consistency: every
artifact verifies, exactly one run and one checkpoint exist, the firewall
audit passes, sources stay byte-identical, and the outcome is whatever the
frozen success policy derives from the counts — the test NEVER asserts that
the model improved.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

#: The FIXED experiment inputs (source constants, never environment inputs).
GATE15_V3_CORPUS_ID = "evalcorpus-8c932345efc3e6e6"
GATE15_V3_CORPUS_DIGEST = "ecdig-e72927cc7d4b6fd0fa141462"
GATE15_READINESS_ID = "ready-0b128bea7400a13f"
GATE15_MODEL_IDENTIFIER = "Qwen/Qwen2.5-0.5B-Instruct"
GATE15_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
GATE15_MODEL_ARCHITECTURE = "Qwen2ForCausalLM"
#: The preregistered training envelope. The Literal-locked Gate 10F safety
#: ceilings (max 64 examples / 64 optimizer steps) ARE the runtime-budget
#: refusal thresholds; the deterministic first-64 canonical-order cap plus
#: CPU-runtime practicality are the preregistered rationale.
GATE15_EXAMPLE_CAP = 64
GATE15_EPOCHS = 2
GATE15_MAX_STEPS = 64
GATE15_MAX_TOTAL_TOKENS = 448
GATE15_EFFECTIVE_BATCH = 2
GATE15_LEARNING_RATE = "0.00002"
GATE15_WARMUP_STEPS = 4
GATE15_SEED = 15

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE15") == "1"
_BASE_DIR = os.environ.get("VERIFIEDNET_BASE_MODEL_DIR", "")
_V3_DIR = os.environ.get("VERIFIEDNET_EVAL_CORPUS_V3_DIR", "")
_V3_PREPARED = os.environ.get("VERIFIEDNET_EVAL_CORPUS_V3_PREPARED_DIR", "")
_READINESS_DIR = os.environ.get("VERIFIEDNET_READINESS_ASSESSMENT_DIR", "")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE15_OUTPUT_ROOT", "")
_MODEL_LICENSE = os.environ.get("VERIFIEDNET_MODEL_LICENSE", "apache-2.0")
#: Optional additional prior-artifact roots to fingerprint (os.pathsep-split).
_PRIOR_DIRS = os.environ.get("VERIFIEDNET_GATE15_PRIOR_ARTIFACT_DIRS", "")


def _skip_unless_enabled() -> None:
    if not _ENABLED:
        pytest.skip("VERIFIEDNET_RUN_GATE15!=1")
    for name, value in (("VERIFIEDNET_BASE_MODEL_DIR", _BASE_DIR),
                        ("VERIFIEDNET_EVAL_CORPUS_V3_DIR", _V3_DIR),
                        ("VERIFIEDNET_EVAL_CORPUS_V3_PREPARED_DIR",
                         _V3_PREPARED),
                        ("VERIFIEDNET_READINESS_ASSESSMENT_DIR",
                         _READINESS_DIR)):
        if not value or not Path(value).is_dir():
            pytest.skip(f"{name} not set / not a dir")
    if not _OUT_ROOT:
        pytest.skip("VERIFIEDNET_GATE15_OUTPUT_ROOT is not set")
    for module in ("torch", "transformers"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} not installed (training-hf extras "
                        "required)")


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_gate15_controlled_experiment_end_to_end(monkeypatch) -> None:
    _skip_unless_enabled()
    out_root = Path(_OUT_ROOT)
    experiments_root = out_root / "controlled-experiments"
    if experiments_root.exists() and any(experiments_root.iterdir()):
        pytest.skip(f"an experiment already exists: {experiments_root}")

    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 15 must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from verifiednet.common.canonical import canonical_json_bytes
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.evaluation import (
        CorpusProvenance,
        DecodingConfig,
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        HfCheckpointInferenceBackend,
        VerifiedBaseModelPredictor,
        VerifiedCheckpointPredictor,
        assess_matched_pair_fairness,
        base_model_predictor_facts,
        build_checkpoint_inference_compatibility,
        build_cpu_inference_device_policy,
        build_default_interpretation_policy,
        build_paired_comparison,
        build_structured_output_report,
        checkpoint_predictor_facts,
        compute_parser_statistics,
        diagnosis_prompt_template,
        diagnosis_task,
        evaluate_prepared_corpus,
        interpret_paired_comparison,
        load_verified_base_model_bundle,
        load_verified_checkpoint_bundle,
        read_evaluation_corpus,
        run_benchmark,
        verify_benchmark,
        verify_comparison,
        verify_evaluation,
        verify_readiness_assessment,
        verify_structured_output_report,
        write_benchmark,
        write_comparison,
        write_evaluation,
        write_structured_output_report,
    )
    from verifiednet.experiment import (
        BenchmarkBinding,
        BenchmarkRankingRow,
        CheckpointBinding,
        EvaluationBindings,
        ExperimentPhase,
        ExperimentRuntimeEnvelope,
        PairedSummary,
        ReliabilitySummary,
        TrainingPhaseBinding,
        advance_phase,
        audit_test_firewall,
        build_experiment_result,
        build_experiment_spec,
        build_success_policy,
        cap_training_corpus,
        compute_family_paired_counts,
        compute_partition_paired_counts,
        corpus_distributions,
        extract_primary_metrics,
        preregister_experiment,
        read_controlled_experiment,
        start_phase_log,
        write_experiment_result,
    )
    from verifiednet.training import (
        HF_FULL_FINETUNE_BACKEND_ID,
        BatchConfig,
        DeterminismCategory,
        EpochBudget,
        ExecutionState,
        HFTrainingEngine,
        HuggingFaceFullFinetuneBackend,
        LocalModelArtifactResolver,
        LocalTokenizerArtifactResolver,
        OptimizationConfig,
        RealTrainingExecutor,
        SchedulerConfig,
        SeedPolicy,
        SequenceLengthPolicy,
        TokenizerSpec,
        TorchTrainingEnvironmentProbe,
        TrainableModelSpec,
        build_bounded_model_policy,
        build_causal_lm_objective_policy,
        build_model_approval,
        build_real_execution_policy,
        build_training_corpus,
        build_training_spec,
        derive_model_spec_id,
        derive_tokenizer_spec_id,
        descriptor_from_manifest,
        diagnosis_input_template,
        diagnosis_target_template,
        diagnosis_training_policy,
        load_training_corpus,
        plan_for_real_backend,
        read_real_checkpoint,
        read_real_execution,
        read_training_authorization,
        read_training_plan,
        select_corpus_slice,
        verify_real_checkpoint,
        verify_real_execution,
        write_training_authorization,
        write_training_corpus,
        write_training_plan,
    )

    base_dir = Path(_BASE_DIR)
    v3_dir = Path(_V3_DIR)
    v3_prepared_dir = Path(_V3_PREPARED)
    readiness_dir = Path(_READINESS_DIR)
    source_fingerprints = {
        "v3_registration": _fingerprint(v3_dir),
        "v3_prepared": _fingerprint(v3_prepared_dir),
        "base_model": _fingerprint(base_dir),
        "readiness": _fingerprint(readiness_dir),
    }
    prior_roots = [Path(p) for p in _PRIOR_DIRS.split(os.pathsep) if p]
    for prior in prior_roots:
        source_fingerprints[f"prior:{prior}"] = _fingerprint(prior)

    # ---- 1. verify v3 corpus + readiness (fixed identities) ---------------
    v3 = read_evaluation_corpus(v3_dir)
    assert v3.manifest.evaluation_corpus_id == GATE15_V3_CORPUS_ID
    assert v3.manifest.corpus_digest == GATE15_V3_CORPUS_DIGEST
    assert verify_readiness_assessment(readiness_dir).verified is True
    readiness = json.loads((readiness_dir / "summary.json").read_bytes())
    assert readiness["assessment_id"] == GATE15_READINESS_ID
    assert readiness["outcome"] == "ready_for_controlled_experiment"
    assert readiness["corpus_id"] == GATE15_V3_CORPUS_ID
    prepared = load_prepared(v3_prepared_dir)
    assert prepared.manifest.prepared_digest == v3.manifest.prepared_digest

    # ---- 2. derive the train-only training corpus (full, then the cap) ----
    task = diagnosis_task()
    input_template = diagnosis_input_template(
        task_id=task.task_id,
        feature_policy_id=prepared.manifest.feature_policy_id)
    target_template = diagnosis_target_template(task_id=task.task_id)
    training_policy = diagnosis_training_policy(
        task_id=task.task_id, input_template=input_template,
        target_template=target_template)
    full_corpus = build_training_corpus(
        prepared, training_data_policy=training_policy,
        input_template=input_template, target_template=target_template)
    eligible = len(full_corpus.examples)
    assert eligible == 128  # every accepted train example is eligible
    capped = cap_training_corpus(full_corpus,
                                 max_example_count=GATE15_EXAMPLE_CAP)
    assert len(capped.examples) == GATE15_EXAMPLE_CAP
    written_corpus = write_training_corpus(
        capped, out_root / "training-corpora")
    corpus_manifest = load_training_corpus(written_corpus.root).manifest
    families, topologies, groups = corpus_distributions(capped, prepared)
    assert sum(count for _f, count in families) == GATE15_EXAMPLE_CAP
    assert len(families) == 4  # every fault family present in the cap
    assert len(topologies) >= 4  # topology diversity survives the cap
    assert groups >= GATE15_EXAMPLE_CAP // 4  # identities, not repeats

    # ---- 3. the preregistered training configuration ----------------------
    model_fields = dict(provider="huggingface",
                        model_identifier=GATE15_MODEL_IDENTIFIER,
                        model_revision=GATE15_MODEL_REVISION,
                        model_class=GATE15_MODEL_ARCHITECTURE)
    model_spec = TrainableModelSpec(
        **model_fields,
        model_spec_id=derive_model_spec_id(load_precision="float32",
                                           **model_fields))
    tok_fields = dict(tokenizer_identifier=GATE15_MODEL_IDENTIFIER,
                      tokenizer_revision=GATE15_MODEL_REVISION,
                      tokenizer_class="AutoTokenizer")
    tokenizer_spec = TokenizerSpec(
        **tok_fields,
        tokenizer_spec_id=derive_tokenizer_spec_id(
            special_vocab_policy="model_defaults", padding_policy="right",
            truncation_policy="fail_closed", **tok_fields))
    spec = build_training_spec(
        training_corpus_id=corpus_manifest.training_corpus_id,
        training_corpus_digest=corpus_manifest.training_corpus_digest,
        task_id=task.task_id, model=model_spec, tokenizer=tokenizer_spec,
        trainer_implementation_id=HF_FULL_FINETUNE_BACKEND_ID,
        sequence_policy=SequenceLengthPolicy(
            max_input_tokens=384, max_target_tokens=64,
            max_total_tokens=GATE15_MAX_TOTAL_TOKENS),
        batch=BatchConfig(per_device_batch_size=1,
                          gradient_accumulation_steps=2,
                          effective_batch_size=GATE15_EFFECTIVE_BATCH),
        optimization=OptimizationConfig(
            optimizer_name="adamw", learning_rate=GATE15_LEARNING_RATE),
        scheduler=SchedulerConfig(scheduler_name="linear_warmup",
                                  warmup_steps=GATE15_WARMUP_STEPS),
        budget=EpochBudget(epochs=GATE15_EPOCHS),
        seed_policy=SeedPolicy(
            data_order_seed=GATE15_SEED, model_init_seed=GATE15_SEED,
            dropout_seed=GATE15_SEED, backend_seed=GATE15_SEED))
    plan = plan_for_real_backend(
        spec=spec, corpus=descriptor_from_manifest(corpus_manifest))
    # runtime-budget refusal BEFORE anything loads
    assert plan.expected_example_count <= GATE15_EXAMPLE_CAP
    assert plan.optimizer_steps <= GATE15_MAX_STEPS
    assert (plan.expected_epochs or 1) <= GATE15_EPOCHS
    written_plan = write_training_plan(plan, out_root / "training-plans")
    loaded_plan = read_training_plan(written_plan.root)

    # ---- 4. PREREGISTRATION (persisted BEFORE authorization + training) ---
    envelope = ExperimentRuntimeEnvelope(
        max_examples=GATE15_EXAMPLE_CAP, max_epochs=GATE15_EPOCHS,
        max_optimizer_steps=GATE15_MAX_STEPS,
        max_sequence_length=GATE15_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE15_EFFECTIVE_BATCH)
    success_policy = build_success_policy(min_eligible_test_examples=30)
    objective_policy = build_causal_lm_objective_policy()
    decoding = DecodingConfig(max_tokens=64)
    template = diagnosis_prompt_template()
    interpretation_policy = build_default_interpretation_policy()
    model_resolver = LocalModelArtifactResolver(base_dir)
    tokenizer_resolver = LocalTokenizerArtifactResolver(base_dir)
    resolved_model = model_resolver.resolve(model_spec)
    resolved_tokenizer = tokenizer_resolver.resolve(tokenizer_spec)
    model_policy = build_bounded_model_policy(
        permitted_model_identifier=GATE15_MODEL_IDENTIFIER,
        permitted_model_revision=GATE15_MODEL_REVISION,
        permitted_architecture_class=GATE15_MODEL_ARCHITECTURE,
        permitted_tokenizer_revision=GATE15_MODEL_REVISION,
        max_declared_parameter_count=600_000_000,
        max_sequence_length=GATE15_MAX_TOTAL_TOKENS,
        max_example_count=GATE15_EXAMPLE_CAP, max_epochs=GATE15_EPOCHS,
        max_optimizer_steps=GATE15_MAX_STEPS,
        max_effective_batch_size=GATE15_EFFECTIVE_BATCH)
    params = resolved_model.declared_parameter_count
    assert params is not None and 100_000_000 < params < 600_000_000
    approval = build_model_approval(
        model_identifier=GATE15_MODEL_IDENTIFIER,
        model_revision=GATE15_MODEL_REVISION,
        tokenizer_identifier=GATE15_MODEL_IDENTIFIER,
        tokenizer_revision=GATE15_MODEL_REVISION,
        architecture_class=GATE15_MODEL_ARCHITECTURE,
        parameter_count=params,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=(
            resolved_tokenizer.resolved_tokenizer_artifact_id),
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        license_identifier=_MODEL_LICENSE,
        license_review="reviewed: upstream LICENSE file on the authoritative "
                       "model repository declares Apache License 2.0")
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "approved-model.json").write_bytes(
        canonical_json_bytes(approval))

    experiment_spec = build_experiment_spec(
        experiment_name="gate15-controlled-retraining-experiment",
        experiment_version=1,
        scientific_question=(
            "Does supervised fine-tuning on the expanded, verified v3-era "
            "training corpus improve structured networking-diagnosis "
            "performance relative to the matched pretrained base model?"),
        hypothesis=(
            "The Gate 15 trained checkpoint will produce a higher "
            "accepted-diagnosis exact-match accuracy than the matched "
            "pretrained base model on evaluation corpus v3, without "
            "reducing abstention accuracy or increasing invalid structured "
            "outputs."),
        evaluation_corpus_id=GATE15_V3_CORPUS_ID,
        evaluation_corpus_digest=GATE15_V3_CORPUS_DIGEST,
        readiness_assessment_id=GATE15_READINESS_ID,
        source_prepared_digest=prepared.manifest.prepared_digest,
        training_corpus_policy_id=training_policy.training_data_policy_id,
        training_corpus_id=corpus_manifest.training_corpus_id,
        training_corpus_digest=corpus_manifest.training_corpus_digest,
        eligible_train_examples=eligible,
        training_example_cap=GATE15_EXAMPLE_CAP,
        cap_rationale=(
            "the Literal-locked Gate 10F real-execution safety envelope "
            "permits at most 64 examples / 64 optimizer steps per bounded "
            "run, and CPU full fine-tuning practicality on the approved "
            "M-series host; deterministic first-64 canonical order"),
        model_approval_id=approval.approval_id,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=(
            resolved_tokenizer.resolved_tokenizer_artifact_id),
        model_identifier=GATE15_MODEL_IDENTIFIER,
        model_revision=GATE15_MODEL_REVISION,
        tokenizer_revision=GATE15_MODEL_REVISION,
        training_spec_id=spec.training_spec_id,
        training_plan_id=plan.training_plan_id,
        training_plan_digest=loaded_plan.manifest.plan_digest,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        objective_policy_id=objective_policy.objective_policy_id,
        runtime_envelope=envelope,
        prompt_template_id=template.prompt_template_id,
        decoding=decoding,
        normalization_policy_id=task.normalization.policy_id,
        scoring_policy_version=task.scoring_policy_version,
        interpretation_policy_id=(
            interpretation_policy.interpretation_policy_id),
        success_policy=success_policy)
    preregistration = preregister_experiment(
        experiment_spec, experiments_root)
    phases = start_phase_log()
    phases = advance_phase(phases,
                           ExperimentPhase.TRAINING_CORPUS_FINALIZED)

    # ---- 5. authorization + bounded policies (environmental evidence) -----
    backend = HuggingFaceFullFinetuneBackend(TorchTrainingEnvironmentProbe())
    auth, snapshot = backend.preflight(
        plan_dir=written_plan.root, corpus_root=written_corpus.root,
        model_resolver=model_resolver, tokenizer_resolver=tokenizer_resolver)
    assert auth.authorized, [
        (f.stage.value, f.code, f.detail)
        for f in auth.findings if f.severity.value == "error"]
    written_auth = write_training_authorization(
        auth, snapshot, out_root / "training-authorizations")
    loaded_auth = read_training_authorization(written_auth.root)
    slice_policy, slice_pairs = select_corpus_slice(
        written_corpus.root, max_example_count=GATE15_EXAMPLE_CAP)
    assert len(slice_pairs) == GATE15_EXAMPLE_CAP  # the slice IS the corpus
    execution_policy = build_real_execution_policy(
        approved_backend_id=HF_FULL_FINETUNE_BACKEND_ID,
        authorization_id=auth.authorization_id,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        corpus_slice_id=slice_policy.corpus_slice_id,
        objective_policy_id=objective_policy.objective_policy_id,
        max_runtime_optimizer_steps=GATE15_MAX_STEPS,
        max_epochs=GATE15_EPOCHS, max_examples=GATE15_EXAMPLE_CAP,
        max_sequence_length=GATE15_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE15_EFFECTIVE_BATCH,
        determinism_acceptance=(
            DeterminismCategory.DETERMINISTIC_SUPPORTED.value,))
    phases = advance_phase(phases, ExperimentPhase.PLAN_AUTHORIZED)

    # ---- 6. exactly ONE bounded real training run -------------------------
    executor = RealTrainingExecutor(HFTrainingEngine())
    written_exec = executor.execute(
        plan_dir=written_plan.root, corpus_dir=written_corpus.root,
        authorization_dir=written_auth.root, model_dir=base_dir,
        tokenizer_dir=base_dir, output_root=out_root,
        model_policy=model_policy, slice_policy=slice_policy,
        execution_policy=execution_policy,
        objective_policy=objective_policy)
    assert written_exec.final_state is ExecutionState.COMPLETED
    assert verify_real_execution(written_exec.root).verified is True
    loaded_exec = read_real_execution(written_exec.root)
    assert loaded_exec.result.completed_optimizer_steps <= GATE15_MAX_STEPS
    assert loaded_exec.result.observed_losses
    assert loaded_exec.result.claims_replay_determinism is False
    executions = list((out_root / "real-training-executions").iterdir())
    assert len(executions) == 1  # ONE training run
    phases = advance_phase(phases, ExperimentPhase.TRAINING_COMPLETED)

    # ---- 7. exactly ONE verified treatment checkpoint ---------------------
    checkpoint_dir = (out_root / "real-checkpoints"
                      / str(written_exec.checkpoint_id))
    assert verify_real_checkpoint(checkpoint_dir).verified is True
    ckpt_manifest = read_real_checkpoint(checkpoint_dir).manifest
    lineage = ckpt_manifest.lineage
    assert lineage.real_execution_id == written_exec.execution_id
    assert lineage.training_plan_id == plan.training_plan_id
    assert lineage.training_corpus_id == corpus_manifest.training_corpus_id
    assert len(list((out_root / "real-checkpoints").iterdir())) == 1
    phases = advance_phase(phases, ExperimentPhase.CHECKPOINT_VERIFIED)

    # ---- 8. firewall audit BEFORE any held-out truth is consulted ---------
    payloads = {
        "training_corpus_store": b"".join(
            p.read_bytes() for p in sorted(written_corpus.root.rglob("*"))
            if p.is_file()),
        "training_plan": b"".join(
            p.read_bytes() for p in sorted(written_plan.root.rglob("*"))
            if p.is_file()),
        "authorization": b"".join(
            p.read_bytes() for p in sorted(written_auth.root.rglob("*"))
            if p.is_file()),
        "execution": b"".join(
            p.read_bytes() for p in sorted(written_exec.root.rglob("*"))
            if p.is_file()),
        "checkpoint_manifest": (checkpoint_dir / "manifest.json").read_bytes(),
    }
    firewall = audit_test_firewall(
        prepared=prepared, training_corpus=capped,
        training_side_payloads=payloads)
    assert firewall.passed is True, [c for c in firewall.checks
                                     if not c.passed]

    # ---- 9. matched predictors (weights are the ONLY difference) ----------
    compatibility = build_checkpoint_inference_compatibility()
    device_policy = build_cpu_inference_device_policy()
    trained_bundle = load_verified_checkpoint_bundle(
        checkpoint_dir, compatibility=compatibility)
    base_bundle = load_verified_base_model_bundle(
        base_dir, model_identifier=GATE15_MODEL_IDENTIFIER,
        model_revision=GATE15_MODEL_REVISION,
        architecture_class=GATE15_MODEL_ARCHITECTURE,
        compatibility=compatibility)
    base = VerifiedBaseModelPredictor(
        task=task, bundle=base_bundle,
        backend=HfCheckpointInferenceBackend(
            bundle=base_bundle, device_policy=device_policy),
        prompt_template=template, device_policy=device_policy,
        decoding=decoding)
    trained = VerifiedCheckpointPredictor(
        task=task, bundle=trained_bundle,
        backend=HfCheckpointInferenceBackend(
            bundle=trained_bundle, device_policy=device_policy),
        prompt_template=template, device_policy=device_policy,
        decoding=decoding)
    phases = advance_phase(phases, ExperimentPhase.TEST_EVALUATION_STARTED)

    # ---- 10. all four evaluations on registered corpus v3 -----------------
    fixed = FixedPriorBaseline(
        task=task, fixed_fault_family="bgp_remote_as_mismatch")
    rule = EvidenceRuleBaseline(
        task=task, default_fault_family="bgp_remote_as_mismatch")
    fixed_run = evaluate_prepared_corpus(prepared, fixed, task)
    rule_run = evaluate_prepared_corpus(prepared, rule, task)
    base_run = evaluate_prepared_corpus(prepared, base, task)
    trained_run = evaluate_prepared_corpus(prepared, trained, task)
    digests: dict[str, str] = {}
    for run in (fixed_run, rule_run, base_run, trained_run):
        written_eval = write_evaluation(run, out_root / "evaluations")
        assert verify_evaluation(written_eval.root).verified is True
        digests[run.evaluation_id] = written_eval.evaluation_digest

    # ---- 11. the unchanged Gate 9 benchmark -------------------------------
    benchmark = run_benchmark(
        prepared, task=task, predictors=[fixed, rule, base, trained])
    assert len(benchmark.comparison) == 4
    written_benchmark = write_benchmark(benchmark, out_root / "benchmarks")
    assert verify_benchmark(written_benchmark.root).verified is True
    phases = advance_phase(phases, ExperimentPhase.BENCHMARK_COMPLETED)

    # ---- 12. fairness + paired comparison + interpretation ----------------
    fairness = assess_matched_pair_fairness(
        base=base_model_predictor_facts(base),
        trained=checkpoint_predictor_facts(trained),
        base_run=base_run, trained_run=trained_run)
    comparison_result = build_paired_comparison(
        base_run, trained_run, fairness=fairness)
    interpretation = interpret_paired_comparison(
        comparison_result.comparison, policy=interpretation_policy,
        corpus_provenance=CorpusProvenance.PROJECT_PERSISTED)
    assert interpretation.engineering_proof_only is False  # real corpus
    written_comparison = write_comparison(
        comparison_result, interpretation, out_root / "comparisons")
    assert verify_comparison(written_comparison.root).verified is True

    # ---- 13. structured-output reliability (diagnostics only) -------------
    report = build_structured_output_report(benchmark)
    written_report = write_structured_output_report(
        report, out_root / "structured-reports")
    assert verify_structured_output_report(written_report.root).verified \
        is True

    # ---- 14. the frozen-policy result --------------------------------------
    losses = loaded_exec.result.observed_losses
    training_binding = TrainingPhaseBinding(
        experiment_id=experiment_spec.experiment_id,
        training_corpus_id=corpus_manifest.training_corpus_id,
        training_corpus_digest=corpus_manifest.training_corpus_digest,
        corpus_slice_id=slice_policy.corpus_slice_id,
        training_spec_id=spec.training_spec_id,
        training_plan_id=plan.training_plan_id,
        training_plan_digest=loaded_plan.manifest.plan_digest,
        authorization_id=auth.authorization_id,
        authorization_digest=loaded_auth.manifest.authorization_digest,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        objective_policy_id=objective_policy.objective_policy_id,
        real_execution_policy_id=execution_policy.real_execution_policy_id,
        model_approval_id=approval.approval_id,
        execution_id=written_exec.execution_id,
        execution_digest=written_exec.execution_digest,
        completed_optimizer_steps=(
            loaded_exec.result.completed_optimizer_steps),
        completed_epochs=loaded_exec.result.completed_epochs,
        observed_loss_count=len(losses),
        first_observed_loss=losses[0], last_observed_loss=losses[-1])
    from verifiednet.datasets.verifier import DatasetCheck

    checkpoint_binding = CheckpointBinding(
        experiment_id=experiment_spec.experiment_id,
        checkpoint_id=ckpt_manifest.checkpoint_id,
        checkpoint_digest=ckpt_manifest.checkpoint_digest,
        lineage_id=lineage.lineage_id,
        real_execution_id=lineage.real_execution_id,
        training_plan_id=lineage.training_plan_id,
        training_corpus_id=lineage.training_corpus_id,
        lineage_checks=(
            DatasetCheck(rule="execution_matches",
                         passed=lineage.real_execution_id
                         == written_exec.execution_id, detail=""),
            DatasetCheck(rule="authorization_matches",
                         passed=lineage.authorization_id
                         == auth.authorization_id, detail=""),
            DatasetCheck(rule="plan_matches",
                         passed=lineage.training_plan_id
                         == plan.training_plan_id, detail=""),
            DatasetCheck(rule="corpus_matches",
                         passed=lineage.training_corpus_id
                         == corpus_manifest.training_corpus_id, detail=""),
            DatasetCheck(rule="model_and_tokenizer_match_base",
                         passed=lineage.model_artifact_id
                         == resolved_model.resolved_model_artifact_id
                         and lineage.tokenizer_artifact_id
                         == resolved_tokenizer
                         .resolved_tokenizer_artifact_id, detail="")))
    evaluation_bindings = EvaluationBindings(
        experiment_id=experiment_spec.experiment_id,
        fixed_prior_evaluation_id=fixed_run.evaluation_id,
        evidence_rule_evaluation_id=rule_run.evaluation_id,
        base_baseline_id=base.spec.baseline_id,
        base_evaluation_id=base_run.evaluation_id,
        base_evaluation_digest=digests[base_run.evaluation_id],
        trained_baseline_id=trained.spec.baseline_id,
        trained_evaluation_id=trained_run.evaluation_id,
        trained_evaluation_digest=digests[trained_run.evaluation_id])
    benchmark_binding = BenchmarkBinding(
        experiment_id=experiment_spec.experiment_id,
        benchmark_id=benchmark.spec.benchmark_id,
        benchmark_digest=written_benchmark.benchmark_digest,
        ranking=tuple(BenchmarkRankingRow(
            predictor_identifier=entry.predictor_identifier,
            rank=entry.rank) for entry in benchmark.ranking))
    paired_summary = PairedSummary(
        experiment_id=experiment_spec.experiment_id,
        comparison_id=comparison_result.comparison.comparison_id,
        comparison_digest=written_comparison.comparison_digest,
        interpretation_conclusion=interpretation.conclusion.value,
        counts_all=compute_partition_paired_counts(
            base_run, trained_run, partitions=None),
        counts_non_train=compute_partition_paired_counts(
            base_run, trained_run,
            partitions=(DatasetPartition.VALIDATION, DatasetPartition.TEST,
                        DatasetPartition.ABSTENTION)),
        counts_test=compute_partition_paired_counts(
            base_run, trained_run, partitions=(DatasetPartition.TEST,)),
        family_test_counts=compute_family_paired_counts(
            base_run, trained_run, partition=DatasetPartition.TEST))
    reliability_summary = ReliabilitySummary(
        experiment_id=experiment_spec.experiment_id,
        report_id=written_report.report_id,
        report_digest=written_report.report_digest,
        base=compute_parser_statistics(base_run),
        trained=compute_parser_statistics(trained_run))
    metrics = extract_primary_metrics(
        base_run, trained_run, comparison_unconfounded=fairness.fair)
    result = build_experiment_result(
        spec=experiment_spec, training=training_binding,
        checkpoint=checkpoint_binding, evaluations=evaluation_bindings,
        benchmark=benchmark_binding, paired=paired_summary,
        reliability=reliability_summary, metrics=metrics,
        qualifiers=(f"gate12_interpretation={interpretation.conclusion.value}",
                    f"firewall_audit={firewall.audit_id}"))
    phases = advance_phase(phases, ExperimentPhase.RESULT_INTERPRETED)
    assert phases.complete is True
    written_experiment = write_experiment_result(
        spec=experiment_spec, training=training_binding,
        checkpoint=checkpoint_binding, evaluations=evaluation_bindings,
        benchmark=benchmark_binding, paired=paired_summary,
        reliability=reliability_summary, result=result,
        experiments_root=experiments_root)
    assert written_experiment.root == preregistration.root
    verification = read_controlled_experiment(written_experiment.root)
    assert verification.result.outcome == result.outcome

    # ---- 15. sources byte-identical; no ML surprise; honest closing -------
    assert _fingerprint(v3_dir) == source_fingerprints["v3_registration"]
    assert _fingerprint(v3_prepared_dir) == source_fingerprints["v3_prepared"]
    assert _fingerprint(base_dir) == source_fingerprints["base_model"]
    assert _fingerprint(readiness_dir) == source_fingerprints["readiness"]
    for prior in prior_roots:
        assert _fingerprint(prior) == source_fingerprints[f"prior:{prior}"]
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    # the OUTCOME is whatever the frozen policy says — never asserted better
    assert result.outcome in ("improved", "regressed", "unchanged", "mixed",
                              "inconclusive")
