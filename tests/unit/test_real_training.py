"""Gate 10F unit tests: policies, slice, objective, execution, checkpoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.training import (
    BoundedTrainingError,
    ExecutionState,
    build_causal_lm_example,
    build_causal_lm_objective_policy,
    build_minimal_safetensors,
    count_safetensors_parameters,
    parse_safetensors_header,
    read_real_checkpoint,
    read_real_execution,
    revalidate_authorization,
    select_corpus_slice,
    validate_finite_loss,
    verify_real_checkpoint,
    verify_real_execution,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def test_policy_ids_deterministic(tmp_path: Path, realtrain_pipeline) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    assert ctx.model_policy.bounded_model_policy_id.startswith("bmodel-")
    assert ctx.slice_policy.corpus_slice_id.startswith("cslice-")
    assert ctx.execution_policy.real_execution_policy_id.startswith("rexecpol-")
    assert ctx.objective_policy.objective_policy_id.startswith("objpol-")
    again = build_causal_lm_objective_policy()
    assert again == ctx.objective_policy


def test_deterministic_slice_selection(tmp_path: Path, realtrain_pipeline) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    s1, p1 = select_corpus_slice(ctx.corpus_root, max_example_count=8)
    s2, p2 = select_corpus_slice(ctx.corpus_root, max_example_count=8)
    assert s1 == s2 and p1 == p2  # deterministic, first-N canonical order
    assert s1 == ctx.slice_policy
    assert len(s1.selected_training_example_ids) == 3  # whole tiny corpus
    smaller, pairs = select_corpus_slice(ctx.corpus_root, max_example_count=2)
    assert smaller.corpus_slice_id != s1.corpus_slice_id  # slice → identity
    assert smaller.selected_training_example_ids == \
        s1.selected_training_example_ids[:2]
    assert len(pairs) == 2


def test_objective_construction_and_label_masking() -> None:
    tokens, labels = build_causal_lm_example(
        input_token_ids=(10, 11, 12), separator_token_ids=(1,),
        target_token_ids=(20, 21), eos_token_id=2, max_total_tokens=16)
    assert tokens == (10, 11, 12, 1, 20, 21, 2)
    # input + separator masked; target + single trailing EOS carry labels
    assert labels == (-100, -100, -100, -100, 20, 21, 2)
    with pytest.raises(BoundedTrainingError):  # overlength fails closed
        build_causal_lm_example(
            input_token_ids=(1,) * 10, separator_token_ids=(1,),
            target_token_ids=(2,) * 10, eos_token_id=2, max_total_tokens=8)


def test_safetensors_structural_helpers() -> None:
    blob = build_minimal_safetensors({"w": ((2, 3), bytes(24))})
    header = parse_safetensors_header(blob)
    assert header["w"]["shape"] == [2, 3]  # type: ignore[index]
    assert count_safetensors_parameters(blob) == 6
    from verifiednet.training import RealCheckpointError

    with pytest.raises(RealCheckpointError):
        parse_safetensors_header(b"short")
    with pytest.raises(RealCheckpointError):
        parse_safetensors_header(b"\xff" * 32)


def test_finite_loss_validation() -> None:
    assert validate_finite_loss("0.693147") == "0.693147"
    for bad in ("nan", "inf", "-Infinity", "not-a-loss"):
        with pytest.raises(ValueError):
            validate_finite_loss(bad)


def test_authorization_revalidation(tmp_path: Path, realtrain_pipeline) -> None:
    from verifiednet.training import (
        LocalModelArtifactResolver,
        LocalTokenizerArtifactResolver,
        read_training_plan,
    )

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    spec = read_training_plan(ctx.plan_dir).plan.request.spec
    model_artifact = LocalModelArtifactResolver(ctx.model_dir).resolve(
        spec.model)
    tokenizer_artifact = LocalTokenizerArtifactResolver(
        ctx.tokenizer_dir).resolve(spec.tokenizer)
    ok, checks = revalidate_authorization(
        ctx.auth_dir, plan_dir=ctx.plan_dir, model_artifact=model_artifact,
        tokenizer_artifact=tokenizer_artifact, model_policy=ctx.model_policy,
        execution_policy=ctx.execution_policy)
    assert ok, [c for c in checks if not c.passed]
    # a changed model hash invalidates the stored authorization
    weights = ctx.model_dir / "model.safetensors"
    original = weights.read_bytes()
    weights.write_bytes(build_minimal_safetensors(
        {"wte.weight": ((4, 4), bytes([1]) * 64),
         "lm_head.weight": ((4, 4), bytes(64))}))
    changed_artifact = LocalModelArtifactResolver(ctx.model_dir).resolve(
        spec.model)
    ok, checks = revalidate_authorization(
        ctx.auth_dir, plan_dir=ctx.plan_dir, model_artifact=changed_artifact,
        tokenizer_artifact=tokenizer_artifact, model_policy=ctx.model_policy,
        execution_policy=ctx.execution_policy)
    assert ok is False
    assert any(c.rule == "model_resolution_unchanged" and not c.passed
               for c in checks)
    weights.write_bytes(original)


def test_stub_execution_completes_and_persists(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    assert written.final_state is ExecutionState.COMPLETED
    assert written.execution_id.startswith("realexec-")
    assert written.checkpoint_id is not None
    result = verify_real_execution(written.root)
    assert result.verified is True, result.failures
    loaded = read_real_execution(written.root)
    assert loaded.result.completed_optimizer_steps == 3
    assert loaded.result.completed_epochs == 3
    assert len(loaded.result.observed_losses) == 3
    assert loaded.result.claims_replay_determinism is False
    assert loaded.result.claims_model_quality is False
    assert loaded.manifest.checkpoint_id == written.checkpoint_id
    # event ordering + monotone steps + finite losses all persisted
    steps = [e.completed_steps for e in loaded.events]
    assert steps == sorted(steps)


def test_real_checkpoint_lineage_and_verification(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    ckpt_root = ctx.output_root / "real-checkpoints" / written.checkpoint_id
    result = verify_real_checkpoint(ckpt_root)
    assert result.verified is True, result.failures
    manifest = read_real_checkpoint(ckpt_root).manifest
    lineage = manifest.lineage
    assert lineage.real_execution_id == written.execution_id
    assert lineage.authorization_id == ctx.execution_policy.authorization_id
    assert lineage.corpus_slice_id == ctx.slice_policy.corpus_slice_id
    assert lineage.completed_optimizer_steps == 3
    assert lineage.parent_checkpoint_id is None
    assert manifest.format_spec.artifact_kind == "full_model_checkpoint"
    assert manifest.format_spec.weights_serialization == "safetensors"
    assert manifest.simulated is False
    # the produced weights are a valid safetensors payload with the SAME
    # parameter count as the source model, but different bytes
    weights = (ckpt_root / "payload" / "model.safetensors").read_bytes()
    source = (ctx.model_dir / "model.safetensors").read_bytes()
    assert count_safetensors_parameters(weights) == \
        count_safetensors_parameters(source)
    assert weights != source


def test_events_have_consistency_classes(tmp_path: Path, realtrain_pipeline) -> None:
    from verifiednet.training import ConsistencyClass, RealExecutionEventType

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    events = read_real_execution(written.root).events
    by_type = {e.event_type: e for e in events}
    # losses are backend-reported, never structurally verified
    step_event = by_type[RealExecutionEventType.OPTIMIZER_STEP_COMPLETED]
    assert step_event.consistency is ConsistencyClass.BACKEND_REPORTED
    assert step_event.loss is not None
    auth_event = by_type[RealExecutionEventType.AUTHORIZATION_ACCEPTED]
    assert auth_event.consistency is ConsistencyClass.STRUCTURALLY_VERIFIED
    types = [e.event_type for e in events]
    assert types[0] is RealExecutionEventType.AUTHORIZATION_ACCEPTED
    assert types[-1] is RealExecutionEventType.CHECKPOINT_PRODUCED


def test_model_approval_record() -> None:
    from pydantic import ValidationError

    from verifiednet.training import (
        ApprovedTrainingModel,
        build_model_approval,
    )

    approval = build_model_approval(
        model_identifier="Qwen/Qwen2.5-0.5B-Instruct",
        model_revision="7ae557604adf67be50417f59c2c2f167def9a775",
        tokenizer_identifier="Qwen/Qwen2.5-0.5B-Instruct",
        tokenizer_revision="7ae557604adf67be50417f59c2c2f167def9a775",
        architecture_class="Qwen2ForCausalLM",
        parameter_count=494_032_768,
        model_artifact_id="modelart-" + "0" * 16,
        tokenizer_artifact_id="tokart-" + "0" * 16,
        bounded_model_policy_id="bmodel-" + "0" * 16,
        license_identifier="apache-2.0",
        license_review="reviewed: upstream LICENSE declares Apache-2.0")
    assert approval.approval_id.startswith("modelappr-")
    assert approval.local_cache_only is True
    # deterministic + self-validating
    again = build_model_approval(**{
        k: getattr(approval, k) for k in (
            "model_identifier", "model_revision", "tokenizer_identifier",
            "tokenizer_revision", "architecture_class", "parameter_count",
            "model_artifact_id", "tokenizer_artifact_id",
            "bounded_model_policy_id", "license_identifier",
            "license_review")})
    assert again == approval
    dump = approval.model_dump()
    with pytest.raises(ValidationError):  # mutable revisions never approved
        ApprovedTrainingModel.model_validate(dump | {"model_revision": "main"})
    with pytest.raises(ValidationError):  # tampered approval id
        ApprovedTrainingModel.model_validate(
            dump | {"approval_id": "modelappr-" + "0" * 16})
    # no host facts are representable
    fields = set(ApprovedTrainingModel.model_fields)
    assert not fields & {"username", "hostname", "cache_path", "timestamp",
                         "hardware_uuid", "serial_number", "home_directory"}
