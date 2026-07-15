"""Gate 12 security proofs: feature-only both predictors, no network,
source immutability across the WHOLE pipeline, isolation from training,
and build-twice byte-identical artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from verifiednet.datasets.features import AbstentionLabels, AcceptedLabels
from verifiednet.evaluation import (
    CorpusProvenance,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    build_default_interpretation_policy,
    build_paired_comparison,
    interpret_paired_comparison,
    run_benchmark,
    verify_benchmark,
    verify_comparison,
    write_benchmark,
    write_comparison,
    write_evaluation,
)

pytestmark = pytest.mark.security

_ABST = '{"prediction_type": "abstention"}'
_RAS = '{"prediction_type": "diagnosis", "fault_family": "bgp_remote_as_mismatch"}'


def _tree_fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return out


def _full_gate12(ctx, out_root: Path):
    """Evaluate + persist both, benchmark all four, compare, persist."""
    task = ctx.ckptctx.task
    write_evaluation(ctx.base_run, out_root / "evaluations")
    write_evaluation(ctx.trained_run, out_root / "evaluations")
    benchmark = run_benchmark(
        ctx.evalctx.loaded, task=task,
        predictors=[
            FixedPriorBaseline(task=task,
                               fixed_fault_family="bgp_remote_as_mismatch"),
            EvidenceRuleBaseline(task=task,
                                 default_fault_family="bgp_remote_as_mismatch"),
            ctx.base, ctx.trained])
    written_benchmark = write_benchmark(benchmark, out_root / "benchmarks")
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    interp = interpret_paired_comparison(
        result.comparison, policy=build_default_interpretation_policy(),
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    written_comparison = write_comparison(
        result, interp, out_root / "comparisons")
    return written_benchmark, written_comparison


def test_both_predictor_prompts_contain_no_labels_or_metadata(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    captured: list[str] = []

    def spying(text: str):
        def responder(prompt: str, decoding) -> str:
            captured.append(prompt)
            return text
        return responder

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=spying(_ABST),
        trained_responder=spying(_RAS))
    secrets: set[str] = set()
    for ex in ctx.evalctx.loaded.examples:
        secrets.update({ex.trace.example_id, ex.trace.group_id,
                        ex.trace.run_id, ex.trace.run_digest,
                        ex.trace.split_policy_id})
        if isinstance(ex.labels, AcceptedLabels):
            secrets.add(ex.labels.scenario_id)
        elif isinstance(ex.labels, AbstentionLabels):
            secrets.update({ex.labels.rejection_code, ex.labels.failed_phase})
    secrets.add(ctx.ckptctx.bundle.manifest.checkpoint_id)
    secrets.add(ctx.base_bundle.base_model_id)
    assert len(captured) == 2 * len(ctx.evalctx.loaded.examples)
    for prompt in captured:
        for secret in secrets:
            assert secret not in prompt, secret


def test_full_gate12_pipeline_uses_no_network(
    tmp_path: Path, matched_pair_pipeline, monkeypatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("the offline Gate 12 pipeline must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    written_benchmark, written_comparison = _full_gate12(
        ctx, tmp_path / "gate12-out")
    assert verify_benchmark(written_benchmark.root).verified is True
    assert verify_comparison(written_comparison.root).verified is True


def test_all_sources_remain_byte_identical_across_the_pipeline(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    sources = {
        "prepared_corpus": Path(ctx.evalctx.prepared_dir),
        "dataset_export": Path(ctx.evalctx.dataset_dir),
        "training_side": Path(str(tmp_path / "trainside")),
        "base_model": Path(str(ctx.base_dir)),
        "checkpoint": Path(str(ctx.ckptctx.checkpoint_dir)),
    }
    before = {name: _tree_fingerprint(root) for name, root in sources.items()}
    _full_gate12(ctx, tmp_path / "gate12-out")
    after = {name: _tree_fingerprint(root) for name, root in sources.items()}
    assert after == before  # every upstream artifact byte-identical
    # derived outputs landed OUTSIDE every source root
    out = tmp_path / "gate12-out"
    assert (out / "evaluations").is_dir()
    assert (out / "benchmarks").is_dir()
    assert (out / "comparisons").is_dir()


def test_evaluation_results_never_flow_into_training(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    # Structural isolation: no training artifact directory gains or changes a
    # file when evaluations/benchmarks/comparisons are produced (covered above
    # byte-for-byte), and the AST guard proves training cannot even import the
    # evaluation package. Here we additionally prove no comparison artifact is
    # a valid training input: the training corpus reader refuses it.
    from verifiednet.training import TrainingStoreError, load_training_pairs

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    _, written_comparison = _full_gate12(ctx, tmp_path / "gate12-out")
    with pytest.raises(TrainingStoreError):
        load_training_pairs(written_comparison.root)


def test_build_twice_produces_byte_identical_artifacts(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    first_benchmark, first_comparison = _full_gate12(ctx, tmp_path / "first")
    second_benchmark, second_comparison = _full_gate12(ctx, tmp_path / "second")
    assert _tree_fingerprint(first_benchmark.root) == \
        _tree_fingerprint(second_benchmark.root)
    assert _tree_fingerprint(first_comparison.root) == \
        _tree_fingerprint(second_comparison.root)
    assert first_benchmark.benchmark_digest == second_benchmark.benchmark_digest
    assert first_comparison.comparison_digest == \
        second_comparison.comparison_digest


def test_no_training_apis_run_during_gate12(
    tmp_path: Path, matched_pair_pipeline, monkeypatch,
) -> None:
    # The fixture legitimately runs the offline stub trainer to CREATE the
    # checkpoint; the traps arm AFTER that, covering the whole measurement
    # phase: no engine run, no optimizer, no checkpoint write may occur.
    import verifiednet.training.hfexecutor as hfexec
    from verifiednet.training import realckptstore

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 12 must never execute training")

    monkeypatch.setattr(hfexec.StubTrainingEngine, "run", _boom)
    monkeypatch.setattr(hfexec.HFTrainingEngine, "run", _boom)
    monkeypatch.setattr(realckptstore, "write_real_checkpoint", _boom)
    written_benchmark, written_comparison = _full_gate12(
        ctx, tmp_path / "gate12-out")
    assert verify_benchmark(written_benchmark.root).verified is True
    assert verify_comparison(written_comparison.root).verified is True
