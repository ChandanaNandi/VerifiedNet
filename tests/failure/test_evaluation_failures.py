"""Gate 7 failure tests: fail-closed engine, store corruption, integrity audit."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    OutcomeCategory,
    audit_evaluation_run,
    diagnosis_task,
    evaluate_prepared_corpus,
    read_evaluation,
    verify_evaluation,
    write_evaluation,
)
from verifiednet.evaluation.contract import NormalizationPolicy
from verifiednet.evaluation.engine import EvaluationError
from verifiednet.evaluation.store import EvaluationStoreError

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _run(tmp_path: Path, eval_pipeline):
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task = diagnosis_task()
    baseline = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    return evaluate_prepared_corpus(ctx.loaded, baseline, task), ctx


def test_baseline_task_mismatch_fails(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    task_a = diagnosis_task()
    task_b = diagnosis_task(normalization=NormalizationPolicy(casefold=False))
    baseline = FixedPriorBaseline(task=task_a, fixed_fault_family="x")
    with pytest.raises(EvaluationError):
        evaluate_prepared_corpus(ctx.loaded, baseline, task_b)


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_feature_leakage_refuses_evaluation(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    from verifiednet.datasets.features import DatasetFeatures, SeparatedDatasetExample

    victim = ctx.loaded.examples[0]
    leaked_features = DatasetFeatures.model_construct(
        schema_version=1, feature_policy_id=victim.features.feature_policy_id,
        topology_hash=victim.features.topology_hash, backend=victim.features.backend,
        baseline_evidence={"ground_truth_reference": victim.trace.run_digest},
        onset_evidence=victim.features.onset_evidence,
    )
    tampered = SeparatedDatasetExample.model_construct(
        schema_version=1, features=leaked_features, labels=victim.labels,
        trace=victim.trace)
    leaky_loaded = dataclasses.replace(
        ctx.loaded, examples=(tampered, *ctx.loaded.examples[1:]))
    task = diagnosis_task()
    baseline = FixedPriorBaseline(task=task, fixed_fault_family="x")
    with pytest.raises(EvaluationError):
        evaluate_prepared_corpus(leaky_loaded, baseline, task)


def test_unsafe_overwrite_refused(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    write_evaluation(run, tmp_path / "evaluations")
    with pytest.raises(EvaluationStoreError):
        write_evaluation(run, tmp_path / "evaluations")


def test_corrupted_records_rejected(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    w = write_evaluation(run, tmp_path / "evaluations")
    victim = w.root / "records.jsonl"
    victim.write_bytes(victim.read_bytes() + b" ")
    result = verify_evaluation(w.root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    with pytest.raises(EvaluationStoreError):
        read_evaluation(w.root)


def test_corrupted_metrics_rejected(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    w = write_evaluation(run, tmp_path / "evaluations")
    victim = w.root / "metrics.json"
    victim.write_bytes(victim.read_bytes() + b" ")
    assert verify_evaluation(w.root).verified is False


def test_tampered_manifest_digest_rejected(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    w = write_evaluation(run, tmp_path / "evaluations")
    m = w.root / "manifest.json"
    data = json.loads(m.read_text())
    data["prepared_digest"] = "0" * 64
    m.write_text(json.dumps(data))
    result = verify_evaluation(w.root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)


def test_missing_file_rejected(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    w = write_evaluation(run, tmp_path / "evaluations")
    (w.root / "confusion.json").unlink()
    result = verify_evaluation(w.root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)


def test_missing_directory_rejected(tmp_path: Path) -> None:
    result = verify_evaluation(tmp_path / "nope")
    assert result.verified is False
    with pytest.raises(EvaluationStoreError):
        read_evaluation(tmp_path / "nope")


def test_integrity_audit_detects_wrong_category(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    # Flip one record's stored category (model_copy skips validation) — the
    # integrity audit recomputes from the record and must catch the tamper.
    r0 = run.records[0]
    flipped = (OutcomeCategory.INCORRECT_DIAGNOSIS
               if r0.outcome_category is OutcomeCategory.CORRECT_DIAGNOSIS
               else OutcomeCategory.CORRECT_DIAGNOSIS)
    tampered_record = r0.model_copy(update={"outcome_category": flipped})
    tampered_run = run.model_copy(
        update={"records": (tampered_record, *run.records[1:])})
    result = audit_evaluation_run(tampered_run)
    assert result.passed is False


def test_run_rejects_wrong_evaluation_id(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        type(run).model_validate(run.model_dump() | {"evaluation_id": "eval-0000000000000000"})


def test_run_rejects_baseline_task_mismatch(tmp_path: Path, eval_pipeline) -> None:
    run, _ = _run(tmp_path, eval_pipeline)
    from pydantic import ValidationError

    other = diagnosis_task(normalization=NormalizationPolicy(casefold=False))
    with pytest.raises(ValidationError):
        type(run).model_validate(run.model_dump() | {"task": other.model_dump()})
