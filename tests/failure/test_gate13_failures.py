"""Gate 13 failure tests: broken corpora, tampered artifacts, mismatches."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from verifiednet.datasets.models import DatasetPartition
from verifiednet.evaluation import (
    CorpusProvenance,
    EvaluationCorpusError,
    StructuredReportError,
    build_generation_policy,
    register_evaluation_corpus,
    verify_corpus_quality,
    verify_evaluation_corpus,
    verify_structured_output_report,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


def _policy_for(ctx, **overrides):
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    kwargs = dict(
        generator="g", split_policy_id=split_ids[0],
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        requested_accepted_runs=3, requested_rejected_runs=1)
    kwargs.update(overrides)
    return build_generation_policy(**kwargs)


def _doctored(loaded, index: int, **trace_overrides):
    """A structurally broken corpus view (validation deliberately bypassed)."""
    example = loaded.examples[index]
    trace = example.trace.model_copy(update=trace_overrides)
    broken = example.model_copy(update={"trace": trace})
    examples = list(loaded.examples)
    examples[index] = broken
    return replace(loaded, examples=tuple(examples))


def test_split_leakage_is_detected(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    accepted = [i for i, e in enumerate(ctx.loaded.examples)
                if e.trace.partition is not DatasetPartition.ABSTENTION]
    victim = ctx.loaded.examples[accepted[0]]
    other = (DatasetPartition.TEST
             if victim.trace.partition is not DatasetPartition.TEST
             else DatasetPartition.TRAIN)
    # duplicate the group into a second partition via a doctored twin view
    twin_trace = victim.trace.model_copy(update={
        "example_id": "ex-" + "f" * 16, "partition": other})
    twin = victim.model_copy(update={"trace": twin_trace})
    leaky = replace(ctx.loaded, examples=tuple(
        sorted([*ctx.loaded.examples, twin],
               key=lambda e: e.trace.example_id)))
    quality = verify_corpus_quality(leaky)
    assert quality.verified is False
    assert any(c.rule == "no_split_leakage" and not c.passed
               for c in quality.checks)


def test_duplicate_ids_and_malformed_examples_are_detected(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    first_id = ctx.loaded.examples[0].trace.example_id
    duped = _doctored(ctx.loaded, 1, example_id=first_id)
    quality = verify_corpus_quality(duped)
    assert quality.verified is False
    assert any(c.rule == "unique_example_ids" and not c.passed
               for c in quality.checks)
    # an accepted example claiming the abstention partition is malformed
    accepted_index = next(
        i for i, e in enumerate(ctx.loaded.examples)
        if e.trace.partition is not DatasetPartition.ABSTENTION)
    malformed = _doctored(ctx.loaded, accepted_index,
                          partition=DatasetPartition.ABSTENTION)
    quality = verify_corpus_quality(malformed)
    assert quality.verified is False
    assert any(c.rule == "no_malformed_examples" and not c.passed
               for c in quality.checks)


def test_registration_fails_closed(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    root = tmp_path / "corpora"
    with pytest.raises(EvaluationCorpusError):  # policy/corpus mismatch
        register_evaluation_corpus(
            ctx.loaded, corpus_version=1,
            provenance=CorpusProvenance.FIXTURE_GENERATED,
            generation_policy=_policy_for(
                ctx, feature_policy_id="feat-" + "f" * 12),
            corpora_root=root)
    # a quality-failing corpus cannot be registered at all
    first_id = ctx.loaded.examples[0].trace.example_id
    broken = _doctored(ctx.loaded, 1, example_id=first_id)
    with pytest.raises(EvaluationCorpusError):
        register_evaluation_corpus(
            broken, corpus_version=1,
            provenance=CorpusProvenance.FIXTURE_GENERATED,
            generation_policy=_policy_for(ctx), corpora_root=root)
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=_policy_for(ctx), corpora_root=root)
    with pytest.raises(EvaluationCorpusError):  # unsafe overwrite refused
        register_evaluation_corpus(
            ctx.loaded, corpus_version=1,
            provenance=CorpusProvenance.FIXTURE_GENERATED,
            generation_policy=_policy_for(ctx), corpora_root=root)
    # per-byte tamper evidence on every stored file
    for name in ("manifest.json", "coverage.json", "quality.json"):
        path = written.root / name
        original = path.read_bytes()
        position = len(original) // 2
        path.write_bytes(original[:position]
                         + bytes([original[position] ^ 0xFF])
                         + original[position + 1:])
        assert verify_evaluation_corpus(written.root).verified is False, name
        path.write_bytes(original)
    assert verify_evaluation_corpus(written.root).verified is True


def test_audit_detects_a_different_prepared_corpus(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.evaluation import audit_evaluation_corpus

    a_root = tmp_path / "a"
    b_root = tmp_path / "b"
    a_root.mkdir()
    b_root.mkdir()
    ctx_a = eval_pipeline(a_root, accepted=_ACC, rejected=["run-rej"])
    ctx_b = eval_pipeline(b_root, accepted=_ACC[:2], rejected=["run-rej"])
    written = register_evaluation_corpus(
        ctx_a.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=_policy_for(ctx_a),
        corpora_root=tmp_path / "corpora")
    ok, checks = audit_evaluation_corpus(written.root, ctx_b.loaded)
    assert ok is False
    assert any(c.rule == "prepared_digest_matches" and not c.passed
               for c in checks)


def test_structured_report_store_failures(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    from verifiednet.evaluation import (
        build_structured_output_report,
        run_benchmark,
        write_structured_output_report,
    )

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: "garbage",
        trained_responder=lambda p, d: '{"prediction_type": "abstention"}')
    benchmark = run_benchmark(ctx.evalctx.loaded, task=ctx.ckptctx.task,
                              predictors=[ctx.base, ctx.trained])
    report = build_structured_output_report(benchmark)
    root = tmp_path / "reports"
    written = write_structured_output_report(report, root)
    with pytest.raises(StructuredReportError):  # unsafe overwrite refused
        write_structured_output_report(report, root)
    for name in ("manifest.json", "report.json"):
        path = written.root / name
        original = path.read_bytes()
        position = len(original) // 2
        path.write_bytes(original[:position]
                         + bytes([original[position] ^ 0xFF])
                         + original[position + 1:])
        assert verify_structured_output_report(
            written.root).verified is False, name
        path.write_bytes(original)
    assert verify_structured_output_report(written.root).verified is True
