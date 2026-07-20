"""Optional Gate 20C operational experiment: the REAL preregistered, one-run
GROUP-AWARE COVERAGE experiment.

This is Gate 19B's experiment with EXACTLY ONE variable changed: the training
source-selection policy moves from the Gate 19A family-balanced 20/20/20/4 (whose
remote-AS coverage is one group / four examples) to the Gate 20C group-aware
budget-preserving 16/16/16/16 over the append-only v4 chain, whose remote-AS 16
examples span >= 8 independent verified TRAIN groups. The pinned base model,
tokenizer, v2 representation (feat-228b357dd9f256fa), v2 prompt
(prompt-d4ff1ee1c637ea70), boundary objective (objpol-7e6428964eae2db8), budget,
target, parser, scoring, benchmark, and success policy (esucc-ab21b8d6e2ab7a70)
are held byte-identical; the base and treatment SLM arms are byte-matched — the
training corpus composition (and thus the weights) is the only difference.
Evaluation uses the byte-identical v3 held-out identities from Gates 18B/19B.

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE20C=1`` AND the
approved materialized base-model dir AND the v3 artifact root AND the v4 root (the
Gate 20B output, holding the v4 prepared corpus and the 16 new verified runs) AND
an output root AND the ``training-hf`` extras. Strict offline mode. The outcome is
whatever the frozen success policy derives; improvement is NEVER asserted.
"""

from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

GATE20C_V3_CORPUS_ID = "evalcorpus-8c932345efc3e6e6"
GATE20C_V3_CORPUS_DIGEST = "ecdig-e72927cc7d4b6fd0fa141462"
GATE20C_READINESS_ID = "ready-0b128bea7400a13f"
GATE20C_V2_FEATURE_POLICY_ID = "feat-228b357dd9f256fa"
GATE20C_V2_PROMPT_ID = "prompt-d4ff1ee1c637ea70"
GATE20C_OBJECTIVE_POLICY_ID = "objpol-7e6428964eae2db8"
GATE20C_MODEL_IDENTIFIER = "Qwen/Qwen2.5-0.5B-Instruct"
GATE20C_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
GATE20C_MODEL_ARCHITECTURE = "Qwen2ForCausalLM"
GATE20C_EXAMPLE_CAP = 64
GATE20C_EPOCHS = 2
GATE20C_MAX_STEPS = 64
GATE20C_MAX_TOTAL_TOKENS = 448
GATE20C_EFFECTIVE_BATCH = 2
GATE20C_LEARNING_RATE = "0.00002"
GATE20C_WARMUP_STEPS = 4
GATE20C_SEED = 15
GATE20C_EXPECTED_COMPOSITION = {
    "bgp_neighbor_removal": 16, "bgp_prefix_withdrawal": 16,
    "bgp_remote_as_mismatch": 16, "iface_admin_shutdown": 16}
GATE20B_CAMPAIGN_RESULT_ID = "rascamp-2241256ebcd32c6c"
GATE20B_READINESS_ID = "rasready-faf453da2f2dae61"

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE20C") == "1"
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")
_V3_ROOT = os.environ.get("VERIFIEDNET_GATE20C_V3_ROOT", "")
_V4_ROOT = os.environ.get("VERIFIEDNET_GATE20C_V4_ROOT", "")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE20C_OUTPUT_ROOT", "")
_MODEL_LICENSE = os.environ.get("VERIFIEDNET_MODEL_LICENSE", "apache-2.0")
_PRIOR_DIRS = os.environ.get("VERIFIEDNET_GATE20C_PRIOR_ARTIFACT_DIRS", "")


