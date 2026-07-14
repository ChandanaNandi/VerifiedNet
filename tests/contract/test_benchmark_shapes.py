"""Contract tests: Gate 9 benchmark models frozen, forbid extras, validate ids."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.evaluation import (
    BenchmarkSpec,
    ComparisonRow,
    FixedPriorBaseline,
    PredictorRegistry,
    PredictorRegistryError,
    RankingEntry,
    derive_benchmark_id,
    diagnosis_task,
)
from verifiednet.evaluation.registry import PredictorEntry

pytestmark = pytest.mark.contract


def _spec(**overrides) -> BenchmarkSpec:
    ids = overrides.pop("predictor_identifiers", ("baseline-000000000000000a",
                                                  "baseline-000000000000000b"))
    fields = {
        "benchmark_name": "b", "task_id": "task-0000000000000000",
        "prepared_digest": "a" * 64, "predictor_identifiers": tuple(sorted(ids)),
        "normalization_policy_id": "norm-0000000000000000", "scoring_policy_version": 1,
    }
    fields.update(overrides)
    bid = derive_benchmark_id(
        benchmark_version=1, benchmark_name=fields["benchmark_name"],
        task_id=fields["task_id"], prepared_digest=fields["prepared_digest"],
        predictor_identifiers=fields["predictor_identifiers"],
        normalization_policy_id=fields["normalization_policy_id"],
        scoring_policy_version=fields["scoring_policy_version"],
    )
    return BenchmarkSpec(benchmark_id=bid, **fields)


def test_benchmark_spec_frozen_and_validated() -> None:
    spec = _spec()
    assert BenchmarkSpec.model_validate_json(spec.model_dump_json()) == spec
    with pytest.raises(ValidationError):
        spec.benchmark_name = "x"  # frozen
    with pytest.raises(ValidationError):  # tampered id rejected
        BenchmarkSpec.model_validate(spec.model_dump() | {"benchmark_id": "bench-0" + "0" * 15})
    with pytest.raises(ValidationError):  # extra forbidden
        BenchmarkSpec.model_validate(spec.model_dump() | {"surprise": 1})


def test_benchmark_id_is_order_independent() -> None:
    a = derive_benchmark_id(
        benchmark_version=1, benchmark_name="b", task_id="task-0",
        prepared_digest="a" * 64, predictor_identifiers=("z-id", "a-id"),
        normalization_policy_id="norm-0", scoring_policy_version=1)
    b = derive_benchmark_id(
        benchmark_version=1, benchmark_name="b", task_id="task-0",
        prepared_digest="a" * 64, predictor_identifiers=("a-id", "z-id"),
        normalization_policy_id="norm-0", scoring_policy_version=1)
    assert a == b


def test_spec_requires_sorted_unique_identifiers() -> None:
    unsorted = ("z-id", "a-id")
    bid = derive_benchmark_id(
        benchmark_version=1, benchmark_name="b", task_id="task-0",
        prepared_digest="a" * 64, predictor_identifiers=unsorted,
        normalization_policy_id="norm-0", scoring_policy_version=1)
    with pytest.raises(ValidationError):  # unsorted identifiers rejected
        BenchmarkSpec(
            benchmark_name="b", task_id="task-0", prepared_digest="a" * 64,
            predictor_identifiers=unsorted, normalization_policy_id="norm-0",
            scoring_policy_version=1, benchmark_id=bid)


def test_comparison_and_ranking_round_trip() -> None:
    row = ComparisonRow(
        predictor_identifier="baseline-0000000000000000",
        evaluation_id="eval-0000000000000000", accepted_evaluated=4, accepted_correct=1,
        exact_match_accuracy="0.250000", abstention_count=1, abstention_correct=1,
        abstention_accuracy="1.000000", invalid_prediction_count=0, evaluation_count=5)
    assert ComparisonRow.model_validate_json(row.model_dump_json()) == row
    entry = RankingEntry(rank=1, predictor_identifier="baseline-0000000000000000",
                         exact_match_accuracy="0.250000", abstention_accuracy="1.000000",
                         invalid_prediction_count=0)
    assert RankingEntry.model_validate_json(entry.model_dump_json()) == entry
    with pytest.raises(ValidationError):
        RankingEntry(rank=0, predictor_identifier="x", invalid_prediction_count=0)  # ge=1


def test_predictor_entry_validation() -> None:
    task = diagnosis_task()
    spec = FixedPriorBaseline(task=task, fixed_fault_family="x").spec
    good = PredictorEntry(
        predictor_identifier=spec.baseline_id, predictor_spec=spec,
        supported_task_ids=(spec.task_id,), supported_feature_policy_ids=("feat-0",))
    assert PredictorEntry.model_validate_json(good.model_dump_json()) == good
    with pytest.raises(ValidationError):  # identifier must equal baseline_id
        PredictorEntry(predictor_identifier="baseline-0000000000000000",
                       predictor_spec=spec, supported_task_ids=(spec.task_id,),
                       supported_feature_policy_ids=("feat-0",))
    with pytest.raises(ValidationError):  # supported must include the task
        PredictorEntry(predictor_identifier=spec.baseline_id, predictor_spec=spec,
                       supported_task_ids=("task-other",),
                       supported_feature_policy_ids=("feat-0",))


def test_registry_rejects_duplicate() -> None:
    task = diagnosis_task()
    reg = PredictorRegistry()
    p = FixedPriorBaseline(task=task, fixed_fault_family="x")
    reg.register(p, supported_feature_policy_ids=("feat-0",))
    with pytest.raises(PredictorRegistryError):
        reg.register(p, supported_feature_policy_ids=("feat-0",))
