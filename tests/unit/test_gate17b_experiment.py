"""Gate 17B unit tests: the boundary-aligned-objective experiment binds the
Gate 17A objective (objpol-7e6428964eae2db8), differs from the Gate 16B
(separator-bearing objective) spec ONLY by the objective-policy id, is
one-run/one-checkpoint locked, and derives a deterministic distinct id."""

from __future__ import annotations

import pytest

from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.experiment import (
    ExperimentRuntimeEnvelope,
    build_experiment_spec,
    build_success_policy,
)
from verifiednet.training import (
    boundary_aligned_objective_policy,
    build_causal_lm_objective_policy,
)

pytestmark = pytest.mark.unit

# Pinned identities (content-checked at import elsewhere).
LEGACY_OBJECTIVE_ID = "objpol-e5f36da1a1292f3d"
BOUNDARY_OBJECTIVE_ID = "objpol-7e6428964eae2db8"
V2_POLICY_ID = "trainpolicy-336332a846b0f791"
TARGET_TEMPLATE_ID = "traintgt-286e4ecdff06833e"
PROMPT_TEMPLATE_ID = "prompt-93808d932655a347"
SUCCESS_POLICY_ID = "esucc-ab21b8d6e2ab7a70"
V3_CORPUS_ID = "evalcorpus-8c932345efc3e6e6"
V3_CORPUS_DIGEST = "ecdig-e72927cc7d4b6fd0fa141462"
READINESS_ID = "ready-0b128bea7400a13f"
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"

_TASK = diagnosis_task()

#: The exact Gate 15/16B training envelope — reused byte-for-byte.
GATE16_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)


def _spec(*, objective_policy_id: str,
          experiment_name: str = "gate17-boundary-aligned-objective"):
    return build_experiment_spec(
        experiment_name=experiment_name, experiment_version=1,
        scientific_question="does boundary-aligned conditioning help?",
        hypothesis="removing the masked separator lifts valid structured output",
        evaluation_corpus_id=V3_CORPUS_ID,
        evaluation_corpus_digest=V3_CORPUS_DIGEST,
        readiness_assessment_id=READINESS_ID,
        source_prepared_digest="prep-" + "0" * 24,
        training_corpus_policy_id=V2_POLICY_ID,
        training_corpus_id="traincorpus-" + "a" * 16,
        training_corpus_digest="traindig-" + "a" * 24,
        eligible_train_examples=128, training_example_cap=64,
        cap_rationale="the Gate 10F Literal envelope permits at most 64",
        model_approval_id="modelappr-" + "0" * 16,
        model_artifact_id="modelart-" + "0" * 16,
        tokenizer_artifact_id="tokart-" + "0" * 16,
        model_identifier=MODEL_ID, model_revision=MODEL_REVISION,
        tokenizer_revision=MODEL_REVISION,
        training_spec_id="trainspec-" + "0" * 16,
        training_plan_id="trainplan-" + "0" * 24,
        training_plan_digest="plandig-" + "0" * 24,
        bounded_model_policy_id="bmodel-" + "0" * 16,
        objective_policy_id=objective_policy_id,
        runtime_envelope=GATE16_ENVELOPE,
        prompt_template_id=PROMPT_TEMPLATE_ID,
        decoding=DecodingConfig(max_tokens=64),
        normalization_policy_id=_TASK.normalization.policy_id,
        scoring_policy_version=_TASK.scoring_policy_version,
        interpretation_policy_id=(
            build_default_interpretation_policy().interpretation_policy_id),
        success_policy=build_success_policy())


def test_boundary_objective_id_is_pinned() -> None:
    assert boundary_aligned_objective_policy().objective_policy_id \
        == BOUNDARY_OBJECTIVE_ID
    assert build_causal_lm_objective_policy().objective_policy_id \
        == LEGACY_OBJECTIVE_ID


def test_gate17b_spec_binds_the_boundary_objective_and_is_one_run_locked(
) -> None:
    spec = _spec(objective_policy_id=BOUNDARY_OBJECTIVE_ID)
    assert spec.experiment_id.startswith("exp-")
    assert spec.objective_policy_id == BOUNDARY_OBJECTIVE_ID
    assert spec.prompt_template_id == PROMPT_TEMPLATE_ID
    assert spec.maximum_training_runs == 1
    assert spec.runtime_envelope.max_training_runs == 1
    assert spec.runtime_envelope.max_treatment_checkpoints == 1
    assert spec.readiness_outcome == "ready_for_controlled_experiment"


def test_experiment_id_is_deterministic() -> None:
    a = _spec(objective_policy_id=BOUNDARY_OBJECTIVE_ID)
    b = _spec(objective_policy_id=BOUNDARY_OBJECTIVE_ID)
    assert a == b
    assert a.experiment_id == b.experiment_id


def test_only_the_objective_changes_the_experiment_id() -> None:
    """A boundary-objective spec differs from an otherwise-identical
    separator-objective (Gate 16B) spec ONLY through the objective-policy id —
    every other frozen control is byte-equal."""
    legacy_spec = _spec(objective_policy_id=LEGACY_OBJECTIVE_ID)
    boundary_spec = _spec(objective_policy_id=BOUNDARY_OBJECTIVE_ID)
    assert legacy_spec.experiment_id != boundary_spec.experiment_id
    for field in ("evaluation_corpus_id", "evaluation_corpus_digest",
                  "readiness_assessment_id", "training_corpus_policy_id",
                  "training_corpus_id", "training_corpus_digest",
                  "prompt_template_id", "normalization_policy_id",
                  "scoring_policy_version", "model_identifier",
                  "model_revision", "tokenizer_revision",
                  "training_example_cap", "primary_metrics",
                  "secondary_metrics", "success_criteria",
                  "failure_criteria"):
        assert getattr(legacy_spec, field) == getattr(boundary_spec, field), \
            field
    assert legacy_spec.success_policy == boundary_spec.success_policy
    assert legacy_spec.runtime_envelope == boundary_spec.runtime_envelope
    assert legacy_spec.decoding == boundary_spec.decoding
    # the objective id is the ONLY differing bound field
    assert legacy_spec.objective_policy_id != boundary_spec.objective_policy_id
