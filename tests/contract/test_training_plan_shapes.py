"""Contract tests: Gate 10B models frozen, validated ids, honest protocol."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    BatchConfig,
    SchedulerConfig,
    TokenizerSpec,
    TrainableModelSpec,
    Trainer,
    TrainingPlan,
    TrainingSpec,
    derive_model_spec_id,
    derive_tokenizer_spec_id,
)

pytestmark = pytest.mark.contract

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b")]


def _model(**overrides) -> TrainableModelSpec:
    fields = dict(provider="fake", model_identifier="fake/tiny-slm",
                  model_revision="a" * 40, model_class="FakeCausalLM")
    fields.update(overrides)
    return TrainableModelSpec(
        **fields,
        model_spec_id=derive_model_spec_id(load_precision="float32", **fields))


def test_model_spec_rejects_mutable_revision() -> None:
    for bad in ("latest", "main", "MASTER", "head"):
        with pytest.raises(ValidationError):
            _model(model_revision=bad)
    with pytest.raises(ValidationError):  # absolute path never enters identity
        _model(model_identifier="/models/local")
    with pytest.raises(ValidationError):  # trust_remote_code locked False
        TrainableModelSpec.model_validate(
            _model().model_dump() | {"trust_remote_code": True})


def test_model_and_tokenizer_ids_self_validate() -> None:
    m = _model()
    assert TrainableModelSpec.model_validate_json(m.model_dump_json()) == m
    with pytest.raises(ValidationError):
        TrainableModelSpec.model_validate(m.model_dump() | {"model_spec_id": "model-" + "0" * 16})
    tok_fields = dict(tokenizer_identifier="t", tokenizer_revision="b" * 40,
                      tokenizer_class="FakeTokenizer")
    tok = TokenizerSpec(
        **tok_fields,
        tokenizer_spec_id=derive_tokenizer_spec_id(
            special_vocab_policy="model_defaults", padding_policy="right",
            truncation_policy="fail_closed", **tok_fields))
    with pytest.raises(ValidationError):
        TokenizerSpec.model_validate(tok.model_dump() | {"padding_policy": "left"})


def test_batch_config_validates_effective_size() -> None:
    with pytest.raises(ValidationError):
        BatchConfig(per_device_batch_size=2, gradient_accumulation_steps=2,
                    effective_batch_size=5)  # must be 4
    with pytest.raises(ValidationError):  # world size locked to 1
        BatchConfig.model_validate({"per_device_batch_size": 2,
                                    "gradient_accumulation_steps": 2,
                                    "declared_world_size": 4,
                                    "effective_batch_size": 16})


def test_scheduler_rejects_contradictory_warmup() -> None:
    with pytest.raises(ValidationError):
        SchedulerConfig(scheduler_name="linear_warmup", warmup_steps=5,
                        warmup_ratio="0.1")
    with pytest.raises(ValidationError):
        SchedulerConfig(scheduler_name="constant", warmup_steps=5)


def test_budget_is_discriminated_union(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    spec = ctx.spec
    # an ambiguous budget carrying both epoch and step fields cannot parse
    with pytest.raises(ValidationError):
        TrainingSpec.model_validate(
            spec.model_dump() | {"budget": {"kind": "epochs", "epochs": 2,
                                            "max_optimizer_steps": 5}})


def test_spec_and_plan_are_frozen_and_forbid_extras(
    tmp_path: Path, plan_pipeline,
) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    spec = ctx.spec
    assert TrainingSpec.model_validate_json(spec.model_dump_json()) == spec
    with pytest.raises(ValidationError):
        spec.task_id = "x"  # frozen
    with pytest.raises(ValidationError):
        TrainingSpec.model_validate(spec.model_dump() | {"surprise": 1})
    with pytest.raises(ValidationError):  # tampered spec id
        TrainingSpec.model_validate(
            spec.model_dump() | {"training_spec_id": "trainspec-" + "0" * 16})
    plan = ctx.trainer.plan(spec=spec, corpus=ctx.descriptor)
    assert TrainingPlan.model_validate_json(plan.model_dump_json()) == plan
    with pytest.raises(ValidationError):  # tampered derived count
        TrainingPlan.model_validate(plan.model_dump() | {"optimizer_steps": 99})


def test_trainer_protocol_exposes_plan_not_train(
    tmp_path: Path, plan_pipeline,
) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    assert isinstance(ctx.trainer, Trainer)
    # the protocol's authoritative operation is plan; there is no train()
    assert hasattr(ctx.trainer, "plan")
    assert not hasattr(ctx.trainer, "train")
    params = inspect.signature(ctx.trainer.plan).parameters
    assert set(params) == {"spec", "corpus"}  # never prepared/eval/benchmark args


def test_no_raw_examples_in_spec_or_plan(tmp_path: Path, plan_pipeline) -> None:
    ctx = plan_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    plan = ctx.trainer.plan(spec=ctx.spec, corpus=ctx.descriptor)
    dumped = plan.model_dump_json() + ctx.spec.model_dump_json()
    from verifiednet.training import load_training_pairs

    pairs = load_training_pairs(ctx.corpus_root)
    for pair in pairs:
        assert pair.target_text not in dumped  # no target text embedded
        # the (long) rendered input never appears in spec/plan artifacts
        assert pair.input_text not in dumped


def test_no_ml_framework_dependencies_imported() -> None:
    import verifiednet.training  # noqa: F401

    for forbidden in ("torch", "transformers", "peft", "accelerate",
                      "bitsandbytes", "deepspeed"):
        assert forbidden not in sys.modules, forbidden
