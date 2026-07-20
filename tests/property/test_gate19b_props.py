"""Gate 19B property tests: the experiment id is deterministic and sensitive to
the training-corpus identity, and the balanced corpus build is deterministic."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.comparison import build_default_interpretation_policy
from verifiednet.experiment import (
    ExperimentRuntimeEnvelope,
    build_experiment_spec,
    build_success_policy,
)
from verifiednet.training import (
    boundary_aligned_objective_policy,
    diagnosis_target_template,
)
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    evidence_observation_input_template,
    evidence_observation_training_policy,
)
from verifiednet.training.selection import (
    family_balanced_selection_policy,
    select_family_balanced,
)

pytestmark = pytest.mark.property

_TASK = diagnosis_task()
OBJ = boundary_aligned_objective_policy().objective_policy_id
_ENVELOPE = ExperimentRuntimeEnvelope(
    max_examples=64, max_epochs=2, max_optimizer_steps=64,
    max_sequence_length=448, max_effective_batch_size=2)
_SMALL = (("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
          ("bgp_remote_as_mismatch", 1))


def _spec(corpus_id: str):
    return build_experiment_spec(
        experiment_name="gate19-family-balanced-corpus", experiment_version=1,
        scientific_question="does balancing reduce collapse?",
        hypothesis="balanced composition improves macro accuracy",
        evaluation_corpus_id="evalcorpus-8c932345efc3e6e6",
        evaluation_corpus_digest="ecdig-e72927cc7d4b6fd0fa141462",
        readiness_assessment_id="ready-0b128bea7400a13f",
        source_prepared_digest="prep-" + "0" * 24,
        training_corpus_policy_id="trainpolicy-b74aac32d850a3b0",
        training_corpus_id=corpus_id, training_corpus_digest="traindig-" + "a" * 24,
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
        prompt_template_id="prompt-d4ff1ee1c637ea70",
        decoding=DecodingConfig(max_tokens=64),
        normalization_policy_id=_TASK.normalization.policy_id,
        scoring_policy_version=_TASK.scoring_policy_version,
        interpretation_policy_id=(
            build_default_interpretation_policy().interpretation_policy_id),
        success_policy=build_success_policy())


def test_experiment_id_is_deterministic() -> None:
    assert _spec("traincorpus-x").experiment_id == _spec("traincorpus-x").experiment_id


def test_experiment_id_sensitive_to_training_corpus() -> None:
    assert _spec("traincorpus-x").experiment_id != _spec("traincorpus-y").experiment_id


def test_balanced_corpus_build_is_deterministic(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b"),
                                            ("pf-ref", "run-c")], rejected=[])
    policy = FeaturePolicyV2()
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    sel = select_family_balanced(
        ctx.loaded, policy=family_balanced_selection_policy(
            target_total=3, allocation=_SMALL))

    def build():
        return build_evidence_observation_corpus(
            ctx.loaded, run_root=ctx.run_root, feature_policy_v2=policy,
            training_data_policy=data_policy, input_template=v3, target_template=tgt,
            selection=sel)

    a, b = build(), build()
    assert a.training_corpus_id == b.training_corpus_id
    assert a == b
