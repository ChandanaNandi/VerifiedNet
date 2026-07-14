"""Gate 9 failure tests: fail-closed runner + benchmark store corruption."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    diagnosis_task,
    read_benchmark,
    run_benchmark,
    verify_benchmark,
    write_benchmark,
)
from verifiednet.evaluation.benchmark import BenchmarkError, compute_ranking
from verifiednet.evaluation.contract import NormalizationPolicy

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _result(tmp_path, eval_pipeline):
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    predictors = [
        FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch"),
        EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch"),
    ]
    return run_benchmark(ctx.loaded, task=task, predictors=predictors), ctx, task


def test_empty_predictors_fails(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    with pytest.raises(BenchmarkError):
        run_benchmark(ctx.loaded, task=diagnosis_task(), predictors=[])


def test_duplicate_predictors_fail(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    p = FixedPriorBaseline(task=task, fixed_fault_family="x")
    with pytest.raises(BenchmarkError):
        run_benchmark(ctx.loaded, task=task, predictors=[p, p])


def test_mismatched_task_fails(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task_a = diagnosis_task()
    task_b = diagnosis_task(normalization=NormalizationPolicy(casefold=False))
    predictor_for_a = FixedPriorBaseline(task=task_a, fixed_fault_family="x")
    with pytest.raises(BenchmarkError):
        run_benchmark(ctx.loaded, task=task_b, predictors=[predictor_for_a])


def test_corrupted_comparison_rejected(tmp_path: Path, eval_pipeline) -> None:
    result, _, _ = _result(tmp_path, eval_pipeline)
    w = write_benchmark(result, tmp_path / "benchmarks")
    victim = w.root / "comparison.json"
    victim.write_bytes(victim.read_bytes() + b" ")
    r = verify_benchmark(w.root)
    assert r.verified is False
    assert any(c.rule == "file_hashes_match" for c in r.failures)
    with pytest.raises(BenchmarkError):
        read_benchmark(w.root)


def test_tampered_manifest_digest_rejected(tmp_path: Path, eval_pipeline) -> None:
    result, _, _ = _result(tmp_path, eval_pipeline)
    w = write_benchmark(result, tmp_path / "benchmarks")
    m = w.root / "manifest.json"
    data = json.loads(m.read_text())
    data["prepared_digest"] = "0" * 64
    m.write_text(json.dumps(data))
    r = verify_benchmark(w.root)
    assert r.verified is False
    assert any(c.rule == "manifest_parses" for c in r.failures)


def test_missing_file_rejected(tmp_path: Path, eval_pipeline) -> None:
    result, _, _ = _result(tmp_path, eval_pipeline)
    w = write_benchmark(result, tmp_path / "benchmarks")
    (w.root / "ranking.json").unlink()
    r = verify_benchmark(w.root)
    assert r.verified is False
    assert any(c.rule == "no_missing_files" for c in r.failures)


def test_inconsistent_ranking_is_rejected_at_write(
    tmp_path: Path, eval_pipeline,
) -> None:
    result, _, _ = _result(tmp_path, eval_pipeline)
    # Swap the ranking so it no longer matches a recomputation from comparison.
    bad_ranking = tuple(reversed(compute_ranking(result.comparison)))
    bad = dataclasses.replace(result, ranking=bad_ranking)
    with pytest.raises(BenchmarkError):  # post-write verify catches it
        write_benchmark(bad, tmp_path / "benchmarks")


def test_missing_directory_rejected(tmp_path: Path) -> None:
    r = verify_benchmark(tmp_path / "nope")
    assert r.verified is False
    with pytest.raises(BenchmarkError):
        read_benchmark(tmp_path / "nope")


def test_overwrite_refused(tmp_path: Path, eval_pipeline) -> None:
    result, _, _ = _result(tmp_path, eval_pipeline)
    write_benchmark(result, tmp_path / "benchmarks")
    with pytest.raises(BenchmarkError):
        write_benchmark(result, tmp_path / "benchmarks")
