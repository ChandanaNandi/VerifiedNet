"""Gate 9 security proof: benchmarking still passes predictors only features."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets.features import DatasetFeatures
from verifiednet.evaluation import (
    BaselineSpec,
    FixedPriorBaseline,
    diagnosis_task,
    run_benchmark,
)
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    InvalidPrediction,
)

pytestmark = pytest.mark.security

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


class _SpyPredictor:
    """Wraps a predictor and asserts every predict call receives ONLY features."""

    def __init__(self, inner: FixedPriorBaseline) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def spec(self) -> BaselineSpec:
        return self._inner.spec

    def predict(
        self, features: DatasetFeatures
    ) -> DiagnosisPrediction | AbstentionPrediction | InvalidPrediction:
        assert isinstance(features, DatasetFeatures)
        for forbidden in ("labels", "trace", "example_id", "group_id", "run_id",
                          "partition", "fault_family", "rejection_code"):
            assert not hasattr(features, forbidden), forbidden
        self.calls += 1
        return self._inner.predict(features)


def test_benchmark_passes_predictors_only_features(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    spy_a = _SpyPredictor(FixedPriorBaseline(task=task, fixed_fault_family="bgp_x"))
    spy_b = _SpyPredictor(FixedPriorBaseline(task=task, fixed_fault_family="bgp_y"))
    result = run_benchmark(ctx.loaded, task=task, predictors=[spy_a, spy_b])
    assert len(result.ranking) == 2
    # every predictor was asked once per example, and only ever saw features
    assert spy_a.calls == len(ctx.loaded.examples)
    assert spy_b.calls == len(ctx.loaded.examples)
