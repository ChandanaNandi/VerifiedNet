"""Gate 9 unit tests: registry, spec, comparison, ranking, write/verify/read."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.evaluation import (
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    PredictorRegistry,
    compute_ranking,
    diagnosis_task,
    read_benchmark,
    run_benchmark,
    verify_benchmark,
    write_benchmark,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c"),
        ("pf-ref", "run-d")]


def _predictors(task):
    return [
        FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch"),
        EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch"),
        EvidenceRuleBaseline(task=task, default_fault_family="iface_admin_shutdown"),
    ]


def test_registry_is_deterministic_and_deduped(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    reg = PredictorRegistry()
    for p in _predictors(task):
        reg.register(p, supported_feature_policy_ids=(ctx.loaded.manifest.feature_policy_id,))
    assert len(reg) == 3
    assert list(reg.identifiers()) == sorted(reg.identifiers())
    # entries are ordered by identifier and expose supported ids
    entries = reg.entries()
    assert [e.predictor_identifier for e in entries] == sorted(reg.identifiers())
    assert all(task.task_id in e.supported_task_ids for e in entries)


def test_run_benchmark_and_persist(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    result = run_benchmark(ctx.loaded, task=task, predictors=_predictors(task))
    assert result.spec.benchmark_id.startswith("bench-")
    assert len(result.comparison) == 3
    assert len(result.ranking) == 3
    assert [r.rank for r in result.ranking] == [1, 2, 3]
    # comparison is ordered by predictor identifier
    ids = [row.predictor_identifier for row in result.comparison]
    assert ids == sorted(ids)

    written = write_benchmark(result, tmp_path / "benchmarks")
    assert written.root.name == result.spec.benchmark_id
    assert verify_benchmark(written.root).verified is True
    loaded = read_benchmark(written.root)
    assert loaded.manifest.benchmark_id == result.spec.benchmark_id
    assert loaded.ranking == result.ranking
    assert loaded.comparison == result.comparison


def test_ranking_orders_by_abstention_then_identifier(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    result = run_benchmark(ctx.loaded, task=task, predictors=_predictors(task))
    # The fixed-prior baseline never abstains -> abstention accuracy 0.0 -> ranked
    # below the evidence-rule baselines (abstention accuracy 1.0).
    by_id = {row.predictor_identifier: row for row in result.comparison}
    fixed_id = next(i for i, r in by_id.items() if r.abstention_accuracy == "0.000000")
    fixed_rank = next(r.rank for r in result.ranking if r.predictor_identifier == fixed_id)
    assert fixed_rank == 3  # last
    # ranking recomputes from comparison
    assert compute_ranking(result.comparison) == result.ranking


def test_comparison_counts_are_consistent(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    result = run_benchmark(ctx.loaded, task=task, predictors=_predictors(task))
    for row in result.comparison:
        assert row.evaluation_count == 5  # 4 accepted + 1 abstention
        assert row.accepted_evaluated == 4
        assert row.abstention_count == 1
        assert row.invalid_prediction_count == 0  # rule baselines never invalid


def test_single_predictor_benchmark(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    only = [FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch")]
    result = run_benchmark(ctx.loaded, task=task, predictors=only)
    assert len(result.ranking) == 1
    assert result.ranking[0].rank == 1
    written = write_benchmark(result, tmp_path / "benchmarks")
    assert verify_benchmark(written.root).verified is True
