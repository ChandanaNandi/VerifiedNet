"""Gate 13 property tests: classifier totality/determinism, stats invariants."""

from __future__ import annotations

from itertools import product

import pytest

from verifiednet.evaluation import (
    MALFORMED_CATEGORIES,
    SCHEMA_FAILURE_CATEGORIES,
    InvalidOutputCategory,
    classify_invalid_output,
    compute_parser_statistics,
    derive_structured_report_id,
)

pytestmark = pytest.mark.property

_REASONS = ("backend_unavailable", "inference_timeout", "backend_error",
            "malformed_json", "not_an_object", "missing_fault_family",
            "unknown_fault_family", "unsupported_prediction_type",
            "some_future_reason")
_EXCERPTS = ("", "   ", "{", '{"a": 1}', '{"a": 1} x', "[1, 2]",
             "prose then {json", "no json here", '{"' * 40, "ab" * 30,
             "x" * 200, ' {"nested": {"deep": 1}}')


def test_classifier_is_total_and_deterministic() -> None:
    all_categories = (set(MALFORMED_CATEGORIES) | set(SCHEMA_FAILURE_CATEGORIES)
                      | {InvalidOutputCategory.BACKEND_FAILURE})
    for reason, excerpt in product(_REASONS, _EXCERPTS):
        first = classify_invalid_output(reason_code=reason, raw_excerpt=excerpt)
        second = classify_invalid_output(reason_code=reason, raw_excerpt=excerpt)
        assert first is second
        assert first in all_categories
        # backend reasons always classify as backend failures, whatever the text
        if reason in ("backend_unavailable", "inference_timeout",
                      "backend_error"):
            assert first is InvalidOutputCategory.BACKEND_FAILURE


def test_category_partition_is_disjoint_and_complete() -> None:
    everything = set(InvalidOutputCategory)
    assert MALFORMED_CATEGORIES & SCHEMA_FAILURE_CATEGORIES == frozenset()
    assert InvalidOutputCategory.BACKEND_FAILURE not in MALFORMED_CATEGORIES
    assert InvalidOutputCategory.BACKEND_FAILURE not in SCHEMA_FAILURE_CATEGORIES
    assert (set(MALFORMED_CATEGORIES) | set(SCHEMA_FAILURE_CATEGORIES)
            | {InvalidOutputCategory.BACKEND_FAILURE}) == everything


def test_degenerate_detection_properties() -> None:
    for token in ("a", "{\"", "ab", "xyz", "abcd"):
        assert classify_invalid_output(
            reason_code="malformed_json", raw_excerpt=token * 12,
        ) is InvalidOutputCategory.DEGENERATE_REPETITION, token
    # short repetitions are NOT flagged (below the 8-repeat threshold)
    assert classify_invalid_output(
        reason_code="malformed_json", raw_excerpt="ab" * 3,
    ) is not InvalidOutputCategory.DEGENERATE_REPETITION


def test_statistics_invariants_across_output_mixes(
    tmp_path, matched_pair_pipeline,
) -> None:
    mixes = (
        lambda p, d: '{"prediction_type": "abstention"}',        # all valid
        lambda p, d: "garbage",                                   # all malformed
        lambda p, d: '{"prediction_type": "other"}',              # schema fail
    )
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=mixes[0], trained_responder=mixes[1])
    runs = [ctx.base_run, ctx.trained_run]
    third = ctx.make_trained(mixes[2])
    from verifiednet.evaluation import evaluate_prepared_corpus

    runs.append(evaluate_prepared_corpus(
        ctx.evalctx.loaded, third, ctx.ckptctx.task))
    for run in runs:
        stats = compute_parser_statistics(run)
        assert stats.total == len(run.records)
        assert (stats.valid_structured_predictions
                + stats.invalid_predictions) == stats.total
        assert (stats.json_valid_outputs + stats.malformed_outputs
                + stats.backend_failures) == stats.total
        assert sum(f.count for f in stats.failure_categories) == \
            stats.invalid_predictions
        assert stats.prompt_compliance_rate == \
            stats.valid_structured_prediction_rate
        assert compute_parser_statistics(run) == stats  # deterministic


def test_report_id_sensitivity() -> None:
    kwargs = {
        "benchmark_id": "bench-" + "0" * 16,
        "task_id": "task-" + "0" * 16,
        "prepared_digest": "0" * 64,
        "predictor_identifiers": ("baseline-a", "baseline-b"),
    }
    base = derive_structured_report_id(**kwargs)  # type: ignore[arg-type]
    assert base == derive_structured_report_id(**kwargs)  # type: ignore[arg-type]
    # order independence: identifiers are canonicalised
    reordered = dict(kwargs)
    reordered["predictor_identifiers"] = ("baseline-b", "baseline-a")
    assert derive_structured_report_id(**reordered) == base  # type: ignore[arg-type]
    for field, mutated in (
            ("benchmark_id", "bench-" + "f" * 16),
            ("task_id", "task-" + "f" * 16),
            ("prepared_digest", "f" * 64),
            ("predictor_identifiers", ("baseline-a",))):
        changed = dict(kwargs)
        changed[field] = mutated
        assert derive_structured_report_id(**changed) != base, field  # type: ignore[arg-type]
