"""Matched base-versus-trained comparison + interpretation policy (Gate 12).

Gate 12 measures; it never optimizes. This module derives EVALUATOR-ONLY
evidence from two unchanged Gate 7 evaluation runs over the SAME prepared
corpus and task: a fairness check (the only intended difference between the
matched predictors is the weights), an exact paired comparison over aligned
``example_id``s, a deterministic disagreement report, and a frozen
interpretation policy that governs WORDING ONLY — it never alters Gate 7
metrics or Gate 9 ranking.

Statistical honesty is structural: raw counts precede every ratio, a
fixture-generated corpus can never yield more than an engineering conclusion,
an underpowered corpus is labeled inconclusive, a confounded pair can never
produce an unqualified training-effect claim, and regressions are always
surfaced even when the ranking improves. Nothing here flows back into
training — comparisons are terminal artifacts (ADR-0022 unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationError, model_validator

from verifiednet.artifacts.durable import atomic_write_bytes, fsync_dir
from verifiednet.common.canonical import canonical_json_bytes
from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_bytes, sha256_canonical
from verifiednet.datasets.models import (
    DatasetFileHash,
    DatasetPartition,
    DatasetPartitionCounts,
)
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.engine import EvaluationRun
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
    InvalidPrediction,
)
from verifiednet.evaluation.scoring import EvaluationRecord, OutcomeCategory
from verifiednet.schemas.base import StrictModel

if TYPE_CHECKING:  # facts builders only; no runtime dependency on predictors
    from verifiednet.evaluation.basemodel import VerifiedBaseModelPredictor
    from verifiednet.evaluation.checkpointpred import (
        VerifiedCheckpointPredictor,
    )

COMPARISON_VERSION = 1
COMPARISON_FORMAT_VERSION = 1
COMPARISON_GENERATOR = "verifiednet.evaluation.comparison"
MANIFEST_FILE = "manifest.json"
SUMMARY_FILE = "summary.json"
DISAGREEMENTS_FILE = "disagreements.jsonl"
COMPARISON_INCOMPLETE_MARKER = ".INCOMPLETE"
EXPECTED_COMPARISON_FILES: frozenset[str] = frozenset(
    {SUMMARY_FILE, DISAGREEMENTS_FILE})

#: The configuration facts that must be IDENTICAL for a matched comparison —
#: weights are meant to be the only difference between base and trained.
MATCHED_FAIRNESS_FIELDS: tuple[str, ...] = (
    "prompt_template_id", "decoding_config_id", "normalization_policy_id",
    "backend_family", "inference_precision", "device_policy_id",
    "compatibility_id",
)


class ComparisonError(VerifiedNetError):
    """A paired comparison could not be built, written, or read."""


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


# ---------------------------------------------------------------------------
# Fairness: matched means matched
# ---------------------------------------------------------------------------


class PairedPredictorFacts(StrictModel):
    """The comparison-relevant configuration facts of one matched predictor."""

    schema_version: Literal[1] = 1
    role: str = Field(min_length=1)
    predictor_id: str = Field(min_length=1)
    baseline_id: str = Field(min_length=1)
    prompt_template_id: str = Field(min_length=1)
    decoding_config_id: str = Field(min_length=1)
    normalization_policy_id: str = Field(min_length=1)
    backend_family: str = Field(min_length=1)
    inference_precision: str = Field(min_length=1)
    device_policy_id: str = Field(min_length=1)
    compatibility_id: str = Field(min_length=1)


def _facts(
    *,
    role: str,
    predictor_id: str,
    baseline_id: str,
    prompt_template_id: str,
    decoding_config_id: str,
    normalization_policy_id: str,
    backend_family: str,
    inference_precision: str,
    device_policy_id: str,
    compatibility_id: str,
) -> PairedPredictorFacts:
    return PairedPredictorFacts(
        role=role, predictor_id=predictor_id, baseline_id=baseline_id,
        prompt_template_id=prompt_template_id,
        decoding_config_id=decoding_config_id,
        normalization_policy_id=normalization_policy_id,
        backend_family=backend_family,
        inference_precision=inference_precision,
        device_policy_id=device_policy_id, compatibility_id=compatibility_id)


def checkpoint_predictor_facts(
    predictor: VerifiedCheckpointPredictor,
    *,
    role: str = "trained_checkpoint",
) -> PairedPredictorFacts:
    """Comparison facts of a Gate 11 checkpoint predictor."""
    spec = predictor.predictor_spec
    return _facts(
        role=role, predictor_id=spec.predictor_id,
        baseline_id=predictor.spec.baseline_id,
        prompt_template_id=spec.prompt_template_id,
        decoding_config_id=spec.decoding.config_id,
        normalization_policy_id=spec.normalization_policy_id,
        backend_family=spec.backend_family,
        inference_precision=spec.inference_precision,
        device_policy_id=spec.device_policy_id,
        compatibility_id=spec.compatibility_id)


def base_model_predictor_facts(
    predictor: VerifiedBaseModelPredictor,
    *,
    role: str = "matched_base_model",
) -> PairedPredictorFacts:
    """Comparison facts of the Gate 12 matched base-model predictor."""
    spec = predictor.predictor_spec
    return _facts(
        role=role, predictor_id=spec.predictor_id,
        baseline_id=predictor.spec.baseline_id,
        prompt_template_id=spec.prompt_template_id,
        decoding_config_id=spec.decoding.config_id,
        normalization_policy_id=spec.normalization_policy_id,
        backend_family=spec.backend_family,
        inference_precision=spec.inference_precision,
        device_policy_id=spec.device_policy_id,
        compatibility_id=spec.compatibility_id)


class MatchedPairFairness(StrictModel):
    """The structured verdict on whether base-vs-trained is unconfounded."""

    schema_version: Literal[1] = 1
    base: PairedPredictorFacts
    trained: PairedPredictorFacts
    task_id: str = Field(min_length=1)
    prepared_digest: str = Field(min_length=1)
    confounded_fields: tuple[str, ...] = Field(default_factory=tuple)
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)
    fair: bool

    @model_validator(mode="after")
    def _consistent(self) -> MatchedPairFairness:
        if list(self.confounded_fields) != sorted(self.confounded_fields):
            raise ValueError("confounded_fields must be sorted")
        if self.fair != all(c.passed for c in self.checks):
            raise ValueError("fair flag inconsistent with checks")
        if self.confounded_fields and self.fair:
            raise ValueError("a confounded pair cannot be fair")
        return self


def assess_matched_pair_fairness(
    *,
    base: PairedPredictorFacts,
    trained: PairedPredictorFacts,
    base_run: EvaluationRun,
    trained_run: EvaluationRun,
) -> MatchedPairFairness:
    """Verify that weights are the ONLY intended difference; fail visible.

    Any differing configuration fact is recorded in ``confounded_fields`` and
    makes the pair unfair — the interpretation layer then refuses to word the
    result as a fine-tuning effect.
    """
    checks: list[DatasetCheck] = []
    confounded: list[str] = []
    for field in MATCHED_FAIRNESS_FIELDS:
        base_value = getattr(base, field)
        trained_value = getattr(trained, field)
        same = base_value == trained_value
        if not same:
            confounded.append(field)
        checks.append(_c(
            f"same_{field}", same,
            "" if same else f"{base_value!r} != {trained_value!r}"))
    checks.append(_c("same_task", base_run.task.task_id == trained_run.task.task_id))
    checks.append(_c("same_prepared_digest",
                     base_run.prepared_digest == trained_run.prepared_digest))
    checks.append(_c("same_feature_policy",
                     base_run.feature_policy_id == trained_run.feature_policy_id))
    checks.append(_c("same_label_policy",
                     base_run.label_policy_id == trained_run.label_policy_id))
    checks.append(_c("distinct_predictors",
                     base.predictor_id != trained.predictor_id
                     and base.baseline_id != trained.baseline_id))
    checks.append(_c("facts_bind_runs",
                     base.baseline_id == base_run.baseline_spec.baseline_id
                     and trained.baseline_id
                     == trained_run.baseline_spec.baseline_id))
    return MatchedPairFairness(
        base=base, trained=trained, task_id=base_run.task.task_id,
        prepared_digest=base_run.prepared_digest,
        confounded_fields=tuple(sorted(confounded)), checks=tuple(checks),
        fair=all(c.passed for c in checks))


# ---------------------------------------------------------------------------
# Paired comparison over aligned example ids
# ---------------------------------------------------------------------------


class TransitionCategory(StrEnum):
    UNCHANGED_CORRECT = "unchanged_correct"
    UNCHANGED_INCORRECT = "unchanged_incorrect"
    IMPROVED = "improved"
    REGRESSED = "regressed"
    CHANGED_BUT_STILL_INCORRECT = "changed_but_still_incorrect"


def _prediction_content(
    prediction: DiagnosisPrediction | AbstentionPrediction | InvalidPrediction,
) -> dict[str, object]:
    data: dict[str, object] = prediction.model_dump(mode="json")
    data.pop("prediction_id", None)
    return data


def _classify(
    base: EvaluationRecord, trained: EvaluationRecord,
) -> tuple[TransitionCategory, bool, bool]:
    """Return (transition, predictions_identical, abstention_changed)."""
    identical = (_prediction_content(base.prediction)
                 == _prediction_content(trained.prediction))
    abstention_changed = (
        isinstance(base.prediction, AbstentionPrediction)
        != isinstance(trained.prediction, AbstentionPrediction))
    if base.correct and trained.correct:
        transition = TransitionCategory.UNCHANGED_CORRECT
    elif base.correct and not trained.correct:
        transition = TransitionCategory.REGRESSED
    elif not base.correct and trained.correct:
        transition = TransitionCategory.IMPROVED
    elif identical:
        transition = TransitionCategory.UNCHANGED_INCORRECT
    else:
        transition = TransitionCategory.CHANGED_BUT_STILL_INCORRECT
    return transition, identical, abstention_changed


class DisagreementRecord(StrictModel):
    """One aligned example where the two predictions DIFFERED (evaluator-only).

    Never fed back into training, never containing chain-of-thought — only
    the structured predictions and their unchanged Gate 7 outcome categories.
    """

    schema_version: Literal[1] = 1
    example_id: str = Field(min_length=1)
    partition: DatasetPartition
    authoritative_target: str = Field(min_length=1)
    base_prediction: (
        DiagnosisPrediction | AbstentionPrediction | InvalidPrediction
    ) = Field(discriminator="outcome_kind")
    trained_prediction: (
        DiagnosisPrediction | AbstentionPrediction | InvalidPrediction
    ) = Field(discriminator="outcome_kind")
    base_outcome: OutcomeCategory
    trained_outcome: OutcomeCategory
    transition: TransitionCategory
    abstention_changed: bool


class PairedComparisonCounts(StrictModel):
    """Exact deterministic paired counts (raw counts, never only ratios)."""

    schema_version: Literal[1] = 1
    total: int = Field(ge=0)
    both_correct: int = Field(ge=0)
    both_incorrect: int = Field(ge=0)
    base_correct_trained_incorrect: int = Field(ge=0)
    base_incorrect_trained_correct: int = Field(ge=0)
    predictions_identical: int = Field(ge=0)
    predictions_differed: int = Field(ge=0)
    base_invalid: int = Field(ge=0)
    trained_invalid: int = Field(ge=0)
    abstention_decision_changes: int = Field(ge=0)

    @model_validator(mode="after")
    def _sums(self) -> PairedComparisonCounts:
        partitioned = (self.both_correct + self.both_incorrect
                       + self.base_correct_trained_incorrect
                       + self.base_incorrect_trained_correct)
        if partitioned != self.total:
            raise ValueError("correctness quadrants must sum to total")
        if self.predictions_identical + self.predictions_differed != self.total:
            raise ValueError("identical + differed must sum to total")
        return self


def derive_comparison_id(
    *,
    task_id: str,
    prepared_digest: str,
    base_baseline_id: str,
    trained_baseline_id: str,
    base_evaluation_id: str,
    trained_evaluation_id: str,
) -> str:
    payload = {
        "task_id": task_id,
        "prepared_digest": prepared_digest,
        "base_baseline_id": base_baseline_id,
        "trained_baseline_id": trained_baseline_id,
        "base_evaluation_id": base_evaluation_id,
        "trained_evaluation_id": trained_evaluation_id,
    }
    return "cmp-" + sha256_canonical(payload)[:16]


class PairedComparison(StrictModel):
    """The deterministic paired result (counts; disagreements ride alongside)."""

    schema_version: Literal[1] = 1
    comparison_version: Literal[1] = 1
    task_id: str = Field(min_length=1)
    prepared_digest: str = Field(min_length=1)
    feature_policy_id: str = Field(min_length=1)
    label_policy_id: str = Field(min_length=1)
    base_baseline_id: str = Field(min_length=1)
    trained_baseline_id: str = Field(min_length=1)
    base_evaluation_id: str = Field(min_length=1)
    trained_evaluation_id: str = Field(min_length=1)
    fairness: MatchedPairFairness
    aligned_partitions: DatasetPartitionCounts
    counts_all: PairedComparisonCounts
    counts_non_train: PairedComparisonCounts
    comparison_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> PairedComparison:
        if self.fairness.task_id != self.task_id:
            raise ValueError("fairness binds a different task")
        if self.fairness.prepared_digest != self.prepared_digest:
            raise ValueError("fairness binds a different prepared corpus")
        if self.fairness.base.baseline_id != self.base_baseline_id:
            raise ValueError("fairness base facts bind a different predictor")
        if self.fairness.trained.baseline_id != self.trained_baseline_id:
            raise ValueError("fairness trained facts bind a different predictor")
        expected = derive_comparison_id(
            task_id=self.task_id, prepared_digest=self.prepared_digest,
            base_baseline_id=self.base_baseline_id,
            trained_baseline_id=self.trained_baseline_id,
            base_evaluation_id=self.base_evaluation_id,
            trained_evaluation_id=self.trained_evaluation_id)
        if self.comparison_id != expected:
            raise ValueError("comparison_id does not match the comparison")
        return self


@dataclass(frozen=True)
class ComparisonResult:
    """In-memory paired comparison + its full disagreement report."""

    comparison: PairedComparison
    disagreements: tuple[DisagreementRecord, ...]


def _counts(
    pairs: list[tuple[EvaluationRecord, EvaluationRecord]],
) -> PairedComparisonCounts:
    both_correct = both_incorrect = improved = regressed = 0
    identical = differed = base_invalid = trained_invalid = abst = 0
    for base, trained in pairs:
        transition, same, abstention_changed = _classify(base, trained)
        if transition is TransitionCategory.UNCHANGED_CORRECT:
            both_correct += 1
        elif transition is TransitionCategory.IMPROVED:
            improved += 1
        elif transition is TransitionCategory.REGRESSED:
            regressed += 1
        else:
            both_incorrect += 1
        identical += 1 if same else 0
        differed += 0 if same else 1
        base_invalid += 1 if isinstance(base.prediction, InvalidPrediction) else 0
        trained_invalid += (
            1 if isinstance(trained.prediction, InvalidPrediction) else 0)
        abst += 1 if abstention_changed else 0
    return PairedComparisonCounts(
        total=len(pairs), both_correct=both_correct,
        both_incorrect=both_incorrect,
        base_correct_trained_incorrect=regressed,
        base_incorrect_trained_correct=improved,
        predictions_identical=identical, predictions_differed=differed,
        base_invalid=base_invalid, trained_invalid=trained_invalid,
        abstention_decision_changes=abst)


def build_paired_comparison(
    base_run: EvaluationRun,
    trained_run: EvaluationRun,
    *,
    fairness: MatchedPairFairness,
) -> ComparisonResult:
    """Align by ``example_id`` and derive exact paired evidence; fail closed.

    Input order cannot matter: both runs are already example-id-sorted by the
    Gate 7 engine, alignment is by id equality, and every derived structure is
    id-sorted. Refuses on any task/corpus/policy/alignment mismatch.
    """
    if base_run.task.task_id != trained_run.task.task_id:
        raise ComparisonError("runs evaluate different tasks")
    if base_run.prepared_digest != trained_run.prepared_digest:
        raise ComparisonError("runs evaluate different prepared corpora")
    if base_run.feature_policy_id != trained_run.feature_policy_id:
        raise ComparisonError("runs use different feature policies")
    if base_run.label_policy_id != trained_run.label_policy_id:
        raise ComparisonError("runs use different label policies")
    if base_run.baseline_spec.baseline_id == trained_run.baseline_spec.baseline_id:
        raise ComparisonError("a paired comparison needs two distinct predictors")
    if fairness.base.baseline_id != base_run.baseline_spec.baseline_id \
            or fairness.trained.baseline_id != trained_run.baseline_spec.baseline_id:
        raise ComparisonError("fairness facts do not bind these evaluation runs")

    base_by_id = {r.example_id: r for r in base_run.records}
    trained_by_id = {r.example_id: r for r in trained_run.records}
    if set(base_by_id) != set(trained_by_id):
        missing = sorted(set(base_by_id) ^ set(trained_by_id))
        raise ComparisonError(f"example alignment mismatch: {missing}")

    pairs: list[tuple[EvaluationRecord, EvaluationRecord]] = []
    disagreements: list[DisagreementRecord] = []
    for example_id in sorted(base_by_id):
        base, trained = base_by_id[example_id], trained_by_id[example_id]
        if base.authoritative_target != trained.authoritative_target:
            raise ComparisonError(
                f"authoritative target mismatch on {example_id}")
        if base.partition is not trained.partition:
            raise ComparisonError(f"partition mismatch on {example_id}")
        pairs.append((base, trained))
        transition, identical, abstention_changed = _classify(base, trained)
        if not identical:
            disagreements.append(DisagreementRecord(
                example_id=example_id, partition=base.partition,
                authoritative_target=base.authoritative_target,
                base_prediction=base.prediction,
                trained_prediction=trained.prediction,
                base_outcome=base.outcome_category,
                trained_outcome=trained.outcome_category,
                transition=transition,
                abstention_changed=abstention_changed))

    partitions = DatasetPartitionCounts(
        train=sum(1 for b, _ in pairs
                  if b.partition is DatasetPartition.TRAIN),
        validation=sum(1 for b, _ in pairs
                       if b.partition is DatasetPartition.VALIDATION),
        test=sum(1 for b, _ in pairs if b.partition is DatasetPartition.TEST),
        abstention=sum(1 for b, _ in pairs
                       if b.partition is DatasetPartition.ABSTENTION))
    comparison = PairedComparison(
        task_id=base_run.task.task_id, prepared_digest=base_run.prepared_digest,
        feature_policy_id=base_run.feature_policy_id,
        label_policy_id=base_run.label_policy_id,
        base_baseline_id=base_run.baseline_spec.baseline_id,
        trained_baseline_id=trained_run.baseline_spec.baseline_id,
        base_evaluation_id=base_run.evaluation_id,
        trained_evaluation_id=trained_run.evaluation_id,
        fairness=fairness, aligned_partitions=partitions,
        counts_all=_counts(pairs),
        counts_non_train=_counts(
            [p for p in pairs if p[0].partition is not DatasetPartition.TRAIN]),
        comparison_id=derive_comparison_id(
            task_id=base_run.task.task_id,
            prepared_digest=base_run.prepared_digest,
            base_baseline_id=base_run.baseline_spec.baseline_id,
            trained_baseline_id=trained_run.baseline_spec.baseline_id,
            base_evaluation_id=base_run.evaluation_id,
            trained_evaluation_id=trained_run.evaluation_id))
    return ComparisonResult(
        comparison=comparison, disagreements=tuple(disagreements))


# ---------------------------------------------------------------------------
# Interpretation policy: wording only, never metrics
# ---------------------------------------------------------------------------


class CorpusProvenance(StrEnum):
    FIXTURE_GENERATED = "fixture_generated"
    PROJECT_PERSISTED = "project_persisted"


class InterpretationConclusion(StrEnum):
    CONFOUNDED = "confounded"
    NO_OBSERVED_EFFECT = "no_observed_effect"
    INCONCLUSIVE_UNDERPOWERED = "inconclusive_underpowered"
    BETTER_ON_THIS_CORPUS = "better_on_this_corpus"
    WORSE_ON_THIS_CORPUS = "worse_on_this_corpus"
    MIXED_ON_THIS_CORPUS = "mixed_on_this_corpus"
    UNCHANGED_ON_THIS_CORPUS = "unchanged_on_this_corpus"


class BenchmarkInterpretationPolicy(StrictModel):
    """Frozen, versioned thresholds that govern conclusion WORDING only.

    Deliberately conservative: a fixture-generated corpus permits only an
    engineering conclusion; fewer eligible test examples than the minimum is
    underpowered; no changed prediction is no observed effect; and any
    regression is always reported. This policy can never change a metric or a
    ranking — it has no access to them.
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    min_eligible_test_examples: int = Field(ge=1)
    min_changed_predictions: int = Field(ge=1)
    exclude_train_partition_from_conclusions: Literal[True] = True
    fixture_corpus_engineering_only: Literal[True] = True
    interpretation_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> BenchmarkInterpretationPolicy:
        if self.interpretation_policy_id != derive_interpretation_policy_id(self):
            raise ValueError(
                "interpretation_policy_id does not match the policy content")
        return self


