"""Deterministic, offline evaluation framework (Gate 7).

The evaluation engine is the one legitimate downstream CONSUMER of the read-only
dataset engine (Gate 6). It loads the prepared corpus, passes ONLY model-visible
``DatasetFeatures`` to a deterministic baseline, compares predictions to labels in
evaluator-only code, and writes an immutable, content-addressed evaluation result.
It never trains, never invokes a model/LLM/embedding, never executes a process,
and never mutates an earlier stage (verified runs → projection → splitting →
export → separation → baseline prediction → evaluation → immutable results).
"""

from verifiednet.evaluation.baseline import (
    Baseline,
    BaselineSpec,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    derive_baseline_id,
)
from verifiednet.evaluation.contract import (
    AcceptedTargetType,
    EvaluationTask,
    NormalizationPolicy,
    derive_task_id,
    diagnosis_task,
)
from verifiednet.evaluation.engine import (
    EvaluationError,
    EvaluationIntegrityResult,
    EvaluationRun,
    IntegrityCode,
    IntegrityFinding,
    audit_evaluation_run,
    compute_aggregate_metrics,
    compute_confusion,
    derive_evaluation_id,
    evaluate_prepared_corpus,
)
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    PredictionOutcome,
    build_abstention_prediction,
    build_diagnosis_prediction,
    derive_prediction_id,
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
    ratio_str,
    score,
)
from verifiednet.evaluation.store import (
    EXPECTED_EVALUATION_FILES,
    MANIFEST_FILE,
    EvaluationManifest,
    EvaluationStoreError,
    EvaluationVerificationResult,
    WrittenEvaluation,
    build_evaluation_export,
    compute_evaluation_digest,
    read_evaluation,
    verify_evaluation,
    write_evaluation,
)

__all__ = [
    "ABSTAIN_LABEL",
    "EXPECTED_EVALUATION_FILES",
    "MANIFEST_FILE",
    "AbstentionMetrics",
    "AbstentionPrediction",
    "AcceptedPartitionMetrics",
    "AcceptedTargetType",
    "AggregateMetrics",
    "Baseline",
    "BaselineSpec",
    "ConfusionCount",
    "CorpusCounts",
    "DiagnosisPrediction",
    "EvaluationError",
    "EvaluationIntegrityResult",
    "EvaluationManifest",
    "EvaluationRecord",
    "EvaluationRun",
    "EvaluationStoreError",
    "EvaluationTask",
    "EvaluationVerificationResult",
    "EvidenceRuleBaseline",
    "FixedPriorBaseline",
    "IntegrityCode",
    "IntegrityFinding",
    "NormalizationPolicy",
    "OutcomeCategory",
    "PartitionSummary",
    "PredictionOutcome",
    "WrittenEvaluation",
    "audit_evaluation_run",
    "build_abstention_prediction",
    "build_diagnosis_prediction",
    "build_evaluation_export",
    "compute_aggregate_metrics",
    "compute_confusion",
    "compute_evaluation_digest",
    "derive_baseline_id",
    "derive_evaluation_id",
    "derive_prediction_id",
    "derive_task_id",
    "diagnosis_task",
    "evaluate_prepared_corpus",
    "ratio_str",
    "read_evaluation",
    "score",
    "verify_evaluation",
    "verify_prediction_id",
    "write_evaluation",
]
