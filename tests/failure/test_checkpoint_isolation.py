"""Gate 10D proofs: no ML/exec/network, source immutability, isolation, ripple.

The checkpoint layer is pure bookkeeping over verified artifacts. These tests
run the ENTIRE Gate 10D pipeline — verify execution, assess eligibility,
produce candidate, write, verify, read — under sabotage and import traps, and
prove: every upstream artifact class stays byte-identical; evaluation and
benchmark changes cannot reach the checkpoint; no training row, label, or
example identity appears in any checkpoint payload; every source identity
ripples into the lineage/identity; and simulation honesty survives tampering.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from verifiednet.training import (
    assess_checkpoint_eligibility,
    read_verified_checkpoint,
    verify_checkpoint,
    write_checkpoint,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

_ML_FRAMEWORKS = ("torch", "transformers", "tokenizers", "safetensors", "peft",
                  "accelerate", "bitsandbytes", "deepspeed")


class _TrapFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in _ML_FRAMEWORKS:
            raise AssertionError(
                f"Gate 10D attempted to import training machinery: {fullname}")
        return None


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _full_gate10d_pipeline(ctx, checkpoints_root: Path):
    eligibility = assess_checkpoint_eligibility(
        ctx.exec_dir, ctx.plan_dir, ctx.format_spec, ctx.production_policy,
        checkpoints_root=checkpoints_root)
    assert eligibility.eligible is True, eligibility.failures
    cand = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    written = write_checkpoint(cand, checkpoints_root)
    assert verify_checkpoint(written.root).verified is True
    loaded = read_verified_checkpoint(written.root)
    assert loaded.manifest.checkpoint_id == cand.intended_checkpoint_id
    return cand, written, loaded


def test_pipeline_under_import_traps(tmp_path: Path, checkpoint_pipeline) -> None:
    for name in _ML_FRAMEWORKS:
        assert name not in sys.modules, f"{name} already imported by the suite"
    trap = _TrapFinder()
    sys.meta_path.insert(0, trap)
    try:
        ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
        _, written, _ = _full_gate10d_pipeline(ctx, tmp_path / "checkpoints")
    finally:
        sys.meta_path.remove(trap)
    files = sorted(str(p.relative_to(written.root))
                   for p in written.root.rglob("*") if p.is_file())
    assert files == ["manifest.json", "payload/checkpoint.json",
                     "payload/config.json", "payload/model.fakebin",
                     "payload/tokenizer-metadata.json"]


def test_pipeline_no_subprocess_no_network(
    tmp_path: Path, checkpoint_pipeline, monkeypatch,
) -> None:
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 10D must not execute or open a network client")

    import verifiednet.evaluation.inference as inference

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(inference, "OllamaBackend", _boom)
    monkeypatch.setattr(inference, "FakeInferenceBackend", _boom)

    _full_gate10d_pipeline(ctx, tmp_path / "checkpoints")


def test_checkpoint_does_not_mutate_any_source(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        diagnosis_task,
        evaluate_prepared_corpus,
        run_benchmark,
        write_benchmark,
        write_evaluation,
    )

    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    planctx = ctx.execctx.planctx
    prepared = load_prepared(planctx.prepared_dir)
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(
        task=task, default_fault_family="bgp_remote_as_mismatch")
    write_evaluation(evaluate_prepared_corpus(prepared, baseline, task),
                     tmp_path / "evaluations")
    write_benchmark(run_benchmark(prepared, task=task, predictors=[
        baseline,
        FixedPriorBaseline(task=task,
                           fixed_fault_family="bgp_remote_as_mismatch"),
    ]), tmp_path / "benchmarks")

    roots = {
        "runs": Path(planctx.run_root),
        "dataset": Path(planctx.dataset_dir),
        "prepared": Path(planctx.prepared_dir),
        "evaluations": tmp_path / "evaluations",
        "benchmarks": tmp_path / "benchmarks",
        "training-corpus": Path(planctx.corpus_root),
        "training-plan": Path(ctx.plan_dir),
        "training-execution": Path(ctx.exec_dir),
    }
    before = {name: _fingerprint(root) for name, root in roots.items()}
    _full_gate10d_pipeline(ctx, tmp_path / "checkpoints")
    after = {name: _fingerprint(root) for name, root in roots.items()}
    assert after == before  # every upstream stage byte-identical


def test_evaluation_isolation(tmp_path: Path, checkpoint_pipeline) -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.evaluation import (
        EvidenceRuleBaseline,
        FixedPriorBaseline,
        diagnosis_task,
        run_benchmark,
        write_benchmark,
    )

    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    cand_before = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                       format_spec=ctx.format_spec,
                                       policy=ctx.production_policy)
    w1 = write_checkpoint(cand_before, tmp_path / "before")

    # produce two DIFFERENT benchmark results (different rankings) in between
    prepared = load_prepared(ctx.execctx.planctx.prepared_dir)
    task = diagnosis_task()
    evidence = EvidenceRuleBaseline(
        task=task, default_fault_family="bgp_remote_as_mismatch")
    prior = FixedPriorBaseline(task=task,
                               fixed_fault_family="bgp_remote_as_mismatch")
    bench_a = run_benchmark(prepared, task=task, predictors=[evidence])
    bench_b = run_benchmark(prepared, task=task, predictors=[evidence, prior])
    assert len(bench_a.ranking) != len(bench_b.ranking)
    write_benchmark(bench_a, tmp_path / "bench-a")
    write_benchmark(bench_b, tmp_path / "bench-b")

    cand_after = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                      format_spec=ctx.format_spec,
                                      policy=ctx.production_policy)
    assert cand_after == cand_before  # byte-carrying model equality
    assert [f.content for f in cand_after.files] == \
        [f.content for f in cand_before.files]
    w2 = write_checkpoint(cand_after, tmp_path / "after")
    assert _fingerprint(w1.root) == _fingerprint(w2.root)  # byte-identical


def test_no_training_content_in_any_payload(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    from verifiednet.training import load_training_corpus, load_training_pairs

    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    _cand, written, _ = _full_gate10d_pipeline(ctx, tmp_path / "checkpoints")

    corpus_root = ctx.execctx.planctx.corpus_root
    pairs = load_training_pairs(corpus_root)
    loaded_corpus = load_training_corpus(corpus_root)

    # every checkpoint byte, textual and binary, including the manifest:
    blobs = [p.read_bytes() for p in sorted(written.root.rglob("*"))
             if p.is_file()]
    combined = b"\x00".join(blobs)

    for pair in pairs:  # rendered inputs and targets never appear
        assert pair.input_text.encode() not in combined
        assert pair.target_text.encode() not in combined
    for example in loaded_corpus.examples:  # identities and labels never appear
        assert example.training_example_id.encode() not in combined
        assert example.trace.source_example_id.encode() not in combined
        assert example.trace.source_group_id.encode() not in combined
        assert example.target.text.encode() not in combined
    # no fault-family label strings in any payload
    from verifiednet.training import TRAINING_CANDIDATE_FAMILIES

    for family in TRAINING_CANDIDATE_FAMILIES:
        assert family.encode() not in combined, family


def test_identity_ripple(tmp_path: Path, checkpoint_pipeline) -> None:
    # A REAL upstream change (a different completed execution: the resumed
    # retry) must ripple into lineage and checkpoint identity.
    from verifiednet.training import write_training_execution

    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    base = ctx.producer.produce(ctx.exec_dir, ctx.plan_dir,
                                format_spec=ctx.format_spec,
                                policy=ctx.production_policy)
    ectx = ctx.execctx
    failed = ectx.engine.execute(ectx.plan, policy=ectx.policy,
                                 fail_after_step=1)
    resumed = ectx.engine.resume(failed, ectx.plan)
    w = write_training_execution(resumed, tmp_path / "resumed-exec")
    other = ctx.producer.produce(w.root, ctx.plan_dir,
                                 format_spec=ctx.format_spec,
                                 policy=ctx.production_policy)
    assert other.lineage.source_execution_id != base.lineage.source_execution_id
    assert other.lineage.lineage_id != base.lineage.lineage_id
    assert other.intended_checkpoint_id != base.intended_checkpoint_id

    # Synthetic single-field ripples: every binding participates in the ids.
    from verifiednet.training import (
        CheckpointLineage,
        derive_checkpoint_id,
        derive_compatibility_id,
        derive_lineage_id,
    )

    lineage_fields = ("source_execution_id", "source_execution_digest",
                      "source_training_plan_id", "source_plan_digest",
                      "training_request_id", "training_spec_id",
                      "training_corpus_id", "training_corpus_digest",
                      "model_spec_id", "tokenizer_spec_id",
                      "trainer_implementation_id", "trainer_capability_id",
                      "execution_policy_id", "retry_number")
    for field in lineage_fields:
        value = 7 if field == "retry_number" else "ripple-test-value"
        mutated = CheckpointLineage.model_construct(
            **{**dict(base.lineage), field: value})
        assert derive_lineage_id(mutated) != base.lineage.lineage_id, field
    # format/policy/compatibility ripples
    mutated_compat = base.compatibility.model_copy(
        update={"architecture_id": "OtherArch"})
    assert (derive_compatibility_id(mutated_compat)
            != base.compatibility.compatibility_id)
    assert derive_checkpoint_id(
        format_spec_id="ckptfmt-" + "0" * 16,
        lineage_id=base.lineage.lineage_id,
        declared_file_roles=tuple(f.role for f in base.files),
        simulated=True, model_spec_id=base.lineage.model_spec_id,
        tokenizer_spec_id=base.lineage.tokenizer_spec_id,
        checkpoint_version=1) != base.intended_checkpoint_id
    assert derive_checkpoint_id(
        format_spec_id=base.format_spec.format_spec_id,
        lineage_id="ckptlin-" + "0" * 16,
        declared_file_roles=tuple(f.role for f in base.files),
        simulated=True, model_spec_id=base.lineage.model_spec_id,
        tokenizer_spec_id=base.lineage.tokenizer_spec_id,
        checkpoint_version=1) != base.intended_checkpoint_id


def test_simulation_honesty_survives_tampering(
    tmp_path: Path, checkpoint_pipeline,
) -> None:
    # Attempt to rewrite a persisted fake checkpoint as real in four ways;
    # every attempt must be rejected by parse-time validation or verification.
    ctx = checkpoint_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    _, written, _ = _full_gate10d_pipeline(ctx, tmp_path / "checkpoints")
    manifest_path = written.root / "manifest.json"
    good = manifest_path.read_bytes()

    attempts = (
        ("compatibility.loadable_as_real_model", lambda d: d["compatibility"]
         .__setitem__("loadable_as_real_model", True)),
        ("format_spec.payload_format=safetensors", lambda d: d["format_spec"]
         .__setitem__("payload_format", "safetensors")),
        ("format_spec.weights_declaration=full_model", lambda d: d["format_spec"]
         .__setitem__("weights_declaration", "full_model")),
        ("format_spec.weights_declaration=lora_adapter", lambda d: d["format_spec"]
         .__setitem__("weights_declaration", "lora_adapter")),
        ("manifest.simulated=false", lambda d: d.__setitem__("simulated", False)),
    )
    for name, mutate in attempts:
        data = json.loads(good)
        mutate(data)
        manifest_path.write_bytes(json.dumps(data).encode())
        result = verify_checkpoint(written.root)
        assert result.verified is False, name
        assert any(c.rule == "manifest_parses" for c in result.failures), name
    manifest_path.write_bytes(good)

    # stripping the fake magic from the payload is also fatal, even with the
    # file hash fixed up in a rewritten (revalidated) manifest — because the
    # digest and hashes are self-validated, the attacker cannot even produce a
    # parsing manifest without recomputing EVERYTHING; and if they only change
    # the payload, the hash check fails:
    victim = written.root / "payload" / "model.fakebin"
    raw = victim.read_bytes()
    victim.write_bytes(b"NOT-FAKE" + raw[8:])
    result = verify_checkpoint(written.root)
    assert result.verified is False
    assert any(c.rule in ("file_hashes_match", "fake_payload_magic_present")
               for c in result.failures)
