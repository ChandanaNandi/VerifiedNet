"""Contract tests: Gate 10D models frozen, Literal-locked, honestly simulated."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import verifiednet.training as training_pkg
from verifiednet.training import (
    CheckpointCandidate,
    CheckpointCompatibility,
    CheckpointFormatSpec,
    CheckpointLineage,
    CheckpointManifest,
    CheckpointProductionPolicy,
    build_default_checkpoint_production_policy,
    build_fake_checkpoint_format_spec,
    read_checkpoint_manifest,
    write_checkpoint,
)

pytestmark = pytest.mark.contract

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _candidate(tmp_path, checkpoint_pipeline):
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    return ctx, ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                     format_spec=ctx.format_spec,
                                     policy=ctx.production_policy)


def test_format_spec_is_locked_to_the_fake_format() -> None:
    spec = build_fake_checkpoint_format_spec()
    assert CheckpointFormatSpec.model_validate_json(spec.model_dump_json()) == spec
    with pytest.raises(ValidationError):
        spec.artifact_kind = "real_checkpoint"  # frozen
    dump = spec.model_dump()
    # every impersonation avenue is Literal-locked shut
    for field, bad in (
        ("artifact_kind", "full_model_checkpoint"),
        ("payload_format", "safetensors"),
        ("weights_declaration", "full_model"),
        ("weights_declaration", "lora_adapter"),
        ("optimizer_state_inclusion", "included"),
        ("resume_state_inclusion", "included"),
        ("serialization_format", "safetensors-v1"),
    ):
        with pytest.raises(ValidationError):
            CheckpointFormatSpec.model_validate(dump | {field: bad})
    with pytest.raises(ValidationError):  # tampered id
        CheckpointFormatSpec.model_validate(
            dump | {"format_spec_id": "ckptfmt-" + "0" * 16})
    with pytest.raises(ValidationError):  # extras forbidden
        CheckpointFormatSpec.model_validate(dump | {"surprise": 1})


def test_policy_locks_and_forbids_parent() -> None:
    policy = build_default_checkpoint_production_policy()
    dump = policy.model_dump()
    for field, bad in (
        ("required_execution_state", "failed"),
        ("include_optimizer_state", True),
        ("include_resume_state", True),
        ("parent_checkpoint_policy", "allowed"),
        ("permitted_artifact_kinds", ("real_checkpoint",)),
    ):
        with pytest.raises(ValidationError):
            CheckpointProductionPolicy.model_validate(dump | {field: bad})


def test_compatibility_cannot_claim_real_loadability(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    _, cand = _candidate(tmp_path, checkpoint_pipeline)
    dump = cand.compatibility.model_dump()
    with pytest.raises(ValidationError):
        CheckpointCompatibility.model_validate(
            dump | {"loadable_as_real_model": True})
    with pytest.raises(ValidationError):
        CheckpointCompatibility.model_validate(dump | {"simulated_only": False})
    with pytest.raises(ValidationError):  # real backend list is locked empty
        CheckpointCompatibility.model_validate(
            dump | {"supported_inference_backends": ["ollama-v1"]})
    with pytest.raises(ValidationError):  # tampered id
        CheckpointCompatibility.model_validate(
            dump | {"compatibility_id": "ckptcompat-" + "0" * 16})


def test_lineage_forbids_parent_and_self_validates(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    _, cand = _candidate(tmp_path, checkpoint_pipeline)
    dump = cand.lineage.model_dump()
    with pytest.raises(ValidationError):  # parent checkpoint is structural None
        CheckpointLineage.model_validate(
            dump | {"parent_checkpoint_id": "checkpoint-" + "0" * 24})
    with pytest.raises(ValidationError):
        CheckpointLineage.model_validate(
            dump | {"lineage_id": "ckptlin-" + "0" * 16})
    with pytest.raises(ValidationError):  # any binding change breaks the id
        CheckpointLineage.model_validate(
            dump | {"source_execution_id": "trainexec-" + "0" * 16})


def test_candidate_and_manifest_are_distinct_and_candidate_is_untrusted(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    _ctx, cand = _candidate(tmp_path, checkpoint_pipeline)
    written = write_checkpoint(cand, tmp_path / "checkpoints")
    manifest = read_checkpoint_manifest(written.root)
    # distinct types: the untrusted candidate is never the verified artifact
    assert type(cand) is CheckpointCandidate
    assert type(manifest) is CheckpointManifest
    assert not isinstance(cand, CheckpointManifest)
    # the candidate carries content but NO hashes to trust
    assert "sha256" not in type(cand.files[0]).model_fields
    # the manifest carries verified hashes but no content
    assert "content" not in type(manifest.files[0]).model_fields
    with pytest.raises(ValidationError):  # candidate rejects claiming real
        CheckpointCandidate.model_validate(
            cand.model_dump() | {"simulated": False})
    with pytest.raises(ValidationError):  # manifest simulation is locked too
        CheckpointManifest.model_validate(
            manifest.model_dump() | {"simulated": False})


def test_unsafe_paths_and_duplicate_roles_rejected(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    _, cand = _candidate(tmp_path, checkpoint_pipeline)
    dump = cand.model_dump()
    base = dump["files"][0]
    for bad_path in ("/etc/passwd", "payload/../escape", "payload\\win",
                     "outside.json", "payload/./x", "~payload/x"):
        with pytest.raises(ValidationError):
            CheckpointCandidate.model_validate(
                dump | {"files": (base | {"relative_path": bad_path},
                                  *dump["files"][1:])})
    with pytest.raises(ValidationError):  # duplicate path
        CheckpointCandidate.model_validate(
            dump | {"files": (dump["files"][0], *dump["files"])})
    dup_role = dump["files"][1] | {"role": dump["files"][0]["role"]}
    with pytest.raises(ValidationError):  # duplicate role
        CheckpointCandidate.model_validate(
            dump | {"files": (dump["files"][0], dup_role, *dump["files"][2:])})


def test_no_model_loading_api_and_no_ml_imports() -> None:
    for name in dir(training_pkg):
        lowered = name.lower()
        assert "load_model" not in lowered, name
        assert "load_tokenizer" not in lowered, name
        assert "merge_adapter" not in lowered, name
        assert "upload" not in lowered and "publish" not in lowered, name
    import verifiednet.training.checkpoint
    import verifiednet.training.checkpointstore
    import verifiednet.training.producer  # noqa: F401

    for forbidden in ("torch", "transformers", "tokenizers", "safetensors",
                      "peft", "accelerate", "bitsandbytes", "deepspeed"):
        assert forbidden not in sys.modules, forbidden
