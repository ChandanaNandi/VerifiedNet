"""Gate 7 proofs: feature-only boundary, no execution, immutability, tampering."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from verifiednet.datasets.features import DatasetFeatures
from verifiednet.evaluation import (
    BaselineSpec,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    diagnosis_task,
    evaluate_prepared_corpus,
    read_evaluation,
    verify_evaluation,
    write_evaluation,
)
from verifiednet.evaluation.prediction import AbstentionPrediction, DiagnosisPrediction

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("pf-ref", "run-c")]


class _SpyBaseline:
    """Wraps a real baseline and asserts it receives ONLY features."""

    def __init__(self, inner: FixedPriorBaseline) -> None:
        self._inner = inner
        self.seen_types: list[str] = []

    @property
    def spec(self) -> BaselineSpec:
        return self._inner.spec

    def predict(self, features: DatasetFeatures) -> DiagnosisPrediction | AbstentionPrediction:
        # The argument must be exactly DatasetFeatures — never a labels, trace, or
        # separated-example object, and it must not expose evaluator-only fields.
        assert isinstance(features, DatasetFeatures)
        for forbidden in ("labels", "trace", "example_id", "group_id", "run_id",
                          "partition", "fault_family", "rejection_code"):
            assert not hasattr(features, forbidden), forbidden
        self.seen_types.append(type(features).__name__)
        return self._inner.predict(features)


def test_feature_only_boundary(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    spy = _SpyBaseline(FixedPriorBaseline(task=task, fixed_fault_family="bgp_x"))
    run = evaluate_prepared_corpus(ctx.loaded, spy, task)
    assert run.metrics.corpus_counts.total == 4
    assert spy.seen_types == ["DatasetFeatures"] * 4  # every call saw only features


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_evaluation_does_not_mutate_sources(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    before = {
        "runs": _fingerprint(Path(ctx.run_root)),
        "dataset": _fingerprint(Path(ctx.dataset_dir)),
        "prepared": _fingerprint(Path(ctx.prepared_dir)),
    }
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    write_evaluation(run, tmp_path / "evaluations")
    verify_evaluation(tmp_path / "evaluations" / run.evaluation_id)
    read_evaluation(tmp_path / "evaluations" / run.evaluation_id)

    after = {
        "runs": _fingerprint(Path(ctx.run_root)),
        "dataset": _fingerprint(Path(ctx.dataset_dir)),
        "prepared": _fingerprint(Path(ctx.prepared_dir)),
    }
    assert after == before  # all three upstream stages are untouched


def test_evaluation_executes_no_process(
    tmp_path: Path, eval_pipeline, monkeypatch,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("evaluation must not spawn a process")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "check_output", _boom)
    monkeypatch.setattr("verifiednet.runtime.process.default_runner", _boom)

    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    written = write_evaluation(run, tmp_path / "evaluations")
    assert verify_evaluation(written.root).verified is True
    read_evaluation(written.root)


def test_deliberate_tampering_is_detected(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    run = evaluate_prepared_corpus(ctx.loaded, baseline, task)
    w = write_evaluation(run, tmp_path / "evaluations")
    # Flip a stored correctness value in records.jsonl WITHOUT updating metrics/
    # manifest — the per-file hash guard and record validation catch it.
    victim = w.root / "records.jsonl"
    tampered = victim.read_bytes().replace(b'"correct":true', b'"correct":false', 1)
    assert tampered != victim.read_bytes()
    victim.write_bytes(tampered)
    result = verify_evaluation(w.root)
    assert result.verified is False
    assert any(c.rule in ("file_hashes_match", "run_reconstructs")
               for c in result.failures)
