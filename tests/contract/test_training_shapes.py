"""Contract tests: Gate 10A training models frozen, Literal-locked, validated."""

from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from verifiednet.training import (
    SupervisedTrainingExample,
    SupervisedTrainingInput,
    SupervisedTrainingTarget,
    TrainingDataPolicy,
    TrainingPair,
    TrainingTraceMetadata,
    derive_training_data_policy_id,
    derive_training_example_id,
    diagnosis_input_template,
    diagnosis_target_template,
    diagnosis_training_policy,
)

pytestmark = pytest.mark.contract

_TASK = "task-0000000000000000"


def _policy() -> TrainingDataPolicy:
    itpl = diagnosis_input_template(task_id=_TASK,
                                    feature_policy_id="feat-0000000000000000")
    ttpl = diagnosis_target_template(task_id=_TASK)
    return diagnosis_training_policy(task_id=_TASK, input_template=itpl,
                                     target_template=ttpl)


def _trace(policy: TrainingDataPolicy) -> TrainingTraceMetadata:
    return TrainingTraceMetadata(
        source_example_id="ex-0123456789abcdef", source_group_id="grp-0123456789abcdef",
        task_id=_TASK, training_data_policy_id=policy.training_data_policy_id,
        input_template_id=policy.input_template_id,
        target_template_id=policy.target_template_id,
        feature_policy_id="feat-0000000000000000",
        label_policy_id="label-0000000000000000", source_schema_version=1)


def test_policy_is_frozen_and_literal_locked() -> None:
    policy = _policy()
    assert TrainingDataPolicy.model_validate_json(policy.model_dump_json()) == policy
    with pytest.raises(ValidationError):
        policy.task_id = "x"  # frozen
    # eligibility fields are Literal-locked: no other configuration exists
    with pytest.raises(ValidationError):
        TrainingDataPolicy.model_validate(
            policy.model_dump() | {"allowed_partition": "validation"})
    with pytest.raises(ValidationError):
        TrainingDataPolicy.model_validate(
            policy.model_dump() | {"allowed_example_kind": "abstention"})
    with pytest.raises(ValidationError):
        TrainingDataPolicy.model_validate(
            policy.model_dump() | {"include_abstention": True})
    with pytest.raises(ValidationError):  # tampered id
        TrainingDataPolicy.model_validate(
            policy.model_dump() | {"training_data_policy_id": "trainpolicy-" + "0" * 16})
    with pytest.raises(ValidationError):  # extra forbidden
        TrainingDataPolicy.model_validate(policy.model_dump() | {"surprise": 1})


def test_trace_metadata_partition_is_literal_train() -> None:
    policy = _policy()
    trace = _trace(policy)
    assert TrainingTraceMetadata.model_validate_json(trace.model_dump_json()) == trace
    with pytest.raises(ValidationError):  # non-train partition unconstructible
        TrainingTraceMetadata.model_validate(trace.model_dump() | {"partition": "test"})
    with pytest.raises(ValidationError):
        TrainingTraceMetadata.model_validate(
            trace.model_dump() | {"example_kind": "abstention"})


def test_training_example_id_is_self_validating() -> None:
    policy = _policy()
    trace = _trace(policy)
    rendered_input = "some deterministic model input"
    rendered_target = '{"fault_family":"x","prediction_type":"diagnosis"}'
    eid = derive_training_example_id(
        source_example_id=trace.source_example_id, task_id=trace.task_id,
        training_data_policy_id=trace.training_data_policy_id,
        input_template_id=trace.input_template_id,
        target_template_id=trace.target_template_id,
        rendered_input=rendered_input, rendered_target=rendered_target)
    good = SupervisedTrainingExample(
        training_example_id=eid,
        input=SupervisedTrainingInput(text=rendered_input),
        target=SupervisedTrainingTarget(text=rendered_target), trace=trace)
    assert SupervisedTrainingExample.model_validate_json(good.model_dump_json()) == good
    # a tampered input invalidates the id binding
    with pytest.raises(ValidationError):
        SupervisedTrainingExample(
            training_example_id=eid,
            input=SupervisedTrainingInput(text=rendered_input + " tampered"),
            target=SupervisedTrainingTarget(text=rendered_target), trace=trace)


def test_templates_validate_their_ids() -> None:
    itpl = diagnosis_input_template(task_id=_TASK,
                                    feature_policy_id="feat-0000000000000000")
    ttpl = diagnosis_target_template(task_id=_TASK)
    with pytest.raises(ValidationError):
        type(itpl).model_validate(
            itpl.model_dump() | {"input_template_id": "traintmpl-" + "0" * 16})
    with pytest.raises(ValidationError):
        type(ttpl).model_validate(
            ttpl.model_dump() | {"target_template_id": "traintgt-" + "0" * 16})


def test_trainer_pair_excludes_metadata() -> None:
    pair = TrainingPair(input_text="in", target_text="out")
    assert set(TrainingPair.model_fields) == {"schema_version", "input_text",
                                              "target_text"}
    with pytest.raises(ValidationError):  # cannot smuggle trace into a pair
        TrainingPair.model_validate(pair.model_dump() | {"trace": {}})


def test_policy_id_changes_with_every_defining_field() -> None:
    base = dict(schema_version=1, policy_version=1, allowed_partition="train",
                allowed_example_kind="accepted_fault",
                allowed_label_kind="accepted_fault", task_id=_TASK,
                input_template_id="traintmpl-a", target_template_id="traintgt-a",
                include_abstention=False)
    reference = derive_training_data_policy_id(**base)
    for key, value in [("task_id", "task-other"), ("input_template_id", "traintmpl-b"),
                       ("target_template_id", "traintgt-b"),
                       ("include_abstention", True), ("allowed_partition", "test")]:
        assert derive_training_data_policy_id(**{**base, key: value}) != reference


def test_no_model_training_dependencies_imported() -> None:
    # Importing the training package must not pull in any training framework.
    import verifiednet.training  # noqa: F401

    for forbidden in ("torch", "transformers", "peft", "bitsandbytes", "accelerate"):
        assert forbidden not in sys.modules, forbidden
