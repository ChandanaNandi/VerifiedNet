"""Gate 15 analysis: capped train-only corpus, paired test evidence,
outcome classification, and the test-set firewall audit.

Everything here is pure and deterministic. The outcome classifier consumes
ONLY raw paired counts and matched aggregate counts under the frozen success
policy — there is no parameter through which a benchmark rank, a training
loss, or a validation-only improvement could establish ``improved``.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.models import (
    DatasetExampleKind,
    DatasetPartition,
)
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation.comparison import PairedComparisonCounts
from verifiednet.evaluation.engine import EvaluationRun
from verifiednet.evaluation.prediction import (
    AbstentionPrediction,
    DiagnosisPrediction,
)
from verifiednet.evaluation.scoring import EvaluationRecord, OutcomeCategory
from verifiednet.evaluation.structured import compute_parser_statistics
from verifiednet.experiment.spec import (
    ControlledExperimentError,
    ExperimentSuccessPolicy,
)
from verifiednet.schemas.base import StrictModel
from verifiednet.training.corpus import (
    SupervisedTrainingExample,
    TrainingCorpus,
    derive_training_corpus_id,
)

ExperimentOutcome = Literal[
    "improved", "regressed", "unchanged", "mixed", "inconclusive",
    "experiment_failed",
]

EXPERIMENT_OUTCOMES: tuple[str, ...] = (
    "improved", "regressed", "unchanged", "mixed", "inconclusive",
    "experiment_failed")


def _c(rule: str, passed: bool, detail: str = "") -> DatasetCheck:
    return DatasetCheck(rule=rule, passed=passed, detail=detail)


# ---------------------------------------------------------------------------
# Preregistered deterministic training-corpus cap
# ---------------------------------------------------------------------------


def cap_training_corpus(
    corpus: TrainingCorpus, *, max_example_count: int,
) -> TrainingCorpus:
    """First-N in canonical corpus order — the ONE approved cap algorithm.

    The Gate 15 experiment trains within the Literal-locked Gate 10F safety
    envelope (max 64 examples / 64 optimizer steps per real execution
    policy), so the preregistered experiment corpus is the deterministic
    first-N prefix of the FULL eligible train-only corpus in its canonical
    (source-example-id) order. Never random, never family-balanced by hand,
    never informed by any model output. A cap covering the whole corpus
    returns an identical corpus.
    """
    if max_example_count < 1:
        raise ControlledExperimentError("the cap must select at least one "
                                        "example")
    selected: tuple[SupervisedTrainingExample, ...] = \
        corpus.examples[:max_example_count]
    if not selected:
        raise ControlledExperimentError("the capped corpus selected zero "
                                        "examples")
    capped_id = derive_training_corpus_id(
        task_id=corpus.task_id,
        training_data_policy_id=corpus.policy.training_data_policy_id,
        input_template_id=corpus.input_template.input_template_id,
        target_template_id=corpus.target_template.target_template_id,
        training_example_ids=tuple(
            e.training_example_id for e in selected))
    return TrainingCorpus(
        training_corpus_id=capped_id, task_id=corpus.task_id,
        policy=corpus.policy, input_template=corpus.input_template,
        target_template=corpus.target_template,
        source_prepared_digest=corpus.source_prepared_digest,
        source_dataset_digest=corpus.source_dataset_digest,
        feature_policy_id=corpus.feature_policy_id,
        label_policy_id=corpus.label_policy_id, examples=selected)


def corpus_distributions(
    corpus: TrainingCorpus, prepared: LoadedPrepared,
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...], int]:
    """(family distribution, topology distribution, distinct group count)
    of a training corpus, resolved against its source prepared corpus."""
    by_example = {e.trace.example_id: e for e in prepared.examples}
    families: Counter[str] = Counter()
    topologies: Counter[str] = Counter()
    groups: set[str] = set()
    for example in corpus.examples:
        source = by_example.get(example.trace.source_example_id)
        if source is None:
            raise ControlledExperimentError(
                "training example is not present in the source prepared "
                f"corpus: {example.trace.source_example_id}")
        from verifiednet.datasets.features import AcceptedLabels

        if not isinstance(source.labels, AcceptedLabels):
            raise ControlledExperimentError(
                "training example resolves to a non-accepted source")
        families[source.labels.fault_family] += 1
        topologies[source.features.topology_hash] += 1
        groups.add(source.trace.group_id)
    return (tuple(sorted(families.items())),
            tuple(sorted(topologies.items())), len(groups))


# ---------------------------------------------------------------------------
# Paired evidence (test-only and per-family), aligned by example id
# ---------------------------------------------------------------------------


class FamilyPairedCounts(StrictModel):
    """Paired quadrant counts for ONE fault family on ONE partition."""

    schema_version: Literal[1] = 1
    fault_family: str = Field(min_length=1)
    counts: PairedComparisonCounts


def _prediction_content(record: EvaluationRecord) -> tuple[str, str]:
    prediction = record.prediction
    if isinstance(prediction, DiagnosisPrediction):
        return ("diagnosis", prediction.fault_family)
    if isinstance(prediction, AbstentionPrediction):
        return ("abstain", "")
    return ("invalid", prediction.reason_code)


def _aligned_records(
    base_run: EvaluationRun, trained_run: EvaluationRun,
) -> tuple[tuple[EvaluationRecord, EvaluationRecord], ...]:
    if base_run.prepared_digest != trained_run.prepared_digest:
        raise ControlledExperimentError(
            "paired analysis requires the SAME prepared corpus")
    base_ids = tuple(r.example_id for r in base_run.records)
    trained_ids = tuple(r.example_id for r in trained_run.records)
    if base_ids != trained_ids:
        raise ControlledExperimentError(
            "paired analysis requires identical example alignment")
    pairs = tuple(zip(base_run.records, trained_run.records, strict=True))
    for base, trained in pairs:
        if base.authoritative_target != trained.authoritative_target:
            raise ControlledExperimentError(
                "paired records disagree on the authoritative target")
    return pairs


def _counts_for(
    pairs: tuple[tuple[EvaluationRecord, EvaluationRecord], ...],
) -> PairedComparisonCounts:
    both_correct = both_incorrect = base_only = trained_only = 0
    identical = differed = base_invalid = trained_invalid = 0
    abstention_changes = 0
    for base, trained in pairs:
        if base.correct and trained.correct:
            both_correct += 1
        elif base.correct and not trained.correct:
            base_only += 1
        elif trained.correct and not base.correct:
            trained_only += 1
        else:
            both_incorrect += 1
        if _prediction_content(base) == _prediction_content(trained):
            identical += 1
        else:
            differed += 1
        if base.outcome_category is OutcomeCategory.INVALID_PREDICTION:
            base_invalid += 1
        if trained.outcome_category is OutcomeCategory.INVALID_PREDICTION:
            trained_invalid += 1
        if isinstance(base.prediction, AbstentionPrediction) != isinstance(
                trained.prediction, AbstentionPrediction):
            abstention_changes += 1
    return PairedComparisonCounts(
        total=len(pairs), both_correct=both_correct,
        both_incorrect=both_incorrect,
        base_correct_trained_incorrect=base_only,
        base_incorrect_trained_correct=trained_only,
        predictions_identical=identical, predictions_differed=differed,
        base_invalid=base_invalid, trained_invalid=trained_invalid,
        abstention_decision_changes=abstention_changes)


def compute_partition_paired_counts(
    base_run: EvaluationRun, trained_run: EvaluationRun,
    *, partitions: tuple[DatasetPartition, ...] | None,
) -> PairedComparisonCounts:
    """Exact paired quadrant counts restricted to the given partitions
    (``None`` covers every aligned example)."""
    pairs = tuple(
        (base, trained)
        for base, trained in _aligned_records(base_run, trained_run)
        if partitions is None or base.partition in partitions)
    return _counts_for(pairs)


def compute_family_paired_counts(
    base_run: EvaluationRun, trained_run: EvaluationRun,
    *, partition: DatasetPartition,
) -> tuple[FamilyPairedCounts, ...]:
    """Per-fault-family paired counts on ONE partition (accepted only)."""
    by_family: dict[str, list[tuple[EvaluationRecord, EvaluationRecord]]] = {}
    for base, trained in _aligned_records(base_run, trained_run):
        if base.partition is not partition:
            continue
        if base.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        by_family.setdefault(base.authoritative_target, []).append(
            (base, trained))
    return tuple(
        FamilyPairedCounts(fault_family=family,
                           counts=_counts_for(tuple(pairs)))
        for family, pairs in sorted(by_family.items()))


# ---------------------------------------------------------------------------
# Primary metrics + the frozen outcome classification
# ---------------------------------------------------------------------------


class ExperimentPrimaryMetrics(StrictModel):
    """The raw counts the outcome classification consumes — nothing else.

    Matched denominators are validator-enforced: base and trained evaluated
    the exact same test and abstention example sets. There is NO field for a
    benchmark rank, a training loss, or a train/validation accuracy — those
    inputs cannot reach the classification.
    """

    schema_version: Literal[1] = 1
    eligible_test_examples: int = Field(ge=0)
    base_test_correct: int = Field(ge=0)
    trained_test_correct: int = Field(ge=0)
    test_evaluated: int = Field(ge=0)
    test_base_incorrect_trained_correct: int = Field(ge=0)
    test_base_correct_trained_incorrect: int = Field(ge=0)
    test_predictions_differed: int = Field(ge=0)
    base_invalid_predictions: int = Field(ge=0)
    trained_invalid_predictions: int = Field(ge=0)
    abstention_count: int = Field(ge=0)
    base_abstention_correct: int = Field(ge=0)
    trained_abstention_correct: int = Field(ge=0)
    comparison_unconfounded: bool

    @model_validator(mode="after")
    def _valid(self) -> ExperimentPrimaryMetrics:
        for name in ("base_test_correct", "trained_test_correct"):
            if getattr(self, name) > self.test_evaluated:
                raise ValueError(f"{name} exceeds the evaluated test count")
        for name in ("base_abstention_correct", "trained_abstention_correct"):
            if getattr(self, name) > self.abstention_count:
                raise ValueError(f"{name} exceeds the abstention count")
        if self.eligible_test_examples != self.test_evaluated:
            raise ValueError(
                "eligible test examples must equal the evaluated test count "
                "(the matched design evaluates every eligible example)")
        return self


def extract_primary_metrics(
    base_run: EvaluationRun, trained_run: EvaluationRun,
    *, comparison_unconfounded: bool,
) -> ExperimentPrimaryMetrics:
    """Pure extraction from the two persisted evaluation runs."""
    test_counts = compute_partition_paired_counts(
        base_run, trained_run, partitions=(DatasetPartition.TEST,))

    def _test_correct(run: EvaluationRun) -> tuple[int, int]:
        for metrics in run.metrics.accepted_partitions:
            if metrics.partition is DatasetPartition.TEST:
                return metrics.correct, metrics.evaluated
        return 0, 0

    base_correct, base_evaluated = _test_correct(base_run)
    trained_correct, trained_evaluated = _test_correct(trained_run)
    if base_evaluated != trained_evaluated:
        raise ControlledExperimentError(
            "base and trained evaluated different test example counts")
    base_statistics = compute_parser_statistics(base_run)
    trained_statistics = compute_parser_statistics(trained_run)
    if base_run.metrics.abstention.count != \
            trained_run.metrics.abstention.count:
        raise ControlledExperimentError(
            "base and trained evaluated different abstention counts")
    return ExperimentPrimaryMetrics(
        eligible_test_examples=base_evaluated,
        base_test_correct=base_correct,
        trained_test_correct=trained_correct,
        test_evaluated=base_evaluated,
        test_base_incorrect_trained_correct=(
            test_counts.base_incorrect_trained_correct),
        test_base_correct_trained_incorrect=(
            test_counts.base_correct_trained_incorrect),
        test_predictions_differed=test_counts.predictions_differed,
        base_invalid_predictions=base_statistics.invalid_predictions,
        trained_invalid_predictions=trained_statistics.invalid_predictions,
        abstention_count=base_run.metrics.abstention.count,
        base_abstention_correct=base_run.metrics.abstention.correct,
        trained_abstention_correct=trained_run.metrics.abstention.correct,
        comparison_unconfounded=comparison_unconfounded)


def success_policy_checks(
    metrics: ExperimentPrimaryMetrics, policy: ExperimentSuccessPolicy,
) -> tuple[DatasetCheck, ...]:
    """The frozen ``improved`` criteria, each as a visible check."""
    return (
        _c("min_eligible_test_examples",
           metrics.eligible_test_examples
           >= policy.min_eligible_test_examples,
           str(metrics.eligible_test_examples)),
        _c("comparison_unconfounded", metrics.comparison_unconfounded),
        _c("accepted_test_accuracy_increased",
           metrics.trained_test_correct > metrics.base_test_correct,
           f"trained={metrics.trained_test_correct}/"
           f"{metrics.test_evaluated} "
           f"base={metrics.base_test_correct}/{metrics.test_evaluated}"),
        _c("net_paired_wins_positive",
           metrics.test_base_incorrect_trained_correct
           > metrics.test_base_correct_trained_incorrect,
           f"wins={metrics.test_base_incorrect_trained_correct} "
           f"losses={metrics.test_base_correct_trained_incorrect}"),
        _c("invalid_predictions_not_increased",
           metrics.trained_invalid_predictions
           <= metrics.base_invalid_predictions
           + policy.max_invalid_prediction_increase,
           f"trained={metrics.trained_invalid_predictions} "
           f"base={metrics.base_invalid_predictions}"),
        _c("abstention_accuracy_not_reduced",
           metrics.trained_abstention_correct
           >= metrics.base_abstention_correct,
           f"trained={metrics.trained_abstention_correct}/"
           f"{metrics.abstention_count} "
           f"base={metrics.base_abstention_correct}/"
           f"{metrics.abstention_count}"),
    )


def classify_experiment_outcome(
    metrics: ExperimentPrimaryMetrics, policy: ExperimentSuccessPolicy,
) -> tuple[ExperimentOutcome, tuple[str, ...]]:
    """The deterministic, total outcome rule (fixed precedence).

    confounded -> experiment_failed (an attribution failure is an experiment
    failure, never a model-quality verdict); underpowered -> inconclusive;
    every frozen criterion met -> improved; any degradation together with any
    improvement (or improvement short of the criteria) -> mixed; degradation
    alone -> regressed; nothing moved -> unchanged.
    """
    reasons = [
        f"eligible_test_examples={metrics.eligible_test_examples}",
        f"test_accuracy_base={metrics.base_test_correct}/"
        f"{metrics.test_evaluated}",
        f"test_accuracy_trained={metrics.trained_test_correct}/"
        f"{metrics.test_evaluated}",
        f"paired_wins={metrics.test_base_incorrect_trained_correct}",
        f"paired_losses={metrics.test_base_correct_trained_incorrect}",
        f"invalid_base={metrics.base_invalid_predictions}",
        f"invalid_trained={metrics.trained_invalid_predictions}",
        f"abstention_base={metrics.base_abstention_correct}/"
        f"{metrics.abstention_count}",
        f"abstention_trained={metrics.trained_abstention_correct}/"
        f"{metrics.abstention_count}",
    ]
    if not metrics.comparison_unconfounded:
        reasons.append("confounded_comparison")
        return "experiment_failed", tuple(reasons)
    if metrics.eligible_test_examples < policy.min_eligible_test_examples:
        reasons.append(
            f"underpowered<{policy.min_eligible_test_examples}")
        return "inconclusive", tuple(reasons)

    accuracy_up = metrics.trained_test_correct > metrics.base_test_correct
    accuracy_down = metrics.trained_test_correct < metrics.base_test_correct
    invalid_up = metrics.trained_invalid_predictions \
        > metrics.base_invalid_predictions \
        + policy.max_invalid_prediction_increase
    invalid_down = metrics.trained_invalid_predictions \
        < metrics.base_invalid_predictions
    abstention_up = metrics.trained_abstention_correct \
        > metrics.base_abstention_correct
    abstention_down = metrics.trained_abstention_correct \
        < metrics.base_abstention_correct
    net_wins = metrics.test_base_incorrect_trained_correct \
        > metrics.test_base_correct_trained_incorrect
    net_losses = metrics.test_base_incorrect_trained_correct \
        < metrics.test_base_correct_trained_incorrect

    improved = (accuracy_up and net_wins and not invalid_up
                and not abstention_down)
    degradation = accuracy_down or invalid_up or abstention_down or net_losses
    improvement = accuracy_up or invalid_down or abstention_up or net_wins

    if improved:
        return "improved", tuple(reasons)
    if degradation and improvement:
        reasons.append("some_metrics_improved_others_regressed")
        return "mixed", tuple(reasons)
    if degradation:
        return "regressed", tuple(reasons)
    if improvement:
        reasons.append("improvement_short_of_the_frozen_criteria")
        return "mixed", tuple(reasons)
    return "unchanged", tuple(reasons)


# ---------------------------------------------------------------------------
# Test-set firewall audit
# ---------------------------------------------------------------------------


class TestFirewallAudit(StrictModel):
    """Structural proof that held-out truth never reached the training side.

    Scans the ACTUAL serialized training-side artifacts for every held-out
    (validation/test/abstention) example id, group id, and run id from the
    source prepared corpus. Content-addressed identifiers are unforgeable
    substrings: their absence is evidence, their presence is a hard failure.
    """

    schema_version: Literal[1] = 1
    prepared_digest: str = Field(min_length=1)
    held_out_example_ids: int = Field(ge=0)
    scanned_payloads: tuple[str, ...] = Field(min_length=1)
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)
    passed: bool
    audit_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TestFirewallAudit:
        if self.passed != all(c.passed for c in self.checks):
            raise ValueError("passed flag inconsistent with the checks")
        if list(self.scanned_payloads) != sorted(set(self.scanned_payloads)):
            raise ValueError("scanned_payloads must be sorted and unique")
        if self.audit_id != derive_firewall_audit_id(self):
            raise ValueError("audit_id does not match the audit content")
        return self


def derive_firewall_audit_id(audit: TestFirewallAudit) -> str:
    payload = audit.model_dump(mode="json")
    payload.pop("audit_id", None)
    return "fwaudit-" + sha256_canonical(payload)[:16]


def audit_test_firewall(
    *,
    prepared: LoadedPrepared,
    training_corpus: TrainingCorpus,
    training_side_payloads: dict[str, bytes],
) -> TestFirewallAudit:
    """Fail-closed firewall audit over the actual training-side bytes.

    ``training_side_payloads`` maps a payload name (e.g. ``training_plan``,
    ``authorization``, ``execution``, ``checkpoint_manifest``) to its exact
    serialized bytes.
    """
    if not training_side_payloads:
        raise ControlledExperimentError(
            "the firewall audit requires at least one training-side payload")
    train_accepted_ids: set[str] = set()
    held_out_tokens: set[str] = set()
    held_out_example_ids = 0
    for example in prepared.examples:
        is_train_accepted = (
            example.trace.partition is DatasetPartition.TRAIN
            and example.trace.example_kind
            is DatasetExampleKind.ACCEPTED_FAULT)
        if is_train_accepted:
            train_accepted_ids.add(example.trace.example_id)
            continue
        held_out_example_ids += 1
        held_out_tokens.update((example.trace.example_id,
                                example.trace.group_id,
                                example.trace.run_id))

    checks: list[DatasetCheck] = []
    checks.append(_c(
        "corpus_binds_this_prepared_corpus",
        training_corpus.source_prepared_digest
        == prepared.manifest.prepared_digest))
    sources = {e.trace.source_example_id for e in training_corpus.examples}
    outside = sorted(sources - train_accepted_ids)
    checks.append(_c(
        "training_sources_are_train_accepted_only", not outside,
        ",".join(outside[:5])))
    checks.append(_c(
        "training_traces_literal_train",
        all(e.trace.partition == "train"
            and e.trace.example_kind == "accepted_fault"
            for e in training_corpus.examples)))

    names = tuple(sorted(training_side_payloads))
    for name in names:
        text = training_side_payloads[name].decode("utf-8", errors="replace")
        leaked = sorted(t for t in held_out_tokens if t and t in text)
        checks.append(_c(
            f"no_held_out_identifier_in_{name}", not leaked,
            ",".join(leaked[:5])))

    passed = all(c.passed for c in checks)
    probe = TestFirewallAudit.model_construct(
        prepared_digest=prepared.manifest.prepared_digest,
        held_out_example_ids=held_out_example_ids,
        scanned_payloads=names, checks=tuple(checks), passed=passed)
    return TestFirewallAudit(
        prepared_digest=prepared.manifest.prepared_digest,
        held_out_example_ids=held_out_example_ids,
        scanned_payloads=names, checks=tuple(checks), passed=passed,
        audit_id=derive_firewall_audit_id(probe))
