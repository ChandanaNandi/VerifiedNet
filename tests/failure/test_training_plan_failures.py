"""Gate 10B failure tests: capability negotiation, binding, store corruption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    OptimizationConfig,
    SchedulerConfig,
    SimulatedTrainingResult,
    TrainerPlanError,
    TrainingPlanStoreError,
    build_training_request,
    read_training_plan,
    verify_training_plan,
    write_training_plan,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def test_unsupported_model_family_fails(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    from verifiednet.training import TrainableModelSpec, derive_model_spec_id

    fields = dict(provider="huggingface", model_identifier="qwen/tiny",
                  model_revision="b" * 40, model_class="QwenForCausalLM")
    other = TrainableModelSpec(
        **fields, model_spec_id=derive_model_spec_id(load_precision="float32", **fields))
    spec = ctx.make_spec(model=other)  # family "huggingface" not simulated by fake
    with pytest.raises(TrainerPlanError):
        ctx.trainer.plan(spec=spec, corpus=ctx.descriptor)


def test_unsupported_precision_fails(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    spec = ctx.make_spec(precision_policy="bfloat16")
    with pytest.raises(TrainerPlanError):
        build_training_request(spec=spec, corpus=ctx.descriptor,
                               capabilities=ctx.trainer.capabilities)


def test_unsupported_optimizer_and_scheduler_fail(
    tmp_path: Path, plan_pipeline,
) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    bad_opt = ctx.make_spec(optimization=OptimizationConfig(
        optimizer_name="sgd", learning_rate="0.01"))
    with pytest.raises(TrainerPlanError):
        ctx.trainer.plan(spec=bad_opt, corpus=ctx.descriptor)
    # a scheduler outside the fake trainer's declared set is impossible to even
    # request through SchedulerConfig's Literal — prove the negotiation layer too:
    ok_sched = ctx.make_spec(scheduler=SchedulerConfig(scheduler_name="constant"))
    plan = ctx.trainer.plan(spec=ok_sched, corpus=ctx.descriptor)  # sanity
    assert plan.optimizer_steps >= 1


def test_wrong_trainer_implementation_fails(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    spec = ctx.make_spec(trainer_implementation_id="other-trainer-v9")
    with pytest.raises(TrainerPlanError):
        ctx.trainer.plan(spec=spec, corpus=ctx.descriptor)


def test_corpus_binding_mismatches_fail(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    wrong_digest = ctx.make_spec(training_corpus_digest="traindig-" + "0" * 24)
    with pytest.raises(ValidationError):  # request validator refuses the binding
        build_training_request(spec=wrong_digest, corpus=ctx.descriptor,
                               capabilities=ctx.trainer.capabilities)
    wrong_corpus = ctx.make_spec(training_corpus_id="traincorpus-" + "0" * 16)
    with pytest.raises(ValidationError):
        build_training_request(spec=wrong_corpus, corpus=ctx.descriptor,
                               capabilities=ctx.trainer.capabilities)
    wrong_task = ctx.make_spec(task_id="task-" + "0" * 16)
    with pytest.raises(ValidationError):
        build_training_request(spec=wrong_task, corpus=ctx.descriptor,
                               capabilities=ctx.trainer.capabilities)


def test_corpus_digest_change_changes_every_identity(
    tmp_path: Path, plan_pipeline,
) -> None:
    # Changing the bound corpus digest must ripple through spec/request/plan ids.
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    base_plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    other_digest = "traindig-" + "f" * 24
    other_spec = ctx.make_spec(training_corpus_digest=other_digest)
    other_desc = ctx.descriptor.model_copy(
        update={"training_corpus_digest": other_digest})
    other_plan = ctx.trainer.plan(spec=other_spec, corpus=other_desc)
    assert other_spec.training_spec_id != ctx.spec.training_spec_id
    assert other_plan.request.request_id != base_plan.request.request_id
    assert other_plan.training_plan_id != base_plan.training_plan_id


def test_fake_result_cannot_claim_a_real_checkpoint(
    tmp_path: Path, plan_pipeline,
) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    sim = ctx.trainer.simulate(plan)
    with pytest.raises(ValidationError):  # produced_checkpoint locked False
        SimulatedTrainingResult.model_validate(
            sim.model_dump() | {"produced_checkpoint": True})
    with pytest.raises(ValidationError):  # simulated locked True
        SimulatedTrainingResult.model_validate(sim.model_dump() | {"simulated": False})


def test_mismatched_simulated_result_refused_at_write(
    tmp_path: Path, plan_pipeline,
) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan_a = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    from verifiednet.training import StepBudget

    plan_b = ctx.trainer.plan(spec=ctx.make_spec(budget=StepBudget(max_optimizer_steps=9)),
                              corpus=ctx.descriptor)
    sim_b = ctx.trainer.simulate(plan_b)
    with pytest.raises(TrainingPlanStoreError):
        write_training_plan(plan_a, tmp_path / "training-plans",
                            simulated_result=sim_b)


def test_corrupted_plan_file_rejected(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    w = write_training_plan(plan, tmp_path / "training-plans")
    victim = w.root / "plan.json"
    victim.write_bytes(victim.read_bytes() + b" ")
    result = verify_training_plan(w.root)
    assert result.verified is False
    assert any(c.rule == "file_hashes_match" for c in result.failures)
    with pytest.raises(TrainingPlanStoreError):
        read_training_plan(w.root)


def test_tampered_manifest_rejected(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    w = write_training_plan(plan, tmp_path / "training-plans")
    m = w.root / "manifest.json"
    data = json.loads(m.read_text())
    data["training_corpus_digest"] = "traindig-" + "0" * 24
    m.write_text(json.dumps(data))
    result = verify_training_plan(w.root)
    assert result.verified is False
    assert any(c.rule == "manifest_parses" for c in result.failures)


def test_missing_file_and_missing_dir_rejected(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    w = write_training_plan(plan, tmp_path / "training-plans")
    (w.root / "request.json").unlink()
    result = verify_training_plan(w.root)
    assert result.verified is False
    assert any(c.rule == "no_missing_files" for c in result.failures)
    assert verify_training_plan(tmp_path / "nope").verified is False


def test_unsafe_overwrite_refused(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    write_training_plan(plan, tmp_path / "training-plans")
    with pytest.raises(TrainingPlanStoreError):
        write_training_plan(plan, tmp_path / "training-plans")
