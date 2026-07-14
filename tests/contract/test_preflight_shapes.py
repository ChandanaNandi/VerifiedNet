"""Contract tests: Gate 10E models frozen, honest, secret-free, training-free."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import verifiednet.training.preflight as preflight_mod
from verifiednet.training import (
    FindingSeverity,
    PreflightFinding,
    PreflightStage,
    RealTrainerBackend,
    RealTrainerBackendSpec,
    ResolvedModelArtifact,
    ResolvedTokenizerArtifact,
    TrainingEnvironmentSnapshot,
    TrainingExecutionAuthorization,
    build_hf_full_finetune_backend_spec,
)

pytestmark = pytest.mark.contract

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _auth(tmp_path, preflight_pipeline):
    ctx = preflight_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    auth, snapshot = ctx.backend.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    return ctx, auth, snapshot


def test_backend_spec_frozen_and_self_validating() -> None:
    spec = build_hf_full_finetune_backend_spec()
    assert RealTrainerBackendSpec.model_validate_json(
        spec.model_dump_json()) == spec
    with pytest.raises(ValidationError):
        spec.backend_name = "other"  # frozen
    dump = spec.model_dump()
    with pytest.raises(ValidationError):  # extras forbidden
        RealTrainerBackendSpec.model_validate(dump | {"surprise": 1})
    with pytest.raises(ValidationError):  # tampered id
        RealTrainerBackendSpec.model_validate(
            dump | {"backend_spec_id": "trainbk-" + "0" * 16})
    with pytest.raises(ValidationError):  # contract change → new id required
        RealTrainerBackendSpec.model_validate(
            dump | {"supported_optimizers": ("adamw", "sgd")})
    with pytest.raises(ValidationError):  # only the modeled training mode
        RealTrainerBackendSpec.model_validate(
            dump | {"training_mode": "lora_single_device"})


def test_no_training_method_on_the_backend_boundary(
    tmp_path: Path, preflight_pipeline,
) -> None:
    ctx, _, _ = _auth(tmp_path, preflight_pipeline)
    assert isinstance(ctx.backend, RealTrainerBackend)
    for name in ("train", "fit", "execute", "run_training", "save_checkpoint"):
        assert not hasattr(ctx.backend, name), name
    assert not hasattr(RealTrainerBackend, "train")
    # preflight requires plan/corpus/resolvers by keyword — no shortcut exists
    import inspect

    params = inspect.signature(ctx.backend.preflight).parameters
    assert {"plan_dir", "corpus_root", "model_resolver",
            "tokenizer_resolver"} <= set(params)


def test_mutable_revisions_unrepresentable_in_resolutions(
    tmp_path: Path, preflight_pipeline,
) -> None:
    _, auth, _ = _auth(tmp_path, preflight_pipeline)
    assert auth.model_artifact is not None
    assert auth.tokenizer_artifact is not None
    for bad in ("latest", "main", "MASTER", "head"):
        with pytest.raises(ValidationError):
            ResolvedModelArtifact.model_validate(
                auth.model_artifact.model_dump() | {"model_revision": bad})
        with pytest.raises(ValidationError):
            ResolvedTokenizerArtifact.model_validate(
                auth.tokenizer_artifact.model_dump()
                | {"tokenizer_revision": bad})


def test_authorization_cannot_be_true_with_errors(
    tmp_path: Path, preflight_pipeline,
) -> None:
    _, auth, _ = _auth(tmp_path, preflight_pipeline)
    dump = auth.model_dump()
    error_finding = PreflightFinding(
        stage=PreflightStage.AUTHORIZATION, code="synthetic_error",
        severity=FindingSeverity.ERROR, message="synthetic",
        remediation="none").model_dump()
    with pytest.raises(ValidationError):
        TrainingExecutionAuthorization.model_validate(
            dump | {"findings": (*dump["findings"], error_finding)})
    with pytest.raises(ValidationError):  # incomplete stages rejected
        TrainingExecutionAuthorization.model_validate(
            dump | {"findings": tuple(dump["findings"][:-1])})
    with pytest.raises(ValidationError):  # out-of-order findings rejected
        TrainingExecutionAuthorization.model_validate(
            dump | {"findings": tuple(reversed(dump["findings"]))})
    with pytest.raises(ValidationError):  # tampered id
        TrainingExecutionAuthorization.model_validate(
            dump | {"authorization_id": "trainauth-" + "0" * 24})
    with pytest.raises(ValidationError):  # authorized without resolutions
        TrainingExecutionAuthorization.model_validate(
            dump | {"model_artifact": None})


def test_snapshot_excludes_host_sensitive_fields(
    tmp_path: Path, preflight_pipeline,
) -> None:
    _, _, snapshot = _auth(tmp_path, preflight_pipeline)
    fields = set(type(snapshot).model_fields)
    forbidden = {"username", "hostname", "home_directory", "cwd",
                 "environment_variables", "process_id", "timestamp",
                 "created_at", "absolute_path"}
    assert not (fields & forbidden)
    # extra="forbid": such fields are structurally unrepresentable
    with pytest.raises(ValidationError):
        TrainingEnvironmentSnapshot.model_validate(
            snapshot.model_dump() | {"hostname": "secret-host"})
    with pytest.raises(ValidationError):
        TrainingEnvironmentSnapshot.model_validate(
            snapshot.model_dump() | {"username": "alice"})


def test_no_ml_imports_and_no_checkpoint_output_in_gate_10e() -> None:
    # importing the whole Gate 10E surface must not pull any ML framework
    import verifiednet.training.authstore
    import verifiednet.training.backend
    import verifiednet.training.preflight
    import verifiednet.training.resolve  # noqa: F401

    for forbidden in ("torch", "transformers", "tokenizers", "safetensors",
                      "peft", "accelerate", "bitsandbytes", "deepspeed"):
        assert forbidden not in sys.modules, forbidden
    # no checkpoint-producing API exists in the preflight surface
    for name in dir(preflight_mod):
        lowered = name.lower()
        assert "write_checkpoint" not in lowered
        assert "save_weights" not in lowered
        assert "load_model" not in lowered


def test_probe_is_injected_never_implicit_network(
    tmp_path: Path, preflight_pipeline,
) -> None:
    # the backend adapter observes only through its probe; swapping the probe
    # changes the evidence without touching plan identity
    ctx, auth, _ = _auth(tmp_path, preflight_pipeline)
    from verifiednet.training import FakeEnvironmentProbe

    other = ctx.make_backend(FakeEnvironmentProbe(
        packages={"torch": ("2.5.1", True), "transformers": ("4.46.0", True)}))
    auth2, _ = other.preflight(
        plan_dir=ctx.plan_dir, corpus_root=ctx.corpus_root,
        model_resolver=ctx.model_resolver,
        tokenizer_resolver=ctx.tokenizer_resolver)
    assert auth2.training_plan_id == auth.training_plan_id  # intent unchanged
    assert auth2.environment_snapshot_id != auth.environment_snapshot_id
    assert auth2.authorization_id != auth.authorization_id
