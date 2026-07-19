"""Optional Gate 18B operational experiment: the REAL preregistered, one-run
DISCRIMINATIVE-EVIDENCE-REPRESENTATION experiment on registered corpus v3.

This is Gate 17B's experiment with EXACTLY ONE variable changed: the model-visible
evidence representation. Training inputs and the base/treatment inference prompts
are rendered from the Gate 18A v2 observable features (feat-228b357dd9f256fa) via
the v2 prompt (prompt-d4ff1ee1c637ea70) instead of the v1 presence-flag features.
The pinned base model, tokenizer, ordered 64 sources, boundary-aligned objective,
budget, target, parser, scoring, benchmark, and success policy are held identical.

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE18B=1`` AND the
approved materialized base-model dir AND the v3 artifact root AND an output root
AND the ``training-hf`` extras. Strict offline mode is enforced. The base and
treatment SLM arms are byte-matched on features, prompt, tokenizer, decoding,
parser, and scoring — the weights are the only difference. The outcome is whatever
the frozen success policy derives; improvement is NEVER asserted.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

GATE18B_V3_CORPUS_ID = "evalcorpus-8c932345efc3e6e6"
GATE18B_V3_CORPUS_DIGEST = "ecdig-e72927cc7d4b6fd0fa141462"
GATE18B_READINESS_ID = "ready-0b128bea7400a13f"
GATE18B_V2_FEATURE_POLICY_ID = "feat-228b357dd9f256fa"
GATE18B_V2_PROMPT_ID = "prompt-d4ff1ee1c637ea70"
GATE18B_OBJECTIVE_POLICY_ID = "objpol-7e6428964eae2db8"
GATE18B_TARGET_TEMPLATE_ID = "traintgt-286e4ecdff06833e"
GATE18B_MODEL_IDENTIFIER = "Qwen/Qwen2.5-0.5B-Instruct"
GATE18B_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
GATE18B_MODEL_ARCHITECTURE = "Qwen2ForCausalLM"
GATE18B_EXAMPLE_CAP = 64
GATE18B_EPOCHS = 2
GATE18B_MAX_STEPS = 64
GATE18B_MAX_TOTAL_TOKENS = 448
GATE18B_EFFECTIVE_BATCH = 2
GATE18B_LEARNING_RATE = "0.00002"
GATE18B_WARMUP_STEPS = 4
GATE18B_SEED = 15

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE18B") == "1"
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")
_V3_ROOT = os.environ.get("VERIFIEDNET_GATE18B_V3_ROOT", "")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE18B_OUTPUT_ROOT", "")
_MODEL_LICENSE = os.environ.get("VERIFIEDNET_MODEL_LICENSE", "apache-2.0")
_PRIOR_DIRS = os.environ.get("VERIFIEDNET_GATE18B_PRIOR_ARTIFACT_DIRS", "")


def _skip_unless_enabled() -> tuple[Path, Path, Path, Path, Path]:
    if not _ENABLED:
        pytest.skip("VERIFIEDNET_RUN_GATE18B!=1")
    if not _MODEL_DIR or not Path(_MODEL_DIR).is_dir():
        pytest.skip("VERIFIEDNET_LOCAL_MODEL_DIR not set / not a dir")
    if not _V3_ROOT or not Path(_V3_ROOT).is_dir():
        pytest.skip("VERIFIEDNET_GATE18B_V3_ROOT not set / not a dir")
    if not _OUT_ROOT:
        pytest.skip("VERIFIEDNET_GATE18B_OUTPUT_ROOT is not set")
    for module in ("torch", "transformers"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} not installed (training-hf extras required)")
    v3 = Path(_V3_ROOT)
    corpus_dir = v3 / "evaluation-corpora" / GATE18B_V3_CORPUS_ID
    prepared_dir = v3 / "chain" / "prepared"
    readiness_dir = v3 / "readiness-assessments" / GATE18B_READINESS_ID
    run_root = v3 / "chain" / "runs"
    for name, path in (("v3 corpus", corpus_dir), ("v3 prepared", prepared_dir),
                       ("readiness", readiness_dir), ("run chain", run_root)):
        if not path.is_dir():
            pytest.skip(f"{name} dir missing under V3 root: {path}")
    return Path(_MODEL_DIR), corpus_dir, prepared_dir, readiness_dir, run_root


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_gate18b_evidence_representation_experiment_end_to_end(monkeypatch) -> None:
    base_dir, v3_dir, prep_dir, readiness_dir, run_root = _skip_unless_enabled()
    out_root = Path(_OUT_ROOT)
    experiments_root = out_root / "controlled-experiments"
    if experiments_root.exists() and any(experiments_root.iterdir()):
        pytest.skip(f"an experiment already exists: {experiments_root}")

    import urllib.request

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("Gate 18B must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from verifiednet.common.canonical import canonical_json_bytes
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.evidence_features import FeaturePolicyV2
    from verifiednet.datasets.evidence_resolution import resolve_prepared_features_v2
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.datasets.verifier import DatasetCheck
    from verifiednet.evaluation import (
        CorpusProvenance,
        DecodingConfig,
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        HfCheckpointInferenceBackend,
        assess_matched_pair_fairness,
        build_checkpoint_inference_compatibility,
        build_cpu_inference_device_policy,
        build_default_interpretation_policy,
        build_paired_comparison,
        build_structured_output_report,
        compute_parser_statistics,
        diagnosis_task,
        evaluate_prepared_corpus,
        interpret_paired_comparison,
        load_verified_base_model_bundle,
        load_verified_checkpoint_bundle,
        read_evaluation_corpus,
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
    from verifiednet.evaluation.comparison import PairedPredictorFacts
    from verifiednet.evaluation.evidence_eval import (
        V2SlmPredictor,
        benchmark_from_runs,
        evaluate_prepared_corpus_v2,
    )
    from verifiednet.evaluation.prompt import (
        DEFAULT_CANDIDATE_FAMILIES,
        derive_prompt_v2_template_id,
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
        boundary_aligned_objective_policy,
        build_bounded_model_policy,
        build_model_approval,
        build_real_execution_policy,
        build_training_spec,
        contract_aligned_input_template,
        contract_aligned_training_policy,
        derive_model_spec_id,
        derive_tokenizer_spec_id,
        descriptor_from_manifest,
        diagnosis_target_template,
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
    from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
    from verifiednet.training.policy import (
        evidence_observation_input_template,
        evidence_observation_training_policy,
    )

    source_fp = {
        "v3_registration": _fingerprint(v3_dir),
        "v3_prepared": _fingerprint(prep_dir),
        "base_model": _fingerprint(base_dir),
        "run_chain": _fingerprint(run_root),
    }
    prior_roots = [Path(p) for p in _PRIOR_DIRS.split(os.pathsep) if p]
    for prior in prior_roots:
        if prior.is_dir():
            source_fp[f"prior:{prior}"] = _fingerprint(prior)

    # ---- 1. verify v3 corpus + readiness -----------------------------------
    v3 = read_evaluation_corpus(v3_dir)
    assert v3.manifest.evaluation_corpus_id == GATE18B_V3_CORPUS_ID
    assert v3.manifest.corpus_digest == GATE18B_V3_CORPUS_DIGEST
    assert verify_readiness_assessment(readiness_dir).verified is True
    prepared = load_prepared(prep_dir)
    assert prepared.manifest.prepared_digest == v3.manifest.prepared_digest

    task = diagnosis_task()
    target_template = diagnosis_target_template(task_id=task.task_id)
    feature_policy_v2 = FeaturePolicyV2()
    assert feature_policy_v2.policy_id == GATE18B_V2_FEATURE_POLICY_ID
    assert derive_prompt_v2_template_id(
        feature_policy_v2_id=feature_policy_v2.policy_id) == GATE18B_V2_PROMPT_ID

    # ---- 2. build the v2 evidence-observation training corpus --------------
    v3_input = evidence_observation_input_template(
        task_id=task.task_id, feature_policy_v2_id=feature_policy_v2.policy_id)
    v3_policy = evidence_observation_training_policy(
        task_id=task.task_id, input_template=v3_input,
        target_template=target_template)
    full_corpus = build_evidence_observation_corpus(
        prepared, run_root=run_root, feature_policy_v2=feature_policy_v2,
        training_data_policy=v3_policy, input_template=v3_input,
        target_template=target_template)
    capped = cap_training_corpus(full_corpus, max_example_count=GATE18B_EXAMPLE_CAP)
    assert len(capped.examples) == GATE18B_EXAMPLE_CAP

    # same-64-source proof vs the Gate 16A/17B contract-aligned corpus
    v1_input = contract_aligned_input_template(
        task_id=task.task_id,
        feature_policy_id=prepared.manifest.feature_policy_id)
    v1_policy = contract_aligned_training_policy(
        task_id=task.task_id, input_template=v1_input,
        target_template=target_template)
    from verifiednet.training import build_training_corpus

    v1_capped = cap_training_corpus(build_training_corpus(
        prepared, training_data_policy=v1_policy, input_template=v1_input,
        target_template=target_template), max_example_count=GATE18B_EXAMPLE_CAP)
    assert [e.trace.source_example_id for e in capped.examples] == \
        [e.trace.source_example_id for e in v1_capped.examples]
    for v2e, v1e in zip(capped.examples, v1_capped.examples, strict=True):
        assert v2e.target.text == v1e.target.text
        assert v2e.input.text != v1e.input.text  # the v2 observation differs
    assert capped.training_corpus_id != v1_capped.training_corpus_id

    written_corpus = write_training_corpus(capped, out_root / "training-corpora")
    corpus_manifest = load_training_corpus(written_corpus.root).manifest

    # ---- 3. training config (Gate 17B exact) -------------------------------
    model_fields = dict(provider="huggingface",
                        model_identifier=GATE18B_MODEL_IDENTIFIER,
                        model_revision=GATE18B_MODEL_REVISION,
                        model_class=GATE18B_MODEL_ARCHITECTURE)
    model_spec = TrainableModelSpec(
        **model_fields,
        model_spec_id=derive_model_spec_id(load_precision="float32", **model_fields))
    tok_fields = dict(tokenizer_identifier=GATE18B_MODEL_IDENTIFIER,
                      tokenizer_revision=GATE18B_MODEL_REVISION,
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
            max_total_tokens=GATE18B_MAX_TOTAL_TOKENS),
        batch=BatchConfig(per_device_batch_size=1, gradient_accumulation_steps=2,
                          effective_batch_size=GATE18B_EFFECTIVE_BATCH),
        optimization=OptimizationConfig(
            optimizer_name="adamw", learning_rate=GATE18B_LEARNING_RATE),
        scheduler=SchedulerConfig(scheduler_name="linear_warmup",
                                  warmup_steps=GATE18B_WARMUP_STEPS),
        budget=EpochBudget(epochs=GATE18B_EPOCHS),
        seed_policy=SeedPolicy(
            data_order_seed=GATE18B_SEED, model_init_seed=GATE18B_SEED,
            dropout_seed=GATE18B_SEED, backend_seed=GATE18B_SEED))
    plan = plan_for_real_backend(
        spec=spec, corpus=descriptor_from_manifest(corpus_manifest))
    written_plan = write_training_plan(plan, out_root / "training-plans")
    loaded_plan = read_training_plan(written_plan.root)

    # ---- 4. PREREGISTRATION ------------------------------------------------
    envelope = ExperimentRuntimeEnvelope(
        max_examples=GATE18B_EXAMPLE_CAP, max_epochs=GATE18B_EPOCHS,
        max_optimizer_steps=GATE18B_MAX_STEPS,
        max_sequence_length=GATE18B_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE18B_EFFECTIVE_BATCH)
    success_policy = build_success_policy(min_eligible_test_examples=30)
    objective_policy = boundary_aligned_objective_policy()
    assert objective_policy.objective_policy_id == GATE18B_OBJECTIVE_POLICY_ID
    decoding = DecodingConfig(max_tokens=64)
    interpretation_policy = build_default_interpretation_policy()
    model_resolver = LocalModelArtifactResolver(base_dir)
    tokenizer_resolver = LocalTokenizerArtifactResolver(base_dir)
    resolved_model = model_resolver.resolve(model_spec)
    resolved_tokenizer = tokenizer_resolver.resolve(tokenizer_spec)
    model_policy = build_bounded_model_policy(
        permitted_model_identifier=GATE18B_MODEL_IDENTIFIER,
        permitted_model_revision=GATE18B_MODEL_REVISION,
        permitted_architecture_class=GATE18B_MODEL_ARCHITECTURE,
        permitted_tokenizer_revision=GATE18B_MODEL_REVISION,
        max_declared_parameter_count=600_000_000,
        max_sequence_length=GATE18B_MAX_TOTAL_TOKENS,
        max_example_count=GATE18B_EXAMPLE_CAP, max_epochs=GATE18B_EPOCHS,
        max_optimizer_steps=GATE18B_MAX_STEPS,
        max_effective_batch_size=GATE18B_EFFECTIVE_BATCH)
    params = resolved_model.declared_parameter_count
    assert params is not None
    approval = build_model_approval(
        model_identifier=GATE18B_MODEL_IDENTIFIER,
        model_revision=GATE18B_MODEL_REVISION,
        tokenizer_identifier=GATE18B_MODEL_IDENTIFIER,
        tokenizer_revision=GATE18B_MODEL_REVISION,
        architecture_class=GATE18B_MODEL_ARCHITECTURE, parameter_count=params,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=resolved_tokenizer.resolved_tokenizer_artifact_id,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        license_identifier=_MODEL_LICENSE,
        license_review="reviewed: upstream LICENSE declares Apache 2.0")
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "approved-model.json").write_bytes(canonical_json_bytes(approval))

    experiment_spec = build_experiment_spec(
        experiment_name="gate18-evidence-representation", experiment_version=1,
        scientific_question=(
            "Does exposing bounded, observable, non-leaking discriminative "
            "network evidence (Gate 18A v2 features) through a v2 prompt, while "
            "holding the pinned base model, ordered 64 sources, boundary-aligned "
            "objective, budget, target, parser, scoring, and success policy "
            "constant, improve accepted fault-family diagnosis accuracy?"),
        hypothesis=(
            "The v2 discriminative representation increases accepted-test "
            "diagnosis accuracy relative to the matched base model and the Gate "
            "17B treatment while preserving structured-output validity; the null "
            "is no accuracy improvement under the frozen success policy."),
        evaluation_corpus_id=GATE18B_V3_CORPUS_ID,
        evaluation_corpus_digest=GATE18B_V3_CORPUS_DIGEST,
        readiness_assessment_id=GATE18B_READINESS_ID,
        source_prepared_digest=prepared.manifest.prepared_digest,
        training_corpus_policy_id=v3_policy.training_data_policy_id,
        training_corpus_id=corpus_manifest.training_corpus_id,
        training_corpus_digest=corpus_manifest.training_corpus_digest,
        eligible_train_examples=len(full_corpus.examples),
        training_example_cap=GATE18B_EXAMPLE_CAP,
        cap_rationale=(
            "identical to Gate 15/16B/17B: the Literal-locked Gate 10F envelope "
            "permits at most 64; deterministic first-64 canonical order — the "
            "SAME 64 sources"),
        model_approval_id=approval.approval_id,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=resolved_tokenizer.resolved_tokenizer_artifact_id,
        model_identifier=GATE18B_MODEL_IDENTIFIER,
        model_revision=GATE18B_MODEL_REVISION,
        tokenizer_revision=GATE18B_MODEL_REVISION,
        training_spec_id=spec.training_spec_id, training_plan_id=plan.training_plan_id,
        training_plan_digest=loaded_plan.manifest.plan_digest,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        objective_policy_id=objective_policy.objective_policy_id,
        runtime_envelope=envelope, prompt_template_id=GATE18B_V2_PROMPT_ID,
        decoding=decoding, normalization_policy_id=task.normalization.policy_id,
        scoring_policy_version=task.scoring_policy_version,
        interpretation_policy_id=interpretation_policy.interpretation_policy_id,
        success_policy=success_policy)
    preregistration = preregister_experiment(experiment_spec, experiments_root)
    phases = start_phase_log()
    phases = advance_phase(phases, ExperimentPhase.TRAINING_CORPUS_FINALIZED)

    # ---- 5. authorization --------------------------------------------------
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
        written_corpus.root, max_example_count=GATE18B_EXAMPLE_CAP)
    assert len(slice_pairs) == GATE18B_EXAMPLE_CAP
    execution_policy = build_real_execution_policy(
        approved_backend_id=HF_FULL_FINETUNE_BACKEND_ID,
        authorization_id=auth.authorization_id,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        corpus_slice_id=slice_policy.corpus_slice_id,
        objective_policy_id=objective_policy.objective_policy_id,
        max_runtime_optimizer_steps=GATE18B_MAX_STEPS, max_epochs=GATE18B_EPOCHS,
        max_examples=GATE18B_EXAMPLE_CAP, max_sequence_length=GATE18B_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE18B_EFFECTIVE_BATCH,
        determinism_acceptance=(
            DeterminismCategory.DETERMINISTIC_SUPPORTED.value,))
    phases = advance_phase(phases, ExperimentPhase.PLAN_AUTHORIZED)

    # ---- 6. one fresh fine-tune from the pinned base -----------------------
    executor = RealTrainingExecutor(HFTrainingEngine())
    written_exec = executor.execute(
        plan_dir=written_plan.root, corpus_dir=written_corpus.root,
        authorization_dir=written_auth.root, model_dir=base_dir,
        tokenizer_dir=base_dir, output_root=out_root, model_policy=model_policy,
        slice_policy=slice_policy, execution_policy=execution_policy,
        objective_policy=objective_policy)
    assert written_exec.final_state is ExecutionState.COMPLETED
    assert verify_real_execution(written_exec.root).verified is True
    loaded_exec = read_real_execution(written_exec.root)
    assert len(list((out_root / "real-training-executions").iterdir())) == 1
    phases = advance_phase(phases, ExperimentPhase.TRAINING_COMPLETED)

    checkpoint_dir = out_root / "real-checkpoints" / str(written_exec.checkpoint_id)
    assert verify_real_checkpoint(checkpoint_dir).verified is True
    ckpt_manifest = read_real_checkpoint(checkpoint_dir).manifest
    lineage = ckpt_manifest.lineage
    assert lineage.parent_checkpoint_id is None
    assert len(list((out_root / "real-checkpoints").iterdir())) == 1
    phases = advance_phase(phases, ExperimentPhase.CHECKPOINT_VERIFIED)

    # ---- 7. firewall audit BEFORE consulting held-out truth ----------------
    payloads = {
        "training_corpus_store": b"".join(
            p.read_bytes() for p in sorted(written_corpus.root.rglob("*"))
            if p.is_file()),
        "checkpoint_manifest": (checkpoint_dir / "manifest.json").read_bytes(),
    }
    firewall = audit_test_firewall(
        prepared=prepared, training_corpus=capped, training_side_payloads=payloads)
    assert firewall.passed is True, [c for c in firewall.checks if not c.passed]

    # ---- 8. matched v2 predictors (weights the ONLY difference) ------------
    compatibility = build_checkpoint_inference_compatibility()
    device_policy = build_cpu_inference_device_policy()
    trained_bundle = load_verified_checkpoint_bundle(
        checkpoint_dir, compatibility=compatibility)
    base_bundle = load_verified_base_model_bundle(
        base_dir, model_identifier=GATE18B_MODEL_IDENTIFIER,
        model_revision=GATE18B_MODEL_REVISION,
        architecture_class=GATE18B_MODEL_ARCHITECTURE, compatibility=compatibility)
    base = V2SlmPredictor(
        task=task, backend=HfCheckpointInferenceBackend(
            bundle=base_bundle, device_policy=device_policy),
        v2_prompt_template_id=GATE18B_V2_PROMPT_ID, model_identity="base_model",
        predictor_name="v2_base_model_predictor", decoding=decoding,
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    trained = V2SlmPredictor(
        task=task, backend=HfCheckpointInferenceBackend(
            bundle=trained_bundle, device_policy=device_policy),
        v2_prompt_template_id=GATE18B_V2_PROMPT_ID,
        model_identity=str(ckpt_manifest.checkpoint_id),
        predictor_name="v2_checkpoint_predictor", decoding=decoding,
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    phases = advance_phase(phases, ExperimentPhase.TEST_EVALUATION_STARTED)

    # ---- 9. resolve v2 features for the whole eval corpus; evaluate --------
    v2_features = resolve_prepared_features_v2(
        prepared, run_root=run_root, policy=feature_policy_v2)
    fixed = FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch")
    rule = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    fixed_run = evaluate_prepared_corpus(prepared, fixed, task)  # frozen v1 reference
    rule_run = evaluate_prepared_corpus(prepared, rule, task)  # frozen v1 reference
    base_run = evaluate_prepared_corpus_v2(
        prepared, base, task, v2_features=v2_features,
        feature_policy_v2_id=feature_policy_v2.policy_id)
    trained_run = evaluate_prepared_corpus_v2(
        prepared, trained, task, v2_features=v2_features,
        feature_policy_v2_id=feature_policy_v2.policy_id)
    digests: dict[str, str] = {}
    for run in (fixed_run, rule_run, base_run, trained_run):
        written_eval = write_evaluation(run, out_root / "evaluations")
        assert verify_evaluation(written_eval.root).verified is True
        digests[run.evaluation_id] = written_eval.evaluation_digest

    # ---- 10. benchmark from the four runs ----------------------------------
    benchmark = benchmark_from_runs(
        (fixed_run, rule_run, base_run, trained_run), task=task,
        prepared_digest=prepared.manifest.prepared_digest)
    assert len(benchmark.comparison) == 4
    written_benchmark = write_benchmark(benchmark, out_root / "benchmarks")
    assert verify_benchmark(written_benchmark.root).verified is True
    phases = advance_phase(phases, ExperimentPhase.BENCHMARK_COMPLETED)

    # ---- 11. fairness + paired comparison + interpretation -----------------
    def _facts(role: str, run) -> PairedPredictorFacts:
        return PairedPredictorFacts(
            role=role, predictor_id=run.baseline_spec.baseline_id,
            baseline_id=run.baseline_spec.baseline_id,
            prompt_template_id=GATE18B_V2_PROMPT_ID,
            decoding_config_id=decoding.config_id,
            normalization_policy_id=task.normalization.policy_id,
            backend_family="hf-checkpoint-inference-v1",
            inference_precision=device_policy.inference_precision,
            device_policy_id=device_policy.device_policy_id,
            compatibility_id=compatibility.compatibility_id)

    fairness = assess_matched_pair_fairness(
        base=_facts("matched_base_model", base_run),
        trained=_facts("trained_checkpoint", trained_run),
        base_run=base_run, trained_run=trained_run)
    comparison_result = build_paired_comparison(base_run, trained_run, fairness=fairness)
    interpretation = interpret_paired_comparison(
        comparison_result.comparison, policy=interpretation_policy,
        corpus_provenance=CorpusProvenance.PROJECT_PERSISTED)
    written_comparison = write_comparison(
        comparison_result, interpretation, out_root / "comparisons")
    assert verify_comparison(written_comparison.root).verified is True

    report = build_structured_output_report(benchmark)
    written_report = write_structured_output_report(
        report, out_root / "structured-reports")
    assert verify_structured_output_report(written_report.root).verified is True

    # ---- 12. frozen-policy result ------------------------------------------
    losses = loaded_exec.result.observed_losses
    training_binding = TrainingPhaseBinding(
        experiment_id=experiment_spec.experiment_id,
        training_corpus_id=corpus_manifest.training_corpus_id,
        training_corpus_digest=corpus_manifest.training_corpus_digest,
        corpus_slice_id=slice_policy.corpus_slice_id,
        training_spec_id=spec.training_spec_id, training_plan_id=plan.training_plan_id,
        training_plan_digest=loaded_plan.manifest.plan_digest,
        authorization_id=auth.authorization_id,
        authorization_digest=loaded_auth.manifest.authorization_digest,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        objective_policy_id=objective_policy.objective_policy_id,
        real_execution_policy_id=execution_policy.real_execution_policy_id,
        model_approval_id=approval.approval_id,
        execution_id=written_exec.execution_id,
        execution_digest=written_exec.execution_digest,
        completed_optimizer_steps=loaded_exec.result.completed_optimizer_steps,
        completed_epochs=loaded_exec.result.completed_epochs,
        observed_loss_count=len(losses), first_observed_loss=losses[0],
        last_observed_loss=losses[-1])
    checkpoint_binding = CheckpointBinding(
        experiment_id=experiment_spec.experiment_id,
        checkpoint_id=ckpt_manifest.checkpoint_id,
        checkpoint_digest=ckpt_manifest.checkpoint_digest,
        lineage_id=lineage.lineage_id, real_execution_id=lineage.real_execution_id,
        training_plan_id=lineage.training_plan_id,
        training_corpus_id=lineage.training_corpus_id,
        lineage_checks=(
            DatasetCheck(rule="fresh_from_base_no_warm_start",
                         passed=lineage.parent_checkpoint_id is None, detail=""),
            DatasetCheck(rule="corpus_matches",
                         passed=lineage.training_corpus_id
                         == corpus_manifest.training_corpus_id, detail="")))
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
            predictor_identifier=e.predictor_identifier, rank=e.rank)
            for e in benchmark.ranking))
    paired_summary = PairedSummary(
        experiment_id=experiment_spec.experiment_id,
        comparison_id=comparison_result.comparison.comparison_id,
        comparison_digest=written_comparison.comparison_digest,
        interpretation_conclusion=interpretation.conclusion.value,
        counts_all=compute_partition_paired_counts(base_run, trained_run, partitions=None),
        counts_non_train=compute_partition_paired_counts(
            base_run, trained_run,
            partitions=(DatasetPartition.VALIDATION, DatasetPartition.TEST,
                        DatasetPartition.ABSTENTION)),
        counts_test=compute_partition_paired_counts(
            base_run, trained_run, partitions=(DatasetPartition.TEST,)),
        family_test_counts=compute_family_paired_counts(
            base_run, trained_run, partition=DatasetPartition.TEST))
    base_reliability = compute_parser_statistics(base_run)
    trained_reliability = compute_parser_statistics(trained_run)
    reliability_summary = ReliabilitySummary(
        experiment_id=experiment_spec.experiment_id, report_id=written_report.report_id,
        report_digest=written_report.report_digest,
        base=base_reliability, trained=trained_reliability)
    metrics = extract_primary_metrics(
        base_run, trained_run, comparison_unconfounded=fairness.fair)
    result = build_experiment_result(
        spec=experiment_spec, training=training_binding,
        checkpoint=checkpoint_binding, evaluations=evaluation_bindings,
        benchmark=benchmark_binding, paired=paired_summary,
        reliability=reliability_summary, metrics=metrics,
        qualifiers=(
            f"gate12_interpretation={interpretation.conclusion.value}",
            f"base_valid_structured={base_reliability.valid_structured_predictions}",
            f"trained_valid_structured={trained_reliability.valid_structured_predictions}",
            "gate17b_treatment_accepted_test_correct=0 (descriptive)"))
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

    # ---- 13. sources byte-identical; honest closing ------------------------
    assert _fingerprint(v3_dir) == source_fp["v3_registration"]
    assert _fingerprint(prep_dir) == source_fp["v3_prepared"]
    assert _fingerprint(base_dir) == source_fp["base_model"]
    assert _fingerprint(run_root) == source_fp["run_chain"]
    for prior in prior_roots:
        if prior.is_dir():
            assert _fingerprint(prior) == source_fp[f"prior:{prior}"]
    assert result.outcome in ("improved", "regressed", "unchanged", "mixed",
                              "inconclusive")
    base_test = [m for m in base_run.metrics.accepted_partitions
                 if m.partition is DatasetPartition.TEST]
    trained_test = [m for m in trained_run.metrics.accepted_partitions
                    if m.partition is DatasetPartition.TEST]
    b_corr = base_test[0].correct if base_test else 0
    t_corr = trained_test[0].correct if trained_test else 0
    t_eval = trained_test[0].evaluated if trained_test else 0
    # dominant predicted family for the trained arm
    from collections import Counter
    preds: Counter = Counter()
    for r in trained_run.records:
        p = r.prediction
        fam = getattr(p, "fault_family", None)
        preds[fam] += 1
    dominant = preds.most_common(1)[0] if preds else (None, 0)
    print(f"GATE18B: base_valid="
          f"{base_reliability.valid_structured_predictions}/{base_reliability.total} "
          f"trained_valid="
          f"{trained_reliability.valid_structured_predictions}/{trained_reliability.total} "
          f"base_test_correct={b_corr}/{t_eval} trained_test_correct={t_corr}/{t_eval} "
          f"trained_dominant_family={dominant} outcome={result.outcome}")
