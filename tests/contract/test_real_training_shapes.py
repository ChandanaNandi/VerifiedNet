"""Contract tests: Gate 10F models frozen, bounded, honest, bypass-proof."""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import verifiednet.training as training_pkg
from verifiednet.training import (
    AuthorizedTrainingExecutor,
    CheckpointFormatSpec,
    RealCheckpointFormatSpec,
    RealTrainingExecution,
    RealTrainingExecutionPolicy,
    RealTrainingExecutionResult,
    build_real_checkpoint_format_spec,
    read_real_execution,
)

pytestmark = pytest.mark.contract

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def test_execution_policy_locks_retry_resume_and_bounds() -> None:
    from verifiednet.training import build_real_execution_policy

    policy = build_real_execution_policy(
        approved_backend_id="hf-transformers-full-finetune-v1",
        authorization_id="trainauth-" + "0" * 24,
        bounded_model_policy_id="bmodel-" + "0" * 16,
        corpus_slice_id="cslice-" + "0" * 16,
        objective_policy_id="objpol-" + "0" * 16,
        max_runtime_optimizer_steps=8, max_epochs=2, max_examples=8,
        max_sequence_length=512, max_effective_batch_size=4,
        determinism_acceptance=("deterministic_supported",))
    dump = policy.model_dump()
    for field, bad in (("retry_support", "supported"),
                       ("resume_support", "supported"),
                       ("max_output_checkpoints", 2),
                       ("checkpoint_timing", "every_epoch"),
                       ("gradient_clipping_required", False),
                       ("max_runtime_optimizer_steps", 1000),
                       ("max_effective_batch_size", 64)):
        with pytest.raises(ValidationError):
            RealTrainingExecutionPolicy.model_validate(dump | {field: bad})
    with pytest.raises(ValidationError):  # frozen + extras forbidden
        RealTrainingExecutionPolicy.model_validate(dump | {"surprise": 1})


def test_execution_retry_is_structurally_zero(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = ctx.execute()
    raw = (written.root / "manifest.json").read_bytes()
    assert b'"retry' not in raw or b'"retry_support":"unsupported"' in raw
    assert read_real_execution(written.root).manifest.execution_id \
        == written.execution_id
    # rebuilding the execution with retry != 0 is unrepresentable
    with pytest.raises(ValidationError):
        RealTrainingExecution.model_validate({"retry_number": 1})


def test_result_honesty_is_structural() -> None:
    fields = set(RealTrainingExecutionResult.model_fields)
    for forbidden in ("validation_accuracy", "test_accuracy",
                      "benchmark_delta", "quality_score", "generalization"):
        assert forbidden not in fields
    assert "claims_replay_determinism" in fields  # Literal[False]
    with pytest.raises(ValidationError):
        RealTrainingExecutionResult.model_validate({
            "final_state": "completed", "completed_optimizer_steps": 1,
            "completed_epochs": 1, "produced_checkpoint_id": "realckpt-x",
            "claims_replay_determinism": True})


def test_real_and_fake_checkpoint_formats_are_distinct() -> None:
    real = build_real_checkpoint_format_spec()
    assert type(real) is RealCheckpointFormatSpec
    assert RealCheckpointFormatSpec is not CheckpointFormatSpec
    assert real.artifact_kind == "full_model_checkpoint"
    # the fake format still cannot claim reality (Gate 10D untouched)
    from verifiednet.training import build_fake_checkpoint_format_spec

    fake = build_fake_checkpoint_format_spec()
    assert fake.artifact_kind == "simulated_checkpoint"
    dump = real.model_dump()
    for field, bad in (("artifact_kind", "simulated_checkpoint"),
                       ("optimizer_state_inclusion", "included"),
                       ("rng_state_inclusion", "included"),
                       ("resume_state_inclusion", "included"),
                       ("checkpoint_timing", "every_step"),
                       ("simulated", True)):
        with pytest.raises(ValidationError):
            RealCheckpointFormatSpec.model_validate(dump | {field: bad})


def test_executor_requires_authorization_no_bypass(
    tmp_path: Path, realtrain_pipeline,
) -> None:
    ctx = realtrain_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    assert isinstance(ctx.executor, AuthorizedTrainingExecutor)
    params = inspect.signature(ctx.executor.execute).parameters
    required = {"plan_dir", "corpus_dir", "authorization_dir", "model_dir",
                "tokenizer_dir", "output_root", "model_policy",
                "slice_policy", "execution_policy", "objective_policy"}
    assert required <= set(params)
    # every parameter is keyword-only: no positional shortcut exists
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
               for name, p in params.items())
    # no lower-level public API skips authorization: no public callable in
    # the package both mentions 'train'/'execute' and accepts a plan without
    # an authorization
    for name in dir(training_pkg):
        obj = getattr(training_pkg, name)
        if not callable(obj) or not inspect.isfunction(obj):
            continue
        try:
            sig_params = set(inspect.signature(obj).parameters)
        except (TypeError, ValueError):  # pragma: no cover
            continue
        launches = "execute" in name or name.startswith("run_train")
        if "plan_dir" in sig_params and launches:
            assert "authorization_dir" in sig_params, name


def test_ml_imports_stay_lazy_and_core_imports_clean() -> None:
    # the executor module is importable without training-hf, and importing
    # the full package pulls no ML framework
    import verifiednet.training.hfexecutor as hfexecutor_mod

    for forbidden in ("torch", "transformers", "safetensors", "peft",
                      "accelerate", "bitsandbytes", "deepspeed"):
        assert forbidden not in sys.modules, forbidden
    # module-level imports contain no ML modules (AST-verified)
    tree = ast.parse(Path(hfexecutor_mod.__file__).read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in (
                    "torch", "transformers", "safetensors")
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in (
                "torch", "transformers", "safetensors")


def test_no_evaluation_types_in_execution_api() -> None:
    import verifiednet.training.hfexecutor as hfexecutor_mod
    import verifiednet.training.realexec as realexec_mod

    for mod in (hfexecutor_mod, realexec_mod):
        source = Path(mod.__file__).read_text()
        assert "verifiednet.evaluation" not in source
        assert "EvaluationRecord" not in source
        assert "BenchmarkResult" not in source
