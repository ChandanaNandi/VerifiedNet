"""Optional Gate 16B operational experiment: the REAL preregistered, one-run
CONTRACT-ALIGNED-CONDITIONING experiment on registered evaluation corpus v3.

This is Gate 15's experiment with EXACTLY ONE variable changed: the training
input is rendered with the Gate 16A contract-aligned v2 template (byte-
identical to the deployed Gate 8 prompt) instead of Gate 15's v1 template.
Targets, source examples, model, budget, objective, prompt, parser, and the
entire measurement stack are held identical.

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE16B=1``
AND the approved base-model snapshot dir AND the v3 artifact root AND an
output root AND the ``training-hf`` extras. Strict offline mode is enforced.

The test asserts STRUCTURAL and SCIENTIFIC-PROCESS consistency: exactly one
run and one checkpoint, the same 64 ordered sources as Gate 15, a clean
firewall, byte-identical sources (incl. prior checkpoints), and whatever
outcome the frozen success policy derives — it NEVER asserts improvement,
and it never claims task improvement from validity alone.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

#: FIXED experiment inputs (source constants, never environment inputs).
GATE16B_V3_CORPUS_ID = "evalcorpus-8c932345efc3e6e6"
GATE16B_V3_CORPUS_DIGEST = "ecdig-e72927cc7d4b6fd0fa141462"
GATE16B_READINESS_ID = "ready-0b128bea7400a13f"
GATE16B_V2_INPUT_TEMPLATE_ID = "traintmpl-c0513ab53036ae9b"
GATE16B_V2_POLICY_ID = "trainpolicy-336332a846b0f791"
GATE16B_TARGET_TEMPLATE_ID = "traintgt-286e4ecdff06833e"
GATE16B_OBJECTIVE_POLICY_ID = "objpol-e5f36da1a1292f3d"
GATE16B_PROMPT_ID = "prompt-93808d932655a347"
GATE16B_MODEL_IDENTIFIER = "Qwen/Qwen2.5-0.5B-Instruct"
GATE16B_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
GATE16B_MODEL_ARCHITECTURE = "Qwen2ForCausalLM"
#: The exact Gate 15 envelope — held byte-for-byte.
GATE16B_EXAMPLE_CAP = 64
GATE16B_EPOCHS = 2
GATE16B_MAX_STEPS = 64
GATE16B_MAX_TOTAL_TOKENS = 448
GATE16B_EFFECTIVE_BATCH = 2
GATE16B_LEARNING_RATE = "0.00002"
GATE16B_WARMUP_STEPS = 4
GATE16B_SEED = 15

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE16B") == "1"
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")
_V3_ROOT = os.environ.get("VERIFIEDNET_GATE16B_V3_ROOT", "")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE16B_OUTPUT_ROOT", "")
_MODEL_LICENSE = os.environ.get("VERIFIEDNET_MODEL_LICENSE", "apache-2.0")
#: Optional prior-artifact roots to fingerprint (os.pathsep-separated):
#: the Gate 15 run, the Gate 15 checkpoint, the Gate 10F.1 checkpoint, etc.
_PRIOR_DIRS = os.environ.get("VERIFIEDNET_GATE16B_PRIOR_ARTIFACT_DIRS", "")


def _skip_unless_enabled() -> tuple[Path, Path, Path, Path]:
    if not _ENABLED:
        pytest.skip("VERIFIEDNET_RUN_GATE16B!=1")
    if not _MODEL_DIR or not Path(_MODEL_DIR).is_dir():
        pytest.skip("VERIFIEDNET_LOCAL_MODEL_DIR not set / not a dir")
    if not _V3_ROOT or not Path(_V3_ROOT).is_dir():
        pytest.skip("VERIFIEDNET_GATE16B_V3_ROOT not set / not a dir")
    if not _OUT_ROOT:
        pytest.skip("VERIFIEDNET_GATE16B_OUTPUT_ROOT is not set")
    for module in ("torch", "transformers"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} not installed (training-hf extras required)")
    v3_root = Path(_V3_ROOT)
    corpus_dir = v3_root / "evaluation-corpora" / GATE16B_V3_CORPUS_ID
    prepared_dir = v3_root / "chain" / "prepared"
    readiness_dir = v3_root / "readiness-assessments" / GATE16B_READINESS_ID
    for name, path in (("v3 corpus", corpus_dir),
                       ("v3 prepared", prepared_dir),
                       ("readiness", readiness_dir)):
        if not path.is_dir():
            pytest.skip(f"{name} dir missing under V3 root: {path}")
    return Path(_MODEL_DIR), corpus_dir, prepared_dir, readiness_dir


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_gate16b_contract_aligned_experiment_end_to_end(monkeypatch) -> None:
    base_dir, v3_dir, v3_prepared_dir, readiness_dir = _skip_unless_enabled()
    out_root = Path(_OUT_ROOT)
    experiments_root = out_root / "controlled-experiments"
    if experiments_root.exists() and any(experiments_root.iterdir()):
        pytest.skip(f"an experiment already exists: {experiments_root}")

    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 16B must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from verifiednet.common.canonical import canonical_json_bytes
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.models import DatasetPartition
    from verifiednet.datasets.verifier import DatasetCheck
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
        contract_aligned_input_template,
        contract_aligned_training_policy,
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

    source_fingerprints = {
        "v3_registration": _fingerprint(v3_dir),
        "v3_prepared": _fingerprint(v3_prepared_dir),
        "base_model": _fingerprint(base_dir),
        "readiness": _fingerprint(readiness_dir),
    }
    prior_roots = [Path(p) for p in _PRIOR_DIRS.split(os.pathsep) if p]
    for prior in prior_roots:
        if prior.is_dir():
            source_fingerprints[f"prior:{prior}"] = _fingerprint(prior)

    # ---- 1. verify v3 corpus + readiness (fixed identities) ---------------
    v3 = read_evaluation_corpus(v3_dir)
    assert v3.manifest.evaluation_corpus_id == GATE16B_V3_CORPUS_ID
    assert v3.manifest.corpus_digest == GATE16B_V3_CORPUS_DIGEST
    assert verify_readiness_assessment(readiness_dir).verified is True
    readiness = json.loads((readiness_dir / "summary.json").read_bytes())
    assert readiness["assessment_id"] == GATE16B_READINESS_ID
    assert readiness["outcome"] == "ready_for_controlled_experiment"
    prepared = load_prepared(v3_prepared_dir)
    assert prepared.manifest.prepared_digest == v3.manifest.prepared_digest

    task = diagnosis_task()
    target_template = diagnosis_target_template(task_id=task.task_id)

    # ---- 2. Gate 16A identities + derive the v2 train-only corpus ----------
    v2_input_template = contract_aligned_input_template(
        task_id=task.task_id,
        feature_policy_id=prepared.manifest.feature_policy_id)
    assert v2_input_template.input_template_id == GATE16B_V2_INPUT_TEMPLATE_ID
    training_policy = contract_aligned_training_policy(
        task_id=task.task_id, input_template=v2_input_template,
        target_template=target_template)
    assert training_policy.training_data_policy_id == GATE16B_V2_POLICY_ID
    assert training_policy.target_template_id == GATE16B_TARGET_TEMPLATE_ID
    full_corpus = build_training_corpus(
        prepared, training_data_policy=training_policy,
        input_template=v2_input_template, target_template=target_template)
    eligible = len(full_corpus.examples)
    assert eligible == 128
    capped = cap_training_corpus(full_corpus,
                                 max_example_count=GATE16B_EXAMPLE_CAP)
    assert len(capped.examples) == GATE16B_EXAMPLE_CAP

    # ---- same-64-source proof against the Gate 15 v1 corpus ----------------
    v1_input_template = diagnosis_input_template(
        task_id=task.task_id,
        feature_policy_id=prepared.manifest.feature_policy_id)
    v1_capped = cap_training_corpus(build_training_corpus(
        prepared,
        training_data_policy=diagnosis_training_policy(
            task_id=task.task_id, input_template=v1_input_template,
            target_template=target_template),
        input_template=v1_input_template, target_template=target_template),
        max_example_count=GATE16B_EXAMPLE_CAP)
    assert [e.trace.source_example_id for e in capped.examples] == \
        [e.trace.source_example_id for e in v1_capped.examples]
    for v2e, v1e in zip(capped.examples, v1_capped.examples, strict=True):
        assert v2e.target.text == v1e.target.text  # identical targets
        assert v2e.input.text != v1e.input.text     # the ONLY difference
    assert capped.training_corpus_id != v1_capped.training_corpus_id

    written_corpus = write_training_corpus(
        capped, out_root / "training-corpora")
    corpus_manifest = load_training_corpus(written_corpus.root).manifest
    families, topologies, groups = corpus_distributions(capped, prepared)
    assert sum(count for _f, count in families) == GATE16B_EXAMPLE_CAP
    assert len(families) == 4
    assert len(topologies) >= 4 and groups >= GATE16B_EXAMPLE_CAP // 4

    # ---- 3. the preregistered training configuration (Gate 15 exact) ------
    model_fields = dict(provider="huggingface",
                        model_identifier=GATE16B_MODEL_IDENTIFIER,
                        model_revision=GATE16B_MODEL_REVISION,
                        model_class=GATE16B_MODEL_ARCHITECTURE)
    model_spec = TrainableModelSpec(
        **model_fields,
        model_spec_id=derive_model_spec_id(load_precision="float32",
                                           **model_fields))
    tok_fields = dict(tokenizer_identifier=GATE16B_MODEL_IDENTIFIER,
                      tokenizer_revision=GATE16B_MODEL_REVISION,
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
            max_total_tokens=GATE16B_MAX_TOTAL_TOKENS),
        batch=BatchConfig(per_device_batch_size=1,
                          gradient_accumulation_steps=2,
                          effective_batch_size=GATE16B_EFFECTIVE_BATCH),
        optimization=OptimizationConfig(
            optimizer_name="adamw", learning_rate=GATE16B_LEARNING_RATE),
        scheduler=SchedulerConfig(scheduler_name="linear_warmup",
                                  warmup_steps=GATE16B_WARMUP_STEPS),
        budget=EpochBudget(epochs=GATE16B_EPOCHS),
        seed_policy=SeedPolicy(
            data_order_seed=GATE16B_SEED, model_init_seed=GATE16B_SEED,
            dropout_seed=GATE16B_SEED, backend_seed=GATE16B_SEED))
    plan = plan_for_real_backend(
        spec=spec, corpus=descriptor_from_manifest(corpus_manifest))
    assert plan.optimizer_steps <= GATE16B_MAX_STEPS
    written_plan = write_training_plan(plan, out_root / "training-plans")
    loaded_plan = read_training_plan(written_plan.root)

    # ---- 4. PREREGISTRATION (before authorization + training) -------------
    envelope = ExperimentRuntimeEnvelope(
        max_examples=GATE16B_EXAMPLE_CAP, max_epochs=GATE16B_EPOCHS,
        max_optimizer_steps=GATE16B_MAX_STEPS,
        max_sequence_length=GATE16B_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE16B_EFFECTIVE_BATCH)
    success_policy = build_success_policy(min_eligible_test_examples=30)
    objective_policy = build_causal_lm_objective_policy()
    assert objective_policy.objective_policy_id == GATE16B_OBJECTIVE_POLICY_ID
    decoding = DecodingConfig(max_tokens=64)
    template = diagnosis_prompt_template()
    assert template.prompt_template_id == GATE16B_PROMPT_ID
    interpretation_policy = build_default_interpretation_policy()
    model_resolver = LocalModelArtifactResolver(base_dir)
    tokenizer_resolver = LocalTokenizerArtifactResolver(base_dir)
    resolved_model = model_resolver.resolve(model_spec)
    resolved_tokenizer = tokenizer_resolver.resolve(tokenizer_spec)
    model_policy = build_bounded_model_policy(
        permitted_model_identifier=GATE16B_MODEL_IDENTIFIER,
        permitted_model_revision=GATE16B_MODEL_REVISION,
        permitted_architecture_class=GATE16B_MODEL_ARCHITECTURE,
        permitted_tokenizer_revision=GATE16B_MODEL_REVISION,
        max_declared_parameter_count=600_000_000,
        max_sequence_length=GATE16B_MAX_TOTAL_TOKENS,
        max_example_count=GATE16B_EXAMPLE_CAP, max_epochs=GATE16B_EPOCHS,
        max_optimizer_steps=GATE16B_MAX_STEPS,
        max_effective_batch_size=GATE16B_EFFECTIVE_BATCH)
    params = resolved_model.declared_parameter_count
    assert params is not None and 100_000_000 < params < 600_000_000
    approval = build_model_approval(
        model_identifier=GATE16B_MODEL_IDENTIFIER,
        model_revision=GATE16B_MODEL_REVISION,
        tokenizer_identifier=GATE16B_MODEL_IDENTIFIER,
        tokenizer_revision=GATE16B_MODEL_REVISION,
        architecture_class=GATE16B_MODEL_ARCHITECTURE,
        parameter_count=params,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=(
            resolved_tokenizer.resolved_tokenizer_artifact_id),
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        license_identifier=_MODEL_LICENSE,
        license_review="reviewed: upstream LICENSE declares Apache 2.0")
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "approved-model.json").write_bytes(
        canonical_json_bytes(approval))

    experiment_spec = build_experiment_spec(
        experiment_name="gate16-contract-aligned-conditioning",
        experiment_version=1,
        scientific_question=(
            "Does training the same pinned base model on the same ordered 64 "
            "examples, same targets, and same budget as Gate 15, but with "
            "training inputs rendered byte-identically to the deployed Gate 8 "
            "prompt (v2), improve structured-output validity and task "
            "performance?"),
        hypothesis=(
            "Using the contract-aligned v2 training input while holding "
            "targets, sources, model, budget, objective, prompt, parser, and "
            "evaluation constant will increase valid structured output "
            "relative to the matched base model and the Gate 15 treatment, "
            "and may improve accepted diagnosis; the null is no improvement "
            "in validity or accepted diagnosis performance."),
        evaluation_corpus_id=GATE16B_V3_CORPUS_ID,
        evaluation_corpus_digest=GATE16B_V3_CORPUS_DIGEST,
        readiness_assessment_id=GATE16B_READINESS_ID,
        source_prepared_digest=prepared.manifest.prepared_digest,
        training_corpus_policy_id=training_policy.training_data_policy_id,
        training_corpus_id=corpus_manifest.training_corpus_id,
        training_corpus_digest=corpus_manifest.training_corpus_digest,
        eligible_train_examples=eligible,
        training_example_cap=GATE16B_EXAMPLE_CAP,
        cap_rationale=(
            "identical to Gate 15: the Literal-locked Gate 10F envelope "
            "permits at most 64 examples / 64 optimizer steps; deterministic "
            "first-64 canonical order — the SAME 64 sources as Gate 15"),
        model_approval_id=approval.approval_id,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=(
            resolved_tokenizer.resolved_tokenizer_artifact_id),
        model_identifier=GATE16B_MODEL_IDENTIFIER,
        model_revision=GATE16B_MODEL_REVISION,
        tokenizer_revision=GATE16B_MODEL_REVISION,
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

    # ---- 5. authorization (fresh — NOT Gate 15's) -------------------------
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
        written_corpus.root, max_example_count=GATE16B_EXAMPLE_CAP)
    assert len(slice_pairs) == GATE16B_EXAMPLE_CAP
    execution_policy = build_real_execution_policy(
        approved_backend_id=HF_FULL_FINETUNE_BACKEND_ID,
        authorization_id=auth.authorization_id,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        corpus_slice_id=slice_policy.corpus_slice_id,
        objective_policy_id=objective_policy.objective_policy_id,
        max_runtime_optimizer_steps=GATE16B_MAX_STEPS,
        max_epochs=GATE16B_EPOCHS, max_examples=GATE16B_EXAMPLE_CAP,
        max_sequence_length=GATE16B_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE16B_EFFECTIVE_BATCH,
        determinism_acceptance=(
            DeterminismCategory.DETERMINISTIC_SUPPORTED.value,))
    phases = advance_phase(phases, ExperimentPhase.PLAN_AUTHORIZED)

    # ---- 6. exactly ONE real run, FRESH from the pinned base --------------
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
    assert loaded_exec.result.completed_optimizer_steps <= GATE16B_MAX_STEPS
    assert loaded_exec.result.observed_losses
    assert loaded_exec.result.claims_replay_determinism is False
    assert len(list((out_root / "real-training-executions").iterdir())) == 1
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
    assert lineage.parent_checkpoint_id is None  # no warm start
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

    # ---- 9. matched predictors (weights the ONLY difference) --------------
    compatibility = build_checkpoint_inference_compatibility()
    device_policy = build_cpu_inference_device_policy()
    trained_bundle = load_verified_checkpoint_bundle(
        checkpoint_dir, compatibility=compatibility)
    base_bundle = load_verified_base_model_bundle(
        base_dir, model_identifier=GATE16B_MODEL_IDENTIFIER,
        model_revision=GATE16B_MODEL_REVISION,
        architecture_class=GATE16B_MODEL_ARCHITECTURE,
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
    assert interpretation.engineering_proof_only is False
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
            DatasetCheck(rule="fresh_from_base_no_warm_start",
                         passed=lineage.parent_checkpoint_id is None,
                         detail=""),
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
    base_reliability = compute_parser_statistics(base_run)
    trained_reliability = compute_parser_statistics(trained_run)
    reliability_summary = ReliabilitySummary(
        experiment_id=experiment_spec.experiment_id,
        report_id=written_report.report_id,
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
            f"firewall_audit={firewall.audit_id}",
            f"base_valid_structured={base_reliability.valid_structured_predictions}",
            f"trained_valid_structured={trained_reliability.valid_structured_predictions}",
            "gate15_treatment_valid_structured=0 (descriptive)"))
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

    # ---- 15. sources byte-identical; honest closing -----------------------
    assert _fingerprint(v3_dir) == source_fingerprints["v3_registration"]
    assert _fingerprint(v3_prepared_dir) == source_fingerprints["v3_prepared"]
    assert _fingerprint(base_dir) == source_fingerprints["base_model"]
    assert _fingerprint(readiness_dir) == source_fingerprints["readiness"]
    for prior in prior_roots:
        if prior.is_dir():
            assert _fingerprint(prior) == source_fingerprints[f"prior:{prior}"]
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    # the outcome is whatever the frozen policy derives — never asserted better
    assert result.outcome in ("improved", "regressed", "unchanged", "mixed",
                              "inconclusive")
    # record the key scientific fact for the log, never asserting improvement
    print(f"GATE16B: base_valid={base_reliability.valid_structured_predictions}"
          f"/{base_reliability.total} "
          f"trained_valid={trained_reliability.valid_structured_predictions}"
          f"/{trained_reliability.total} outcome={result.outcome}")
