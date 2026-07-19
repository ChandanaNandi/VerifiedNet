"""Gate 18B unit tests: the v3 evidence-observation template/policy, the v2
training-corpus builder (inputs byte-identical to the deployed v2 prompt), and
the v2 evaluation/predictor/benchmark plumbing over the synthetic chain."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.datasets.evidence_resolution import (
    resolve_features_v2,
    resolve_prepared_features_v2,
)
from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition
from verifiednet.evaluation import DecodingConfig, diagnosis_task
from verifiednet.evaluation.evidence_eval import (
    V2SlmPredictor,
    benchmark_from_runs,
    evaluate_prepared_corpus_v2,
)
from verifiednet.evaluation.inference import InferenceResponse
from verifiednet.evaluation.prompt import (
    DEFAULT_CANDIDATE_FAMILIES,
    derive_prompt_v2_template_id,
    render_diagnosis_prompt_v2,
)
from verifiednet.training import diagnosis_target_template
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    EVIDENCE_OBSERVATION_TEMPLATE_VERSION,
    evidence_observation_input_template,
    evidence_observation_training_policy,
    render_training_input_v2,
)

pytestmark = pytest.mark.unit

V2_FEAT_ID = "feat-228b357dd9f256fa"
V2_PROMPT_ID = "prompt-d4ff1ee1c637ea70"
_TASK = diagnosis_task()


class _FixedBackend:
    """A deterministic fake backend returning one fixed family as valid JSON."""

    def __init__(self, family: str) -> None:
        self._family = family

    def generate(self, prompt: str, *, decoding: DecodingConfig) -> InferenceResponse:
        return InferenceResponse(
            text=f'{{"fault_family":"{self._family}","prediction_type":"diagnosis"}}')


def _v3_template():
    return evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=FeaturePolicyV2().policy_id)


def test_v3_template_and_policy_ids() -> None:
    v3 = _v3_template()
    assert v3.template_version == EVIDENCE_OBSERVATION_TEMPLATE_VERSION
    assert v3.feature_policy_id == V2_FEAT_ID
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    pol = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    assert pol.input_template_id == v3.input_template_id
    assert pol.target_template_id == tgt.target_template_id
    assert derive_prompt_v2_template_id(feature_policy_v2_id=V2_FEAT_ID) == V2_PROMPT_ID


def test_resolve_and_build_v2_corpus_inputs_match_deployed_prompt(
    tmp_path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b")],
                        rejected=["run-rej"])
    prepared, run_root = ctx.loaded, ctx.run_root
    policy = FeaturePolicyV2()
    v3 = _v3_template()
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    corpus = build_evidence_observation_corpus(
        prepared, run_root=run_root, feature_policy_v2=policy,
        training_data_policy=data_policy, input_template=v3, target_template=tgt)
    assert corpus.examples
    assert corpus.feature_policy_id == V2_FEAT_ID
    # every training input is byte-identical to the deployed v2 prompt for the
    # same resolved v2 features
    by_id = {e.trace.source_example_id: e for e in corpus.examples}
    for ex in prepared.examples:
        if ex.trace.partition is not DatasetPartition.TRAIN:
            continue
        if ex.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        f = resolve_features_v2(ex, run_root=run_root, policy=policy)
        deployed = render_diagnosis_prompt_v2(f)
        assert by_id[ex.trace.example_id].input.text == deployed
        assert by_id[ex.trace.example_id].input.text == render_training_input_v2(f)


def test_v2_evaluation_and_benchmark_plumbing(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    prepared, run_root = ctx.loaded, ctx.run_root
    policy = FeaturePolicyV2()
    v2_features = resolve_prepared_features_v2(
        prepared, run_root=run_root, policy=policy)
    predictor = V2SlmPredictor(
        task=_TASK, backend=_FixedBackend("bgp_remote_as_mismatch"),
        v2_prompt_template_id=V2_PROMPT_ID, model_identity="base-snapshot",
        predictor_name="v2_base_slm", decoding=DecodingConfig(max_tokens=64),
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    run = evaluate_prepared_corpus_v2(
        prepared, predictor, _TASK, v2_features=v2_features,
        feature_policy_v2_id=policy.policy_id)
    assert run.feature_policy_id == V2_FEAT_ID
    assert run.records
    assert run.evaluation_id.startswith("eval-")
    # a second predictor (distinct identity) + benchmark from runs
    other = V2SlmPredictor(
        task=_TASK, backend=_FixedBackend("iface_admin_shutdown"),
        v2_prompt_template_id=V2_PROMPT_ID, model_identity="trained-ckpt",
        predictor_name="v2_checkpoint_slm", decoding=DecodingConfig(max_tokens=64),
        candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    run2 = evaluate_prepared_corpus_v2(
        prepared, other, _TASK, v2_features=v2_features,
        feature_policy_v2_id=policy.policy_id)
    assert run.baseline_spec.baseline_id != run2.baseline_spec.baseline_id
    benchmark = benchmark_from_runs(
        (run, run2), task=_TASK, prepared_digest=prepared.manifest.prepared_digest)
    assert len(benchmark.comparison) == 2
    assert len(benchmark.ranking) == 2
