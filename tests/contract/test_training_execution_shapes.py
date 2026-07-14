"""Contract tests: Gate 10C models frozen, transition-checked, self-validating."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    EVENT_TRANSITIONS,
    FINAL_STATES,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    ExecutionEvent,
    ExecutionEventType,
    ExecutionPolicy,
    ExecutionState,
    TrainingExecution,
    build_execution_policy,
)

pytestmark = pytest.mark.contract

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def test_transition_tables_are_complete_and_closed() -> None:
    # every state appears in the table; terminal states allow nothing
    assert set(LEGAL_TRANSITIONS) == set(ExecutionState)
    assert LEGAL_TRANSITIONS[ExecutionState.COMPLETED] == frozenset()
    assert LEGAL_TRANSITIONS[ExecutionState.CANCELLED] == frozenset()
    assert TERMINAL_STATES == {ExecutionState.COMPLETED, ExecutionState.CANCELLED}
    assert FINAL_STATES == TERMINAL_STATES | {ExecutionState.FAILED}
    # every event type maps only to legal transitions
    for event_type, pairs in EVENT_TRANSITIONS.items():
        assert pairs, event_type
        for before, after in pairs:
            assert after in LEGAL_TRANSITIONS[before], (event_type, before, after)
    assert set(EVENT_TRANSITIONS) == set(ExecutionEventType)


def test_policy_is_frozen_and_self_validating() -> None:
    policy = build_execution_policy(max_retries=2, allow_resume=True)
    assert ExecutionPolicy.model_validate_json(policy.model_dump_json()) == policy
    with pytest.raises(ValidationError):
        policy.max_retries = 5  # frozen
    with pytest.raises(ValidationError):
        ExecutionPolicy.model_validate(policy.model_dump() | {"surprise": 1})
    with pytest.raises(ValidationError):  # tampered id
        ExecutionPolicy.model_validate(
            policy.model_dump() | {"execution_policy_id": "execpol-" + "0" * 16})
    with pytest.raises(ValidationError):  # retries without resume are incoherent
        build_execution_policy(max_retries=1, allow_resume=False)


def test_event_rejects_illegal_and_mismatched_transitions(
    tmp_path: Path, execution_pipeline,
) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    good = ex.events[0].model_dump()
    with pytest.raises(ValidationError):  # planned -> running is illegal
        ExecutionEvent.model_validate(good | {"state_after": "running"})
    with pytest.raises(ValidationError):  # legal transition, wrong event type
        ExecutionEvent.model_validate(good | {"event_type": "batch_completed"})
    with pytest.raises(ValidationError):  # tampered event hash
        ExecutionEvent.model_validate(good | {"event_hash": "evhash-" + "0" * 24})
    with pytest.raises(ValidationError):  # content change breaks the hash
        ExecutionEvent.model_validate(good | {"completed_steps": 7})


def test_execution_is_frozen_self_validating_and_simulated(
    tmp_path: Path, execution_pipeline,
) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    assert TrainingExecution.model_validate_json(ex.model_dump_json()) == ex
    with pytest.raises(ValidationError):
        ex.final_state = ExecutionState.FAILED  # frozen
    dump = ex.model_dump()
    with pytest.raises(ValidationError):
        TrainingExecution.model_validate(dump | {"surprise": 1})
    with pytest.raises(ValidationError):  # tampered execution id
        TrainingExecution.model_validate(
            dump | {"execution_id": "trainexec-" + "0" * 16})
    with pytest.raises(ValidationError):  # tampered digest
        TrainingExecution.model_validate(
            dump | {"execution_digest": "execdig-" + "0" * 24})
    with pytest.raises(ValidationError):  # tampered derived count
        TrainingExecution.model_validate(dump | {"completed_epochs": 99})
    with pytest.raises(ValidationError):  # simulation cannot be denied
        TrainingExecution.model_validate(dump | {"simulated": False})
    with pytest.raises(ValidationError):  # only the fake engine exists here
        TrainingExecution.model_validate(
            dump | {"engine_implementation_id": "real-trainer-v1"})


def test_events_are_reordering_and_dropping_proof(
    tmp_path: Path, execution_pipeline,
) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    dump = ex.model_dump()
    events = dump["events"]
    swapped = [events[0], events[2], events[1], *events[3:]]
    with pytest.raises(ValidationError):  # reordered log breaks the chain
        TrainingExecution.model_validate(dump | {"events": swapped})
    with pytest.raises(ValidationError):  # dropped event breaks the replay
        TrainingExecution.model_validate(
            dump | {"events": [events[0], *events[2:]]})
    with pytest.raises(ValidationError):  # duplicated event breaks sequencing
        TrainingExecution.model_validate(
            dump | {"events": [events[0], events[0], *events[1:]]})


def test_final_state_must_be_final(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    dump = ex.model_dump()
    for not_final in ("planned", "validated", "starting", "running", "resumed"):
        with pytest.raises(ValidationError):
            TrainingExecution.model_validate(dump | {"final_state": not_final})
