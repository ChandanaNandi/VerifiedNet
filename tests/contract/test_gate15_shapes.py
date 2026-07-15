"""Gate 15 contract tests: frozen preregistration, one-run rule,
readiness-Literal, unweakenable success policy, honest result claims."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from verifiednet.experiment import (
    ControlledTrainingExperimentResult,
    ControlledTrainingExperimentSpec,
    ExperimentPhaseLog,
    ExperimentPrimaryMetrics,
    ExperimentRuntimeEnvelope,
    ExperimentSuccessPolicy,
    TrainingPhaseBinding,
    build_success_policy,
)

pytestmark = pytest.mark.contract


def test_success_policy_cannot_be_weakened() -> None:
    policy = build_success_policy()
    dump = policy.model_dump(mode="json")
    for field, weaker in (("require_unconfounded_comparison", False),
                          ("require_accepted_test_accuracy_increase", False),
                          ("require_net_paired_wins", False),
                          ("max_invalid_prediction_increase", 5),
                          ("forbid_abstention_regression", False)):
        with pytest.raises(ValidationError):
            ExperimentSuccessPolicy.model_validate_json(json.dumps(
                dump | {field: weaker}))
    with pytest.raises(ValidationError):  # tampered id
        ExperimentSuccessPolicy.model_validate_json(json.dumps(
            dump | {"success_policy_id": "esucc-" + "0" * 16}))
    with pytest.raises(ValidationError):  # frozen
        policy.min_eligible_test_examples = 1  # type: ignore[misc]


def test_spec_is_frozen_id_locked_and_one_run_only(
    tmp_path, experiment_pipeline,
) -> None:
    spec = experiment_pipeline(tmp_path).spec
    dump = spec.model_dump(mode="json")
    with pytest.raises(ValidationError):  # a second run is unrepresentable
        ControlledTrainingExperimentSpec.model_validate_json(json.dumps(
            dump | {"maximum_training_runs": 2}))
    with pytest.raises(ValidationError):  # an unready corpus cannot be bound
        ControlledTrainingExperimentSpec.model_validate_json(json.dumps(
            dump | {"readiness_outcome":
                    "coverage_threshold_met_but_low_diversity"}))
    with pytest.raises(ValidationError):  # tampered hypothesis breaks the id
        ControlledTrainingExperimentSpec.model_validate_json(json.dumps(
            dump | {"hypothesis": "revised after seeing the results"}))
    with pytest.raises(ValidationError):  # extras forbidden
        ControlledTrainingExperimentSpec.model_validate_json(json.dumps(
            dump | {"note": "x"}))
    with pytest.raises(ValidationError):  # frozen
        spec.training_example_cap = 1  # type: ignore[misc]


def test_runtime_envelope_ceilings_are_locked() -> None:
    with pytest.raises(ValidationError):  # a second checkpoint ceiling
        ExperimentRuntimeEnvelope.model_validate_json(json.dumps({
            "schema_version": 1, "max_examples": 8, "max_epochs": 1,
            "max_optimizer_steps": 8, "max_sequence_length": 128,
            "max_effective_batch_size": 1, "max_training_runs": 1,
            "max_treatment_checkpoints": 2}))
    with pytest.raises(ValidationError):  # beyond the Gate 10F Literal bound
        ExperimentRuntimeEnvelope(
            max_examples=65, max_epochs=1, max_optimizer_steps=8,
            max_sequence_length=128, max_effective_batch_size=1)
    with pytest.raises(ValidationError):
        ExperimentRuntimeEnvelope(
            max_examples=8, max_epochs=1, max_optimizer_steps=65,
            max_sequence_length=128, max_effective_batch_size=1)


def test_phase_log_prefix_rule_is_structural() -> None:
    with pytest.raises(ValidationError, match="prefix"):
        ExperimentPhaseLog.model_validate_json(json.dumps({
            "schema_version": 1,
            "phases": ["PREREGISTERED", "TRAINING_COMPLETED"]}))  # skip
    with pytest.raises(ValidationError, match="prefix"):
        ExperimentPhaseLog.model_validate_json(json.dumps({
            "schema_version": 1,
            "phases": ["TRAINING_CORPUS_FINALIZED"]}))  # wrong start
    with pytest.raises(ValidationError, match="prefix"):
        ExperimentPhaseLog.model_validate_json(json.dumps({
            "schema_version": 1,
            "phases": ["PREREGISTERED", "TRAINING_CORPUS_FINALIZED",
                       "PREREGISTERED"]}))  # backward


def test_result_cannot_claim_improvement_the_counts_refuse(
    tmp_path, experiment_pipeline,
) -> None:
    result = experiment_pipeline(tmp_path).result
    dump = result.model_dump(mode="json")
    if result.outcome != "improved":
        with pytest.raises(ValidationError, match="outcome"):
            ControlledTrainingExperimentResult.model_validate_json(
                json.dumps(dump | {"outcome": "improved"}))
    with pytest.raises(ValidationError):  # tampered counts break the checks
        ControlledTrainingExperimentResult.model_validate_json(json.dumps(
            dump | {"metrics": dump["metrics"]
                    | {"trained_test_correct": 2}}))
    with pytest.raises(ValidationError):  # tampered result id
        ControlledTrainingExperimentResult.model_validate_json(json.dumps(
            dump | {"experiment_result_id": "expres-" + "0" * 16}))
    with pytest.raises(ValidationError):  # incomplete phase log
        ControlledTrainingExperimentResult.model_validate_json(json.dumps(
            dump | {"phases": dump["phases"][:-1]}))


def test_training_binding_cannot_carry_a_failed_run(
    tmp_path, experiment_pipeline,
) -> None:
    training = experiment_pipeline(tmp_path).training
    dump = training.model_dump(mode="json")
    with pytest.raises(ValidationError):
        TrainingPhaseBinding.model_validate_json(json.dumps(
            dump | {"final_state": "failed"}))


def test_classification_inputs_have_no_channel_for_forbidden_evidence() -> None:
    fields = set(ExperimentPrimaryMetrics.model_fields)
    # rank alone / training loss / train- or validation-accuracy can never
    # establish improvement: there is no field to carry them.
    assert not fields & {"rank", "ranking", "benchmark_rank", "loss",
                         "training_loss", "train_accuracy",
                         "train_correct", "validation_accuracy",
                         "validation_correct", "overall_accuracy"}
    spec_fields = set(ControlledTrainingExperimentSpec.model_fields)
    assert not spec_fields & {"accuracy", "rank", "prediction", "loss"}


def test_evaluation_artifacts_cannot_be_training_inputs(
    tmp_path, experiment_pipeline,
) -> None:
    from verifiednet.training import TrainingStoreError, load_training_pairs

    ctx = experiment_pipeline(tmp_path)
    with pytest.raises(TrainingStoreError):
        load_training_pairs(ctx.written.root)


def test_v3_corpus_identity_is_fixed_in_the_operational_contract() -> None:
    # The operational Gate 15 test pins EXACTLY the registered v3 corpus and
    # its readiness assessment — a different corpus refuses before anything
    # runs, and the pins are source constants, not environment inputs.
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "integration"
              / "test_gate15_operational.py").read_text()
    assert 'GATE15_V3_CORPUS_ID = "evalcorpus-8c932345efc3e6e6"' in source
    assert ('GATE15_V3_CORPUS_DIGEST = '
            '"ecdig-e72927cc7d4b6fd0fa141462"') in source
    assert 'GATE15_READINESS_ID = "ready-0b128bea7400a13f"' in source
    assert ('GATE15_MODEL_REVISION = '
            '"7ae557604adf67be50417f59c2c2f167def9a775"') in source
