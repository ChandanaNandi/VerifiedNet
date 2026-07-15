"""Preregistered controlled-experiment specification (Gate 15).

The experiment specification is the scientific contract: the question, the
hypothesis, the frozen metrics, the frozen success policy, every identity the
experiment binds (corpus, model, tokenizer, training spec/plan, bounded
policies), and the hard one-run/one-checkpoint rule — all persisted BEFORE
any training executes and unmodifiable afterwards (content-addressed and
self-validating; the finalization writer refuses a byte-changed spec).

The MACHINE-SPECIFIC authorization (ADR-0026: execution authorization is
environmental evidence) and the execution policy that binds it cannot exist
at preregistration time; the spec therefore preregisters the execution
ENVELOPE (the exact ceilings the eventual execution policy must carry) and
the training-binding artifact records the realized ``rexecpol-``/
authorization ids, checked against the envelope fail-closed.

Phases advance strictly forward through the declared sequence — a backward
or skipped transition is unrepresentable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.evaluation.inference import DecodingConfig
from verifiednet.schemas.base import StrictModel

EXPERIMENT_CONTRACT_VERSION = 1


class ControlledExperimentError(VerifiedNetError):
    """A controlled-experiment artifact could not be built, written, or read."""


# ---------------------------------------------------------------------------
# Ordered experiment phases (no backward transition, no skips)
# ---------------------------------------------------------------------------


class ExperimentPhase(StrEnum):
    PREREGISTERED = "PREREGISTERED"
    TRAINING_CORPUS_FINALIZED = "TRAINING_CORPUS_FINALIZED"
    PLAN_AUTHORIZED = "PLAN_AUTHORIZED"
    TRAINING_COMPLETED = "TRAINING_COMPLETED"
    CHECKPOINT_VERIFIED = "CHECKPOINT_VERIFIED"
    TEST_EVALUATION_STARTED = "TEST_EVALUATION_STARTED"
    BENCHMARK_COMPLETED = "BENCHMARK_COMPLETED"
    RESULT_INTERPRETED = "RESULT_INTERPRETED"


#: The one legal order. Held-out truth becomes readable only at
#: ``TEST_EVALUATION_STARTED`` — strictly after ``CHECKPOINT_VERIFIED``.
EXPERIMENT_PHASE_SEQUENCE: tuple[ExperimentPhase, ...] = (
    ExperimentPhase.PREREGISTERED,
    ExperimentPhase.TRAINING_CORPUS_FINALIZED,
    ExperimentPhase.PLAN_AUTHORIZED,
    ExperimentPhase.TRAINING_COMPLETED,
    ExperimentPhase.CHECKPOINT_VERIFIED,
    ExperimentPhase.TEST_EVALUATION_STARTED,
    ExperimentPhase.BENCHMARK_COMPLETED,
    ExperimentPhase.RESULT_INTERPRETED,
)


class ExperimentPhaseLog(StrictModel):
    """The strictly-forward phase declaration. A log that is not an exact
    prefix of the canonical sequence is unrepresentable."""

    schema_version: Literal[1] = 1
    phases: tuple[ExperimentPhase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _forward_only(self) -> ExperimentPhaseLog:
        expected = EXPERIMENT_PHASE_SEQUENCE[:len(self.phases)]
        if self.phases != expected:
            raise ValueError(
                "phase log must be an exact prefix of the canonical "
                "experiment phase sequence (no backward transition, no skips)")
        return self

    @property
    def complete(self) -> bool:
        return self.phases == EXPERIMENT_PHASE_SEQUENCE


def start_phase_log() -> ExperimentPhaseLog:
    return ExperimentPhaseLog(phases=(ExperimentPhase.PREREGISTERED,))


def advance_phase(
    log: ExperimentPhaseLog, phase: ExperimentPhase,
) -> ExperimentPhaseLog:
    """Advance to exactly the next canonical phase; anything else refuses."""
    position = len(log.phases)
    if position >= len(EXPERIMENT_PHASE_SEQUENCE):
        raise ControlledExperimentError("the experiment is already complete")
    expected = EXPERIMENT_PHASE_SEQUENCE[position]
    if phase is not expected:
        raise ControlledExperimentError(
            f"illegal phase transition to {phase.value!r}: the next legal "
            f"phase is {expected.value!r}")
    return ExperimentPhaseLog(phases=(*log.phases, phase))


# ---------------------------------------------------------------------------
# Frozen success policy (the exact conditions for every outcome word)
# ---------------------------------------------------------------------------


class ExperimentSuccessPolicy(StrictModel):
    """The frozen conditions under which each outcome word may be used.

    ``improved`` requires ALL of: enough eligible test examples, an
    unconfounded comparison, strictly higher accepted test accuracy, more
    paired wins than losses, no increase in invalid predictions, and no
    abstention regression. Every requirement is Literal-locked — a weaker
    Gate 15 success policy is unrepresentable. Raw paired counts stay
    visible; rank alone can never satisfy anything here (there is no rank
    field to consult).
    """

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    min_eligible_test_examples: int = Field(ge=1)
    require_unconfounded_comparison: Literal[True] = True
    require_accepted_test_accuracy_increase: Literal[True] = True
    require_net_paired_wins: Literal[True] = True
    max_invalid_prediction_increase: Literal[0] = 0
    forbid_abstention_regression: Literal[True] = True
    success_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ExperimentSuccessPolicy:
        if self.success_policy_id != derive_success_policy_id(self):
            raise ValueError(
                "success_policy_id does not match the policy content")
        return self


def derive_success_policy_id(policy: ExperimentSuccessPolicy) -> str:
    payload = policy.model_dump(mode="json")
    payload.pop("success_policy_id", None)
    return "esucc-" + sha256_canonical(payload)[:16]


def build_success_policy(
    *, min_eligible_test_examples: int = 30,
) -> ExperimentSuccessPolicy:
    probe = ExperimentSuccessPolicy.model_construct(
        min_eligible_test_examples=min_eligible_test_examples)
    return ExperimentSuccessPolicy(
        min_eligible_test_examples=min_eligible_test_examples,
        success_policy_id=derive_success_policy_id(probe))


# ---------------------------------------------------------------------------
# Preregistered runtime envelope (the ceilings the execution policy must carry)
# ---------------------------------------------------------------------------


class ExperimentRuntimeEnvelope(StrictModel):
    """The preregistered hard ceilings for the ONE training run.

    Mirrors the Literal-locked Gate 10F ``RealTrainingExecutionPolicy``
    ceilings; the realized execution policy must carry EXACTLY these bounds
    (checked fail-closed before any model loads). Wall-clock time is
    deliberately absent — it is never part of an immutable identity.
    """

    schema_version: Literal[1] = 1
    max_examples: int = Field(ge=1, le=64)
    max_epochs: int = Field(ge=1, le=8)
    max_optimizer_steps: int = Field(ge=1, le=64)
    max_sequence_length: int = Field(ge=1, le=2048)
    max_effective_batch_size: int = Field(ge=1, le=8)
    max_training_runs: Literal[1] = 1
    max_treatment_checkpoints: Literal[1] = 1


# ---------------------------------------------------------------------------
# The preregistered experiment specification
# ---------------------------------------------------------------------------


class ControlledTrainingExperimentSpec(StrictModel):
    """The frozen, content-addressed preregistration of ONE experiment.

    Persisted before the training run exists; the finalization writer
    verifies the persisted bytes never changed. ``readiness_outcome`` is
    Literal-locked: an experiment specification against a corpus whose
    readiness assessment did not authorize a controlled experiment is
    unrepresentable (ADR-0032). ``maximum_training_runs`` and the envelope's
    checkpoint ceiling are Literal ``1`` — a second run or a second
    treatment checkpoint cannot be specified, only a NEW experiment in a
    later gate.
    """

    schema_version: Literal[1] = 1
    experiment_contract_version: Literal[1] = 1
    experiment_name: str = Field(min_length=1)
    experiment_version: int = Field(ge=1)
    scientific_question: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)

    evaluation_corpus_id: str = Field(min_length=1)
    evaluation_corpus_digest: str = Field(min_length=1)
    readiness_assessment_id: str = Field(min_length=1)
    readiness_outcome: Literal["ready_for_controlled_experiment"] = (
        "ready_for_controlled_experiment")
    source_prepared_digest: str = Field(min_length=1)

    training_corpus_policy_id: str = Field(min_length=1)
    training_corpus_id: str = Field(min_length=1)
    training_corpus_digest: str = Field(min_length=1)
    eligible_train_examples: int = Field(ge=1)
    training_example_cap: int = Field(ge=1)
    cap_algorithm: Literal["first-n-canonical-order-v1"] = (
        "first-n-canonical-order-v1")
    cap_rationale: str = Field(min_length=1)

    model_approval_id: str = Field(min_length=1)
    model_artifact_id: str = Field(min_length=1)
    tokenizer_artifact_id: str = Field(min_length=1)
    model_identifier: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)

    training_spec_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    training_plan_digest: str = Field(min_length=1)
    bounded_model_policy_id: str = Field(min_length=1)
    objective_policy_id: str = Field(min_length=1)
    runtime_envelope: ExperimentRuntimeEnvelope

    prompt_template_id: str = Field(min_length=1)
    decoding: DecodingConfig
    normalization_policy_id: str = Field(min_length=1)
    scoring_policy_version: int = Field(ge=1)
    interpretation_policy_id: str = Field(min_length=1)

    primary_metrics: tuple[str, ...] = Field(min_length=1)
    secondary_metrics: tuple[str, ...] = Field(min_length=1)
    success_criteria: tuple[str, ...] = Field(min_length=1)
    failure_criteria: tuple[str, ...] = Field(min_length=1)
    success_policy: ExperimentSuccessPolicy

    maximum_training_runs: Literal[1] = 1
    experiment_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ControlledTrainingExperimentSpec:
        if self.training_example_cap > self.eligible_train_examples:
            raise ValueError(
                "training_example_cap cannot exceed the eligible train "
                "examples")
        if self.training_example_cap > self.runtime_envelope.max_examples:
            raise ValueError(
                "training_example_cap exceeds the preregistered runtime "
                "envelope")
        for name in ("primary_metrics", "secondary_metrics",
                     "success_criteria", "failure_criteria"):
            values = getattr(self, name)
            if list(values) != sorted(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        if self.experiment_id != derive_experiment_id(self):
            raise ValueError("experiment_id does not match the specification")
        return self


def derive_experiment_id(spec: ControlledTrainingExperimentSpec) -> str:
    payload = spec.model_dump(mode="json")
    payload.pop("experiment_id", None)
    return "exp-" + sha256_canonical(payload)[:16]


def build_experiment_spec(
    *,
    experiment_name: str,
    experiment_version: int,
    scientific_question: str,
    hypothesis: str,
    evaluation_corpus_id: str,
    evaluation_corpus_digest: str,
    readiness_assessment_id: str,
    source_prepared_digest: str,
    training_corpus_policy_id: str,
    training_corpus_id: str,
    training_corpus_digest: str,
    eligible_train_examples: int,
    training_example_cap: int,
    cap_rationale: str,
    model_approval_id: str,
    model_artifact_id: str,
    tokenizer_artifact_id: str,
    model_identifier: str,
    model_revision: str,
    tokenizer_revision: str,
    training_spec_id: str,
    training_plan_id: str,
    training_plan_digest: str,
    bounded_model_policy_id: str,
    objective_policy_id: str,
    runtime_envelope: ExperimentRuntimeEnvelope,
    prompt_template_id: str,
    decoding: DecodingConfig,
    normalization_policy_id: str,
    scoring_policy_version: int,
    interpretation_policy_id: str,
    success_policy: ExperimentSuccessPolicy,
    primary_metrics: tuple[str, ...] = (),
    secondary_metrics: tuple[str, ...] = (),
    success_criteria: tuple[str, ...] = (),
    failure_criteria: tuple[str, ...] = (),
) -> ControlledTrainingExperimentSpec:
    """Assemble the frozen specification (defaults: the Gate 15 metric sets)."""
    fields: dict[str, object] = {
        "experiment_name": experiment_name,
        "experiment_version": experiment_version,
        "scientific_question": scientific_question,
        "hypothesis": hypothesis,
        "evaluation_corpus_id": evaluation_corpus_id,
        "evaluation_corpus_digest": evaluation_corpus_digest,
        "readiness_assessment_id": readiness_assessment_id,
        "source_prepared_digest": source_prepared_digest,
        "training_corpus_policy_id": training_corpus_policy_id,
        "training_corpus_id": training_corpus_id,
        "training_corpus_digest": training_corpus_digest,
        "eligible_train_examples": eligible_train_examples,
        "training_example_cap": training_example_cap,
        "cap_rationale": cap_rationale,
        "model_approval_id": model_approval_id,
        "model_artifact_id": model_artifact_id,
        "tokenizer_artifact_id": tokenizer_artifact_id,
        "model_identifier": model_identifier,
        "model_revision": model_revision,
        "tokenizer_revision": tokenizer_revision,
        "training_spec_id": training_spec_id,
        "training_plan_id": training_plan_id,
        "training_plan_digest": training_plan_digest,
        "bounded_model_policy_id": bounded_model_policy_id,
        "objective_policy_id": objective_policy_id,
        "runtime_envelope": runtime_envelope,
        "prompt_template_id": prompt_template_id,
        "decoding": decoding,
        "normalization_policy_id": normalization_policy_id,
        "scoring_policy_version": scoring_policy_version,
        "interpretation_policy_id": interpretation_policy_id,
        "primary_metrics": primary_metrics or GATE15_PRIMARY_METRICS,
        "secondary_metrics": secondary_metrics or GATE15_SECONDARY_METRICS,
        "success_criteria": success_criteria or GATE15_SUCCESS_CRITERIA,
        "failure_criteria": failure_criteria or GATE15_FAILURE_CRITERIA,
        "success_policy": success_policy,
    }
    probe = ControlledTrainingExperimentSpec.model_construct(**fields)  # type: ignore[arg-type]
    return ControlledTrainingExperimentSpec(
        **fields,  # type: ignore[arg-type]
        experiment_id=derive_experiment_id(probe))


#: The frozen Gate 15 metric names (sorted; identity-bearing via the spec).
GATE15_PRIMARY_METRICS: tuple[str, ...] = (
    "base_correct_trained_wrong_test_count",
    "base_wrong_trained_correct_test_count",
    "invalid_prediction_count",
    "test_accepted_exact_match_accuracy",
    "valid_structured_prediction_rate",
)
GATE15_SECONDARY_METRICS: tuple[str, ...] = (
    "abstention_accuracy",
    "confusion_counts",
    "parser_failure_categories",
    "per_family_accepted_accuracy",
    "prediction_change_count",
    "validation_accepted_accuracy",
)
GATE15_SUCCESS_CRITERIA: tuple[str, ...] = (
    "comparison_unconfounded",
    "eligible_test_examples>=30",
    "every_bound_artifact_verifies",
    "trained_abstention_accuracy_not_lower_than_base",
    "trained_invalid_prediction_count_not_greater_than_base",
    "trained_net_paired_wins_positive",
    "trained_test_accepted_accuracy_greater_than_base",
)
GATE15_FAILURE_CRITERIA: tuple[str, ...] = (
    "abstention_performance_decreases=>regressed_or_mixed",
    "accepted_test_accuracy_decreases=>regressed_or_mixed",
    "infrastructure_failure=>experiment_failed_never_model_quality",
    "invalid_predictions_increase=>regressed_or_mixed",
)
