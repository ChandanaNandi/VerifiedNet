"""The fake execution engine: deterministic simulated execution (Gate 10C).

``FakeExecutionEngine`` turns a verified Gate 10B ``TrainingPlan`` into a
``TrainingExecution`` trace by pure arithmetic. It simulates epoch progress,
batch progress, optimizer-step progress, completion, scripted failure,
scripted cancellation, and resume — with NO randomness, NO timing, NO
sleeping, NO subprocess, NO network, and NO ML framework. Failure and
cancellation are EXPLICIT scripts (``fail_after_step`` / ``cancel_after_step``)
so every outcome is reproducible; nothing in this gate fails spontaneously.

Resume semantics: a failed execution is resumed as a NEW execution with
``retry_number + 1`` (a new execution id — retries are first-class, auditable
artifacts, never in-place mutations). The resumed log begins with the explicit
``failed → resumed → running`` transition, its progress continues exactly at
the step after the failure point, and the resume binding (previous execution
id + previous completed steps) is validated at parse time. Retry policy is
fail-closed: resume requires ``allow_resume`` and ``retry_number + 1`` within
``max_retries``.
"""

from __future__ import annotations

from verifiednet.training.execution import (
    ExecutionPolicy,
    ExecutionState,
    TrainingExecution,
    TrainingExecutionError,
    build_events_from_frames,
    derive_execution_digest,
    derive_execution_id,
    epochs_completed_at,
    expected_event_frames,
)
from verifiednet.training.trainer import TrainingPlan


def _final_from_script(
    *,
    planned_steps: int,
    offset_steps: int,
    fail_after_step: int | None,
    cancel_after_step: int | None,
) -> tuple[ExecutionState, int]:
    """Resolve the scripted outcome into (final_state, completed_steps)."""
    if fail_after_step is not None and cancel_after_step is not None:
        raise TrainingExecutionError(
            "an execution script cannot both fail and cancel")
    scripted = fail_after_step if fail_after_step is not None else cancel_after_step
    if scripted is None:
        return ExecutionState.COMPLETED, planned_steps
    if not offset_steps < scripted < planned_steps:
        raise TrainingExecutionError(
            f"scripted stop at step {scripted} must lie strictly between the "
            f"resume offset ({offset_steps}) and the planned steps "
            f"({planned_steps})")
    state = (ExecutionState.FAILED if fail_after_step is not None
             else ExecutionState.CANCELLED)
    return state, scripted


class FakeExecutionEngine:
    """Deterministic simulated executor for verified training plans."""

    engine_implementation_id = "fake-execution-engine-v1"

    def execute(
        self,
        plan: TrainingPlan,
        *,
        policy: ExecutionPolicy,
        fail_after_step: int | None = None,
        cancel_after_step: int | None = None,
    ) -> TrainingExecution:
        """Simulate a fresh execution of ``plan`` under ``policy``."""
        return self._run(
            plan, policy=policy, retry_number=0, resumed_from=None,
            offset_steps=0, fail_after_step=fail_after_step,
            cancel_after_step=cancel_after_step)

    def resume(
        self,
        previous: TrainingExecution,
        plan: TrainingPlan,
        *,
        fail_after_step: int | None = None,
        cancel_after_step: int | None = None,
    ) -> TrainingExecution:
        """Resume a FAILED execution as a new execution (retry_number + 1)."""
        if previous.final_state is not ExecutionState.FAILED:
            raise TrainingExecutionError(
                f"only failed executions can be resumed, not "
                f"{previous.final_state}")
        policy = previous.execution_policy
        if not policy.allow_resume:
            raise TrainingExecutionError("the execution policy forbids resume")
        retry = previous.retry_number + 1
        if retry > policy.max_retries:
            raise TrainingExecutionError(
                f"retry {retry} exceeds max_retries={policy.max_retries}")
        if previous.training_plan_id != plan.training_plan_id:
            raise TrainingExecutionError(
                "resume must use the same training plan as the failed execution")
        return self._run(
            plan, policy=policy, retry_number=retry,
            resumed_from=previous,
            offset_steps=previous.completed_optimizer_steps,
            fail_after_step=fail_after_step,
            cancel_after_step=cancel_after_step)

    def _run(
        self,
        plan: TrainingPlan,
        *,
        policy: ExecutionPolicy,
        retry_number: int,
        resumed_from: TrainingExecution | None,
        offset_steps: int,
        fail_after_step: int | None,
        cancel_after_step: int | None,
    ) -> TrainingExecution:
        accum = plan.request.spec.batch.gradient_accumulation_steps
        final_state, completed_steps = _final_from_script(
            planned_steps=plan.optimizer_steps, offset_steps=offset_steps,
            fail_after_step=fail_after_step, cancel_after_step=cancel_after_step)
        execution_id = derive_execution_id(
            training_plan_id=plan.training_plan_id,
            trainer_capability_id=plan.request.trainer_capability_id,
            execution_policy_id=policy.execution_policy_id,
            retry_number=retry_number)
        frames = expected_event_frames(
            planned_optimizer_steps=plan.optimizer_steps,
            planned_batches_per_epoch=plan.batches_per_epoch,
            gradient_accumulation_steps=accum,
            planned_epochs=plan.expected_epochs,
            resumed=resumed_from is not None, offset_steps=offset_steps,
            final_state=final_state, completed_steps=completed_steps)
        events = build_events_from_frames(execution_id, frames)
        completed_epochs = epochs_completed_at(
            step=completed_steps,
            planned_optimizer_steps=plan.optimizer_steps,
            planned_batches_per_epoch=plan.batches_per_epoch,
            gradient_accumulation_steps=accum,
            planned_epochs=plan.expected_epochs)
        resumed_from_id = (
            resumed_from.execution_id if resumed_from is not None else None)
        resumed_from_steps = (
            resumed_from.completed_optimizer_steps
            if resumed_from is not None else None)
        digest_probe = TrainingExecution.model_construct(
            execution_id=execution_id,
            training_plan_id=plan.training_plan_id,
            trainer_capability_id=plan.request.trainer_capability_id,
            execution_policy=policy, retry_number=retry_number,
            resumed_from_execution_id=resumed_from_id,
            resumed_from_completed_steps=resumed_from_steps,
            gradient_accumulation_steps=accum,
            planned_optimizer_steps=plan.optimizer_steps,
            planned_batches_per_epoch=plan.batches_per_epoch,
            planned_epochs=plan.expected_epochs, final_state=final_state,
            completed_optimizer_steps=completed_steps,
            completed_epochs=completed_epochs, events=events)
        return TrainingExecution(
            execution_id=execution_id,
            training_plan_id=plan.training_plan_id,
            trainer_capability_id=plan.request.trainer_capability_id,
            execution_policy=policy, retry_number=retry_number,
            resumed_from_execution_id=resumed_from_id,
            resumed_from_completed_steps=resumed_from_steps,
            gradient_accumulation_steps=accum,
            planned_optimizer_steps=plan.optimizer_steps,
            planned_batches_per_epoch=plan.batches_per_epoch,
            planned_epochs=plan.expected_epochs, final_state=final_state,
            completed_optimizer_steps=completed_steps,
            completed_epochs=completed_epochs, events=events,
            execution_digest=derive_execution_digest(digest_probe),
        )
