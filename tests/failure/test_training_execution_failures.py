"""Gate 10C failure tests: illegal scripts, resume refusals, store corruption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    ExecutionState,
    TrainingExecutionError,
    TrainingExecutionStoreError,
    build_execution_policy,
    compute_execution_store_digest,
    read_training_execution,
    verify_training_execution,
    write_training_execution,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def test_contradictory_and_out_of_range_scripts(
    tmp_path: Path, execution_pipeline,
) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    with pytest.raises(TrainingExecutionError):  # fail AND cancel is ambiguous
        ctx.engine.execute(ctx.plan, policy=ctx.policy,
                           fail_after_step=1, cancel_after_step=2)
    with pytest.raises(TrainingExecutionError):  # step 0 means nothing ran
        ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=0)
    with pytest.raises(TrainingExecutionError):  # at/after the end -> completed
        ctx.engine.execute(ctx.plan, policy=ctx.policy,
                           fail_after_step=ctx.plan.optimizer_steps)
    with pytest.raises(TrainingExecutionError):
        ctx.engine.execute(ctx.plan, policy=ctx.policy, cancel_after_step=99)


def test_resume_refusals(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    completed = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    with pytest.raises(TrainingExecutionError):  # only FAILED is resumable
        ctx.engine.resume(completed, ctx.plan)
    cancelled = ctx.engine.execute(ctx.plan, policy=ctx.policy,
                                   cancel_after_step=1)
    with pytest.raises(TrainingExecutionError):  # cancellation is terminal
        ctx.engine.resume(cancelled, ctx.plan)

    failed = ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=1)
    from verifiednet.training import StepBudget

    other_plan = ctx.make_plan(budget=StepBudget(max_optimizer_steps=9))
    with pytest.raises(TrainingExecutionError):  # resume binds the SAME plan
        ctx.engine.resume(failed, other_plan)


def test_resume_forbidden_and_retries_exhausted(
    tmp_path: Path, execution_pipeline,
) -> None:
    no_resume = execution_pipeline(tmp_path / "a", accepted=_ACC,
                                   rejected=["run-rej"], max_retries=0,
                                   allow_resume=False)
    failed = no_resume.engine.execute(no_resume.plan, policy=no_resume.policy,
                                      fail_after_step=1)
    with pytest.raises(TrainingExecutionError):
        no_resume.engine.resume(failed, no_resume.plan)

    one_retry = execution_pipeline(tmp_path / "b", accepted=_ACC,
                                   rejected=["run-rej"], max_retries=1)
    f0 = one_retry.engine.execute(one_retry.plan, policy=one_retry.policy,
                                  fail_after_step=1)
    f1 = one_retry.engine.resume(f0, one_retry.plan, fail_after_step=2)
    assert f1.retry_number == 1
    assert f1.final_state is ExecutionState.FAILED
    with pytest.raises(TrainingExecutionError):  # retry 2 > max_retries=1
        one_retry.engine.resume(f1, one_retry.plan)


def test_resume_consistency_is_validated(
    tmp_path: Path, execution_pipeline,
) -> None:
    from verifiednet.training import TrainingExecution

    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    failed = ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=1)
    resumed = ctx.engine.resume(failed, ctx.plan)
    dump = resumed.model_dump()
    with pytest.raises(ValidationError):  # wrong resume offset breaks replay
        TrainingExecution.model_validate(
            dump | {"resumed_from_completed_steps": 2})
    with pytest.raises(ValidationError):  # a fresh run cannot claim retry > 0
        TrainingExecution.model_validate(
            dump | {"resumed_from_execution_id": None,
                    "resumed_from_completed_steps": None})
    with pytest.raises(ValidationError):  # self-resume is incoherent
        TrainingExecution.model_validate(
            dump | {"resumed_from_execution_id": resumed.execution_id})
    fresh = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    with pytest.raises(ValidationError):  # retry 0 cannot carry a resume link
        TrainingExecution.model_validate(
            fresh.model_dump()
            | {"resumed_from_execution_id": failed.execution_id,
               "resumed_from_completed_steps": 1})


def test_retry_number_bounded_by_policy() -> None:
    with pytest.raises(ValidationError):
        build_execution_policy(max_retries=99, allow_resume=True)  # le=8


def test_corrupted_events_log_rejected(tmp_path: Path, execution_pipeline) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    w = write_training_execution(ex, tmp_path / "training-executions")
    victim = w.root / "events.jsonl"
    victim.write_bytes(victim.read_bytes() + b" ")
    result = verify_training_execution(w.root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    with pytest.raises(TrainingExecutionStoreError):
        read_training_execution(w.root)


def test_duplicated_event_rejected_even_with_consistent_hashes(
    tmp_path: Path, execution_pipeline,
) -> None:
    # An attacker rewrites events.jsonl AND fixes up the manifest's file hash,
    # event count, and store digest. The replayed skeleton still refuses.
    from verifiednet.common.hashing import sha256_bytes

    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    w = write_training_execution(ex, tmp_path / "training-executions")
    events_path = w.root / "events.jsonl"
    lines = events_path.read_bytes().decode().splitlines()
    forged = ("\n".join([lines[0], *lines]) + "\n").encode()
    events_path.write_bytes(forged)

    manifest_path = w.root / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["files"] = [{"relative_path": "events.jsonl",
                      "sha256": sha256_bytes(forged), "size": len(forged)}]
    data["event_count"] = len(lines) + 1
    from verifiednet.datasets.models import DatasetFileHash

    data["execution_store_digest"] = compute_execution_store_digest(
        schema_version=data["schema_version"],
        execution_format_version=data["execution_format_version"],
        execution_id=data["execution_id"],
        training_plan_id=data["training_plan_id"],
        trainer_capability_id=data["trainer_capability_id"],
        execution_policy_id=data["execution_policy"]["execution_policy_id"],
        retry_number=data["retry_number"], final_state=data["final_state"],
        event_count=data["event_count"],
        execution_digest=data["execution_digest"],
        generated_by=data["generated_by"],
        files=(DatasetFileHash(relative_path="events.jsonl",
                               sha256=sha256_bytes(forged),
                               size=len(forged)),),
    )
    manifest_path.write_text(json.dumps(data))
    result = verify_training_execution(w.root)
    assert result.verified is False
    assert any(c.rule == "execution_replays_and_matches"
               for c in result.failures)


def test_tampered_manifest_digest_rejected(
    tmp_path: Path, execution_pipeline,
) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    w = write_training_execution(ex, tmp_path / "training-executions")
    manifest_path = w.root / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["execution_digest"] = "execdig-" + "0" * 24
    manifest_path.write_text(json.dumps(data))
    result = verify_training_execution(w.root)
    assert result.verified is False  # store digest no longer matches
    assert any(c.rule == "manifest_parses" for c in result.failures)


def test_one_authoritative_outcome_per_execution_identity(
    tmp_path: Path, execution_pipeline,
) -> None:
    # Identity binds plan + capability + policy + retry number — NOT the
    # outcome. A second, conflicting outcome for the same attempt (e.g. a
    # "completed" record after a persisted "failed" record) is refused: one
    # attempt has exactly one authoritative outcome on disk.
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    failed = ctx.engine.execute(ctx.plan, policy=ctx.policy, fail_after_step=1)
    completed = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    assert failed.execution_id == completed.execution_id
    root = tmp_path / "training-executions"
    write_training_execution(failed, root)
    with pytest.raises(TrainingExecutionStoreError):
        write_training_execution(completed, root)


def test_missing_files_and_unsafe_overwrite(
    tmp_path: Path, execution_pipeline,
) -> None:
    ctx = execution_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    ex = ctx.engine.execute(ctx.plan, policy=ctx.policy)
    root = tmp_path / "training-executions"
    w = write_training_execution(ex, root)
    with pytest.raises(TrainingExecutionStoreError):  # refuse overwrite
        write_training_execution(ex, root)
    (w.root / "events.jsonl").unlink()
    result = verify_training_execution(w.root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)
    assert verify_training_execution(tmp_path / "nope").verified is False
