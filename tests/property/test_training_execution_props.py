"""Gate 10C property tests: deterministic execution and replay stability."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.training import (
    ExecutionEventType,
    ExecutionState,
    epochs_completed_at,
    expected_event_frames,
)

pytestmark = pytest.mark.property


@given(batches=st.integers(1, 12), accum=st.integers(1, 6),
       epochs=st.integers(1, 6))
@settings(max_examples=200)
def test_replayed_skeleton_invariants_epoch_budget(
    batches: int, accum: int, epochs: int,
) -> None:
    steps_per_epoch = -(-batches // accum)
    planned = epochs * steps_per_epoch
    frames = expected_event_frames(
        planned_optimizer_steps=planned, planned_batches_per_epoch=batches,
        gradient_accumulation_steps=accum, planned_epochs=epochs,
        resumed=False, offset_steps=0,
        final_state=ExecutionState.COMPLETED, completed_steps=planned)
    # replay determinism: byte-for-byte identical on every derivation
    again = expected_event_frames(
        planned_optimizer_steps=planned, planned_batches_per_epoch=batches,
        gradient_accumulation_steps=accum, planned_epochs=epochs,
        resumed=False, offset_steps=0,
        final_state=ExecutionState.COMPLETED, completed_steps=planned)
    assert frames == again
    by_type = {t: [f for f in frames if f.event_type is t]
               for t in ExecutionEventType}
    assert len(by_type[ExecutionEventType.OPTIMIZER_STEP_COMPLETED]) == planned
    assert len(by_type[ExecutionEventType.BATCH_COMPLETED]) == batches * epochs
    assert len(by_type[ExecutionEventType.EPOCH_COMPLETED]) == epochs
    assert len(by_type[ExecutionEventType.EXECUTION_COMPLETED]) == 1
    # progress is monotone and ends exactly at the plan
    progress = [f.completed_steps for f in frames]
    assert progress == sorted(progress)
    assert frames[-1].completed_steps == planned
    assert epochs_completed_at(
        step=planned, planned_optimizer_steps=planned,
        planned_batches_per_epoch=batches, gradient_accumulation_steps=accum,
        planned_epochs=epochs) == epochs


@given(batches=st.integers(1, 12), accum=st.integers(1, 6),
       epochs=st.integers(1, 6), data=st.data())
@settings(max_examples=200)
def test_resume_partition_reconstructs_the_full_run(
    batches: int, accum: int, epochs: int, data,
) -> None:
    """Failing at ANY step and resuming yields exactly the full run's progress."""
    steps_per_epoch = -(-batches // accum)
    planned = epochs * steps_per_epoch
    if planned < 2:
        return  # nothing strictly between start and completion
    fail_at = data.draw(st.integers(1, planned - 1))

    full = expected_event_frames(
        planned_optimizer_steps=planned, planned_batches_per_epoch=batches,
        gradient_accumulation_steps=accum, planned_epochs=epochs,
        resumed=False, offset_steps=0,
        final_state=ExecutionState.COMPLETED, completed_steps=planned)
    failed = expected_event_frames(
        planned_optimizer_steps=planned, planned_batches_per_epoch=batches,
        gradient_accumulation_steps=accum, planned_epochs=epochs,
        resumed=False, offset_steps=0,
        final_state=ExecutionState.FAILED, completed_steps=fail_at)
    resumed = expected_event_frames(
        planned_optimizer_steps=planned, planned_batches_per_epoch=batches,
        gradient_accumulation_steps=accum, planned_epochs=epochs,
        resumed=True, offset_steps=fail_at,
        final_state=ExecutionState.COMPLETED, completed_steps=planned)

    def progress(frames):
        keep = {ExecutionEventType.BATCH_COMPLETED,
                ExecutionEventType.OPTIMIZER_STEP_COMPLETED,
                ExecutionEventType.EPOCH_COMPLETED}
        return [f for f in frames if f.event_type in keep]

    # failed-run progress + resumed-run progress == full-run progress, exactly,
    # in order, with no overlap and no gap — regardless of where it failed.
    assert progress(failed) + progress(resumed) == progress(full)


def test_engine_is_deterministic_end_to_end(
    tmp_path_factory, execution_pipeline,
) -> None:
    from verifiednet.common.canonical import canonical_json_bytes

    tmp = tmp_path_factory.mktemp("exec")
    ctx = execution_pipeline(tmp, accepted=[("ras-ref", "run-a"),
                                            ("nr-rev", "run-b")],
                             rejected=["run-rej"])
    for script in ({}, {"fail_after_step": 1}, {"cancel_after_step": 2}):
        a = ctx.engine.execute(ctx.plan, policy=ctx.policy, **script)
        b = ctx.engine.execute(ctx.plan, policy=ctx.policy, **script)
        assert a == b
        assert canonical_json_bytes(a) == canonical_json_bytes(b)
    failed = ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=1)
    r1 = ctx.engine.resume(failed, ctx.plan)
    r2 = ctx.engine.resume(failed, ctx.plan)
    assert canonical_json_bytes(r1) == canonical_json_bytes(r2)


def test_write_twice_byte_identical(tmp_path_factory, execution_pipeline) -> None:
    import hashlib

    from verifiednet.training import write_training_execution

    tmp = tmp_path_factory.mktemp("exec2")
    ctx = execution_pipeline(tmp, accepted=[("ras-ref", "run-a"),
                                            ("nr-rev", "run-b")],
                             rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    w1 = write_training_execution(ex, tmp / "r1")
    w2 = write_training_execution(ex, tmp / "r2")

    def fingerprint(root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(root.rglob("*")) if p.is_file()}

    assert fingerprint(w1.root) == fingerprint(w2.root)
