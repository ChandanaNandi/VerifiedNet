"""Gate 10D failure tests: ineligible sources, tampering, store corruption."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    CheckpointCandidate,
    CheckpointError,
    CheckpointManifest,
    CheckpointStoreError,
    assess_checkpoint_eligibility,
    audit_checkpoint_lineage,
    open_checkpoint_payload,
    read_checkpoint_manifest,
    verify_checkpoint,
    write_checkpoint,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def _written(tmp_path, ctx):
    cand = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    return cand, write_checkpoint(cand, tmp_path / "checkpoints")


def test_failed_and_cancelled_executions_cannot_produce(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    for script in ({"fail_after_step": 1}, {"cancel_after_step": 2}):
        bad_dir = ctx.run_execution(**script)
        with pytest.raises(CheckpointError):
            ctx.producer.produce(bad_dir, ctx.plan_dir,
                                 format_spec=ctx.format_spec,
                                 policy=ctx.production_policy)


def test_incomplete_or_corrupt_execution_rejected(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    # corrupt the execution artifact: eligibility must fail via verification,
    # never via trusting a state string
    (ctx.exec_dir / "events.jsonl").write_bytes(
        (ctx.exec_dir / "events.jsonl").read_bytes() + b" ")
    res = assess_checkpoint_eligibility(
        ctx.exec_dir, ctx.plan_dir, ctx.format_spec, ctx.production_policy)
    assert res.eligible is False
    assert any(c.rule == "execution_artifact_verifies" for c in res.failures)
    res = assess_checkpoint_eligibility(
        tmp_path / "nope", ctx.plan_dir, ctx.format_spec, ctx.production_policy)
    assert res.eligible is False


def test_wrong_plan_binding_rejected(tmp_path: Path, checkpoint_pipeline) -> None:
    from verifiednet.training import StepBudget, write_training_plan

    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    other_plan = ctx.execctx.make_plan(budget=StepBudget(max_optimizer_steps=9))
    w = write_training_plan(other_plan, tmp_path / "other-plans")
    res = assess_checkpoint_eligibility(
        ctx.exec_dir, w.root, ctx.format_spec, ctx.production_policy)
    assert res.eligible is False
    assert any(c.rule == "execution_plan_binding" for c in res.failures)
    with pytest.raises(CheckpointError):
        ctx.producer.produce(ctx.exec_dir, w.root,
                             format_spec=ctx.format_spec,
                             policy=ctx.production_policy)


def test_every_lineage_binding_tamper_fails_at_parse(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cand, _ = _written(tmp_path, ctx)
    lineage = cand.lineage.model_dump()
    for field in ("source_execution_id", "source_execution_digest",
                  "source_training_plan_id", "source_plan_digest",
                  "training_spec_id", "training_corpus_id",
                  "training_corpus_digest", "model_spec_id",
                  "tokenizer_spec_id"):
        from verifiednet.training import CheckpointLineage

        with pytest.raises(ValidationError):  # lineage_id no longer matches
            CheckpointLineage.model_validate(lineage | {field: "tampered-value"})
    dump = cand.model_dump()
    with pytest.raises(ValidationError):  # wrong intended checkpoint id
        CheckpointCandidate.model_validate(
            dump | {"intended_checkpoint_id": "checkpoint-" + "0" * 24})


def test_fake_artifact_cannot_be_marked_real_or_parented(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    _cand, written = _written(tmp_path, ctx)
    manifest = read_checkpoint_manifest(written.root)
    dump = manifest.model_dump()
    with pytest.raises(ValidationError):
        CheckpointManifest.model_validate(dump | {"simulated": False})
    compat = dump["compatibility"] | {"loadable_as_real_model": True}
    with pytest.raises(ValidationError):
        CheckpointManifest.model_validate(dump | {"compatibility": compat})
    lineage = dump["lineage"] | {"parent_checkpoint_id": "checkpoint-" + "0" * 24}
    with pytest.raises(ValidationError):
        CheckpointManifest.model_validate(dump | {"lineage": lineage})
    # the independent audit passes on the genuine manifest...
    assert all(c.passed for c in audit_checkpoint_lineage(manifest))
    # ...and stays closed even against model_construct validation bypass:
    bypassed = CheckpointManifest.model_construct(
        **{**dict(manifest), "checkpoint_id": "checkpoint-" + "0" * 24})
    audit = {c.rule: c.passed for c in audit_checkpoint_lineage(bypassed)}
    assert audit["checkpoint_id_recomputes"] is False
    from verifiednet.training import CheckpointLineage

    bypassed_lineage = manifest.model_copy(update={
        "lineage": CheckpointLineage.model_construct(
            **{**dict(manifest.lineage), "lineage_id": "ckptlin-" + "0" * 16})})
    audit = {c.rule: c.passed for c in audit_checkpoint_lineage(bypassed_lineage)}
    assert audit["lineage_id_recomputes"] is False


def test_candidate_payload_without_magic_rejected(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cand, _ = _written(tmp_path, ctx)
    dump = cand.model_dump()
    files = list(dump["files"])
    idx = next(i for i, f in enumerate(files)
               if f["relative_path"] == "payload/model.fakebin")
    files[idx] = files[idx] | {"content": b"REAL-WEIGHTS\x00\x01"}
    with pytest.raises(ValidationError):  # magic is mandatory
        CheckpointCandidate.model_validate(dump | {"files": tuple(files)})


def test_store_corruption_matrix(tmp_path: Path, checkpoint_pipeline) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cand, written = _written(tmp_path, ctx)

    # payload byte corruption → hash mismatch
    victim = written.root / "payload" / "model.fakebin"
    original = victim.read_bytes()
    victim.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    victim.write_bytes(original)

    # size mismatch (append) → size check
    victim.write_bytes(original + b"x")
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule in ("file_sizes_match", "file_hashes_match")
               for c in result.failures)
    victim.write_bytes(original)
    assert verify_checkpoint(written.root).verified is True  # restored

    # unexpected file
    stray = written.root / "payload" / "extra.bin"
    stray.write_bytes(b"stray")
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule == "no_unexpected_files" for c in result.failures)
    stray.unlink()

    # missing required file
    victim.unlink()
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)
    victim.write_bytes(original)

    # executable payload
    os.chmod(victim, 0o755)  # noqa: S103 - deliberately executable to prove rejection
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule == "no_executable_payloads" for c in result.failures)
    os.chmod(victim, 0o644)

    # symlink smuggling
    link = written.root / "payload" / "link.json"
    link.symlink_to(victim)
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule in ("no_symlinks", "no_unexpected_files")
               for c in result.failures)
    link.unlink()

    # malformed manifest
    manifest_path = written.root / "manifest.json"
    good = manifest_path.read_bytes()
    manifest_path.write_bytes(good[:-2])
    assert verify_checkpoint(written.root).verified is False
    manifest_path.write_bytes(good)

    # tampered digest inside the manifest → self-validation fails at parse
    data = json.loads(good)
    data["checkpoint_digest"] = "ckptdig-" + "0" * 24
    manifest_path.write_bytes(json.dumps(data).encode())
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)
    manifest_path.write_bytes(good)
    assert verify_checkpoint(written.root).verified is True

    with pytest.raises(CheckpointStoreError):  # unsafe overwrite
        write_checkpoint(cand, tmp_path / "checkpoints")

    with pytest.raises(CheckpointStoreError):  # undeclared payload access
        open_checkpoint_payload(written.root, "payload/secret.bin")

    assert verify_checkpoint(tmp_path / "missing").verified is False


def test_eligibility_blocks_duplicate_logical_checkpoint(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    _, written = _written(tmp_path, ctx)
    res = assess_checkpoint_eligibility(
        ctx.exec_dir, ctx.plan_dir, ctx.format_spec, ctx.production_policy,
        checkpoints_root=tmp_path / "checkpoints")
    assert res.eligible is False
    assert any(c.rule == "no_existing_checkpoint" for c in res.failures)
    assert written.root.exists()
