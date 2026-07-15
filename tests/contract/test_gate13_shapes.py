"""Gate 13 contract tests: frozen models, Literal locks, unchanged parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.evaluation import (
    CorpusProvenance,
    EvaluationCorpusGenerationPolicy,
    EvaluationCorpusManifest,
    ParserStatistics,
    StructuredOutputReport,
    build_generation_policy,
    compute_parser_statistics,
    register_evaluation_corpus,
)

pytestmark = pytest.mark.contract

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


def test_generation_policy_is_frozen_and_source_locked() -> None:
    policy = build_generation_policy(
        generator="g", split_policy_id="split-x", feature_policy_id="feat-x",
        label_policy_id="label-x", requested_accepted_runs=3,
        requested_rejected_runs=1)
    dump = policy.model_dump(mode="json")
    with pytest.raises(ValidationError):  # only verified runs may be a source
        EvaluationCorpusGenerationPolicy.model_validate(
            dump | {"source_kind": "synthetic_llm_generated"})
    with pytest.raises(ValidationError):  # tampered id
        EvaluationCorpusGenerationPolicy.model_validate(
            dump | {"generation_policy_id": "ecgen-" + "0" * 16})
    with pytest.raises(ValidationError):  # content change breaks the id
        EvaluationCorpusGenerationPolicy.model_validate(
            dump | {"requested_accepted_runs": 99})


def test_corpus_manifest_quality_flag_is_literal_true(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=build_generation_policy(
            generator="g", split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=3, requested_rejected_runs=1),
        corpora_root=tmp_path / "corpora")
    raw = json.loads((written.root / "manifest.json").read_bytes())
    # an UNVERIFIED corpus registration is unrepresentable
    with pytest.raises(ValidationError):
        EvaluationCorpusManifest.model_validate_json(
            json.dumps(raw | {"quality_verified": False}))
    with pytest.raises(ValidationError):  # tampered digest
        EvaluationCorpusManifest.model_validate_json(
            json.dumps(raw | {"corpus_digest": "ecdig-" + "0" * 24}))
    with pytest.raises(ValidationError):  # extras forbidden
        EvaluationCorpusManifest.model_validate_json(
            json.dumps(raw | {"note": "x"}))


def test_parser_statistics_enforce_count_rate_consistency(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path,
        base_responder=lambda p, d: "garbage",
        trained_responder=lambda p, d: '{"prediction_type": "abstention"}')
    stats = compute_parser_statistics(ctx.base_run)
    raw = json.loads(stats.model_dump_json())
    with pytest.raises(ValidationError):  # stored rate must match counts
        ParserStatistics.model_validate_json(
            json.dumps(raw | {"json_validity_rate": "0.999999"}))
    with pytest.raises(ValidationError):  # category counts must sum
        ParserStatistics.model_validate_json(
            json.dumps(raw | {"invalid_predictions": raw["total"] + 1,
                              "valid_structured_predictions": -1}))
    with pytest.raises(ValidationError):  # sum partition must hold
        ParserStatistics.model_validate_json(
            json.dumps(raw | {"backend_failures": raw["backend_failures"] + 1}))


def test_report_rows_are_sorted_and_id_locked(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    from verifiednet.evaluation import build_structured_output_report, run_benchmark

    ctx = matched_pair_pipeline(
        tmp_path,
        base_responder=lambda p, d: "garbage",
        trained_responder=lambda p, d: '{"prediction_type": "abstention"}')
    benchmark = run_benchmark(ctx.evalctx.loaded, task=ctx.ckptctx.task,
                              predictors=[ctx.base, ctx.trained])
    report = build_structured_output_report(benchmark)
    raw = json.loads(report.model_dump_json())
    with pytest.raises(ValidationError):  # unsorted rows refused
        StructuredOutputReport.model_validate_json(
            json.dumps(raw | {"rows": list(reversed(raw["rows"]))}))
    with pytest.raises(ValidationError):  # tampered id refused
        StructuredOutputReport.model_validate_json(
            json.dumps(raw | {"report_id": "sor-" + "0" * 16}))


def test_gate8_parsing_semantics_are_unchanged() -> None:
    # The classifier is diagnostics-only: the authoritative parser still maps
    # prose-wrapped JSON to the SAME invalid prediction it always produced.
    from verifiednet.evaluation import NormalizationPolicy, parse_backend_response

    prediction = parse_backend_response(
        ' prose first {"prediction_type": "abstention"}',
        baseline_id="baseline-" + "0" * 16, task_id="task-" + "0" * 16,
        features_payload={"feature_policy_id": "feat-x"},
        normalization=NormalizationPolicy(),
        normalized_candidates=frozenset({"bgp_remote_as_mismatch"}))
    assert prediction.outcome_kind == "invalid"
    assert prediction.reason_code == "malformed_json"  # unchanged Gate 8 rule
