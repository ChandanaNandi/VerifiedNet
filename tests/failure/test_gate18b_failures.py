"""Gate 18B failure tests: v3 render, wrong template version, policy/template
mismatch, missing evidence, wrong feature policy at eval, and a non-v3 policy
all fail closed."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.datasets.evidence_resolution import (
    EvidenceResolutionError,
    resolve_prepared_features_v2,
)
from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.evidence_eval import (
    V2SlmPredictor,
    evaluate_prepared_corpus_v2,
)
from verifiednet.evaluation.inference import InferenceResponse
from verifiednet.evaluation.prompt import DEFAULT_CANDIDATE_FAMILIES
from verifiednet.training import diagnosis_input_template, diagnosis_target_template
from verifiednet.training.corpus import TrainingCorpusError
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    evidence_observation_input_template,
    evidence_observation_training_policy,
)

pytestmark = pytest.mark.failure

_TASK = diagnosis_task()


class _OkBackend:
    def generate(self, prompt: str, *, decoding: DecodingConfig) -> InferenceResponse:
        return InferenceResponse(
            text='{"fault_family":"bgp_remote_as_mismatch","prediction_type":"diagnosis"}')


def test_evidence_policy_refuses_a_non_v3_input_template() -> None:
    v1 = diagnosis_input_template(task_id=_TASK.task_id, feature_policy_id="feat-x")
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    with pytest.raises(ValueError, match="v3 input template"):
        evidence_observation_training_policy(
            task_id=_TASK.task_id, input_template=v1, target_template=tgt)


def test_corpus_builder_refuses_a_non_v3_template(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")], rejected=[])
    v1 = diagnosis_input_template(
        task_id=_TASK.task_id, feature_policy_id=FeaturePolicyV2().policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=FeaturePolicyV2().policy_id)
    policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    with pytest.raises(TrainingCorpusError, match="v3 contract"):
        build_evidence_observation_corpus(
            ctx.loaded, run_root=ctx.run_root, feature_policy_v2=FeaturePolicyV2(),
            training_data_policy=policy, input_template=v1, target_template=tgt)


def test_missing_evidence_root_fails_closed(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")], rejected=[])
    with pytest.raises(EvidenceResolutionError, match="missing"):
        resolve_prepared_features_v2(
            ctx.loaded, run_root=tmp_path / "does-not-exist",
            policy=FeaturePolicyV2())


def test_eval_refuses_wrong_feature_policy_id(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")], rejected=[])
    v2 = resolve_prepared_features_v2(
        ctx.loaded, run_root=ctx.run_root, policy=FeaturePolicyV2())
    predictor = V2SlmPredictor(
        task=_TASK, backend=_OkBackend(),
        v2_prompt_template_id="prompt-d4ff1ee1c637ea70",
        model_identity="base", predictor_name="v2_base_slm",
        decoding=DecodingConfig(max_tokens=64),
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    from verifiednet.evaluation.engine import EvaluationError

    with pytest.raises(EvaluationError, match="inconsistent v2 feature policy"):
        evaluate_prepared_corpus_v2(
            ctx.loaded, predictor, _TASK, v2_features=v2,
            feature_policy_v2_id="feat-wrong")


def test_eval_refuses_missing_v2_features(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")], rejected=[])
    predictor = V2SlmPredictor(
        task=_TASK, backend=_OkBackend(),
        v2_prompt_template_id="prompt-d4ff1ee1c637ea70",
        model_identity="base", predictor_name="v2_base_slm",
        decoding=DecodingConfig(max_tokens=64),
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    from verifiednet.evaluation.engine import EvaluationError

    with pytest.raises(EvaluationError, match="no v2 features"):
        evaluate_prepared_corpus_v2(
            ctx.loaded, predictor, _TASK, v2_features={},
            feature_policy_v2_id=FeaturePolicyV2().policy_id)
