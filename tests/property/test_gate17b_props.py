"""Gate 17B property tests: experiment-id stability, sensitivity to the
objective-policy id and every other frozen control, and outcome-policy
determinism over the plausible Gate 17B metric shape (validity gain)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.experiment import (
    ExperimentRuntimeEnvelope,
    build_experiment_spec,
    build_success_policy,
)

pytestmark = pytest.mark.property

BOUNDARY_OBJECTIVE_ID = "objpol-7e6428964eae2db8"
_TASK = diagnosis_task()
_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)

_BASE_KWARGS = dict(
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
    success_policy=build_success_policy(),
)


def _spec(**overrides):
    return build_experiment_spec(**{**_BASE_KWARGS, **overrides})


def test_experiment_id_is_stable() -> None:
    assert _spec().experiment_id == _spec().experiment_id


@settings(max_examples=100, deadline=None)
@given(field_value=st.sampled_from([
    ("objective_policy_id", "objpol-e5f36da1a1292f3d"),
    ("training_corpus_policy_id", "trainpolicy-47cd597b27119125"),
    ("training_corpus_id", "traincorpus-" + "z" * 16),
    ("prompt_template_id", "prompt-" + "f" * 16),
    ("evaluation_corpus_id", "evalcorpus-" + "f" * 16),
    ("model_revision", "0" * 40),
    ("training_example_cap", 32),
    ("scoring_policy_version", 2),
    ("normalization_policy_id", "norm-other"),
]))
def test_experiment_id_is_sensitive_to_every_frozen_control(
    field_value,
) -> None:
    field, value = field_value
    assert _spec(**{field: value}).experiment_id != _spec().experiment_id


def test_objective_is_the_only_intended_id_driver_vs_gate16b() -> None:
    # switching boundary -> legacy objective is the ONLY intended change
    # between the Gate 16B and Gate 17B experiments.
    legacy = _spec(objective_policy_id="objpol-e5f36da1a1292f3d")
    boundary = _spec()
    assert legacy.experiment_id != boundary.experiment_id
    assert legacy.model_dump(mode="json") | {
        "objective_policy_id": boundary.objective_policy_id,
        "experiment_id": boundary.experiment_id,
    } == boundary.model_dump(mode="json")


def test_outcome_policy_determinism_for_validity_gain() -> None:
    from verifiednet.experiment import (
        ExperimentPrimaryMetrics,
        classify_experiment_outcome,
    )

    policy = build_success_policy()  # min 30 eligible test examples
    # the plausible Gate 17B shape: validity improves markedly (fewer invalid)
    # but accepted-test accuracy stays 0 -> mixed, deterministically.
    metrics = ExperimentPrimaryMetrics(
        eligible_test_examples=36, base_test_correct=0,
        trained_test_correct=0, test_evaluated=36,
        test_base_incorrect_trained_correct=0,
        test_base_correct_trained_incorrect=0,
        test_predictions_differed=30,
        base_invalid_predictions=36, trained_invalid_predictions=0,
        abstention_count=24, base_abstention_correct=0,
        trained_abstention_correct=0, comparison_unconfounded=True)
    outcome, _ = classify_experiment_outcome(metrics, policy)
    assert outcome == "mixed"  # validity gain without accuracy gain
    assert classify_experiment_outcome(metrics, policy)[0] == outcome
