"""Gate 10F failure tests: refusals, bounds, tamper matrix, store corruption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verifiednet.training import (
    RealCheckpointError,
    RealExecutionError,
    RealExecutionStoreError,
    build_minimal_safetensors,
    read_real_execution,
    select_corpus_slice,
    verify_real_checkpoint,
    verify_real_execution,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def test_missing_or_corrupted_authorization_refused(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    with pytest.raises(RealExecutionError):  # missing
        ctx.execute(authorization_dir=tmp_path / "nope")
    victim = Path(ctx.auth_dir) / "authorization.json"
    original = victim.read_bytes()
    victim.write_bytes(original + b" ")
    with pytest.raises(RealExecutionError):  # corrupted
        ctx.execute()
    victim.write_bytes(original)


def test_changed_model_and_vocab_hashes_refused(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    weights = Path(ctx.model_dir) / "model.safetensors"
    original = weights.read_bytes()
    weights.write_bytes(build_minimal_safetensors(
        {"wte.weight": ((4, 4), bytes([7]) * 64),
         "lm_head.weight": ((4, 4), bytes(64))}))
    with pytest.raises(RealExecutionError):  # model resolution changed
        ctx.execute()
    weights.write_bytes(original)
    tok = Path(ctx.tokenizer_dir) / "tokenizer.json"
    tok_original = tok.read_bytes()
    tok.write_bytes(tok_original + b" ")
    with pytest.raises(RealExecutionError):  # tokenizer resolution changed
        ctx.execute()
    tok.write_bytes(tok_original)
    ctx.execute()  # restored evidence executes cleanly


def test_missing_local_files_refuse_never_download(
    tmp_path: Path, realtrain_pipeline, monkeypatch,
) -> None:
    import urllib.request

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("network fallback attempted")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    with pytest.raises(RealExecutionError):
        ctx.execute(model_dir=tmp_path / "missing-model")
    with pytest.raises(RealExecutionError):
        ctx.execute(tokenizer_dir=tmp_path / "missing-tokenizer")


def test_slice_mismatch_refused(tmp_path: Path, realtrain_pipeline) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    smaller, _ = select_corpus_slice(ctx.corpus_root, max_example_count=2)
    with pytest.raises(RealExecutionError):  # policy binds a different slice
        ctx.execute(slice_policy=smaller)


def test_bounds_refused_before_model_loading(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    from verifiednet.training import build_bounded_model_policy

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def tightened(**overrides):
        fields = dict(
            permitted_model_identifier=(
                ctx.model_policy.permitted_model_identifier),
            permitted_model_revision=(
                ctx.model_policy.permitted_model_revision),
            permitted_architecture_class=(
                ctx.model_policy.permitted_architecture_class),
            permitted_tokenizer_revision=(
                ctx.model_policy.permitted_tokenizer_revision),
            max_declared_parameter_count=1_000_000,
            max_sequence_length=1024, max_example_count=16, max_epochs=4,
            max_optimizer_steps=16, max_effective_batch_size=4)
        fields.update(overrides)
        return build_bounded_model_policy(**fields)

    cases = {
        "parameter count too large": tightened(max_declared_parameter_count=1),
        "too many examples": tightened(max_example_count=1),
        "too many epochs": tightened(max_epochs=1),
        "too many optimizer steps": tightened(max_optimizer_steps=1),
        "excessive batch size": tightened(max_effective_batch_size=1),
        "unsupported sequence length": tightened(max_sequence_length=8),
        "unauthorized model identity": tightened(
            permitted_model_identifier="someone-else/model"),
    }
    for name, policy in cases.items():
        with pytest.raises(RealExecutionError, match="revalidation refused"):
            ctx.execute(model_policy=policy)
        # refusal happened BEFORE any execution artifact was produced
        assert not (Path(ctx.output_root) / "real-training-executions").exists(), name


def test_wrong_plan_refused(tmp_path: Path, realtrain_pipeline) -> None:
    # the fake-trainer plan cannot be executed by the real path: its
    # authorization binding cannot match (different implementation id)
    from verifiednet.training import write_training_plan

    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    planctx = ctx.prectx.planctx
    fake_plan = planctx.trainer.plan(spec=planctx.spec,
                                     corpus=planctx.descriptor)
    w = write_training_plan(fake_plan, tmp_path / "fake-plans")
    with pytest.raises(RealExecutionError):
        ctx.execute(plan_dir=w.root)


def test_checkpoint_tamper_matrix(tmp_path: Path, realtrain_pipeline) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    root = Path(ctx.output_root) / "real-checkpoints" / written.checkpoint_id
    assert verify_real_checkpoint(root).verified is True

    # weight bytes
    weights = root / "payload" / "model.safetensors"
    original = weights.read_bytes()
    weights.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    assert verify_real_checkpoint(root).verified is False
    weights.write_bytes(original)

    # config bytes
    config = root / "payload" / "config.json"
    config_original = config.read_bytes()
    config.write_bytes(config_original + b" ")
    assert verify_real_checkpoint(root).verified is False
    config.write_bytes(config_original)

    # tokenizer metadata
    tok = root / "payload" / "tokenizer.json"
    tok_original = tok.read_bytes()
    tok.write_bytes(tok_original + b" ")
    assert verify_real_checkpoint(root).verified is False
    tok.write_bytes(tok_original)

    # lineage / execution id / authorization id inside the manifest
    manifest_path = root / "manifest.json"
    good = manifest_path.read_bytes()
    for field, value in (("real_execution_id", "realexec-" + "0" * 24),
                         ("authorization_id", "trainauth-" + "0" * 24),
                         ("training_corpus_digest", "traindig-" + "0" * 24)):
        data = json.loads(good)
        data["lineage"][field] = value
        manifest_path.write_bytes(json.dumps(data).encode())
        result = verify_real_checkpoint(root)
        assert result.verified is False, field
        assert any(c.rule == "manifest_parses" for c in result.failures)
    manifest_path.write_bytes(good)
    assert verify_real_checkpoint(root).verified is True

    # unexpected file + unsafe overwrite via the candidate boundary
    stray = root / "payload" / "extra.bin"
    stray.write_bytes(b"stray")
    assert verify_real_checkpoint(root).verified is False
    stray.unlink()
    # same identity → same output directories → unsafe overwrite refused
    # (the checkpoint store refuses first; the execution store would too)
    with pytest.raises((RealExecutionStoreError, RealCheckpointError)):
        ctx.execute()


def test_execution_store_corruption(tmp_path: Path, realtrain_pipeline) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    events = written.root / "events.jsonl"
    original = events.read_bytes()
    events.write_bytes(original + b" ")
    result = verify_real_execution(written.root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    events.write_bytes(original)

    # a completed manifest cannot drop its checkpoint id (self-validating)
    manifest_path = written.root / "manifest.json"
    good = manifest_path.read_bytes()
    data = json.loads(good)
    data["checkpoint_id"] = None
    manifest_path.write_bytes(json.dumps(data).encode())
    result = verify_real_execution(written.root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)
    manifest_path.write_bytes(good)
    read_real_execution(written.root)  # healthy again


def test_non_finite_loss_unrepresentable() -> None:
    from pydantic import ValidationError

    from verifiednet.training import (
        ConsistencyClass,
        ExecutionState,
        RealExecutionEvent,
        RealExecutionEventType,
    )

    with pytest.raises(ValidationError):
        RealExecutionEvent(
            execution_id="realexec-x", sequence=0,
            event_type=RealExecutionEventType.OPTIMIZER_STEP_COMPLETED,
            state_before=ExecutionState.RUNNING,
            state_after=ExecutionState.RUNNING, completed_steps=1,
            loss="nan", consistency=ConsistencyClass.BACKEND_REPORTED,
            prev_event_hash="realexec-x", event_hash="revhash-" + "0" * 24)