def _skip_unless_enabled() -> tuple[Path, Path, Path, Path, Path, Path]:
    if not _ENABLED:
        pytest.skip("VERIFIEDNET_RUN_GATE20C!=1")
    for name, val in (("VERIFIEDNET_LOCAL_MODEL_DIR", _MODEL_DIR),
                      ("VERIFIEDNET_GATE20C_V3_ROOT", _V3_ROOT),
                      ("VERIFIEDNET_GATE20C_V4_ROOT", _V4_ROOT)):
        if not val or not Path(val).is_dir():
            pytest.skip(f"{name} not set / not a dir")
    if not _OUT_ROOT:
        pytest.skip("VERIFIEDNET_GATE20C_OUTPUT_ROOT is not set")
    for module in ("torch", "transformers"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} not installed (training-hf extras required)")
    v3 = Path(_V3_ROOT)
    v4 = Path(_V4_ROOT)
    corpus_dir = v3 / "evaluation-corpora" / GATE20C_V3_CORPUS_ID
    v3_prepared = v3 / "chain" / "prepared"
    readiness_dir = v3 / "readiness-assessments" / GATE20C_READINESS_ID
    v3_runs = v3 / "chain" / "runs"
    v4_prepared = v4 / "chain" / "prepared"
    new_runs = v4 / "chain" / "new-runs"
    for name, path in (("v3 corpus", corpus_dir), ("v3 prepared", v3_prepared),
                       ("readiness", readiness_dir), ("v3 runs", v3_runs),
                       ("v4 prepared", v4_prepared), ("v4 new-runs", new_runs)):
        if not path.is_dir():
            pytest.skip(f"{name} dir missing: {path}")
    return Path(_MODEL_DIR), corpus_dir, v3_prepared, readiness_dir, v3_runs, v4


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _merge_run_roots(v3_runs: Path, new_runs: Path, dest: Path) -> Path:
    """Build a merged run library (symlinks) of every v3 run + every new Gate 20B
    run so v2 evidence for any selected v4 TRAIN source resolves under one root.
    Read-only over the sources; creates only symlinks under ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    for runs in (v3_runs, new_runs):
        for child in sorted(runs.iterdir()):
            if child.name == "index.json" or not child.is_dir():
                continue
            link = dest / child.name
            if not link.exists():
                link.symlink_to(child.resolve())
    return dest


def test_gate20c_group_aware_coverage_experiment_end_to_end(monkeypatch) -> None:
    base_dir, v3_corpus_dir, v3_prep_dir, readiness_dir, v3_runs, v4 = \
        _skip_unless_enabled()
    out_root = Path(_OUT_ROOT)
    experiments_root = out_root / "controlled-experiments"
    if experiments_root.exists() and any(experiments_root.iterdir()):
        pytest.skip(f"an experiment already exists: {experiments_root}")

    import urllib.request

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("Gate 20C must not use the network")

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
    from verifiednet.training.selection import (
        compare_training_corpora,
        family_balanced_selection_policy,
        group_balanced_selection_policy,
        independent_group_counts,
        select_family_balanced,
        select_group_balanced,
    )

    v4_prep_dir = v4 / "chain" / "prepared"
    new_runs = v4 / "chain" / "new-runs"
    source_fp = {
        "v3_registration": _fingerprint(v3_corpus_dir),
        "v3_prepared": _fingerprint(v3_prep_dir),
        "v3_runs": _fingerprint(v3_runs),
        "v4_prepared": _fingerprint(v4_prep_dir),
        "v4_new_runs": _fingerprint(new_runs),
        "base_model": _fingerprint(base_dir),
    }
    prior_roots = [Path(p) for p in _PRIOR_DIRS.split(os.pathsep) if p]
    for prior in prior_roots:
        if prior.is_dir():
            source_fp[f"prior:{prior}"] = _fingerprint(prior)

    # ---- 1. verify v3 eval corpus + readiness + Gate 20B v4 immutability ----
    v3 = read_evaluation_corpus(v3_corpus_dir)
    assert v3.manifest.evaluation_corpus_id == GATE20C_V3_CORPUS_ID
    assert v3.manifest.corpus_digest == GATE20C_V3_CORPUS_DIGEST
    assert verify_readiness_assessment(readiness_dir).verified is True
    v3_prepared = load_prepared(v3_prep_dir)
    assert v3_prepared.manifest.prepared_digest == v3.manifest.prepared_digest
    v4_prepared = load_prepared(v4_prep_dir)
    b20 = json.loads((v4 / "gate20b" / "campaign-result.json").read_bytes())
    assert b20["result_id"] == GATE20B_CAMPAIGN_RESULT_ID
    assert b20["verified_group_count"] == 8 and b20["accepted_example_count"] == 16
    r20 = json.loads((v4 / "gate20b" / "readiness.json").read_bytes())
    assert r20["result_id"] == GATE20B_READINESS_ID

    task = diagnosis_task()
    target_template = diagnosis_target_template(task_id=task.task_id)
    feature_policy_v2 = FeaturePolicyV2()
    assert feature_policy_v2.policy_id == GATE20C_V2_FEATURE_POLICY_ID
    assert derive_prompt_v2_template_id(
        feature_policy_v2_id=feature_policy_v2.policy_id) == GATE20C_V2_PROMPT_ID
    v2_input = evidence_observation_input_template(
        task_id=task.task_id, feature_policy_v2_id=feature_policy_v2.policy_id)
    v2_policy = evidence_observation_training_policy(
        task_id=task.task_id, input_template=v2_input, target_template=target_template)

    # ---- 2. build the GROUP-AWARE 16/16/16/16 v2 training corpus (sole var) --
    merged_runs = _merge_run_roots(v3_runs, new_runs, out_root / "merged-runs")
    group_policy = group_balanced_selection_policy()
    group_selection = select_group_balanced(v4_prepared, policy=group_policy)
    assert group_selection.total_count == GATE20C_EXAMPLE_CAP
    igc = {q.fault_family: q.count for q in independent_group_counts(group_selection)}
    assert igc["bgp_remote_as_mismatch"] >= 8, igc
    corpus_kw = dict(feature_policy_v2=feature_policy_v2,
                     training_data_policy=v2_policy, input_template=v2_input,
                     target_template=target_template)
    coverage_full = build_evidence_observation_corpus(
        v4_prepared, run_root=merged_runs, selection=group_selection, **corpus_kw)
    capped = cap_training_corpus(coverage_full, max_example_count=GATE20C_EXAMPLE_CAP)
    assert len(capped.examples) == GATE20C_EXAMPLE_CAP
    fam_counts = collections.Counter(
        json.loads(e.target.text)["fault_family"] for e in capped.examples)
    assert dict(fam_counts) == GATE20C_EXPECTED_COMPOSITION, dict(fam_counts)

    # comparison vs the Gate 19B family-balanced 20/20/20/4 corpus (built over v3)
    gate19b_selection = select_family_balanced(
        v3_prepared, policy=family_balanced_selection_policy())
    gate19b_corpus = cap_training_corpus(
        build_evidence_observation_corpus(
            v3_prepared, run_root=v3_runs, selection=gate19b_selection, **corpus_kw),
        max_example_count=GATE20C_EXAMPLE_CAP)
    corpus_comparison = compare_training_corpora(gate19b_corpus, capped)
    assert corpus_comparison.shared_inputs_equal
    assert corpus_comparison.shared_targets_equal
    assert corpus_comparison.feature_policy_equal
    assert corpus_comparison.input_template_equal
    assert corpus_comparison.target_template_equal

    written_corpus = write_training_corpus(capped, out_root / "training-corpora")
    corpus_manifest = load_training_corpus(written_corpus.root).manifest
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "selection-result.json").write_bytes(
        canonical_json_bytes(group_selection))
    (out_root / "selection-policy.json").write_bytes(canonical_json_bytes(group_policy))
    (out_root / "corpus-comparison.json").write_bytes(
        canonical_json_bytes(corpus_comparison))

    # ---- 3. training config (Gate 19B exact) -------------------------------
    model_fields = dict(provider="huggingface",
                        model_identifier=GATE20C_MODEL_IDENTIFIER,
                        model_revision=GATE20C_MODEL_REVISION,
                        model_class=GATE20C_MODEL_ARCHITECTURE)
    model_spec = TrainableModelSpec(
        **model_fields,
        model_spec_id=derive_model_spec_id(load_precision="float32", **model_fields))
    tok_fields = dict(tokenizer_identifier=GATE20C_MODEL_IDENTIFIER,
                      tokenizer_revision=GATE20C_MODEL_REVISION,
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
            max_total_tokens=GATE20C_MAX_TOTAL_TOKENS),
        batch=BatchConfig(per_device_batch_size=1, gradient_accumulation_steps=2,
                          effective_batch_size=GATE20C_EFFECTIVE_BATCH),
        optimization=OptimizationConfig(
            optimizer_name="adamw", learning_rate=GATE20C_LEARNING_RATE),
        scheduler=SchedulerConfig(scheduler_name="linear_warmup",
                                  warmup_steps=GATE20C_WARMUP_STEPS),
        budget=EpochBudget(epochs=GATE20C_EPOCHS),
        seed_policy=SeedPolicy(
            data_order_seed=GATE20C_SEED, model_init_seed=GATE20C_SEED,
            dropout_seed=GATE20C_SEED, backend_seed=GATE20C_SEED))
    plan = plan_for_real_backend(
        spec=spec, corpus=descriptor_from_manifest(corpus_manifest))
    written_plan = write_training_plan(plan, out_root / "training-plans")
    loaded_plan = read_training_plan(written_plan.root)

    # ---- 4. PREREGISTRATION ------------------------------------------------
    envelope = ExperimentRuntimeEnvelope(
        max_examples=GATE20C_EXAMPLE_CAP, max_epochs=GATE20C_EPOCHS,
        max_optimizer_steps=GATE20C_MAX_STEPS,
        max_sequence_length=GATE20C_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE20C_EFFECTIVE_BATCH)
    success_policy = build_success_policy(min_eligible_test_examples=30)
    objective_policy = boundary_aligned_objective_policy()
    assert objective_policy.objective_policy_id == GATE20C_OBJECTIVE_POLICY_ID
    decoding = DecodingConfig(max_tokens=64)
    interpretation_policy = build_default_interpretation_policy()
    model_resolver = LocalModelArtifactResolver(base_dir)
    tokenizer_resolver = LocalTokenizerArtifactResolver(base_dir)
    resolved_model = model_resolver.resolve(model_spec)
    resolved_tokenizer = tokenizer_resolver.resolve(tokenizer_spec)
    model_policy = build_bounded_model_policy(
        permitted_model_identifier=GATE20C_MODEL_IDENTIFIER,
        permitted_model_revision=GATE20C_MODEL_REVISION,
        permitted_architecture_class=GATE20C_MODEL_ARCHITECTURE,
        permitted_tokenizer_revision=GATE20C_MODEL_REVISION,
        max_declared_parameter_count=600_000_000,
        max_sequence_length=GATE20C_MAX_TOTAL_TOKENS,
        max_example_count=GATE20C_EXAMPLE_CAP, max_epochs=GATE20C_EPOCHS,
        max_optimizer_steps=GATE20C_MAX_STEPS,
        max_effective_batch_size=GATE20C_EFFECTIVE_BATCH)
    params = resolved_model.declared_parameter_count
    assert params is not None
    approval = build_model_approval(
        model_identifier=GATE20C_MODEL_IDENTIFIER,
        model_revision=GATE20C_MODEL_REVISION,
        tokenizer_identifier=GATE20C_MODEL_IDENTIFIER,
        tokenizer_revision=GATE20C_MODEL_REVISION,
        architecture_class=GATE20C_MODEL_ARCHITECTURE, parameter_count=params,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=resolved_tokenizer.resolved_tokenizer_artifact_id,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        license_identifier=_MODEL_LICENSE,
        license_review="reviewed: upstream LICENSE declares Apache 2.0")
    (out_root / "approved-model.json").write_bytes(canonical_json_bytes(approval))

    experiment_spec = build_experiment_spec(
        experiment_name="gate20c-group-aware-coverage", experiment_version=1,
        scientific_question=(
            "Does group-aware remote-AS TRAIN coverage (16 examples across >= 8 "
            "independent verified v4 groups, a budget-preserving 16/16/16/16 "
            "corpus) cause the pinned 0.5B model to bind bgp_remote_as_changed to "
            "bgp_remote_as_mismatch, holding the model, v2 representation, prompt, "
            "objective, budget, target, parser, scoring, and success policy "
            "constant relative to Gate 19B?"),
        hypothesis=(
            "Adequate independent remote-AS TRAIN coverage yields non-zero "
            "held-out remote-AS recall and macro accuracy above Gate 19B's 0.667, "
            "preserving structured-output validity and the already-learned "
            "families; the null is unchanged remote-AS recall and no macro gain."),
        evaluation_corpus_id=GATE20C_V3_CORPUS_ID,
        evaluation_corpus_digest=GATE20C_V3_CORPUS_DIGEST,
        readiness_assessment_id=GATE20C_READINESS_ID,
        source_prepared_digest=v4_prepared.manifest.prepared_digest,
        training_corpus_policy_id=v2_policy.training_data_policy_id,
        training_corpus_id=corpus_manifest.training_corpus_id,
        training_corpus_digest=corpus_manifest.training_corpus_digest,
        eligible_train_examples=len(coverage_full.examples),
        training_example_cap=GATE20C_EXAMPLE_CAP,
        cap_rationale=(
            "Gate 20C group-aware selection: 16/16/16/16 over the append-only v4 "
            "TRAIN partition; remote-AS spans >= 8 independent verified groups; "
            "budget-preserving 64 examples / 64 steps"),
        model_approval_id=approval.approval_id,
        model_artifact_id=resolved_model.resolved_model_artifact_id,
        tokenizer_artifact_id=resolved_tokenizer.resolved_tokenizer_artifact_id,
        model_identifier=GATE20C_MODEL_IDENTIFIER,
        model_revision=GATE20C_MODEL_REVISION,
        tokenizer_revision=GATE20C_MODEL_REVISION,
        training_spec_id=spec.training_spec_id, training_plan_id=plan.training_plan_id,
        training_plan_digest=loaded_plan.manifest.plan_digest,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        objective_policy_id=objective_policy.objective_policy_id,
        runtime_envelope=envelope, prompt_template_id=GATE20C_V2_PROMPT_ID,
        decoding=decoding, normalization_policy_id=task.normalization.policy_id,
        scoring_policy_version=task.scoring_policy_version,
        interpretation_policy_id=interpretation_policy.interpretation_policy_id,
        success_policy=success_policy)
    preregistration = preregister_experiment(experiment_spec, experiments_root)
    assert preregistration.root.is_dir()
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
        written_corpus.root, max_example_count=GATE20C_EXAMPLE_CAP)
    assert len(slice_pairs) == GATE20C_EXAMPLE_CAP
    execution_policy = build_real_execution_policy(
        approved_backend_id=HF_FULL_FINETUNE_BACKEND_ID,
        authorization_id=auth.authorization_id,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        corpus_slice_id=slice_policy.corpus_slice_id,
        objective_policy_id=objective_policy.objective_policy_id,
        max_runtime_optimizer_steps=GATE20C_MAX_STEPS, max_epochs=GATE20C_EPOCHS,
        max_examples=GATE20C_EXAMPLE_CAP, max_sequence_length=GATE20C_MAX_TOTAL_TOKENS,
        max_effective_batch_size=GATE20C_EFFECTIVE_BATCH,
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
    # the corpus is built from v4 TRAIN sources, so the firewall audits against v4
    # (whose held-out val/test identities are byte-identical to v3's frozen held-out)
    firewall = audit_test_firewall(
        prepared=v4_prepared, training_corpus=capped, training_side_payloads=payloads)
    assert firewall.passed is True, [c for c in firewall.checks if not c.passed]

    # ---- 8. matched v2 predictors (weights the ONLY difference) ------------
    compatibility = build_checkpoint_inference_compatibility()
    device_policy = build_cpu_inference_device_policy()
    trained_bundle = load_verified_checkpoint_bundle(
        checkpoint_dir, compatibility=compatibility)
    base_bundle = load_verified_base_model_bundle(
        base_dir, model_identifier=GATE20C_MODEL_IDENTIFIER,
        model_revision=GATE20C_MODEL_REVISION,
        architecture_class=GATE20C_MODEL_ARCHITECTURE, compatibility=compatibility)
    base = V2SlmPredictor(
        task=task, backend=HfCheckpointInferenceBackend(
            bundle=base_bundle, device_policy=device_policy),
        v2_prompt_template_id=GATE20C_V2_PROMPT_ID, model_identity="base_model",
        predictor_name="v2_base_model_predictor", decoding=decoding,
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    trained = V2SlmPredictor(
        task=task, backend=HfCheckpointInferenceBackend(
            bundle=trained_bundle, device_policy=device_policy),
        v2_prompt_template_id=GATE20C_V2_PROMPT_ID,
        model_identity=str(ckpt_manifest.checkpoint_id),
        predictor_name="v2_checkpoint_predictor", decoding=decoding,
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    phases = advance_phase(phases, ExperimentPhase.TEST_EVALUATION_STARTED)

    # ---- 9. resolve v2 features on the frozen v3 held-out; evaluate --------
    v2_features = resolve_prepared_features_v2(
        v3_prepared, run_root=v3_runs, policy=feature_policy_v2)
    fixed = FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch")
    rule = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    fixed_run = evaluate_prepared_corpus(v3_prepared, fixed, task)
    rule_run = evaluate_prepared_corpus(v3_prepared, rule, task)
    base_run = evaluate_prepared_corpus_v2(
        v3_prepared, base, task, v2_features=v2_features,
        feature_policy_v2_id=feature_policy_v2.policy_id)
    trained_run = evaluate_prepared_corpus_v2(
        v3_prepared, trained, task, v2_features=v2_features,
        feature_policy_v2_id=feature_policy_v2.policy_id)
    digests: dict[str, str] = {}
    for run in (fixed_run, rule_run, base_run, trained_run):
        written_eval = write_evaluation(run, out_root / "evaluations")
        assert verify_evaluation(written_eval.root).verified is True
        digests[run.evaluation_id] = written_eval.evaluation_digest

    # ---- 10. benchmark -----------------------------------------------------
    benchmark = benchmark_from_runs(
        (fixed_run, rule_run, base_run, trained_run), task=task,
        prepared_digest=v3_prepared.manifest.prepared_digest)
    assert len(benchmark.comparison) == 4
    written_benchmark = write_benchmark(benchmark, out_root / "benchmarks")
    assert verify_benchmark(written_benchmark.root).verified is True
    phases = advance_phase(phases, ExperimentPhase.BENCHMARK_COMPLETED)

    # ---- 11. fairness + paired comparison + interpretation -----------------
    def _facts(role: str, run) -> PairedPredictorFacts:
        return PairedPredictorFacts(
            role=role, predictor_id=run.baseline_spec.baseline_id,
            baseline_id=run.baseline_spec.baseline_id,
            prompt_template_id=GATE20C_V2_PROMPT_ID,
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
            f"selection_policy_id={group_policy.policy_id}",
            f"selection_result_digest={group_selection.selection_digest}",
            f"balanced_composition={dict(fam_counts)}",
            f"remoteas_selected_groups={igc['bgp_remote_as_mismatch']}",
            f"gate19b_corpus_overlap={corpus_comparison.intersection_count}",
            f"gate20b_campaign={GATE20B_CAMPAIGN_RESULT_ID}",
            f"base_valid_structured={base_reliability.valid_structured_predictions}",
            f"trained_valid_structured={trained_reliability.valid_structured_predictions}"))
    phases = advance_phase(phases, ExperimentPhase.RESULT_INTERPRETED)
    assert phases.complete is True
    written_result = write_experiment_result(
        spec=experiment_spec, training=training_binding,
        checkpoint=checkpoint_binding, evaluations=evaluation_bindings,
        benchmark=benchmark_binding, paired=paired_summary,
        reliability=reliability_summary, result=result,
        experiments_root=experiments_root)
    assert written_result.root == preregistration.root
    verification = read_controlled_experiment(written_result.root)
    assert verification.result.outcome == result.outcome

    # ---- 13. source + prior immutability -----------------------------------
    after = {
        "v3_registration": _fingerprint(v3_corpus_dir),
        "v3_prepared": _fingerprint(v3_prep_dir),
        "v3_runs": _fingerprint(v3_runs),
        "v4_prepared": _fingerprint(v4_prep_dir),
        "v4_new_runs": _fingerprint(new_runs),
        "base_model": _fingerprint(base_dir),
    }
    for prior in prior_roots:
        if prior.is_dir():
            after[f"prior:{prior}"] = _fingerprint(prior)
    assert after == source_fp, "a source/prior artifact was mutated"

    metric_map = metrics.model_dump(mode="json")
    print(f"GATE20C: experiment={experiment_spec.experiment_id} "
          f"result_root={written_result.root.name} "
          f"selection={group_policy.policy_id} "
          f"corpus={corpus_manifest.training_corpus_id} "
          f"ras_selected_groups={igc['bgp_remote_as_mismatch']} "
          f"composition={dict(fam_counts)} "
          f"checkpoint={ckpt_manifest.checkpoint_id} parent={lineage.parent_checkpoint_id} "
          f"loss={losses[0]}->{losses[-1]} "
          f"steps={loaded_exec.result.completed_optimizer_steps} "
          f"outcome={result.outcome} interpretation={interpretation.conclusion.value} "
          f"metrics={metric_map} "
          f"base_valid={base_reliability.valid_structured_predictions} "
          f"trained_valid={trained_reliability.valid_structured_predictions} "
          f"gate19b_overlap={corpus_comparison.intersection_count}")
