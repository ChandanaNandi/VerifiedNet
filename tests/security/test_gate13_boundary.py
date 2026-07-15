"""Gate 13 security proofs: registration and reporting are read-only,
network-free, deterministic, and isolated from training."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    CorpusProvenance,
    build_generation_policy,
    build_structured_output_report,
    register_evaluation_corpus,
    run_benchmark,
    write_structured_output_report,
)

pytestmark = pytest.mark.security

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


def _tree_fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _policy_for(ctx):
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    return build_generation_policy(
        generator="g", split_policy_id=split_ids[0],
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        requested_accepted_runs=3, requested_rejected_runs=1)


def test_registration_never_touches_the_prepared_corpus(
    tmp_path: Path, eval_pipeline, monkeypatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 13 must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    prepared = Path(str(ctx.prepared_dir))
    dataset = Path(str(ctx.dataset_dir))
    before = (_tree_fingerprint(prepared), _tree_fingerprint(dataset))
    register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=_policy_for(ctx),
        corpora_root=tmp_path / "corpora")
    assert (_tree_fingerprint(prepared), _tree_fingerprint(dataset)) == before


def test_reporting_never_touches_sources_and_is_build_twice_identical(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: "garbage",
        trained_responder=lambda p, d: '{"prediction_type": "abstention"}')
    sources = {
        "prepared": Path(str(ctx.evalctx.prepared_dir)),
        "checkpoint": Path(str(ctx.ckptctx.checkpoint_dir)),
        "base_model": Path(str(ctx.base_dir)),
    }
    before = {k: _tree_fingerprint(v) for k, v in sources.items()}
    benchmark = run_benchmark(ctx.evalctx.loaded, task=ctx.ckptctx.task,
                              predictors=[ctx.base, ctx.trained])
    report = build_structured_output_report(benchmark)
    first = write_structured_output_report(report, tmp_path / "first")
    second = write_structured_output_report(report, tmp_path / "second")
    assert _tree_fingerprint(first.root) == _tree_fingerprint(second.root)
    assert {k: _tree_fingerprint(v) for k, v in sources.items()} == before


def test_registration_is_build_twice_byte_identical(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy = _policy_for(ctx)
    first = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=policy, corpora_root=tmp_path / "first")
    second = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=policy, corpora_root=tmp_path / "second")
    assert _tree_fingerprint(first.root) == _tree_fingerprint(second.root)
    assert first.corpus_digest == second.corpus_digest


def test_gate13_artifacts_are_not_training_inputs(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.training import TrainingStoreError, load_training_pairs

    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=_policy_for(ctx),
        corpora_root=tmp_path / "corpora")
    with pytest.raises(TrainingStoreError):
        load_training_pairs(written.root)


def test_no_identity_or_host_values_in_registration_artifacts(
    tmp_path: Path, eval_pipeline,
) -> None:
    # Registration artifacts are evaluator-side reports; they must still never
    # embed host facts, absolute paths, or per-example run digests.
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=_policy_for(ctx),
        corpora_root=tmp_path / "corpora")
    blob = b"".join((written.root / n).read_bytes()
                    for n in ("manifest.json", "coverage.json",
                              "quality.json"))
    text = blob.decode("utf-8")
    assert str(tmp_path) not in text
    for example in ctx.loaded.examples:
        assert example.trace.run_digest not in text
        assert example.trace.example_id not in text