def derive_interpretation_policy_id(
    policy: BenchmarkInterpretationPolicy,
) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("interpretation_policy_id", None)
    return "interp-" + sha256_canonical(payload)[:16]


def build_default_interpretation_policy(
    *,
    min_eligible_test_examples: int = 30,
    min_changed_predictions: int = 1,
) -> BenchmarkInterpretationPolicy:
    probe = BenchmarkInterpretationPolicy.model_construct(
        min_eligible_test_examples=min_eligible_test_examples,
        min_changed_predictions=min_changed_predictions)
    return BenchmarkInterpretationPolicy(
        min_eligible_test_examples=min_eligible_test_examples,
        min_changed_predictions=min_changed_predictions,
        interpretation_policy_id=derive_interpretation_policy_id(probe))


class BenchmarkInterpretation(StrictModel):
    """The deterministic, policy-governed wording of one paired result."""

    schema_version: Literal[1] = 1
    policy: BenchmarkInterpretationPolicy
    corpus_provenance: CorpusProvenance
    eligible_test_examples: int = Field(ge=0)
    conclusion: InterpretationConclusion
    engineering_proof_only: bool
    qualifiers: tuple[str, ...] = Field(default_factory=tuple)
    reasons: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _valid(self) -> BenchmarkInterpretation:
        if list(self.qualifiers) != sorted(set(self.qualifiers)):
            raise ValueError("qualifiers must be sorted and unique")
        return self


