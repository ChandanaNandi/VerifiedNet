"""Gate 10C unit tests: transitions, ids, fake execution, cancel, resume, store."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.training import (
    ExecutionEventType,
    ExecutionState,
    derive_execution_id,
    is_legal_transition,
    read_training_execution,
    verify_training_execution,
    write_training_execution,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def test_legal_and_illegal_transitions() -> None:
    s = ExecutionState
    legal = [(s.PLANNED, s.VALIDATED), (s.VALIDATED, s.STARTING),
             (s.STARTING, s.RUNNING), (s.RUNNING, s.RUNNING),
             (s.RUNNING, s.COMPLETED), (s.RUNNING, s.FAILED),
             (s.RUNNING, s.CANCELLED), (s.FAILED, s.RESUMED),
             (s.RESUMED, s.RUNNING)]
    for a, b in legal:
        assert is_legal_transition(a, b), (a, b)
    illegal = [(s.PLANNED, s.RUNNING), (s.VALIDATED, s.RUNNING),
               (s.RUNNING, s.PLANNED), (s.COMPLETED, s.RUNNING),
               (s.CANCELLED, s.RESUMED), (s.FAILED, s.RUNNING),
               (s.RESUMED, s.RESUMED), (s.COMPLETED, s.COMPLETED)]
    for a, b in illegal:
        assert not is_legal_transition(a, b), (a, b)


def test_execution_id_derivation(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    kw = dict(training_plan_id=ctx.plan.training_plan_id,
              trainer_capability_id=ctx.plan.request.trainer_capability_id,
              execution_policy_id=ctx.policy.execution_policy_id)
    assert derive_execution_id(retry_number=0, **kw) == \
        derive_execution_id(retry_number=0, **kw)
    # every identity input changes the execution id — including retry number
    assert derive_execution_id(retry_number=0, **kw) != \
        derive_execution_id(retry_number=1, **kw)
    other = kw | {"training_plan_id": "trainplan-" + "0" * 24}
    assert derive_execution_id(retry_number=0, **kw) != \
        derive_execution_id(retry_number=0, **other)


def test_fresh_execution_completes(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    assert ex.final_state is ExecutionState.COMPLETED
    assert ex.retry_number == 0
    assert ex.resumed_from_execution_id is None
    assert ex.completed_optimizer_steps == ctx.plan.optimizer_steps == 3
    assert ex.completed_epochs == 3
    assert ex.simulated is True
    # lifecycle prefix + (2 batches + 1 step + 1 epoch) * 3 epochs + terminal
    assert len(ex.events) == 3 + 12 + 1
    types = [e.event_type for e in ex.events]
    assert types[0] is ExecutionEventType.EXECUTION_VALIDATED
    assert types[1] is ExecutionEventType.EXECUTION_STARTING
    assert types[2] is ExecutionEventType.EXECUTION_STARTED
    assert types[-1] is ExecutionEventType.EXECUTION_COMPLETED
    assert ex.execution_digest.startswith("execdig-")
    assert ex.execution_id.startswith("trainexec-")


def test_cancellation(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy, cancel_after_step=2)
    assert ex.final_state is ExecutionState.CANCELLED
    assert ex.completed_optimizer_steps == 2
    assert ex.completed_epochs == 2  # 1 step per epoch in the default plan
    last = ex.events[-1]
    assert last.event_type is ExecutionEventType.EXECUTION_CANCELLED
    assert last.detail == "simulated_cancellation_after_step_2"
    assert last.state_before is ExecutionState.RUNNING
    assert last.state_after is ExecutionState.CANCELLED


def test_failure_and_resume(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    failed = ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=1)
    assert failed.final_state is ExecutionState.FAILED
    assert failed.completed_optimizer_steps == 1
    assert failed.events[-1].detail == "simulated_failure_after_step_1"

    resumed = ctx.engine.resume(failed, ctx.plan)
    assert resumed.retry_number == 1
    assert resumed.execution_id != failed.execution_id
    assert resumed.resumed_from_execution_id == failed.execution_id
    assert resumed.resumed_from_completed_steps == 1
    assert resumed.final_state is ExecutionState.COMPLETED
    assert resumed.completed_optimizer_steps == 3
    assert resumed.completed_epochs == 3
    types = [e.event_type for e in resumed.events]
    assert types[0] is ExecutionEventType.EXECUTION_RESUMED
    assert types[1] is ExecutionEventType.EXECUTION_STARTED
    assert resumed.events[0].state_before is ExecutionState.FAILED
    assert resumed.events[0].state_after is ExecutionState.RESUMED
    # progress restarts exactly after the failure point: epochs 1 and 2 only
    assert {e.epoch_index for e in resumed.events if e.epoch_index is not None} \
        == {1, 2}
    # lifecycle prefix (2) + (2 batches + 1 step + 1 epoch) * 2 epochs + terminal
    assert len(resumed.events) == 2 + 8 + 1


def test_step_budget_stops_mid_epoch(tmp_path: Path, execution_pipeline) -> None:
    from verifiednet.training import BatchConfig, StepBudget

    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    # accumulation 1: 2 batches/epoch = 2 steps/epoch; 3 steps end mid-epoch 1
    plan = ctx.make_plan(
        budget=StepBudget(max_optimizer_steps=3),
        batch=BatchConfig(per_device_batch_size=2,
                          gradient_accumulation_steps=1,
                          effective_batch_size=2))
    ex = ctx.engine.execute(plan, policy=ctx.policy)
    assert ex.final_state is ExecutionState.COMPLETED
    assert ex.completed_optimizer_steps == 3
    assert ex.completed_epochs == 1  # epoch 1 never finished
    epoch_events = [e for e in ex.events
                    if e.event_type is ExecutionEventType.EPOCH_COMPLETED]
    assert [e.epoch_index for e in epoch_events] == [0]


def test_event_chain_and_sequences(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    prev = ex.execution_id
    for i, event in enumerate(ex.events):
        assert event.sequence == i
        assert event.execution_id == ex.execution_id
        assert event.prev_event_hash == prev
        assert event.event_hash.startswith("evhash-")
        prev = event.event_hash
    steps = [e.completed_steps for e in ex.events]
    assert steps == sorted(steps)  # progress is monotone


def test_write_verify_read_round_trip(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    written = write_training_execution(ex, tmp_path / "training-executions")
    assert written.root.name == ex.execution_id
    assert written.event_count == len(ex.events)
    result = verify_training_execution(written.root)
    assert result.verified is True, result.failures
    assert result.execution_digest == ex.execution_digest
    loaded = read_training_execution(written.root)
    assert loaded.execution == ex
    assert loaded.manifest.event_count == len(ex.events)
    assert loaded.manifest.simulated is True
    assert loaded.manifest.final_state is ExecutionState.COMPLETED


def test_failed_then_resumed_both_persist(
    tmp_path: Path, execution_pipeline,
) -> None:
    # a retry is a SEPARATE artifact: both directories exist and verify
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    failed = ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=2)
    resumed = ctx.engine.resume(failed, ctx.plan)
    root = tmp_path / "training-executions"
    w1 = write_training_execution(failed, root)
    w2 = write_training_execution(resumed, root)
    assert w1.root != w2.root
    assert verify_training_execution(w1.root).verified is True
    assert verify_training_execution(w2.root).verified is True
    back = read_training_execution(w2.root)
    assert back.execution.resumed_from_execution_id == failed.execution_id
