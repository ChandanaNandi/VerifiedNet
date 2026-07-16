"""Gate 17B failure tests: a legacy-objective substitution, an arbitrary
objective, a wrong budget, and a second run/checkpoint all yield a different or
unrepresentable experiment; the frozen result validator refuses a dishonest
improvement claim under the boundary-objective experiment unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.experiment import (
    ControlledTrainingExperimentResult,
    ExperimentRuntimeEnvelope,
    build_experiment_spec,
    build_success_policy,
)

pytestmark = pytest.mark.failure

LEGACY_OBJECTIVE_ID = "objpol-e5f36da1a1292f3d"
BOUNDARY_OBJECTIVE_ID = "objpol-7e6428964eae2db8"
_TASK = diagnosis_task()
_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)


def _spec(**overrides):
    kwargs = dict(
        experiment_name="gate17-boundary-aligned-objective",
        experiment_version=1,
        scientific_question="does boundary-aligned conditioning help?",
        hypothesis="removing the masked separator lifts valid structured output",
        evaluation_corpus_id="evalcorpus-8c932345efc3e6e6",
        evaluation_corpus_digest="ecdig-e72927cc7d4b6fd0fa141462",
        readiness_assessment_id="ready-0b128bea7400a13f",
        source_prepared_digest="prep-" + "0" * 24,
        training_corpus_policy_id="trainpolicy-336332a846b0f791",
        training_corpus_id="traincorpus-" + "a" * 16,
        training_corpus_digest="traindig-" + "a" * 24,
        eligible_train_examples=128, training_example_cap=64,
        cap_rationale="the Gate 10F Literal envelope permits at most 64",
        model_approval_id="modelappr-" + "0" * 16,
        model_artifact_id="modelart-" + "0" * 16,
        tokenizer_artifact_id="tokart-" + "0" * 16,
        model_identifier="Qwen/Qwen2.5-0.5B-Instruct",
        model_revision="7ae557604adf67be50417f59c2c2f167def9a775",
        tokenizer_revision="7ae557604adf67be50417f59c2c2f167def9a775",
        training_spec_id="trainspec-" + "0" * 16,
        training_plan_id="trainplan-" + "0" * 24,
        training_plan_digest="plandig-" + "0" * 24,
        bounded_model_policy_id="bmodel-" + "0" * 16,
        objective_policy_id=BOUNDARY_OBJECTIVE_ID,
        runtime_envelope=_ENVELOPE,
        prompt_template_id="prompt-93808d932655a347",
        decoding=DecodingConfig(max_tokens=64),
        normalization_policy_id=_TASK.normalization.policy_id,
        scoring_policy_version=_TASK.scoring_policy_version,
        interpretation_policy_id=(
            build_default_interpretation_policy().interpretation_policy_id),
        success_policy=build_success_policy())
    kwargs.update(overrides)
    return build_experiment_spec(**kwargs)


def test_legacy_objective_substitution_yields_a_different_experiment() -> None:
    # a spec that silently binds the Gate 16B separator-bearing objective is
    # NOT the Gate 17B experiment
    boundary = _spec()
    legacy = _spec(objective_policy_id=LEGACY_OBJECTIVE_ID)
    assert legacy.experiment_id != boundary.experiment_id


def test_arbitrary_objective_yields_a_different_experiment() -> None:
    assert _spec(objective_policy_id="objpol-" + "f" * 16).experiment_id \
        != _spec().experiment_id


def test_wrong_training_budget_yields_a_different_experiment() -> None:
    boundary = _spec()
    smaller = _spec(
        training_example_cap=32,
        runtime_envelope=ExperimentRuntimeEnvelope(
            max_examples=32, max_epochs=2, max_optimizer_steps=64,
            max_sequence_length=448, max_effective_batch_size=2))
    assert smaller.experiment_id != boundary.experiment_id
    with pytest.raises(ValidationError, match="exceeds the preregistered"):
        _spec(training_example_cap=64,
              runtime_envelope=ExperimentRuntimeEnvelope(
                  max_examples=32, max_epochs=2, max_optimizer_steps=64,
                  max_sequence_length=448, max_effective_batch_size=2))


def test_a_second_execution_or_checkpoint_cannot_be_authorized() -> None:
    from verifiednet.training import build_real_execution_policy

    with pytest.raises(ValidationError):  # steps beyond the Literal ceiling
        build_real_execution_policy(
            approved_backend_id="hf-full-finetune-backend-v1",
            authorization_id="trainauth-x", bounded_model_policy_id="bmodel-x",
            corpus_slice_id="cslice-x",
            objective_policy_id=BOUNDARY_OBJECTIVE_ID,
            max_runtime_optimizer_steps=128,  # > 64 Literal ceiling
            max_epochs=2, max_examples=64, max_sequence_length=448,
            max_effective_batch_size=2,
            determinism_acceptance=("deterministic_supported",))


def test_result_cannot_claim_improved_without_the_counts(
    tmp_path: Path, experiment_pipeline,
) -> None:
    # the frozen Gate 15 result validator governs Gate 17B unchanged
    result = experiment_pipeline(tmp_path).result
    dump = result.model_dump(mode="json")
    if result.outcome != "improved":
        with pytest.raises(ValidationError, match="outcome"):
            ControlledTrainingExperimentResult.model_validate_json(
                __import__("json").dumps(dump | {"outcome": "improved"}))
