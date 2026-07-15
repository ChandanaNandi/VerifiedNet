"""Gate 14 security proofs: no model execution, no network, training
artifacts untouched, v1 immutable, build-twice reproducibility."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    CorpusProvenance,
    build_generation_policy,
    register_evaluation_corpus,
)

pytestmark = pytest.mark.security

_V1_ACC = [("ras-ref", "run-a"), ("nr-ref", "run-b"), ("pf-ref", "run-c")]


def _tree_fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _register(ctx, tmp_path: Path, sub: str, *, version: int = 1):
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    return register_evaluation_corpus(
        ctx.loaded, corpus_version=version,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=build_generation_policy(
            generator="g", split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=3, requested_rejected_runs=1),
        corpora_root=tmp_path / sub)


def test_gate14_pipeline_is_model_free_and_network_free(
    tmp_path: Path, eval_pipeline, expansion_corpus_pipeline, monkeypatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 14 must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    # trap EVERY training door and model-loading path
    import verifiednet.training.hfexecutor as hfexec
    from verifiednet.training import realckptstore

    def _trainboom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 14 must never train or write checkpoints")

    monkeypatch.setattr(hfexec.StubTrainingEngine, "run", _trainboom)
    monkeypatch.setattr(hfexec.HFTrainingEngine, "run", _trainboom)
    monkeypatch.setattr(realckptstore, "write_real_checkpoint", _trainboom)

    v2root = tmp_path / "v2side"
    v2root.mkdir()
    ctx, _accepted, _rejected = expansion_corpus_pipeline(v2root, runs_cap=1)
    _register(ctx, tmp_path, "corpora")
    # the whole campaign + registration imported NO ML runtime
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_v1_and_training_artifacts_remain_byte_identical(
    tmp_path: Path, eval_pipeline, ckpt_predictor_pipeline,
    expansion_corpus_pipeline,
) -> None:
    # v1 corpus + a full training-side artifact tree exist BEFORE expansion
    v1root = tmp_path / "v1side"
    v1root.mkdir()
    v1ctx = eval_pipeline(v1root, accepted=_V1_ACC, rejected=["run-rej"])
    v1_written = _register(v1ctx, tmp_path, "corpora")
    trainroot = tmp_path / "trainside"
    trainroot.mkdir()
    ckptctx = ckpt_predictor_pipeline(
        trainroot, accepted=[("ras-ref", "run-a"), ("nr-rev", "run-b"),
                             ("if-ref", "run-c")], rejected=["run-rej"])
    sources = {
        "v1_registration": v1_written.root,
        "v1_prepared": Path(str(v1ctx.prepared_dir)),
        "training_side": trainroot,
        "checkpoint": Path(str(ckptctx.checkpoint_dir)),
    }
    before = {k: _tree_fingerprint(v) for k, v in sources.items()}

    v2root = tmp_path / "v2side"
    v2root.mkdir()
    ctx, _acc, _rej = expansion_corpus_pipeline(v2root, runs_cap=1)
    _register(ctx, tmp_path, "corpora-v2")

    after = {k: _tree_fingerprint(v) for k, v in sources.items()}
    assert after == before  # v1 + every training artifact byte-identical


def test_expansion_is_build_twice_byte_identical(
    tmp_path: Path, expansion_corpus_pipeline,
) -> None:
    """Identical campaign inputs — INCLUDING the artifact root — rebuild to
    byte-identical corpora. (Run transcripts honestly record the execution
    work-dir in ``argv`` as runtime evidence, a Gate 4 contract; so
    reproducibility is defined over identical inputs incl. the root, exactly
    how the operational corpus persists at its fixed project artifact root.)"""
    import shutil

    shared = tmp_path / "shared"
    shared.mkdir()
    first_ctx, _a, _r = expansion_corpus_pipeline(shared, runs_cap=1)
    first_digest = first_ctx.loaded.manifest.prepared_digest
    first_reg = _register(first_ctx, tmp_path, "first-corpora")
    first_bytes = _tree_fingerprint(first_reg.root)
    shutil.rmtree(shared)
    shared.mkdir()
    second_ctx, _a2, _r2 = expansion_corpus_pipeline(shared, runs_cap=1)
    assert second_ctx.loaded.manifest.prepared_digest == first_digest
    second_reg = _register(second_ctx, tmp_path, "second-corpora")
    assert second_reg.evaluation_corpus_id == first_reg.evaluation_corpus_id
    assert second_reg.corpus_digest == first_reg.corpus_digest
    assert _tree_fingerprint(second_reg.root) == first_bytes


def test_expansion_artifacts_are_not_training_inputs(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.training import TrainingStoreError, load_training_pairs

    ctx = eval_pipeline(tmp_path, accepted=_V1_ACC, rejected=["run-rej"])
    written = _register(ctx, tmp_path, "corpora")
    with pytest.raises(TrainingStoreError):
        load_training_pairs(written.root)


def test_planner_has_no_channel_for_model_or_benchmark_inputs() -> None:
    # The planning function's SIGNATURE is the proof: coverage, candidates,
    # policy, split policy, rejected count — no evaluation, no benchmark, no
    # predictions, no checkpoint facts can flow in.
    import inspect

    from verifiednet.evaluation import plan_evaluation_corpus_expansion

    parameters = set(inspect.signature(
        plan_evaluation_corpus_expansion).parameters)
    assert parameters == {"current_coverage", "candidates", "policy",
                          "split_policy", "planned_rejected_runs"}
