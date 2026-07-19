"""Gate 18B property tests: the v2 corpus build and the v2 evaluation are
deterministic (build-twice equality) over the synthetic chain."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.datasets.evidence_resolution import resolve_prepared_features_v2
from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.evidence_eval import (
    V2SlmPredictor,
    evaluate_prepared_corpus_v2,
)
from verifiednet.evaluation.inference import InferenceResponse
from verifiednet.evaluation.prompt import DEFAULT_CANDIDATE_FAMILIES
from verifiednet.training import diagnosis_target_template
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    evidence_observation_input_template,
    evidence_observation_training_policy,
)

pytestmark = pytest.mark.property

_TASK = diagnosis_task()


class _OkBackend:
    def generate(self, prompt: str, *, decoding: DecodingConfig) -> InferenceResponse:
        return InferenceResponse(
            text='{"fault_family":"bgp_remote_as_mismatch","prediction_type":"diagnosis"}')


def test_v2_corpus_build_is_deterministic(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b")], rejected=[])
    policy = FeaturePolicyV2()
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)

    def build():
        return build_evidence_observation_corpus(
            ctx.loaded, run_root=ctx.run_root, feature_policy_v2=policy,
            training_data_policy=data_policy, input_template=v3, target_template=tgt)

    a, b = build(), build()
    assert a.training_corpus_id == b.training_corpus_id
    assert a == b


def test_v2_evaluation_is_deterministic(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")], rejected=[])
    policy = FeaturePolicyV2()
    v2 = resolve_prepared_features_v2(
        ctx.loaded, run_root=ctx.run_root, policy=policy)

    def run():
        predictor = V2SlmPredictor(
            task=_TASK, backend=_OkBackend(),
            v2_prompt_template_id="prompt-d4ff1ee1c637ea70",
            model_identity="base", predictor_name="v2_base_slm",
            decoding=DecodingConfig(max_tokens=64),
            candidate_families=DEFAULT_CANDIDATE_FAMILIES)
        return evaluate_prepared_corpus_v2(
            ctx.loaded, predictor, _TASK, v2_features=v2,
            feature_policy_v2_id=policy.policy_id)

    a, b = run(), run()
    assert a.evaluation_id == b.evaluation_id
    assert resolve_prepared_features_v2(
        ctx.loaded, run_root=ctx.run_root, policy=policy) == v2
