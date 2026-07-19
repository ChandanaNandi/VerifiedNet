"""Gate 18B contract tests: frozen identities, the v2 experiment spec differs
from the Gate 17B spec ONLY by the v2 feature/prompt binding, deployed==training
bytes, and v1/objective/success-policy remain byte-unchanged."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import DatasetFeaturesV2, FeaturePolicyV2
from verifiednet.datasets.features import FeaturePolicy
from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.evaluation.prompt import (
    render_diagnosis_prompt_v2,
)
from verifiednet.experiment import (
    ExperimentRuntimeEnvelope,
    build_experiment_spec,
    build_success_policy,
)
from verifiednet.training import boundary_aligned_objective_policy
from verifiednet.training.policy import render_training_input_v2

pytestmark = pytest.mark.contract

V1_FEAT = "feat-4f792db1ef08ee5f"
V2_FEAT = "feat-228b357dd9f256fa"
V1_PROMPT = "prompt-93808d932655a347"
V2_PROMPT = "prompt-d4ff1ee1c637ea70"
OBJ = "objpol-7e6428964eae2db8"
SUCCESS = "esucc-ab21b8d6e2ab7a70"
_TASK = diagnosis_task()
_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)


def test_frozen_ids_unchanged() -> None:
    assert FeaturePolicy().policy_id == V1_FEAT
    assert FeaturePolicyV2().policy_id == V2_FEAT
    assert boundary_aligned_objective_policy().objective_policy_id == OBJ
    assert build_success_policy().success_policy_id == SUCCESS


def _spec(*, prompt_template_id: str, training_corpus_id: str):
    return build_experiment_spec(
        experiment_name="gate18-evidence-representation", experiment_version=1,
        scientific_question="does discriminative evidence help accuracy?",
        hypothesis="v2 observable evidence improves accepted diagnosis accuracy",
        evaluation_corpus_id="evalcorpus-8c932345efc3e6e6",
        evaluation_corpus_digest="ecdig-e72927cc7d4b6fd0fa141462",
        readiness_assessment_id="ready-0b128bea7400a13f",
        source_prepared_digest="prep-" + "0" * 24,
        training_corpus_policy_id="trainpolicy-b74aac32d850a3b0",
        training_corpus_id=training_corpus_id,
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
        objective_policy_id=OBJ, runtime_envelope=_ENVELOPE,
        prompt_template_id=prompt_template_id,
        decoding=DecodingConfig(max_tokens=64),
        normalization_policy_id=_TASK.normalization.policy_id,
        scoring_policy_version=_TASK.scoring_policy_version,
        interpretation_policy_id=(
            build_default_interpretation_policy().interpretation_policy_id),
        success_policy=build_success_policy())


def test_v2_experiment_differs_from_gate17b_only_by_representation() -> None:
    # Gate 17B: v1 prompt + v2-contract-aligned corpus. Gate 18B: v2 prompt +
    # v2 evidence corpus. Holding all else, the experiment ids differ, and every
    # other frozen control is byte-equal.
    g17b = _spec(prompt_template_id=V1_PROMPT,
                 training_corpus_id="traincorpus-" + "b" * 16)
    g18b = _spec(prompt_template_id=V2_PROMPT,
                 training_corpus_id="traincorpus-" + "c" * 16)
    assert g17b.experiment_id != g18b.experiment_id
    for field in ("evaluation_corpus_id", "objective_policy_id",
                  "normalization_policy_id", "scoring_policy_version",
                  "model_identifier", "model_revision", "training_example_cap",
                  "success_criteria", "failure_criteria"):
        assert getattr(g17b, field) == getattr(g18b, field), field
    assert g17b.success_policy == g18b.success_policy
    assert g17b.runtime_envelope == g18b.runtime_envelope
    assert g18b.objective_policy_id == OBJ


def test_deployed_v2_prompt_equals_training_input_bytes() -> None:
    f = DatasetFeaturesV2(
        feature_policy_id=V2_FEAT, backend="frr-compose", topology_hash="a" * 64,
        bgp_worst_peer_state="idle", interface_any_admin_down=True,
        interface_any_oper_down=True, reachability_all_success=False,
        bgp_peer_removed=False, bgp_remote_as_changed=False,
        bgp_route_withdrawn=True)
    assert render_diagnosis_prompt_v2(f) == render_training_input_v2(f)
