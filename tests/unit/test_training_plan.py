"""Gate 10B unit tests: spec ids, arithmetic, fake trainer, plan store."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.training import (
    EpochBudget,
    StepBudget,
    canonical_decimal,
    compute_batches_per_epoch,
    compute_optimizer_steps_per_epoch,
    read_training_plan,
    verify_training_plan,
    write_training_plan,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def test_all_spec_ids_deterministic(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    a, b = ctx.make_spec(), ctx.make_spec()
    assert a.training_spec_id == b.training_spec_id
    assert a.model.model_spec_id == b.model.model_spec_id
    assert a.tokenizer.tokenizer_spec_id == b.tokenizer.tokenizer_spec_id
    assert a.training_spec_id.startswith("trainspec-")


def test_spec_id_changes_with_hyperparameters(tmp_path: Path, plan_pipeline) -> None:
    from verifiednet.training import OptimizationConfig, SeedPolicy

    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    base = ctx.spec
    lr = ctx.make_spec(optimization=OptimizationConfig(optimizer_name="adamw",
                                                       learning_rate="2e-4"))
    seeds = ctx.make_spec(seed_policy=SeedPolicy(data_order_seed=99, model_init_seed=2,
                                                 dropout_seed=3, backend_seed=4))
    budget = ctx.make_spec(budget=EpochBudget(epochs=5))
    assert len({base.training_spec_id, lr.training_spec_id, seeds.training_spec_id,
                budget.training_spec_id}) == 4


def test_canonical_decimal_normalization() -> None:
    assert canonical_decimal("1e-3") == "0.001"
    assert canonical_decimal("0.0010") == "0.001"
    assert canonical_decimal("1E+1") == "10"
    assert canonical_decimal("0.9") == "0.9"
    with pytest.raises(ValueError):
        canonical_decimal("nan")
    with pytest.raises(ValueError):
        canonical_decimal("not-a-number")


def test_batch_and_step_arithmetic() -> None:
    # ceil division with explicit remainder behavior
    assert compute_batches_per_epoch(3, 2) == 2   # partial final batch counts
    assert compute_batches_per_epoch(4, 2) == 2
    assert compute_batches_per_epoch(1, 8) == 1
    assert compute_optimizer_steps_per_epoch(2, 2) == 1
    assert compute_optimizer_steps_per_epoch(3, 2) == 2  # partial window flushes


def test_epoch_budget_plan_counts(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    # 3 examples / batch 2 = 2 batches; / accum 2 = 1 step/epoch; * 3 epochs = 3
    assert plan.expected_example_count == 3
    assert plan.batches_per_epoch == 2
    assert plan.expected_epochs == 3
    assert plan.optimizer_steps == 3
    assert plan.effective_batch_size == 4


def test_step_budget_plan_counts(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    spec = ctx.make_spec(budget=StepBudget(max_optimizer_steps=7))
    plan = ctx.trainer.plan(spec=spec, corpus=ctx.descriptor)
    assert plan.optimizer_steps == 7
    assert plan.expected_epochs is None  # steps budget derives no epoch count


def test_descriptor_matches_manifest(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    d = ctx.descriptor
    assert d.training_corpus_id == ctx.manifest.training_corpus_id
    assert d.training_corpus_digest == ctx.manifest.training_corpus_digest
    assert d.example_count == ctx.manifest.example_count == 3
    assert d.source_partition == "train"
    # the descriptor never carries example text
    assert not hasattr(d, "examples")
    assert "text" not in type(d).model_fields


def test_plan_and_request_ids_deterministic(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    p1 = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    p2 = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    assert p1.request.request_id == p2.request.request_id
    assert p1.training_plan_id == p2.training_plan_id
    assert p1.determinism_claim.value == "deterministic"


def test_fake_simulation_is_deterministic_and_explicit(
    tmp_path: Path, plan_pipeline,
) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    sim1 = ctx.trainer.simulate(plan)
    sim2 = ctx.trainer.simulate(plan)
    assert sim1 == sim2
    assert sim1.simulated is True
    assert sim1.produced_checkpoint is False
    assert sim1.simulated_completed_steps == plan.optimizer_steps


def test_write_verify_read_round_trip(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    sim = ctx.trainer.simulate(plan)
    written = write_training_plan(plan, tmp_path / "training-plans",
                                  simulated_result=sim)
    assert written.root.name == plan.training_plan_id
    result = verify_training_plan(written.root)
    assert result.verified is True, result.failures
    loaded = read_training_plan(written.root)
    assert loaded.plan.training_plan_id == plan.training_plan_id
    assert loaded.request.request_id == plan.request.request_id
    assert loaded.simulated_result == sim
    assert loaded.manifest.simulated is True


def test_plan_without_simulation(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    written = write_training_plan(plan, tmp_path / "training-plans")
    assert verify_training_plan(written.root).verified is True
    loaded = read_training_plan(written.root)
    assert loaded.simulated_result is None
    assert loaded.manifest.simulated is False
