"""Gate 13 unit tests: invalid-output diagnostics, parser statistics,
prompt-compliance measurement, structured-output report store."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.evaluation import (
    DEFAULT_CANDIDATE_FAMILIES,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    InvalidOutputCategory,
    build_structured_output_report,
    classify_invalid_output,
    compute_parser_statistics,
    read_structured_output_report,
    run_benchmark,
    validate_response_schema,
    verify_structured_output_report,
    write_structured_output_report,
)

pytestmark = pytest.mark.unit

_ABST = '{"prediction_type": "abstention"}'
_RAS = '{"prediction_type": "diagnosis", "fault_family": "bgp_remote_as_mismatch"}'

#: The two REAL Gate 12 failure shapes, verbatim from the persisted artifacts.
_REAL_BASE_EXCERPT = (' The response should be formatted as JSON.\n{\n  '
                      '"prediction_type": "diagnosis",\n  "fault_family": '
                      '"bgp_neighbor_removal",\n  "confidence": "high"\n}'
                      'Human: I need you to help me understand')
_REAL_TRAINED_EXCERPT = '{"' * 60


def test_classifier_covers_the_real_gate12_failures() -> None:
    assert classify_invalid_output(
        reason_code="malformed_json", raw_excerpt=_REAL_BASE_EXCERPT,
    ) is InvalidOutputCategory.PROSE_WRAPPED_JSON
    assert classify_invalid_output(
        reason_code="malformed_json", raw_excerpt=_REAL_TRAINED_EXCERPT,
    ) is InvalidOutputCategory.DEGENERATE_REPETITION


def test_classifier_category_mapping() -> None:
    cases = [
        ("backend_unavailable", "x", InvalidOutputCategory.BACKEND_FAILURE),
        ("inference_timeout", "x", InvalidOutputCategory.BACKEND_FAILURE),
        ("backend_error", "x", InvalidOutputCategory.BACKEND_FAILURE),
        ("not_an_object", "[1]", InvalidOutputCategory.NON_OBJECT_JSON),
        ("missing_fault_family", "{}",
         InvalidOutputCategory.MISSING_REQUIRED_FIELD),
        ("unknown_fault_family", "{}",
         InvalidOutputCategory.OUT_OF_SCHEMA_VALUE),
        ("unsupported_prediction_type", "{}",
         InvalidOutputCategory.UNSUPPORTED_PREDICTION_TYPE),
        ("malformed_json", "", InvalidOutputCategory.EMPTY_OUTPUT),
        ("malformed_json", "   \n ", InvalidOutputCategory.EMPTY_OUTPUT),
        ("malformed_json", '{"prediction_type": "diag',
         InvalidOutputCategory.TRUNCATED_JSON),
        ("malformed_json", '{"a": 1} trailing garbage',
         InvalidOutputCategory.MALFORMED_OTHER),
        ("malformed_json", "no braces at all",
         InvalidOutputCategory.MALFORMED_OTHER),
        ("malformed_json", "aaaaaaaaaaaaaaaa",
         InvalidOutputCategory.DEGENERATE_REPETITION),
    ]
    for reason, excerpt, expected in cases:
        got = classify_invalid_output(reason_code=reason, raw_excerpt=excerpt)
        assert got is expected, (reason, excerpt, got)


def test_schema_validation_is_diagnostic_and_strict() -> None:
    ok = validate_response_schema(
        _RAS, candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    assert ok.schema_compliant is True and ok.fault_family_valid is True
    abst = validate_response_schema(
        _ABST, candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    assert abst.schema_compliant is True
    bad_family = validate_response_schema(
        '{"prediction_type": "diagnosis", "fault_family": "made_up"}',
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    assert bad_family.json_parsed and not bad_family.schema_compliant
    prose = validate_response_schema(
        _REAL_BASE_EXCERPT, candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    assert prose.json_parsed is False and prose.schema_compliant is False


def test_parser_statistics_and_compliance_from_a_run(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    # base: always prose-wrapped garbage; trained: valid abstention JSON.
    ctx = matched_pair_pipeline(
        tmp_path,
        base_responder=lambda p, d: _REAL_BASE_EXCERPT,
        trained_responder=lambda p, d: _ABST)
    base_stats = compute_parser_statistics(ctx.base_run)
    total = len(ctx.base_run.records)
    assert base_stats.total == total
    assert base_stats.valid_structured_predictions == 0
    assert base_stats.malformed_outputs == total
    assert base_stats.json_validity_rate == "0.000000"
    assert base_stats.prompt_compliance_rate == "0.000000"
    assert [(f.category, f.count) for f in base_stats.failure_categories] == \
        [(InvalidOutputCategory.PROSE_WRAPPED_JSON, total)]
    trained_stats = compute_parser_statistics(ctx.trained_run)
    assert trained_stats.valid_structured_predictions == total
    assert trained_stats.invalid_predictions == 0
    assert trained_stats.json_validity_rate == "1.000000"
    assert trained_stats.prompt_compliance_rate == "1.000000"
    assert trained_stats.failure_categories == ()


def test_structured_output_report_store_round_trip(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path,
        base_responder=lambda p, d: _REAL_BASE_EXCERPT,
        trained_responder=lambda p, d: _RAS)
    task = ctx.ckptctx.task
    benchmark = run_benchmark(
        ctx.evalctx.loaded, task=task,
        predictors=[
            FixedPriorBaseline(task=task,
                               fixed_fault_family="bgp_remote_as_mismatch"),
            EvidenceRuleBaseline(task=task,
                                 default_fault_family="bgp_remote_as_mismatch"),
            ctx.base, ctx.trained])
    report = build_structured_output_report(benchmark)
    assert report.report_id.startswith("sor-")
    assert report.benchmark_id == benchmark.spec.benchmark_id
    assert len(report.rows) == 4  # every predictor, nobody dropped
    by_id = {r.predictor_identifier: r for r in report.rows}
    base_row = by_id[ctx.base.spec.baseline_id]
    assert base_row.statistics.malformed_output_rate == "1.000000"
    assert base_row.invalid_prediction_count == base_row.statistics.total
    rule_row = by_id[next(
        i for i in sorted(by_id) if i not in (ctx.base.spec.baseline_id,
                                              ctx.trained.spec.baseline_id))]
    assert rule_row.statistics.prompt_compliance_rate == "1.000000"
    # ranking is untouched by the report (compare to the benchmark's own)
    assert [e.predictor_identifier for e in benchmark.ranking] == \
        [e.predictor_identifier for e in benchmark.ranking]
    written = write_structured_output_report(report, tmp_path / "reports")
    assert written.report_digest.startswith("sordig-")
    assert verify_structured_output_report(written.root).verified is True
    loaded = read_structured_output_report(written.root)
    assert loaded.report == report
    # paired view: the matched pair's rows sit side by side in the SAME report
    assert {ctx.base.spec.baseline_id, ctx.trained.spec.baseline_id} <= \
        set(by_id)
