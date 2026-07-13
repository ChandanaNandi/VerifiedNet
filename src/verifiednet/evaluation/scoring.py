"""Scoring, per-example records, and aggregate metrics (Gate 7).

Scoring is exact normalized equality for the fault-family task — no fuzzy or
semantic matching. Accepted and abstention outcomes are scored with distinct,
structured categories and kept in separate metrics; abstention examples are never
scored as a healthy/no-fault class and never enter the accepted confusion matrix.

All ratios are derived from integer counts via a deterministic decimal string
(6 places, ROUND_HALF_EVEN). Zero-denominator policy: a ratio is ``None`` when no
eligible example exists (never ``0`` and never ``NaN``).
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.datasets.features import (
    AbstentionLabels,
    AcceptedLabels,
    DatasetTraceMetadata,
)
from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition
from verifiednet.evaluation.contract import NormalizationPolicy
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
)
from verifiednet.schemas.base import StrictModel

#: The explicit token used for an abstention "class" in the accepted-side view.
ABSTAIN_LABEL = "<abstain>"


class OutcomeCategory(StrEnum):
    CORRECT_DIAGNOSIS = "correct_diagnosis"
    INCORRECT_DIAGNOSIS = "incorrect_diagnosis"
    ABSTAINED_ON_DIAGNOSIS = "abstained_on_diagnosis"
    CORRECT_ABSTENTION = "correct_abstention"
    FALSE_DIAGNOSIS_ON_REJECTED = "false_diagnosis_on_rejected"


_CORRECT = frozenset(
    {OutcomeCategory.CORRECT_DIAGNOSIS, OutcomeCategory.CORRECT_ABSTENTION}
)


def ratio_str(numerator: int, denominator: int) -> str | None:
    """Deterministic 6-place decimal string, or ``None`` when denominator is 0."""
    if denominator == 0:
        return None
    value = (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_EVEN
    )
    return str(value)


def score(
    prediction: DiagnosisPrediction | AbstentionPrediction,
    labels: AcceptedLabels | AbstentionLabels,
    *,
    normalization: NormalizationPolicy,
) -> tuple[OutcomeCategory, bool, str | None]:
    """Return (outcome_category, correct, mismatch_reason) for one example."""
    if isinstance(labels, AcceptedLabels):
        if isinstance(prediction, AbstentionPrediction):
            return (OutcomeCategory.ABSTAINED_ON_DIAGNOSIS, False,
                    "abstained on a required diagnosis")
        expected = normalization.normalize(labels.fault_family)
        got = normalization.normalize(prediction.fault_family)
        if expected == got:
            return (OutcomeCategory.CORRECT_DIAGNOSIS, True, None)
        return (OutcomeCategory.INCORRECT_DIAGNOSIS, False,
                f"predicted {got!r} != expected {expected!r}")
    # abstention (rejected) example
    if isinstance(prediction, AbstentionPrediction):
        return (OutcomeCategory.CORRECT_ABSTENTION, True, None)
    return (OutcomeCategory.FALSE_DIAGNOSIS_ON_REJECTED, False,
            "predicted a fault family on a rejected example")


class EvaluationRecord(StrictModel):
    """One immutable per-example evaluation result."""

    schema_version: Literal[1] = 1
    task_id: str
    baseline_id: str
    feature_policy_id: str
    label_policy_id: str
    prediction_id: str
    # trace identity for auditing (never fed to the baseline)
    example_id: str
    group_id: str
    run_id: str
    partition: DatasetPartition
    example_kind: DatasetExampleKind
    prediction: DiagnosisPrediction | AbstentionPrediction = Field(
        discriminator="outcome_kind"
    )
    authoritative_target: str
    correct: bool
    outcome_category: OutcomeCategory
    mismatch_reason: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> EvaluationRecord:
        if self.prediction.prediction_id != self.prediction_id:
            raise ValueError("record prediction_id does not match its prediction")
        if (self.outcome_category in _CORRECT) != self.correct:
            raise ValueError("correct flag inconsistent with outcome_category")
        return self


def build_record(
    *,
    task_id: str,
    baseline_id: str,
    feature_policy_id: str,
    label_policy_id: str,
    labels: AcceptedLabels | AbstentionLabels,
    trace: DatasetTraceMetadata,
    prediction: DiagnosisPrediction | AbstentionPrediction,
    normalization: NormalizationPolicy,
) -> EvaluationRecord:
    category, correct, reason = score(prediction, labels, normalization=normalization)
    target = labels.fault_family if isinstance(labels, AcceptedLabels) else "abstain"
    return EvaluationRecord(
        task_id=task_id, baseline_id=baseline_id, feature_policy_id=feature_policy_id,
        label_policy_id=label_policy_id, prediction_id=prediction.prediction_id,
        example_id=trace.example_id, group_id=trace.group_id, run_id=trace.run_id,
        partition=trace.partition, example_kind=trace.example_kind, prediction=prediction,
        authoritative_target=target, correct=correct, outcome_category=category,
        mismatch_reason=reason,
    )


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------


class CorpusCounts(StrictModel):
    schema_version: Literal[1] = 1
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    abstention: int = Field(ge=0)
    train: int = Field(ge=0)
    validation: int = Field(ge=0)
    test: int = Field(ge=0)
    abstention_partition: int = Field(ge=0)


class AcceptedPartitionMetrics(StrictModel):
    schema_version: Literal[1] = 1
    partition: DatasetPartition
    evaluated: int = Field(ge=0)
    correct: int = Field(ge=0)
    incorrect: int = Field(ge=0)
    abstained: int = Field(ge=0)
    exact_match_accuracy: str | None = None


class AbstentionMetrics(StrictModel):
    schema_version: Literal[1] = 1
    count: int = Field(ge=0)
    correct: int = Field(ge=0)
    false_diagnosis: int = Field(ge=0)
    abstention_accuracy: str | None = None


class PartitionSummary(StrictModel):
    schema_version: Literal[1] = 1
    partition: DatasetPartition
    example_count: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    accuracy: str | None = None


class ConfusionCount(StrictModel):
    schema_version: Literal[1] = 1
    authoritative_class: str
    predicted: str
    count: int = Field(ge=1)


class AggregateMetrics(StrictModel):
    schema_version: Literal[1] = 1
    corpus_counts: CorpusCounts
    accepted_partitions: tuple[AcceptedPartitionMetrics, ...] = Field(default_factory=tuple)
    abstention: AbstentionMetrics
    overall_accuracy: str | None = None
