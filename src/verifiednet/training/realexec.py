"""Real execution contracts: events, consistency, result, identity (Gate 10F).

A REAL backend is not replay-deterministic: exact losses, gradients, weight
deltas, and kernel behavior cannot be re-derived from a header, so Gate 10C's
event-skeleton replay guarantee deliberately does NOT apply here (ADR-0027).
Instead every piece of runtime evidence carries an explicit CONSISTENCY class:

* ``structurally_verified``   — recomputed by the verifier (bindings, ids,
                                ordering, monotone counts, final state);
* ``recomputable``            — re-derivable from verified inputs (planned
                                step arithmetic, slice membership);
* ``backend_reported``        — honest observations (losses, applied
                                deterministic settings) that are recorded,
                                validated for form (finite canonical
                                decimals), but never re-derived;
* ``non_recomputable``        — explicitly out of scope (kernel behavior,
                                per-step durations — not persisted at all).

Retries and resume are structurally unsupported (``retry_number:
Literal[0]``); cancellation is unsupported in this gate. A completed
execution MUST reference exactly one produced checkpoint; a failed execution
MUST NOT reference any.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel
from verifiednet.training.authstore import read_training_authorization
from verifiednet.training.bounds import (
    BoundedCorpusSlicePolicy,
    BoundedTrainingModelPolicy,
    RealTrainingExecutionPolicy,
)
from verifiednet.training.execution import ExecutionState
from verifiednet.training.planstore import read_training_plan
from verifiednet.training.resolve import (
    ResolvedModelArtifact,
    ResolvedTokenizerArtifact,
)


class RealExecutionError(VerifiedNetError):
    """A real training execution could not be built, bound, or validated."""

#: States a real execution may use. Resume/cancel are NOT in this set.
REAL_EXECUTION_STATES: frozenset[ExecutionState] = frozenset({
    ExecutionState.PLANNED, ExecutionState.VALIDATED, ExecutionState.STARTING,
    ExecutionState.RUNNING, ExecutionState.COMPLETED, ExecutionState.FAILED,
})
REAL_FINAL_STATES: frozenset[ExecutionState] = frozenset({
    ExecutionState.COMPLETED, ExecutionState.FAILED,
})


class ConsistencyClass(StrEnum):
    STRUCTURALLY_VERIFIED = "structurally_verified"
    RECOMPUTABLE = "recomputable"
    BACKEND_REPORTED = "backend_reported"
    NON_RECOMPUTABLE = "non_recomputable"


class RealFailureClass(StrEnum):
    AUTHORIZATION_INVALIDATED = "authorization_invalidated"
    MODEL_RESOLUTION_CHANGED = "model_resolution_changed"
    TOKENIZER_RESOLUTION_CHANGED = "tokenizer_resolution_changed"
    MODEL_LOAD_FAILED = "model_load_failed"
    TOKENIZER_LOAD_FAILED = "tokenizer_load_failed"
    CORPUS_LOAD_FAILED = "corpus_load_failed"
    TOKENIZATION_FAILED = "tokenization_failed"
    UNSUPPORTED_EXAMPLE_LENGTH = "unsupported_example_length"
    DEVICE_ALLOCATION_FAILED = "device_allocation_failed"
    OPTIMIZER_INIT_FAILED = "optimizer_init_failed"
    SCHEDULER_INIT_FAILED = "scheduler_init_failed"
    NON_FINITE_LOSS = "non_finite_loss"
    OPTIMIZER_STEP_FAILED = "optimizer_step_failed"
    CHECKPOINT_SERIALIZATION_FAILED = "checkpoint_serialization_failed"
    VERIFICATION_FAILED = "verification_failed"
    BOUNDS_EXCEEDED = "bounds_exceeded"


class RealExecutionEventType(StrEnum):
    AUTHORIZATION_ACCEPTED = "authorization_accepted"
    MODEL_ARTIFACT_VERIFIED = "model_artifact_verified"
    TOKENIZER_ARTIFACT_VERIFIED = "tokenizer_artifact_verified"
    CORPUS_SLICE_LOADED = "corpus_slice_loaded"
    TOKENIZATION_COMPLETED = "tokenization_completed"
    MODEL_LOADED = "model_loaded"
    TOKENIZER_LOADED = "tokenizer_loaded"
    OPTIMIZER_INITIALIZED = "optimizer_initialized"
    SCHEDULER_INITIALIZED = "scheduler_initialized"
    TRAINING_STARTED = "training_started"
    OPTIMIZER_STEP_COMPLETED = "optimizer_step_completed"
    EPOCH_COMPLETED = "epoch_completed"
    TRAINING_COMPLETED = "training_completed"
    EXECUTION_FAILED = "execution_failed"
    CHECKPOINT_PRODUCED = "checkpoint_produced"


def validate_finite_loss(value: str) -> str:
    """A recorded loss must be a finite canonical decimal string."""
    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"loss is not a decimal: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"loss is not finite: {value!r}")
    return value


class RealExecutionEvent(StrictModel):
    """One immutable real-execution event. No timestamps, no durations, no
    raw prompts/targets/gradients/tensors — ever."""

    schema_version: Literal[1] = 1
    execution_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    event_type: RealExecutionEventType
    state_before: ExecutionState
    state_after: ExecutionState
    completed_steps: int = Field(ge=0)
    epoch_index: int | None = Field(default=None, ge=0)
    batch_index: int | None = Field(default=None, ge=0)
    step_index: int | None = Field(default=None, ge=1)
    loss: str | None = None
    detail_code: str = ""
    consistency: ConsistencyClass
    prev_event_hash: str = Field(min_length=1)
    event_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealExecutionEvent:
        if self.state_before not in REAL_EXECUTION_STATES or (
                self.state_after not in REAL_EXECUTION_STATES):
            raise ValueError("real executions never use resume/cancel states")
        if self.loss is not None:
            validate_finite_loss(self.loss)
        if self.event_hash != derive_real_event_hash(self):
            raise ValueError("event_hash does not match the event content")
        return self


def derive_real_event_hash(event: RealExecutionEvent) -> str:
    payload = event.model_dump(mode="json")
    payload.pop("event_hash", None)
    return "revhash-" + sha256_canonical(payload)[:24]


def build_real_event(
    *, execution_id: str, sequence: int,
    event_type: RealExecutionEventType, state_before: ExecutionState,
    state_after: ExecutionState, completed_steps: int,
    prev_event_hash: str, epoch_index: int | None = None,
    batch_index: int | None = None, step_index: int | None = None,
    loss: str | None = None, detail_code: str = "",
    consistency: ConsistencyClass = ConsistencyClass.STRUCTURALLY_VERIFIED,
) -> RealExecutionEvent:
    probe = RealExecutionEvent.model_construct(
        execution_id=execution_id, sequence=sequence, event_type=event_type,
        state_before=state_before, state_after=state_after,
        completed_steps=completed_steps, epoch_index=epoch_index,
        batch_index=batch_index, step_index=step_index, loss=loss,
        detail_code=detail_code, consistency=consistency,
        prev_event_hash=prev_event_hash)
    return RealExecutionEvent(
        execution_id=execution_id, sequence=sequence, event_type=event_type,
        state_before=state_before, state_after=state_after,
        completed_steps=completed_steps, epoch_index=epoch_index,
        batch_index=batch_index, step_index=step_index, loss=loss,
        detail_code=detail_code, consistency=consistency,
        prev_event_hash=prev_event_hash,
        event_hash=derive_real_event_hash(probe))


def derive_real_execution_id(
    *, training_plan_id: str, plan_digest: str, authorization_id: str,
    authorization_digest: str, backend_spec_id: str, model_artifact_id: str,
    tokenizer_artifact_id: str, bounded_model_policy_id: str,
    corpus_slice_id: str, real_execution_policy_id: str,
) -> str:
    payload = {
        "training_plan_id": training_plan_id, "plan_digest": plan_digest,
        "authorization_id": authorization_id,
        "authorization_digest": authorization_digest,
        "backend_spec_id": backend_spec_id,
        "model_artifact_id": model_artifact_id,
        "tokenizer_artifact_id": tokenizer_artifact_id,
        "bounded_model_policy_id": bounded_model_policy_id,
        "corpus_slice_id": corpus_slice_id,
        "real_execution_policy_id": real_execution_policy_id,
        "retry_number": 0,
    }
    return "realexec-" + sha256_canonical(payload)[:24]


class RealTrainingExecutionResult(StrictModel):
    """Honest runtime evidence. It records what happened; it NEVER claims
    bit-identical repeatability, model quality, validation/test accuracy,
    benchmark movement, or generalization — those claims are structurally
    absent from this model."""

    schema_version: Literal[1] = 1
    final_state: ExecutionState
    completed_optimizer_steps: int = Field(ge=0)
    completed_epochs: int = Field(ge=0)
    observed_losses: tuple[str, ...] = Field(default_factory=tuple)
    applied_deterministic_settings: tuple[str, ...] = Field(
        default_factory=tuple)
    failure_class: RealFailureClass | None = None
    failure_detail: str = ""
    produced_checkpoint_id: str | None = None
    claims_replay_determinism: Literal[False] = False
    claims_model_quality: Literal[False] = False

    @model_validator(mode="after")
    def _valid(self) -> RealTrainingExecutionResult:
        if self.final_state not in REAL_FINAL_STATES:
            raise ValueError("real executions end only completed or failed")
        for loss in self.observed_losses:
            validate_finite_loss(loss)
        if self.final_state is ExecutionState.COMPLETED:
            if self.produced_checkpoint_id is None:
                raise ValueError("a completed execution must reference its "
                                 "one produced checkpoint")
            if self.failure_class is not None:
                raise ValueError("completed executions carry no failure class")
        else:
            if self.produced_checkpoint_id is not None:
                raise ValueError("a failed execution never publishes a "
                                 "checkpoint")
            if self.failure_class is None:
                raise ValueError("failed executions must classify the failure")
        return self


class RealTrainingExecution(StrictModel):
    """The complete real-execution record: bindings + events + result."""

    schema_version: Literal[1] = 1
    execution_format_version: Literal[1] = 1
    simulated: Literal[False] = False
    execution_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    authorization_digest: str = Field(min_length=1)
    backend_spec_id: str = Field(min_length=1)
    model_artifact_id: str = Field(min_length=1)
    tokenizer_artifact_id: str = Field(min_length=1)
    execution_policy: RealTrainingExecutionPolicy
    slice_policy: BoundedCorpusSlicePolicy
    retry_number: Literal[0] = 0
    planned_optimizer_steps: int = Field(ge=1)
    slice_expected_steps: int = Field(ge=1)
    events: tuple[RealExecutionEvent, ...] = Field(min_length=1)
    result: RealTrainingExecutionResult
    execution_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RealTrainingExecution:
        expected_id = derive_real_execution_id(
            training_plan_id=self.training_plan_id,
            plan_digest=self.plan_digest,
            authorization_id=self.authorization_id,
            authorization_digest=self.authorization_digest,
            backend_spec_id=self.backend_spec_id,
            model_artifact_id=self.model_artifact_id,
            tokenizer_artifact_id=self.tokenizer_artifact_id,
            bounded_model_policy_id=(
                self.execution_policy.bounded_model_policy_id),
            corpus_slice_id=self.slice_policy.corpus_slice_id,
            real_execution_policy_id=(
                self.execution_policy.real_execution_policy_id))
        if self.execution_id != expected_id:
            raise ValueError("execution_id does not match the bindings")
        if self.execution_policy.corpus_slice_id != (
                self.slice_policy.corpus_slice_id):
            raise ValueError("execution policy binds a different slice")
        if self.execution_policy.authorization_id != self.authorization_id:
            raise ValueError("execution policy binds a different authorization")
        prev = self.execution_id
        steps = 0
        for i, event in enumerate(self.events):
            if event.execution_id != self.execution_id:
                raise ValueError(f"event {i} belongs to another execution")
            if event.sequence != i:
                raise ValueError(f"event {i} has sequence {event.sequence}")
            if event.prev_event_hash != prev:
                raise ValueError(f"event {i} breaks the hash chain")
            prev = event.event_hash
            if event.completed_steps < steps:
                raise ValueError(f"event {i} regresses completed steps")
            steps = event.completed_steps
        if self.events[-1].state_after is not self.result.final_state:
            raise ValueError("final event state disagrees with the result")
        if steps != self.result.completed_optimizer_steps:
            raise ValueError("event steps disagree with the result")
        if steps > self.planned_optimizer_steps:
            raise ValueError("completed steps exceed the authorized plan")
        if steps > self.execution_policy.max_runtime_optimizer_steps:
            raise ValueError("completed steps exceed the execution policy")
        if (self.result.final_state is ExecutionState.COMPLETED
                and steps != self.slice_expected_steps):
            raise ValueError(
                "a completed execution must complete the slice-derived steps")
        if self.execution_digest != derive_real_execution_digest(self):
            raise ValueError("execution_digest does not match the execution")
        return self


def derive_real_execution_digest(execution: RealTrainingExecution) -> str:
    payload = {
        "schema_version": execution.schema_version,
        "execution_format_version": execution.execution_format_version,
        "execution_id": execution.execution_id,
        "training_plan_id": execution.training_plan_id,
        "plan_digest": execution.plan_digest,
        "authorization_id": execution.authorization_id,
        "authorization_digest": execution.authorization_digest,
        "backend_spec_id": execution.backend_spec_id,
        "model_artifact_id": execution.model_artifact_id,
        "tokenizer_artifact_id": execution.tokenizer_artifact_id,
        "execution_policy":
            execution.execution_policy.real_execution_policy_id,
        "corpus_slice_id": execution.slice_policy.corpus_slice_id,
        "planned_optimizer_steps": execution.planned_optimizer_steps,
        "slice_expected_steps": execution.slice_expected_steps,
        "final_state": execution.result.final_state.value,
        "event_hashes": [e.event_hash for e in execution.events],
        "result": execution.result.model_dump(mode="json"),
    }
    return "rexecdig-" + sha256_canonical(payload)[:24]


# ---------------------------------------------------------------------------
# Authorization revalidation (immediately before any model loading)
# ---------------------------------------------------------------------------


def revalidate_authorization(
    authorization_dir: str | Path,
    *,
    plan_dir: str | Path,
    model_artifact: ResolvedModelArtifact,
    tokenizer_artifact: ResolvedTokenizerArtifact,
    model_policy: BoundedTrainingModelPolicy,
    execution_policy: RealTrainingExecutionPolicy,
) -> tuple[bool, tuple[DatasetCheck, ...]]:
    """Revalidate stored authorization evidence against current reality.

    If ANY evidence changed, execution is refused and a NEW authorization is
    required — an authorization is never mutated or refreshed in place.
    """
    checks: list[DatasetCheck] = []

    def _c(rule: str, passed: bool, detail: str = "") -> None:
        checks.append(DatasetCheck(rule=rule, passed=passed, detail=detail))

    try:
        loaded = read_training_authorization(authorization_dir)
    except Exception as exc:
        _c("authorization_verifies", False, str(exc).splitlines()[0])
        return False, tuple(checks)
    _c("authorization_verifies", True)
    auth = loaded.authorization
    _c("authorized_true", auth.authorized)

    try:
        loaded_plan = read_training_plan(plan_dir)
    except Exception as exc:
        _c("plan_verifies", False, str(exc).splitlines()[0])
        return False, tuple(checks)
    _c("plan_verifies", True)
    plan = loaded_plan.plan
    spec = plan.request.spec

    _c("plan_binding_matches",
       auth.training_plan_id == plan.training_plan_id
       and auth.plan_digest == loaded_plan.manifest.plan_digest)
    _c("corpus_binding_matches",
       auth.training_corpus_id == spec.training_corpus_id
       and auth.training_corpus_digest == spec.training_corpus_digest)
    _c("backend_matches",
       execution_policy.approved_backend_id == auth.backend_spec_id
       or execution_policy.approved_backend_id
       == spec.trainer_implementation_id)
    _c("authorization_policy_binding",
       execution_policy.authorization_id == auth.authorization_id)
    _c("model_resolution_unchanged",
       auth.model_artifact is not None
       and auth.model_artifact.content_hash == model_artifact.content_hash
       and auth.model_artifact.resolved_model_artifact_id
       == model_artifact.resolved_model_artifact_id)
    _c("tokenizer_resolution_unchanged",
       auth.tokenizer_artifact is not None
       and auth.tokenizer_artifact.content_hash
       == tokenizer_artifact.content_hash
       and auth.tokenizer_artifact.resolved_tokenizer_artifact_id
       == tokenizer_artifact.resolved_tokenizer_artifact_id)
    _c("determinism_category_allowed",
       auth.determinism_category.value
       in execution_policy.determinism_acceptance)

    # bounded-model policy satisfaction
    params = model_artifact.declared_parameter_count
    _c("model_policy_family",
       spec.model.provider == model_policy.permitted_model_family
       and spec.model.model_identifier
       == model_policy.permitted_model_identifier
       and spec.model.model_revision
       == model_policy.permitted_model_revision
       and spec.model.model_class
       == model_policy.permitted_architecture_class
       and spec.tokenizer.tokenizer_revision
       == model_policy.permitted_tokenizer_revision)
    _c("model_policy_parameter_bound",
       params is not None
       and params <= model_policy.max_declared_parameter_count,
       f"params={params}")
    _c("model_policy_shape_bounds",
       spec.sequence_policy.max_total_tokens <= model_policy.max_sequence_length
       and plan.expected_example_count <= model_policy.max_example_count
       and (plan.expected_epochs or 1) <= model_policy.max_epochs
       and plan.optimizer_steps <= model_policy.max_optimizer_steps
       and spec.batch.effective_batch_size
       <= model_policy.max_effective_batch_size)
    _c("execution_policy_bounds",
       plan.optimizer_steps <= execution_policy.max_runtime_optimizer_steps
       and (plan.expected_epochs or 1) <= execution_policy.max_epochs
       and plan.expected_example_count <= execution_policy.max_examples
       and spec.sequence_policy.max_total_tokens
       <= execution_policy.max_sequence_length
       and spec.batch.effective_batch_size
       <= execution_policy.max_effective_batch_size)

    return all(c.passed for c in checks), tuple(checks)
