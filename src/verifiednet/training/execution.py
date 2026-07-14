"""Training execution: states, events, identity, replayable trace (Gate 10C).

Gate 10C manages HOW a planned training run executes — as pure orchestration,
never as real training. Execution in this gate happens only through the
deterministic simulator (``verifiednet.training.engine``): no ML framework, no
gradients, no checkpoints, no randomness, no timing, no sleeping.

The core idea is that a deterministic execution's event log is a pure function
of its header: given the planned counts, the resume offset, the final state,
and the completed-step count, the ENTIRE expected sequence of events (types,
indices, cumulative progress, state transitions) is re-derivable. The
``TrainingExecution`` model validator replays that expected skeleton and
compares it frame-by-frame against the stored events — so a tampered, missing,
duplicated, or reordered event fails at parse time, not at audit time. Events
are additionally hash-chained (each event binds the previous event's hash), and
the execution digest binds the header to the ordered event hashes.

State machine (no other transition is legal):

    PLANNED → VALIDATED → STARTING → RUNNING → COMPLETED
                                     RUNNING → RUNNING      (progress)
                                     RUNNING → FAILED
                                     RUNNING → CANCELLED
                          FAILED → RESUMED → RUNNING        (a NEW execution)

``COMPLETED`` and ``CANCELLED`` are terminal. ``FAILED`` is resumable: a resume
creates a NEW execution artifact with ``retry_number + 1`` (and therefore a new
execution id), whose log begins with the explicit ``failed → resumed → running``
transition and whose progress continues exactly where the failed run stopped.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.schemas.base import StrictModel

EXECUTION_EVENT_VERSION = 1
TRAINING_EXECUTION_VERSION = 1
FAKE_EXECUTION_ENGINE_ID = "fake-execution-engine-v1"


class TrainingExecutionError(VerifiedNetError):
    """An execution could not be built, resumed, or validated."""


# ---------------------------------------------------------------------------
# States and the legal-transition table
# ---------------------------------------------------------------------------


class ExecutionState(StrEnum):
    PLANNED = "planned"
    VALIDATED = "validated"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESUMED = "resumed"


#: The complete legal-transition table. Anything not listed is illegal.
LEGAL_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.PLANNED: frozenset({ExecutionState.VALIDATED}),
    ExecutionState.VALIDATED: frozenset({ExecutionState.STARTING}),
    ExecutionState.STARTING: frozenset({ExecutionState.RUNNING}),
    ExecutionState.RUNNING: frozenset({
        ExecutionState.RUNNING, ExecutionState.COMPLETED,
        ExecutionState.FAILED, ExecutionState.CANCELLED,
    }),
    ExecutionState.FAILED: frozenset({ExecutionState.RESUMED}),
    ExecutionState.RESUMED: frozenset({ExecutionState.RUNNING}),
    ExecutionState.COMPLETED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
}

#: States an execution ARTIFACT may end in. ``FAILED`` is final for the
#: artifact but resumable by a new execution; the other two are terminal.
FINAL_STATES: frozenset[ExecutionState] = frozenset({
    ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED,
})
TERMINAL_STATES: frozenset[ExecutionState] = frozenset({
    ExecutionState.COMPLETED, ExecutionState.CANCELLED,
})


def is_legal_transition(before: ExecutionState, after: ExecutionState) -> bool:
    return after in LEGAL_TRANSITIONS[before]


class ExecutionEventType(StrEnum):
    EXECUTION_VALIDATED = "execution_validated"
    EXECUTION_STARTING = "execution_starting"
    EXECUTION_STARTED = "execution_started"
    BATCH_COMPLETED = "batch_completed"
    OPTIMIZER_STEP_COMPLETED = "optimizer_step_completed"
    EPOCH_COMPLETED = "epoch_completed"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_CANCELLED = "execution_cancelled"
    EXECUTION_RESUMED = "execution_resumed"


#: Which (state_before, state_after) transitions each event type may carry.
EVENT_TRANSITIONS: dict[
    ExecutionEventType, frozenset[tuple[ExecutionState, ExecutionState]]
] = {
    ExecutionEventType.EXECUTION_VALIDATED: frozenset(
        {(ExecutionState.PLANNED, ExecutionState.VALIDATED)}),
    ExecutionEventType.EXECUTION_STARTING: frozenset(
        {(ExecutionState.VALIDATED, ExecutionState.STARTING)}),
    ExecutionEventType.EXECUTION_STARTED: frozenset({
        (ExecutionState.STARTING, ExecutionState.RUNNING),
        (ExecutionState.RESUMED, ExecutionState.RUNNING),
    }),
    ExecutionEventType.BATCH_COMPLETED: frozenset(
        {(ExecutionState.RUNNING, ExecutionState.RUNNING)}),
    ExecutionEventType.OPTIMIZER_STEP_COMPLETED: frozenset(
        {(ExecutionState.RUNNING, ExecutionState.RUNNING)}),
    ExecutionEventType.EPOCH_COMPLETED: frozenset(
        {(ExecutionState.RUNNING, ExecutionState.RUNNING)}),
    ExecutionEventType.EXECUTION_COMPLETED: frozenset(
        {(ExecutionState.RUNNING, ExecutionState.COMPLETED)}),
    ExecutionEventType.EXECUTION_FAILED: frozenset(
        {(ExecutionState.RUNNING, ExecutionState.FAILED)}),
    ExecutionEventType.EXECUTION_CANCELLED: frozenset(
        {(ExecutionState.RUNNING, ExecutionState.CANCELLED)}),
    ExecutionEventType.EXECUTION_RESUMED: frozenset(
        {(ExecutionState.FAILED, ExecutionState.RESUMED)}),
}


# ---------------------------------------------------------------------------
# Execution policy and identity
# ---------------------------------------------------------------------------


class ExecutionPolicy(StrictModel):
    """Frozen, content-addressed retry/resume policy for an execution."""

    schema_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    max_retries: int = Field(ge=0, le=8)
    allow_resume: bool
    execution_policy_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ExecutionPolicy:
        if self.max_retries > 0 and not self.allow_resume:
            raise ValueError("max_retries > 0 requires allow_resume")
        expected = derive_execution_policy_id(
            max_retries=self.max_retries, allow_resume=self.allow_resume)
        if self.execution_policy_id != expected:
            raise ValueError("execution_policy_id does not match the policy")
        return self


def derive_execution_policy_id(*, max_retries: int, allow_resume: bool) -> str:
    payload = {
        "schema_version": 1, "policy_version": 1,
        "max_retries": max_retries, "allow_resume": allow_resume,
    }
    return "execpol-" + sha256_canonical(payload)[:16]


def build_execution_policy(*, max_retries: int, allow_resume: bool) -> ExecutionPolicy:
    return ExecutionPolicy(
        max_retries=max_retries, allow_resume=allow_resume,
        execution_policy_id=derive_execution_policy_id(
            max_retries=max_retries, allow_resume=allow_resume))


def derive_execution_id(
    *,
    training_plan_id: str,
    trainer_capability_id: str,
    execution_policy_id: str,
    retry_number: int,
) -> str:
    """Deterministic execution identity. A retry is a DIFFERENT execution."""
    payload = {
        "schema_version": 1,
        "training_plan_id": training_plan_id,
        "trainer_capability_id": trainer_capability_id,
        "execution_policy_id": execution_policy_id,
        "retry_number": retry_number,
    }
    return "trainexec-" + sha256_canonical(payload)[:16]


# ---------------------------------------------------------------------------
# Events (immutable, hash-chained, transition-checked)
# ---------------------------------------------------------------------------


class ExecutionEvent(StrictModel):
    """One immutable, deterministic execution event.

    Events carry no timestamps: ordering is the ``sequence`` number plus the
    hash chain (``prev_event_hash`` → ``event_hash``). Event 0 chains from the
    execution id itself, so a log cannot be grafted onto another execution.
    """

    schema_version: Literal[1] = 1
    execution_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    event_type: ExecutionEventType
    state_before: ExecutionState
    state_after: ExecutionState
    epoch_index: int | None = Field(default=None, ge=0)
    batch_index: int | None = Field(default=None, ge=0)
    step_index: int | None = Field(default=None, ge=1)
    completed_steps: int = Field(ge=0)
    detail: str = ""
    prev_event_hash: str = Field(min_length=1)
    event_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ExecutionEvent:
        if not is_legal_transition(self.state_before, self.state_after):
            raise ValueError(
                f"illegal transition {self.state_before} -> {self.state_after}")
        allowed = EVENT_TRANSITIONS[self.event_type]
        if (self.state_before, self.state_after) not in allowed:
            raise ValueError(
                f"event {self.event_type} cannot carry transition "
                f"{self.state_before} -> {self.state_after}")
        if self.event_hash != derive_event_hash(self):
            raise ValueError("event_hash does not match the event content")
        return self


def derive_event_hash(event: ExecutionEvent) -> str:
    payload = event.model_dump(mode="json")
    payload.pop("event_hash", None)
    return "evhash-" + sha256_canonical(payload)[:24]


def _event(
    *,
    execution_id: str,
    sequence: int,
    event_type: ExecutionEventType,
    state_before: ExecutionState,
    state_after: ExecutionState,
    prev_event_hash: str,
    epoch_index: int | None = None,
    batch_index: int | None = None,
    step_index: int | None = None,
    completed_steps: int,
    detail: str = "",
) -> ExecutionEvent:
    payload = {
        "schema_version": 1,
        "execution_id": execution_id,
        "sequence": sequence,
        "event_type": event_type.value,
        "state_before": state_before.value,
        "state_after": state_after.value,
        "epoch_index": epoch_index,
        "batch_index": batch_index,
        "step_index": step_index,
        "completed_steps": completed_steps,
        "detail": detail,
        "prev_event_hash": prev_event_hash,
    }
    return ExecutionEvent(
        execution_id=execution_id, sequence=sequence, event_type=event_type,
        state_before=state_before, state_after=state_after,
        epoch_index=epoch_index, batch_index=batch_index, step_index=step_index,
        completed_steps=completed_steps, detail=detail,
        prev_event_hash=prev_event_hash,
        event_hash="evhash-" + sha256_canonical(payload)[:24],
    )


# ---------------------------------------------------------------------------
# The deterministic event skeleton (shared by the engine AND the validator)
# ---------------------------------------------------------------------------


class EventFrame(StrictModel):
    """The content of one expected event, before hashing/sequencing."""

    event_type: ExecutionEventType
    state_before: ExecutionState
    state_after: ExecutionState
    epoch_index: int | None = None
    batch_index: int | None = None
    step_index: int | None = None
    completed_steps: int
    detail: str = ""


def _progress_frames(
    *,
    planned_optimizer_steps: int,
    planned_batches_per_epoch: int,
    gradient_accumulation_steps: int,
    planned_epochs: int | None,
) -> list[tuple[EventFrame, int]]:
    """All progress frames for a full run, each tagged with its owning step.

    A batch frame's owning step is the optimizer step that flushes its window;
    an epoch frame's owning step is the last optimizer step of that epoch.
    Deterministic, exhaustive, and bounded by ``planned_optimizer_steps``.
    """
    frames: list[tuple[EventFrame, int]] = []
    running = ExecutionState.RUNNING
    step = 0
    epoch = 0
    while step < planned_optimizer_steps:
        if planned_epochs is not None and epoch >= planned_epochs:
            raise TrainingExecutionError(
                "planned epochs exhausted before planned steps were reached")
        batch_in_epoch = 0
        while batch_in_epoch < planned_batches_per_epoch:
            if step >= planned_optimizer_steps:
                break
            window = min(gradient_accumulation_steps,
                         planned_batches_per_epoch - batch_in_epoch)
            owning_step = step + 1
            for b in range(batch_in_epoch, batch_in_epoch + window):
                frames.append((EventFrame(
                    event_type=ExecutionEventType.BATCH_COMPLETED,
                    state_before=running, state_after=running,
                    epoch_index=epoch, batch_index=b, completed_steps=step,
                ), owning_step))
            step += 1
            frames.append((EventFrame(
                event_type=ExecutionEventType.OPTIMIZER_STEP_COMPLETED,
                state_before=running, state_after=running,
                epoch_index=epoch, step_index=step, completed_steps=step,
            ), step))
            batch_in_epoch += window
        if batch_in_epoch >= planned_batches_per_epoch:
            # the epoch genuinely finished (not a mid-epoch step-budget stop)
            frames.append((EventFrame(
                event_type=ExecutionEventType.EPOCH_COMPLETED,
                state_before=running, state_after=running,
                epoch_index=epoch, completed_steps=step,
            ), step))
        epoch += 1
    return frames


def expected_event_frames(
    *,
    planned_optimizer_steps: int,
    planned_batches_per_epoch: int,
    gradient_accumulation_steps: int,
    planned_epochs: int | None,
    resumed: bool,
    offset_steps: int,
    final_state: ExecutionState,
    completed_steps: int,
) -> list[EventFrame]:
    """Replay the EXACT expected event sequence for an execution header.

    Because the simulator is deterministic, the full log is a pure function of
    these header values. The engine builds events from this skeleton and the
    ``TrainingExecution`` validator re-derives it to check the stored log.
    """
    if final_state not in FINAL_STATES:
        raise TrainingExecutionError(f"not a final state: {final_state}")
    if final_state is ExecutionState.COMPLETED:
        if completed_steps != planned_optimizer_steps:
            raise TrainingExecutionError(
                "a completed execution must complete every planned step")
    else:
        if not offset_steps <= completed_steps < planned_optimizer_steps:
            raise TrainingExecutionError(
                "failed/cancelled executions stop strictly before the plan ends")
    if resumed:
        if not 0 < offset_steps < planned_optimizer_steps:
            raise TrainingExecutionError(
                "a resumed execution needs prior progress short of completion")
        prefix = [
            EventFrame(event_type=ExecutionEventType.EXECUTION_RESUMED,
                       state_before=ExecutionState.FAILED,
                       state_after=ExecutionState.RESUMED,
                       completed_steps=offset_steps),
            EventFrame(event_type=ExecutionEventType.EXECUTION_STARTED,
                       state_before=ExecutionState.RESUMED,
                       state_after=ExecutionState.RUNNING,
                       completed_steps=offset_steps),
        ]
    else:
        if offset_steps != 0:
            raise TrainingExecutionError("a fresh execution starts at step 0")
        prefix = [
            EventFrame(event_type=ExecutionEventType.EXECUTION_VALIDATED,
                       state_before=ExecutionState.PLANNED,
                       state_after=ExecutionState.VALIDATED, completed_steps=0),
            EventFrame(event_type=ExecutionEventType.EXECUTION_STARTING,
                       state_before=ExecutionState.VALIDATED,
                       state_after=ExecutionState.STARTING, completed_steps=0),
            EventFrame(event_type=ExecutionEventType.EXECUTION_STARTED,
                       state_before=ExecutionState.STARTING,
                       state_after=ExecutionState.RUNNING, completed_steps=0),
        ]

    progress = [
        frame for frame, owning_step in _progress_frames(
            planned_optimizer_steps=planned_optimizer_steps,
            planned_batches_per_epoch=planned_batches_per_epoch,
            gradient_accumulation_steps=gradient_accumulation_steps,
            planned_epochs=planned_epochs,
        )
        if offset_steps < owning_step <= completed_steps
    ]

    if final_state is ExecutionState.COMPLETED:
        terminal = EventFrame(
            event_type=ExecutionEventType.EXECUTION_COMPLETED,
            state_before=ExecutionState.RUNNING,
            state_after=ExecutionState.COMPLETED, completed_steps=completed_steps)
    elif final_state is ExecutionState.FAILED:
        terminal = EventFrame(
            event_type=ExecutionEventType.EXECUTION_FAILED,
            state_before=ExecutionState.RUNNING,
            state_after=ExecutionState.FAILED, completed_steps=completed_steps,
            detail=f"simulated_failure_after_step_{completed_steps}")
    else:
        terminal = EventFrame(
            event_type=ExecutionEventType.EXECUTION_CANCELLED,
            state_before=ExecutionState.RUNNING,
            state_after=ExecutionState.CANCELLED, completed_steps=completed_steps,
            detail=f"simulated_cancellation_after_step_{completed_steps}")
    return [*prefix, *progress, terminal]


def epochs_completed_at(
    *,
    step: int,
    planned_optimizer_steps: int,
    planned_batches_per_epoch: int,
    gradient_accumulation_steps: int,
    planned_epochs: int | None,
) -> int:
    """How many FULL epochs are complete once ``step`` optimizer steps ran."""
    return sum(
        1 for frame, owning_step in _progress_frames(
            planned_optimizer_steps=planned_optimizer_steps,
            planned_batches_per_epoch=planned_batches_per_epoch,
            gradient_accumulation_steps=gradient_accumulation_steps,
            planned_epochs=planned_epochs,
        )
        if frame.event_type is ExecutionEventType.EPOCH_COMPLETED
        and owning_step <= step
    )


# ---------------------------------------------------------------------------
# The execution record
# ---------------------------------------------------------------------------


class TrainingExecution(StrictModel):
    """One finished (completed/failed/cancelled) simulated execution trace.

    Self-validating: the execution id, the event chain, every transition, the
    replayed event skeleton, all progress counts, resume consistency, and the
    execution digest are re-derived at parse time. ``simulated`` is
    Literal-locked ``True`` — nothing in this gate can claim real training.
    """

    schema_version: Literal[1] = 1
    execution_format_version: Literal[1] = 1
    simulated: Literal[True] = True
    engine_implementation_id: Literal["fake-execution-engine-v1"] = (
        "fake-execution-engine-v1")
    execution_id: str = Field(min_length=1)
    training_plan_id: str = Field(min_length=1)
    trainer_capability_id: str = Field(min_length=1)
    execution_policy: ExecutionPolicy
    retry_number: int = Field(ge=0)
    resumed_from_execution_id: str | None = None
    resumed_from_completed_steps: int | None = Field(default=None, ge=1)
    gradient_accumulation_steps: int = Field(ge=1)
    planned_optimizer_steps: int = Field(ge=1)
    planned_batches_per_epoch: int = Field(ge=1)
    planned_epochs: int | None = Field(default=None, ge=1)
    final_state: ExecutionState
    completed_optimizer_steps: int = Field(ge=0)
    completed_epochs: int = Field(ge=0)
    events: tuple[ExecutionEvent, ...] = Field(min_length=1)
    execution_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> TrainingExecution:
        expected_id = derive_execution_id(
            training_plan_id=self.training_plan_id,
            trainer_capability_id=self.trainer_capability_id,
            execution_policy_id=self.execution_policy.execution_policy_id,
            retry_number=self.retry_number)
        if self.execution_id != expected_id:
            raise ValueError("execution_id does not match the execution content")
        if self.retry_number > self.execution_policy.max_retries:
            raise ValueError("retry_number exceeds the policy's max_retries")
        resumed = self.resumed_from_execution_id is not None
        if resumed != (self.retry_number > 0):
            raise ValueError("retry_number and resumed_from must agree")
        if resumed != (self.resumed_from_completed_steps is not None):
            raise ValueError("resumed_from fields must be set together")
        if resumed and not self.execution_policy.allow_resume:
            raise ValueError("policy does not allow resume")
        if resumed and self.resumed_from_execution_id == self.execution_id:
            raise ValueError("an execution cannot resume from itself")
        if self.final_state not in FINAL_STATES:
            raise ValueError(f"final_state {self.final_state} is not final")

        offset = self.resumed_from_completed_steps or 0
        try:
            frames = expected_event_frames(
                planned_optimizer_steps=self.planned_optimizer_steps,
                planned_batches_per_epoch=self.planned_batches_per_epoch,
                gradient_accumulation_steps=self.gradient_accumulation_steps,
                planned_epochs=self.planned_epochs,
                resumed=resumed, offset_steps=offset,
                final_state=self.final_state,
                completed_steps=self.completed_optimizer_steps)
        except TrainingExecutionError as exc:
            raise ValueError(str(exc)) from exc
        if len(self.events) != len(frames):
            raise ValueError(
                f"event count {len(self.events)} does not match the replayed "
                f"skeleton ({len(frames)} events)")

        prev_hash = self.execution_id
        for i, (event, frame) in enumerate(
                zip(self.events, frames, strict=True)):
            if event.execution_id != self.execution_id:
                raise ValueError(f"event {i} belongs to another execution")
            if event.sequence != i:
                raise ValueError(f"event {i} has sequence {event.sequence}")
            if event.prev_event_hash != prev_hash:
                raise ValueError(f"event {i} breaks the hash chain")
            prev_hash = event.event_hash
            if (event.event_type != frame.event_type
                    or event.state_before != frame.state_before
                    or event.state_after != frame.state_after
                    or event.epoch_index != frame.epoch_index
                    or event.batch_index != frame.batch_index
                    or event.step_index != frame.step_index
                    or event.completed_steps != frame.completed_steps
                    or event.detail != frame.detail):
                raise ValueError(
                    f"event {i} does not match the replayed skeleton")

        expected_epochs_done = epochs_completed_at(
            step=self.completed_optimizer_steps,
            planned_optimizer_steps=self.planned_optimizer_steps,
            planned_batches_per_epoch=self.planned_batches_per_epoch,
            gradient_accumulation_steps=self.gradient_accumulation_steps,
            planned_epochs=self.planned_epochs)
        if self.completed_epochs != expected_epochs_done:
            raise ValueError("completed_epochs does not match the replay")
        if self.execution_digest != derive_execution_digest(self):
            raise ValueError("execution_digest does not match the execution")
        return self


def derive_execution_digest(execution: TrainingExecution) -> str:
    payload = {
        "schema_version": execution.schema_version,
        "execution_format_version": execution.execution_format_version,
        "simulated": execution.simulated,
        "engine_implementation_id": execution.engine_implementation_id,
        "execution_id": execution.execution_id,
        "training_plan_id": execution.training_plan_id,
        "trainer_capability_id": execution.trainer_capability_id,
        "execution_policy_id": execution.execution_policy.execution_policy_id,
        "retry_number": execution.retry_number,
        "resumed_from_execution_id": execution.resumed_from_execution_id,
        "resumed_from_completed_steps": execution.resumed_from_completed_steps,
        "gradient_accumulation_steps": execution.gradient_accumulation_steps,
        "planned_optimizer_steps": execution.planned_optimizer_steps,
        "planned_batches_per_epoch": execution.planned_batches_per_epoch,
        "planned_epochs": execution.planned_epochs,
        "final_state": execution.final_state.value,
        "completed_optimizer_steps": execution.completed_optimizer_steps,
        "completed_epochs": execution.completed_epochs,
        "event_hashes": [e.event_hash for e in execution.events],
    }
    return "execdig-" + sha256_canonical(payload)[:24]


def build_events_from_frames(
    execution_id: str, frames: list[EventFrame],
) -> tuple[ExecutionEvent, ...]:
    """Hash-chain a replayed skeleton into concrete events (chain seed = id)."""
    events: list[ExecutionEvent] = []
    prev_hash = execution_id
    for i, frame in enumerate(frames):
        event = _event(
            execution_id=execution_id, sequence=i,
            event_type=frame.event_type, state_before=frame.state_before,
            state_after=frame.state_after, epoch_index=frame.epoch_index,
            batch_index=frame.batch_index, step_index=frame.step_index,
            completed_steps=frame.completed_steps, detail=frame.detail,
            prev_event_hash=prev_hash)
        events.append(event)
        prev_hash = event.event_hash
    return tuple(events)