def interpret_paired_comparison(
    comparison: PairedComparison,
    *,
    policy: BenchmarkInterpretationPolicy,
    corpus_provenance: CorpusProvenance,
) -> BenchmarkInterpretation:
    """Deterministic wording from counts + policy + provenance. Nothing else.

    Conclusions use only the conclusion-eligible (non-train) counts — the
    training partition can never be evidence of generalization.
    """
    counts = comparison.counts_non_train
    eligible_test = comparison.aligned_partitions.test
    improved = counts.base_incorrect_trained_correct
    regressed = counts.base_correct_trained_incorrect
    changed = counts.predictions_differed

    qualifiers: set[str] = set()
    engineering_only = (
        corpus_provenance is CorpusProvenance.FIXTURE_GENERATED
        and policy.fixture_corpus_engineering_only)
    if engineering_only:
        qualifiers.add("fixture_generated_corpus_engineering_proof_only")
    if regressed > 0:
        qualifiers.add("regressions_present")
    if 0 < improved + regressed <= 1:
        qualifiers.add("anecdotal_single_example")

    reasons = (
        f"eligible_test_examples={eligible_test}",
        f"non_train_changed_predictions={changed}",
        f"non_train_improved={improved}",
        f"non_train_regressed={regressed}",
        f"non_train_total={counts.total}",
    )

    if not comparison.fairness.fair:
        conclusion = InterpretationConclusion.CONFOUNDED
        qualifiers.add("confounded_fields=" + ",".join(
            comparison.fairness.confounded_fields) if
            comparison.fairness.confounded_fields else "fairness_checks_failed")
    elif changed == 0:
        conclusion = InterpretationConclusion.NO_OBSERVED_EFFECT
    elif (eligible_test < policy.min_eligible_test_examples
          or changed < policy.min_changed_predictions):
        conclusion = InterpretationConclusion.INCONCLUSIVE_UNDERPOWERED
        qualifiers.add(
            "engineering proof only — insufficient evidence for "
            "model-quality conclusions")
    elif improved > 0 and regressed == 0:
        conclusion = InterpretationConclusion.BETTER_ON_THIS_CORPUS
    elif regressed > 0 and improved == 0:
        conclusion = InterpretationConclusion.WORSE_ON_THIS_CORPUS
    elif improved > 0 and regressed > 0:
        conclusion = InterpretationConclusion.MIXED_ON_THIS_CORPUS
    else:
        conclusion = InterpretationConclusion.UNCHANGED_ON_THIS_CORPUS

    return BenchmarkInterpretation(
        policy=policy, corpus_provenance=corpus_provenance,
        eligible_test_examples=eligible_test, conclusion=conclusion,
        engineering_proof_only=engineering_only,
        qualifiers=tuple(sorted(qualifiers)), reasons=reasons)


