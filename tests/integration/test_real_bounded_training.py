"""OPTIONAL integration: the first bounded REAL weight mutation (Gate 10F.1).

DOUBLE-GATED: deselected by default (`-m "not integration"`), and even when
integration tests run it additionally requires:

    VERIFIEDNET_RUN_REAL_TRAINING=1
    VERIFIEDNET_LOCAL_MODEL_DIR=<dereferenced approved snapshot copy>
    VERIFIEDNET_MODEL_REVISION=<exact immutable HF snapshot commit>
    the training-hf extras (torch + transformers + safetensors)

Optional:

    VERIFIEDNET_MODEL_IDENTIFIER   (default Qwen/Qwen2.5-0.5B-Instruct)
    VERIFIEDNET_MODEL_ARCHITECTURE (default Qwen2ForCausalLM)
    VERIFIEDNET_MODEL_LICENSE      (default apache-2.0)
    VERIFIEDNET_REAL_OUTPUT_ROOT   (preserve artifacts outside tmp)

It builds the COMPLETE real chain for the approved model on this machine:
Gate 10B plan (real backend, exact pinned revisions, tiny epoch budget,
batch 1) → Gate 10E authorization (torch-backed probe + local content-hashed
resolvers over the approved snapshot copy) → Gate 10F bounded execution with
the real HF engine (strictly local-files-only; the network is trapped) →
exactly one genuine verified checkpoint. It proves real weight mutation by
tensor-byte hashing, never exposing values, and it runs no evaluation and no
benchmark. The tiny slice proves EXECUTION, not a useful networking model.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

_ENABLED = os.environ.get("VERIFIEDNET_RUN_REAL_TRAINING") == "1"
_HAS_TORCH = importlib.util.find_spec("torch") is not None
_HAS_TRANSFORMERS = importlib.util.find_spec("transformers") is not None
_MODEL_DIR = os.environ.get("VERIFIEDNET_LOCAL_MODEL_DIR", "")
_MODEL_REVISION = os.environ.get("VERIFIEDNET_MODEL_REVISION", "")
_MODEL_IDENTIFIER = os.environ.get(
    "VERIFIEDNET_MODEL_IDENTIFIER", "Qwen/Qwen2.5-0.5B-Instruct")
_MODEL_ARCHITECTURE = os.environ.get(
    "VERIFIEDNET_MODEL_ARCHITECTURE", "Qwen2ForCausalLM")
_MODEL_LICENSE = os.environ.get("VERIFIEDNET_MODEL_LICENSE", "apache-2.0")


@pytest.mark.skipif(not _ENABLED,
                    reason="set VERIFIEDNET_RUN_REAL_TRAINING=1 to enable "
                           "the bounded real-training integration test")
@pytest.mark.skipif(not (_HAS_TORCH and _HAS_TRANSFORMERS),
                    reason="training-hf extras are not installed")
@pytest.mark.skipif(not _MODEL_DIR,
                    reason="VERIFIEDNET_LOCAL_MODEL_DIR is not set")
@pytest.mark.skipif(not _MODEL_REVISION,
                    reason="VERIFIEDNET_MODEL_REVISION is not set")
def test_bounded_real_training_mutates_weights(
    tmp_path: Path, plan_pipeline, monkeypatch,
) -> None:
    import urllib.request

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
        RealTrainingExecutor,
        SequenceLengthPolicy,
        TokenizerSpec,
        TorchTrainingEnvironmentProbe,
        TrainableModelSpec,
        build_bounded_model_policy,
        build_causal_lm_objective_policy,
        build_model_approval,
        build_real_execution_policy,
        derive_model_spec_id,
        derive_tokenizer_spec_id,
        plan_for_real_backend,
        read_real_checkpoint,
        read_real_execution,
        select_corpus_slice,
        verify_real_checkpoint,
        verify_real_execution,
        write_training_authorization,
        write_training_plan,
    )

    def _no_network(*a: object, **k: object) -> object:
        raise AssertionError("real training attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    model_dir = Path(_MODEL_DIR)
    output_root = Path(os.environ.get("VERIFIEDNET_REAL_OUTPUT_ROOT",
                                      str(tmp_path / "real-out")))

    # ---- the approved bounded spec for THIS exact snapshot ----------------
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    model_fields = dict(provider="huggingface",
                        model_identifier=_MODEL_IDENTIFIER,
                        model_revision=_MODEL_REVISION,
                        model_class=_MODEL_ARCHITECTURE)
    model_spec = TrainableModelSpec(
        **model_fields,
        model_spec_id=derive_model_spec_id(load_precision="float32",
                                           **model_fields))
    tok_fields = dict(tokenizer_identifier=_MODEL_IDENTIFIER,
                      tokenizer_revision=_MODEL_REVISION,
                      tokenizer_class="AutoTokenizer")
    tokenizer_spec = TokenizerSpec(
        **tok_fields,
        tokenizer_spec_id=derive_tokenizer_spec_id(
            special_vocab_policy="model_defaults", padding_policy="right",
            truncation_policy="fail_closed", **tok_fields))
    spec = ctx.make_spec(
        model=model_spec, tokenizer=tokenizer_spec,
        trainer_implementation_id=HF_FULL_FINETUNE_BACKEND_ID,
        sequence_policy=SequenceLengthPolicy(
            max_input_tokens=384, max_target_tokens=64, max_total_tokens=448),
        batch=BatchConfig(per_device_batch_size=1,
                          gradient_accumulation_steps=1,
                          effective_batch_size=1),
        budget=EpochBudget(epochs=1))
    plan = plan_for_real_backend(spec=spec, corpus=ctx.descriptor)
    written_plan = write_training_plan(plan, output_root / "training-plans")

    # ---- REAL Gate 10E authorization on THIS machine -----------------------
    model_resolver = LocalModelArtifactResolver(model_dir)
    tokenizer_resolver = LocalTokenizerArtifactResolver(model_dir)
    backend = HuggingFaceFullFinetuneBackend(TorchTrainingEnvironmentProbe())
    auth, snapshot = backend.preflight(
        plan_dir=written_plan.root, corpus_root=ctx.corpus_root,
        model_resolver=model_resolver, tokenizer_resolver=tokenizer_resolver)
    assert auth.authorized, [
        (f.stage.value, f.code, f.detail)
        for f in auth.findings if f.severity.value == "error"]
    written_auth = write_training_authorization(
        auth, snapshot, output_root / "training-authorizations")
    assert auth.model_artifact is not None
    params = auth.model_artifact.declared_parameter_count
    assert params is not None and 100_000_000 < params < 600_000_000

    # ---- bounded policies + explicit approval record ----------------------
    model_policy = build_bounded_model_policy(
        permitted_model_identifier=_MODEL_IDENTIFIER,
        permitted_model_revision=_MODEL_REVISION,
        permitted_architecture_class=_MODEL_ARCHITECTURE,
        permitted_tokenizer_revision=_MODEL_REVISION,
        max_declared_parameter_count=600_000_000,
        max_sequence_length=1024, max_example_count=16, max_epochs=4,
        max_optimizer_steps=16, max_effective_batch_size=4)
    assert auth.tokenizer_artifact is not None
    approval = build_model_approval(
        model_identifier=_MODEL_IDENTIFIER,
        model_revision=_MODEL_REVISION,
        tokenizer_identifier=_MODEL_IDENTIFIER,
        tokenizer_revision=_MODEL_REVISION,
        architecture_class=_MODEL_ARCHITECTURE,
        parameter_count=params,
        model_artifact_id=auth.model_artifact.resolved_model_artifact_id,
        tokenizer_artifact_id=(
            auth.tokenizer_artifact.resolved_tokenizer_artifact_id),
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        license_identifier=_MODEL_LICENSE,
        license_review="reviewed: upstream LICENSE file on the authoritative "
                       "model repository declares Apache License 2.0")
    from verifiednet.common.canonical import canonical_json_bytes

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "approved-model.json").write_bytes(
        canonical_json_bytes(approval))

    slice_policy, slice_pairs = select_corpus_slice(
        ctx.corpus_root, max_example_count=1)
    assert len(slice_pairs) == 1  # minimum needed to prove weight mutation
    objective_policy = build_causal_lm_objective_policy()
    execution_policy = build_real_execution_policy(
        approved_backend_id=HF_FULL_FINETUNE_BACKEND_ID,
        authorization_id=auth.authorization_id,
        bounded_model_policy_id=model_policy.bounded_model_policy_id,
        corpus_slice_id=slice_policy.corpus_slice_id,
        objective_policy_id=objective_policy.objective_policy_id,
        max_runtime_optimizer_steps=16, max_epochs=4, max_examples=16,
        max_sequence_length=1024, max_effective_batch_size=4,
        determinism_acceptance=(
            DeterminismCategory.DETERMINISTIC_SUPPORTED.value,))

    # ---- the bounded REAL run ---------------------------------------------
    source_blob = (model_dir / "model.safetensors").read_bytes()
    executor = RealTrainingExecutor(HFTrainingEngine())
    written = executor.execute(
        plan_dir=written_plan.root, corpus_dir=ctx.corpus_root,
        authorization_dir=written_auth.root, model_dir=model_dir,
        tokenizer_dir=model_dir, output_root=output_root,
        model_policy=model_policy, slice_policy=slice_policy,
        execution_policy=execution_policy, objective_policy=objective_policy)

    # ---- the required real proof -------------------------------------------
    assert written.final_state is ExecutionState.COMPLETED
    assert verify_real_execution(written.root).verified is True
    loaded = read_real_execution(written.root)
    assert loaded.result.completed_optimizer_steps >= 1
    assert loaded.result.observed_losses  # finite testimony, never "quality"
    assert loaded.result.claims_replay_determinism is False

    ckpt = output_root / "real-checkpoints" / written.checkpoint_id
    assert verify_real_checkpoint(ckpt).verified is True
    manifest = read_real_checkpoint(ckpt).manifest
    assert manifest.lineage.real_execution_id == written.execution_id
    assert manifest.lineage.authorization_id == auth.authorization_id
    assert manifest.lineage.corpus_slice_id == slice_policy.corpus_slice_id
    assert manifest.format_spec.payload_format == (
        "verifiednet.real-checkpoint-v1")
    # exactly one checkpoint was produced
    assert len(list((output_root / "real-checkpoints").iterdir())) == 1

    # REAL weight mutation: one trainable tensor changed. The source snapshot
    # is bf16 and the checkpoint is fp32, so a naive byte comparison would be
    # vacuously different — compare BOTH tensors upcast to float32, hashing
    # serialized bytes only (values are never exposed or persisted).
    import torch
    from safetensors import safe_open

    from verifiednet.training import parse_safetensors_header

    trained_path = ckpt / "payload" / "model.safetensors"
    trained_names = sorted(
        k for k in parse_safetensors_header(trained_path.read_bytes())
        if k != "__metadata__")
    tensor_name = next(  # a dense non-embedding weight: gradient is certain
        (k for k in trained_names if "layers.0" in k and k.endswith(".weight")),
        trained_names[0])

    def fp32_hash(path: Path, name: str) -> str:
        with safe_open(str(path), framework="pt") as f:  # type: ignore[no-untyped-call]
            tensor = f.get_tensor(name).to(torch.float32)
        return hashlib.sha256(tensor.numpy().tobytes()).hexdigest()

    before = fp32_hash(model_dir / "model.safetensors", tensor_name)
    after = fp32_hash(trained_path, tensor_name)
    assert before != after, f"tensor {tensor_name} did not change"
    # …while the source pretrained artifact remained byte-identical
    assert (model_dir / "model.safetensors").read_bytes() == source_blob
