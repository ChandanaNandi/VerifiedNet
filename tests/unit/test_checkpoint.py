"""Gate 10D unit tests: ids, eligibility, producer, payloads, store."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.training import (
    FAKE_PAYLOAD_MAGIC,
    CheckpointFileRole,
    assess_checkpoint_eligibility,
    build_default_checkpoint_production_policy,
    build_fake_checkpoint_format_spec,
    fake_payload_bytes,
    open_checkpoint_payload,
    read_checkpoint_manifest,
    read_verified_checkpoint,
    verify_checkpoint,
    write_checkpoint,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def test_format_spec_and_policy_ids_deterministic() -> None:
    a, b = build_fake_checkpoint_format_spec(), build_fake_checkpoint_format_spec()
    assert a == b
    assert a.format_spec_id.startswith("ckptfmt-")
    pa = build_default_checkpoint_production_policy()
    pb = build_default_checkpoint_production_policy()
    assert pa == pb
    assert pa.production_policy_id.startswith("ckptpol-")


def test_lineage_compatibility_and_checkpoint_ids(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    c1 = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                              format_spec=ctx.format_spec,
                              policy=ctx.production_policy)
    c2 = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                              format_spec=ctx.format_spec,
                              policy=ctx.production_policy)
    assert c1 == c2  # fully deterministic candidate
    assert c1.lineage.lineage_id.startswith("ckptlin-")
    assert c1.compatibility.compatibility_id.startswith("ckptcompat-")
    assert c1.intended_checkpoint_id.startswith("checkpoint-")
    assert c1.lineage.parent_checkpoint_id is None
    assert c1.lineage.retry_number == 0
    # lineage binds the exact persisted sources
    assert c1.lineage.source_execution_id == ctx.exec_dir.name
    assert c1.lineage.source_training_plan_id == ctx.plan_dir.name


def test_fake_payload_bytes_deterministic() -> None:
    kw = dict(execution_id="trainexec-x", training_plan_id="trainplan-y",
              training_spec_id="trainspec-z", model_spec_id="model-m",
              tokenizer_spec_id="tok-t", completed_steps=3,
              format_spec_id="ckptfmt-f")
    a, b = fake_payload_bytes(**kw), fake_payload_bytes(**kw)
    assert a == b
    assert a.startswith(FAKE_PAYLOAD_MAGIC)
    assert len(a) == len(FAKE_PAYLOAD_MAGIC) + 8 * 32
    c = fake_payload_bytes(**(kw | {"completed_steps": 4}))
    assert c != a  # every identity input shapes the bytes


def test_eligibility_completed_and_rejections(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ok = assess_checkpoint_eligibility(
        ctx.exec_dir, ctx.plan_dir, ctx.format_spec, ctx.production_policy,
        checkpoints_root=tmp_path / "checkpoints")
    assert ok.eligible is True, ok.failures

    failed_dir = ctx.run_execution(fail_after_step=1)
    res = assess_checkpoint_eligibility(
        failed_dir, ctx.plan_dir, ctx.format_spec, ctx.production_policy)
    assert res.eligible is False
    assert any(c.rule == "execution_completed" for c in res.failures)

    cancelled_dir = ctx.run_execution(cancel_after_step=2)
    res = assess_checkpoint_eligibility(
        cancelled_dir, ctx.plan_dir, ctx.format_spec, ctx.production_policy)
    assert res.eligible is False
    assert any(c.rule == "execution_completed" for c in res.failures)


def test_resumed_completed_execution_is_eligible(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    from verifiednet.training import write_training_execution

    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ectx = ctx.execctx
    failed = ectx.engine.execute(ectx.plan, policy=ectx.policy,
                                 fail_after_step=1)
    resumed = ectx.engine.resume(failed, ectx.plan)
    w = write_training_execution(resumed, tmp_path / "resumed-exec")
    res = assess_checkpoint_eligibility(
        w.root, ctx.plan_dir, ctx.format_spec, ctx.production_policy)
    assert res.eligible is True, res.failures
    # the checkpoint binds THROUGH the execution artifact: retry number is in
    # the lineage; no checkpoint parent is invented for a resumed execution.
    cand = ctx.producer.produce(w.root, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    assert cand.lineage.retry_number == 1
    assert cand.lineage.source_execution_id == resumed.execution_id
    assert cand.lineage.parent_checkpoint_id is None


def test_file_roles_and_layout(tmp_path: Path, checkpoint_pipeline) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cand = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    paths = [f.relative_path for f in cand.files]
    assert paths == sorted(paths)
    assert paths == ["payload/checkpoint.json", "payload/config.json",
                     "payload/model.fakebin", "payload/tokenizer-metadata.json"]
    roles = {f.relative_path: f.role for f in cand.files}
    assert roles["payload/model.fakebin"] is CheckpointFileRole.FAKE_MODEL_PAYLOAD
    assert all(f.required for f in cand.files)


def test_write_verify_read_round_trip(tmp_path: Path, checkpoint_pipeline) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cand = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    written = write_checkpoint(cand, tmp_path / "checkpoints")
    assert written.root.name == cand.intended_checkpoint_id
    assert written.file_count == 4
    assert written.total_bytes == sum(len(f.content) for f in cand.files)
    result = verify_checkpoint(written.root)
    assert result.verified is True, result.failures
    manifest = read_checkpoint_manifest(written.root)
    assert manifest.checkpoint_id == cand.intended_checkpoint_id
    assert manifest.checkpoint_digest == written.checkpoint_digest
    assert manifest.simulated is True
    loaded = read_verified_checkpoint(written.root)
    assert len(loaded.payloads) == 4
    assert not hasattr(loaded, "load_model")
    blob = open_checkpoint_payload(written.root, "payload/model.fakebin")
    assert blob.startswith(FAKE_PAYLOAD_MAGIC)


def test_manifest_and_digest_content(tmp_path: Path, checkpoint_pipeline) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cand = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    written = write_checkpoint(cand, tmp_path / "checkpoints")
    manifest = read_checkpoint_manifest(written.root)
    assert manifest.lineage == cand.lineage
    assert manifest.compatibility == cand.compatibility
    assert manifest.format_spec == cand.format_spec
    assert manifest.production_policy == cand.production_policy
    assert manifest.checkpoint_digest.startswith("ckptdig-")
    assert manifest.total_bytes == sum(f.size for f in manifest.files)
    raw = (written.root / "manifest.json").read_bytes()
    forbidden = (b"timestamp", b"hostname", b"username", b"duration",
                 b"process_id", b"gpu", b"/home/", b"/Users/")
    assert not any(tok in raw for tok in forbidden)