# ---------------------------------------------------------------------------
# Immutable comparison store: comparisons/<comparison_id>/
# ---------------------------------------------------------------------------


class ComparisonSummaryFile(StrictModel):
    schema_version: Literal[1] = 1
    comparison: PairedComparison
    interpretation: BenchmarkInterpretation


def compute_comparison_digest(
    *,
    schema_version: int,
    comparison_format_version: int,
    comparison_id: str,
    task_id: str,
    prepared_digest: str,
    base_evaluation_id: str,
    trained_evaluation_id: str,
    generated_by: str,
    files: tuple[DatasetFileHash, ...],
) -> str:
    payload = {
        "schema_version": schema_version,
        "comparison_format_version": comparison_format_version,
        "comparison_id": comparison_id,
        "task_id": task_id,
        "prepared_digest": prepared_digest,
        "base_evaluation_id": base_evaluation_id,
        "trained_evaluation_id": trained_evaluation_id,
        "generated_by": generated_by,
        "files": [
            {"relative_path": f.relative_path, "sha256": f.sha256,
             "size": f.size}
            for f in sorted(files, key=lambda f: f.relative_path)
        ],
    }
    return "cmpdig-" + sha256_canonical(payload)[:24]


class ComparisonManifest(StrictModel):
    schema_version: Literal[1] = 1
    comparison_format_version: Literal[1] = 1
    comparison_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    prepared_digest: str = Field(min_length=1)
    base_baseline_id: str = Field(min_length=1)
    trained_baseline_id: str = Field(min_length=1)
    base_evaluation_id: str = Field(min_length=1)
    trained_evaluation_id: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)
    files: tuple[DatasetFileHash, ...] = Field(min_length=1)
    comparison_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ComparisonManifest:
        paths = [f.relative_path for f in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("manifest files must be path-sorted and unique")
        expected = compute_comparison_digest(
            schema_version=self.schema_version,
            comparison_format_version=self.comparison_format_version,
            comparison_id=self.comparison_id, task_id=self.task_id,
            prepared_digest=self.prepared_digest,
            base_evaluation_id=self.base_evaluation_id,
            trained_evaluation_id=self.trained_evaluation_id,
            generated_by=self.generated_by, files=self.files)
        if self.comparison_digest != expected:
            raise ValueError("comparison_digest does not match the content")
        return self


@dataclass(frozen=True)
class WrittenComparison:
    root: Path
    comparison_id: str
    comparison_digest: str
    disagreement_count: int


def _disagreements_bytes(records: tuple[DisagreementRecord, ...]) -> bytes:
    return b"".join(canonical_json_bytes(r) + b"\n" for r in records)


def write_comparison(
    result: ComparisonResult,
    interpretation: BenchmarkInterpretation,
    comparisons_root: str | Path,
) -> WrittenComparison:
    """Write ``comparisons/<comparison_id>/`` atomically; never overwrite."""
    comparison = result.comparison
    if len(result.disagreements) != comparison.counts_all.predictions_differed:
        raise ComparisonError(
            "disagreement count does not match predictions_differed")
    ids = [d.example_id for d in result.disagreements]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ComparisonError("disagreements must be example-id-sorted, unique")
    summary_payload = canonical_json_bytes(ComparisonSummaryFile(
        comparison=comparison, interpretation=interpretation))
    disagreements_payload = _disagreements_bytes(result.disagreements)
    content = {SUMMARY_FILE: summary_payload,
               DISAGREEMENTS_FILE: disagreements_payload}
    files = tuple(sorted(
        (DatasetFileHash(relative_path=name, sha256=sha256_bytes(payload),
                         size=len(payload))
         for name, payload in content.items()),
        key=lambda f: f.relative_path))
    manifest = ComparisonManifest(
        comparison_id=comparison.comparison_id, task_id=comparison.task_id,
        prepared_digest=comparison.prepared_digest,
        base_baseline_id=comparison.base_baseline_id,
        trained_baseline_id=comparison.trained_baseline_id,
        base_evaluation_id=comparison.base_evaluation_id,
        trained_evaluation_id=comparison.trained_evaluation_id,
        generated_by=COMPARISON_GENERATOR, files=files,
        comparison_digest=compute_comparison_digest(
            schema_version=1,
            comparison_format_version=COMPARISON_FORMAT_VERSION,
            comparison_id=comparison.comparison_id,
            task_id=comparison.task_id,
            prepared_digest=comparison.prepared_digest,
            base_evaluation_id=comparison.base_evaluation_id,
            trained_evaluation_id=comparison.trained_evaluation_id,
            generated_by=COMPARISON_GENERATOR, files=files))

    root = Path(comparisons_root) / comparison.comparison_id
    if root.exists() and any(root.iterdir()):
        raise ComparisonError(f"comparison already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / COMPARISON_INCOMPLETE_MARKER
    marker.write_bytes(b"incomplete\n")
    fsync_dir(root)
    for rel, payload in content.items():
        atomic_write_bytes(root / rel, payload)
    atomic_write_bytes(root / MANIFEST_FILE, canonical_json_bytes(manifest))
    verification = verify_comparison(root)
    hard = [c for c in verification.failures
            if c.rule != "incomplete_marker_absent"]
    if hard:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in hard)
        raise ComparisonError(f"post-write verification failed: {detail}")
    marker.unlink()
    fsync_dir(root)
    return WrittenComparison(
        root=root, comparison_id=comparison.comparison_id,
        comparison_digest=manifest.comparison_digest,
        disagreement_count=len(result.disagreements))


class ComparisonVerificationResult(StrictModel):
    schema_version: Literal[1] = 1
    verified: bool
    comparison_digest: str | None = None
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def _parse_disagreements(data: bytes) -> tuple[DisagreementRecord, ...]:
    lines = [line for line in data.split(b"\n") if line]
    return tuple(DisagreementRecord.model_validate_json(line) for line in lines)


def verify_comparison(comparison_dir: str | Path) -> ComparisonVerificationResult:
    """Verify artifact consistency; fail closed.

    This validates the PERSISTED artifact (hashes, digest, alignment between
    summary counts, disagreement records, and recomputed transitions and
    interpretation). It deliberately does NOT re-run any model: model-output
    replay is not claimed anywhere in VerifiedNet — the persisted structured
    predictions bound into the immutable evaluations are the evidence.
    """
    root = Path(comparison_dir)
    checks: list[DatasetCheck] = []
    if not root.is_dir():
        checks.append(_c("comparison_dir_present", False, str(root)))
        return ComparisonVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("comparison_dir_present", True))
    checks.append(_c("incomplete_marker_absent",
                     not (root / COMPARISON_INCOMPLETE_MARKER).exists()))
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        checks.append(_c("manifest_present", False))
        return ComparisonVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_present", True))
    try:
        manifest = ComparisonManifest.model_validate_json(
            manifest_path.read_bytes())
    except ValidationError as exc:
        checks.append(_c("manifest_parses", False, str(exc).splitlines()[0]))
        return ComparisonVerificationResult(verified=False, checks=tuple(checks))
    checks.append(_c("manifest_parses", True))
    digest = manifest.comparison_digest

    on_disk = {str(p.relative_to(root)) for p in root.rglob("*")
               if p.is_file() and p.name != COMPARISON_INCOMPLETE_MARKER}
    allowed = EXPECTED_COMPARISON_FILES | {MANIFEST_FILE}
    checks.append(_c("no_missing_files", not sorted(allowed - on_disk)))
    checks.append(_c("no_unexpected_files", not sorted(on_disk - allowed)))

    hash_ok, detail = True, ""
    for fh in manifest.files:
        path = root / fh.relative_path
        if not path.is_file():
            hash_ok, detail = False, f"missing {fh.relative_path}"
            break
        raw = path.read_bytes()
        if len(raw) != fh.size or sha256_bytes(raw) != fh.sha256:
            hash_ok, detail = False, f"mismatch for {fh.relative_path}"
            break
    checks.append(_c("file_hashes_match", hash_ok, detail))
    if not hash_ok:
        return ComparisonVerificationResult(
            verified=False, comparison_digest=digest, checks=tuple(checks))

    summary_ok, aligned_ok, interp_ok = True, True, True
    detail = ""
    try:
        summary = ComparisonSummaryFile.model_validate_json(
            (root / SUMMARY_FILE).read_bytes())
        disagreements = _parse_disagreements(
            (root / DISAGREEMENTS_FILE).read_bytes())
    except ValidationError as exc:
        summary_ok = False
        detail = str(exc).splitlines()[0]
        checks.append(_c("summary_parses", False, detail))
        return ComparisonVerificationResult(
            verified=False, comparison_digest=digest, checks=tuple(checks))
    checks.append(_c("summary_parses", True))
    comparison = summary.comparison
    checks.append(_c(
        "manifest_binds_summary",
        comparison.comparison_id == manifest.comparison_id
        and comparison.task_id == manifest.task_id
        and comparison.prepared_digest == manifest.prepared_digest
        and comparison.base_evaluation_id == manifest.base_evaluation_id
        and comparison.trained_evaluation_id
        == manifest.trained_evaluation_id))
    ids = [d.example_id for d in disagreements]
    aligned_ok = (
        ids == sorted(ids) and len(ids) == len(set(ids))
        and len(disagreements)
        == comparison.counts_all.predictions_differed)
    checks.append(_c("disagreements_align_with_counts", aligned_ok))
    interp = interpret_paired_comparison(
        comparison, policy=summary.interpretation.policy,
        corpus_provenance=summary.interpretation.corpus_provenance)
    interp_ok = interp == summary.interpretation
    checks.append(_c("interpretation_recomputes", interp_ok))

    return ComparisonVerificationResult(
        verified=all(c.passed for c in checks) and summary_ok,
        comparison_digest=digest, checks=tuple(checks))


@dataclass(frozen=True)
class LoadedComparison:
    manifest: ComparisonManifest
    comparison: PairedComparison
    disagreements: tuple[DisagreementRecord, ...]
    interpretation: BenchmarkInterpretation


def read_comparison(comparison_dir: str | Path) -> LoadedComparison:
    """Verify then reconstruct a comparison; fail closed."""
    root = Path(comparison_dir)
    result = verify_comparison(root)
    if not result.verified:
        detail = "; ".join(f"{c.rule}: {c.detail}" for c in result.failures)
        raise ComparisonError(f"comparison failed verification: {detail}")
    manifest = ComparisonManifest.model_validate_json(
        (root / MANIFEST_FILE).read_bytes())
    summary = ComparisonSummaryFile.model_validate_json(
        (root / SUMMARY_FILE).read_bytes())
    return LoadedComparison(
        manifest=manifest, comparison=summary.comparison,
        disagreements=_parse_disagreements(
            (root / DISAGREEMENTS_FILE).read_bytes()),
        interpretation=summary.interpretation)
