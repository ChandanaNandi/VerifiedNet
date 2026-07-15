"""Gate 16B property tests: experiment-id stability + sensitivity to every
frozen control and to the single independent variable; same-source ordering
independence; and outcome-policy determinism over the Gate 16B metric shape."""

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

_TASK = diagnosis_task()
_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)

_BASE_KWARGS = dict(
    experiment_name="gate16-contract-aligned-conditioning",
    experiment_version=1,
    scientific_question="does contract-aligned conditioning help?",
    hypothesis="v2 conditioning increases valid structured output",
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
    objective_policy_id="objpol-e5f36da1a1292f3d",
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
    ("training_corpus_policy_id", "trainpolicy-47cd597b27119125"),
    ("training_corpus_id", "traincorpus-" + "z" * 16),
    ("training_corpus_digest", "traindig-" + "z" * 24),
    ("prompt_template_id", "prompt-" + "f" * 16),
    ("objective_policy_id", "objpol-" + "f" * 16),
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


def test_independent_variable_is_the_only_intended_id_driver() -> None:
    # switching v2 -> v1 policy (and the corpus id/digest it produces) is the
    # ONLY intended change between the Gate 15 and Gate 16B experiments.
    v1 = _spec(training_corpus_policy_id="trainpolicy-47cd597b27119125",
               training_corpus_id="traincorpus-" + "b" * 16,
               training_corpus_digest="traindig-" + "b" * 24)
    v2 = _spec()
    assert v1.experiment_id != v2.experiment_id
    # holding everything else, the frozen controls are byte-equal
    assert v1.model_dump(mode="json") | {
        "training_corpus_policy_id": v2.training_corpus_policy_id,
        "training_corpus_id": v2.training_corpus_id,
        "training_corpus_digest": v2.training_corpus_digest,
        "experiment_id": v2.experiment_id,
    } == v2.model_dump(mode="json")


def test_same_source_ordering_is_cap_prefix_stable(
    tmp_path, gate14b_corpus_pipeline, gate16_corpora,
) -> None:
    # build the prepared corpus ONCE, then exercise every cap deterministically
    loaded = gate14b_corpus_pipeline(tmp_path, runs_cap=1)[0].loaded
    train_accepted = sum(
        1 for e in loaded.examples
        if e.trace.partition.value == "train"
        and e.trace.example_kind.value == "accepted_fault")
    assert train_accepted >= 3
    prev_sources: list[str] = []
    for cap in range(1, min(train_accepted, 6) + 1):
        v1, v2 = gate16_corpora(loaded, max_example_count=cap)
        v1_sources = [e.trace.source_example_id for e in v1.examples]
        v2_sources = [e.trace.source_example_id for e in v2.examples]
        assert v1_sources == v2_sources  # v1/v2 select identical sources
        assert len(v1.examples) == cap
        assert v1_sources[:len(prev_sources)] == prev_sources  # prefix-stable
        prev_sources = v1_sources


def test_outcome_policy_determinism_holds_for_gate16b_metrics() -> None:
    from verifiednet.experiment import (
        ExperimentPrimaryMetrics,
        classify_experiment_outcome,
    )

    policy = build_success_policy()  # min 30 eligible test examples
    # the plausible Gate 16B shape: validity improves (fewer invalid) but
    # accuracy stays 0 -> mixed, deterministically
    metrics = ExperimentPrimaryMetrics(
        eligible_test_examples=36, base_test_correct=0,
        trained_test_correct=0, test_evaluated=36,
        test_base_incorrect_trained_correct=0,
        test_base_correct_trained_incorrect=0,
        test_predictions_differed=20,
        base_invalid_predictions=36, trained_invalid_predictions=10,
        abstention_count=24, base_abstention_correct=0,
        trained_abstention_correct=0, comparison_unconfounded=True)
    outcome, _ = classify_experiment_outcome(metrics, policy)
    assert outcome == "mixed"  # validity gain without accuracy gain
    assert classify_experiment_outcome(metrics, policy)[0] == outcome
