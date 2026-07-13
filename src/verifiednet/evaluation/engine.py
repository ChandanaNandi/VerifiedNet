"""Pure evaluation engine + integrity audit (Gate 7).

``evaluate_prepared_corpus`` is a PURE function (no filesystem, network,
subprocess, randomness, or timestamps). It enforces the FEATURE-ONLY boundary
(only ``example.features`` reaches the baseline; labels and trace stay in
evaluator-only code), fails closed on a feature-leakage finding or a policy/task
mismatch, and returns an immutable ``EvaluationRun`` whose ``evaluation_id`` is a
non-recursive content hash.

``audit_evaluation_run`` independently recomputes every derived value from the
records alone (correctness, outcome category, counts, accuracy, confusion, and the
evaluation id) and fails closed on any ERROR — it never trusts a stored value.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.feature_leakage import audit_separated_example
from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition, LeakageSeverity
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.evaluation.baseline import Baseline, BaselineSpec
from verifiednet.evaluation.contract import EvaluationTask, NormalizationPolicy
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    InvalidPrediction,
    verify_prediction_id,
)
from verifiednet.evaluation.scoring import (
    ABSTAIN_LABEL,
    AbstentionMetrics,
    AcceptedPartitionMetrics,
    AggregateMetrics,
    ConfusionCount,
    CorpusCounts,
    EvaluationRecord,
    OutcomeCategory,
    PartitionSummary,
    build_record,
    ratio_str,
)
from verifiednet.schemas.base import StrictModel

EVALUATION_VERSION = 1
_ACCEPTED_PARTITIONS = (
    DatasetPartition.TRAIN, DatasetPartition.VALIDATION, DatasetPartition.TEST,
)
_CORRECT = frozenset(
    {OutcomeCategory.CORRECT_DIAGNOSIS, OutcomeCategory.CORRECT_ABSTENTION}
)


class EvaluationError(VerifiedNetError):
    """Evaluation could not proceed (leakage, policy/task mismatch, bad id)."""


# ---------------------------------------------------------------------------
# Metric computation (pure, from records)
# ---------------------------------------------------------------------------


def _recategorize(
    record: EvaluationRecord, normalization: NormalizationPolicy
) -> tuple[OutcomeCategory, bool]:
    pred = record.prediction
    if isinstance(pred, InvalidPrediction):
        return OutcomeCategory.INVALID_PREDICTION, False
    if record.example_kind is DatasetExampleKind.ACCEPTED_FAULT:
        if isinstance(pred, AbstentionPrediction):
            return OutcomeCategory.ABSTAINED_ON_DIAGNOSIS, False
        expected = normalization.normalize(record.authoritative_target)
        got = normalization.normalize(pred.fault_family)
        if expected == got:
            return OutcomeCategory.CORRECT_DIAGNOSIS, True
        return OutcomeCategory.INCORRECT_DIAGNOSIS, False
    if isinstance(pred, AbstentionPrediction):
        return OutcomeCategory.CORRECT_ABSTENTION, True
    return OutcomeCategory.FALSE_DIAGNOSIS_ON_REJECTED, False


def compute_corpus_counts(records: tuple[EvaluationRecord, ...]) -> CorpusCounts:
    by_part: Counter[DatasetPartition] = Counter(r.partition for r in records)
    accepted = sum(1 for r in records
                   if r.example_kind is DatasetExampleKind.ACCEPTED_FAULT)
    abstention = sum(1 for r in records
                     if r.example_kind is DatasetExampleKind.ABSTENTION)
    return CorpusCounts(
        total=len(records), accepted=accepted, abstention=abstention,
        train=by_part.get(DatasetPartition.TRAIN, 0),
        validation=by_part.get(DatasetPartition.VALIDATION, 0),
        test=by_part.get(DatasetPartition.TEST, 0),
        abstention_partition=by_part.get(DatasetPartition.ABSTENTION, 0),
    )


def compute_accepted_partition_metrics(
    records: tuple[EvaluationRecord, ...],
) -> tuple[AcceptedPartitionMetrics, ...]:
    out: list[AcceptedPartitionMetrics] = []
    for part in _ACCEPTED_PARTITIONS:
        members = [r for r in records
                   if r.partition is part
                   and r.example_kind is DatasetExampleKind.ACCEPTED_FAULT]
        if not members:
            continue
        correct = sum(1 for r in members
                      if r.outcome_category is OutcomeCategory.CORRECT_DIAGNOSIS)
        # Invalid model output is folded into the incorrect count (it is an
        # incorrect diagnosis attempt); the metrics schema is unchanged.
        incorrect = sum(1 for r in members
                        if r.outcome_category in (OutcomeCategory.INCORRECT_DIAGNOSIS,
                                                  OutcomeCategory.INVALID_PREDICTION))
        abstained = sum(1 for r in members
                        if r.outcome_category is OutcomeCategory.ABSTAINED_ON_DIAGNOSIS)
        out.append(AcceptedPartitionMetrics(
            partition=part, evaluated=len(members), correct=correct,
            incorrect=incorrect, abstained=abstained,
            exact_match_accuracy=ratio_str(correct, len(members)),
        ))
    return tuple(out)


def compute_abstention_metrics(records: tuple[EvaluationRecord, ...]) -> AbstentionMetrics:
    members = [r for r in records if r.example_kind is DatasetExampleKind.ABSTENTION]
    correct = sum(1 for r in members
                  if r.outcome_category is OutcomeCategory.CORRECT_ABSTENTION)
    false_diag = sum(1 for r in members
                     if r.outcome_category is OutcomeCategory.FALSE_DIAGNOSIS_ON_REJECTED)
    return AbstentionMetrics(
        count=len(members), correct=correct, false_diagnosis=false_diag,
        abstention_accuracy=ratio_str(correct, len(members)),
    )


def compute_partition_summaries(
    records: tuple[EvaluationRecord, ...],
) -> tuple[PartitionSummary, ...]:
    parts = sorted({r.partition for r in records}, key=lambda p: p.value)
    out: list[PartitionSummary] = []
    for part in parts:
        members = [r for r in records if r.partition is part]
        correct = sum(1 for r in members if r.correct)
        out.append(PartitionSummary(
            partition=part, example_count=len(members), correct_count=correct,
            accuracy=ratio_str(correct, len(members)),
        ))
    return tuple(out)


def compute_confusion(records: tuple[EvaluationRecord, ...]) -> tuple[ConfusionCount, ...]:
    counts: Counter[tuple[str, str]] = Counter()
    for r in records:
        if r.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue  # abstention examples never enter the accepted confusion matrix
        if isinstance(r.prediction, InvalidPrediction):
            continue  # invalid output is not a diagnosis/abstain outcome
        predicted = (r.prediction.fault_family
                     if isinstance(r.prediction, DiagnosisPrediction) else ABSTAIN_LABEL)
        counts[(r.authoritative_target, predicted)] += 1
    return tuple(
        ConfusionCount(authoritative_class=a, predicted=p, count=n)
        for (a, p), n in sorted(counts.items())
    )


def compute_aggregate_metrics(records: tuple[EvaluationRecord, ...]) -> AggregateMetrics:
    total_correct = sum(1 for r in records if r.correct)
    return AggregateMetrics(
        corpus_counts=compute_corpus_counts(records),
        accepted_partitions=compute_accepted_partition_metrics(records),
        abstention=compute_abstention_metrics(records),
        overall_accuracy=ratio_str(total_correct, len(records)),
    )


def derive_evaluation_id(
    *,
    task_id: str,
    baseline_id: str,
    prepared_digest: str,
    scoring_policy_version: int,
    prediction_ids: tuple[str, ...],
    metrics: AggregateMetrics,
) -> str:
    payload = {
        "task_id": task_id,
        "baseline_id": baseline_id,
        "prepared_digest": prepared_digest,
        "scoring_policy_version": scoring_policy_version,
        "prediction_ids": list(prediction_ids),
        "metrics": metrics.model_dump(mode="json"),
    }
    return "eval-" + sha256_canonical(payload)[:16]


class EvaluationRun(StrictModel):
    """The immutable, content-addressed result of one evaluation."""

    schema_version: Literal[1] = 1
    evaluation_version: Literal[1] = 1
    task: EvaluationTask
    baseline_spec: BaselineSpec
    prepared_digest: str
    dataset_digest: str | None = None
    feature_policy_id: str
    label_policy_id: str
    evaluation_id: str
    records: tuple[EvaluationRecord, ...] = Field(default_factory=tuple)
    metrics: AggregateMetrics
    confusion: tuple[ConfusionCount, ...] = Field(default_factory=tuple)
    partition_summaries: tuple[PartitionSummary, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _valid(self) -> EvaluationRun:
        if self.baseline_spec.task_id != self.task.task_id:
            raise ValueError("baseline_spec.task_id does not match task.task_id")
        ids = [r.example_id for r in self.records]
        if ids != sorted(ids):
            raise ValueError("records must be ordered by example_id")
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate example_id in records")
        expected = derive_evaluation_id(
            task_id=self.task.task_id, baseline_id=self.baseline_spec.baseline_id,
            prepared_digest=self.prepared_digest,
            scoring_policy_version=self.task.scoring_policy_version,
            prediction_ids=tuple(r.prediction_id for r in self.records),
            metrics=self.metrics,
        )
        if self.evaluation_id != expected:
            raise ValueError("evaluation_id does not match the evaluation content")
        return self


def evaluate_prepared_corpus(
    prepared: LoadedPrepared, baseline: Baseline, task: EvaluationTask
) -> EvaluationRun:
    """Evaluate a prepared corpus with a baseline under a task (pure, fail closed)."""
    spec: BaselineSpec = baseline.spec
    if spec.task_id != task.task_id:
        raise EvaluationError("baseline was built for a different task")

    manifest = prepared.manifest
    permitted = set(task.permitted_partitions)

    records: list[EvaluationRecord] = []
    for example in prepared.examples:  # already sorted by example_id
        if example.trace.partition not in permitted:
            raise EvaluationError(
                f"partition {example.trace.partition.value} not permitted by the task"
            )
        # Leakage resistance: re-audit the model-visible features; refuse on ERROR.
        leak = audit_separated_example(example)
        if not leak.passed:
            raise EvaluationError("feature-leakage audit failed; refusing to evaluate")

        features = example.features  # FEATURE-ONLY boundary
        if features.feature_policy_id != manifest.feature_policy_id:
            raise EvaluationError("inconsistent feature policy across the corpus")
        prediction = baseline.predict(features)
        if not verify_prediction_id(
            prediction, baseline_id=spec.baseline_id, task_id=task.task_id,
            feature_policy_id=features.feature_policy_id,
            feature_payload=features.model_dump(mode="json"),
        ):
            raise EvaluationError("baseline produced a non-deterministic prediction id")

        records.append(build_record(
            task_id=task.task_id, baseline_id=spec.baseline_id,
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            labels=example.labels, trace=example.trace, prediction=prediction,
            normalization=task.normalization,
        ))

    # Sort by example_id so the result is independent of input iteration order.
    ordered = tuple(sorted(records, key=lambda r: r.example_id))
    metrics = compute_aggregate_metrics(ordered)
    confusion = compute_confusion(ordered)
    summaries = compute_partition_summaries(ordered)
    evaluation_id = derive_evaluation_id(
        task_id=task.task_id, baseline_id=spec.baseline_id,
        prepared_digest=manifest.prepared_digest,
        scoring_policy_version=task.scoring_policy_version,
        prediction_ids=tuple(r.prediction_id for r in ordered), metrics=metrics,
    )
    return EvaluationRun(
        task=task, baseline_spec=spec, prepared_digest=manifest.prepared_digest,
        dataset_digest=manifest.source_dataset_digest,
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id, evaluation_id=evaluation_id,
        records=ordered, metrics=metrics, confusion=confusion,
        partition_summaries=summaries,
    )


# ---------------------------------------------------------------------------
# Integrity audit (recompute everything derivable from records)
# ---------------------------------------------------------------------------


class IntegrityCode(StrEnum):
    RECORD_CORRECTNESS = "record_correctness"
    RECORD_CATEGORY = "record_category"
    METRICS_MISMATCH = "metrics_mismatch"
    CONFUSION_MISMATCH = "confusion_mismatch"
    EVALUATION_ID_MISMATCH = "evaluation_id_mismatch"
    DUPLICATE_RECORD = "duplicate_record"


class IntegrityFinding(StrictModel):
    schema_version: Literal[1] = 1
    code: IntegrityCode
    severity: LeakageSeverity
    detail: str = ""


class EvaluationIntegrityResult(StrictModel):
    schema_version: Literal[1] = 1
    passed: bool
    findings: tuple[IntegrityFinding, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _fail_closed(self) -> EvaluationIntegrityResult:
        has_error = any(f.severity is LeakageSeverity.ERROR for f in self.findings)
        if self.passed and has_error:
            raise ValueError("integrity audit cannot pass with ERROR findings")
        return self

    @property
    def errors(self) -> tuple[IntegrityFinding, ...]:
        return tuple(f for f in self.findings if f.severity is LeakageSeverity.ERROR)


def _err(code: IntegrityCode, detail: str) -> IntegrityFinding:
    return IntegrityFinding(code=code, severity=LeakageSeverity.ERROR, detail=detail)


def audit_evaluation_run(run: EvaluationRun) -> EvaluationIntegrityResult:
    """Recompute every derived value from the records; never trust stored values."""
    findings: list[IntegrityFinding] = []
    norm = run.task.normalization

    seen: set[str] = set()
    for r in run.records:
        if r.example_id in seen:
            findings.append(_err(IntegrityCode.DUPLICATE_RECORD, r.example_id))
        seen.add(r.example_id)
        category, correct = _recategorize(r, norm)
        if r.outcome_category is not category:
            findings.append(_err(
                IntegrityCode.RECORD_CATEGORY,
                f"{r.example_id}: stored {r.outcome_category.value} != {category.value}"))
        if r.correct != correct:
            findings.append(_err(
                IntegrityCode.RECORD_CORRECTNESS,
                f"{r.example_id}: stored correct={r.correct} != {correct}"))

    if compute_aggregate_metrics(run.records) != run.metrics:
        findings.append(_err(IntegrityCode.METRICS_MISMATCH, "aggregate metrics differ"))
    if compute_confusion(run.records) != run.confusion:
        findings.append(_err(IntegrityCode.CONFUSION_MISMATCH, "confusion counts differ"))

    expected_id = derive_evaluation_id(
        task_id=run.task.task_id, baseline_id=run.baseline_spec.baseline_id,
        prepared_digest=run.prepared_digest,
        scoring_policy_version=run.task.scoring_policy_version,
        prediction_ids=tuple(r.prediction_id for r in run.records), metrics=run.metrics,
    )
    if run.evaluation_id != expected_id:
        findings.append(_err(IntegrityCode.EVALUATION_ID_MISMATCH, "evaluation_id differs"))

    has_error = any(f.severity is LeakageSeverity.ERROR for f in findings)
    return EvaluationIntegrityResult(passed=not has_error, findings=tuple(findings))
