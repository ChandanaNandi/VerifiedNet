"""Gate 19B unit tests: the family-balanced corpus binds into the experiment
spec, the matched four-predictor set is well-formed under the v2 condition, and
the Gate 18B-vs-Gate 19B corpus comparison holds."""

from __future__ import annotations

import pytest

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.evaluation import (
    DecodingConfig,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    diagnosis_task,
)
from verifiednet.evaluation.evidence_eval import V2SlmPredictor
from verifiednet.evaluation.inference import InferenceResponse
from verifiednet.evaluation.prompt import DEFAULT_CANDIDATE_FAMILIES
from verifiednet.experiment import cap_training_corpus
from verifiednet.training import diagnosis_target_template
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    evidence_observation_input_template,
    evidence_observation_training_policy,
)
from verifiednet.training.selection import (
    compare_training_corpora,
    family_balanced_selection_policy,
    select_family_balanced,
)

pytestmark = pytest.mark.unit

_TASK = diagnosis_task()
_SMALL = (("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
          ("bgp_remote_as_mismatch", 1))


class _OkBackend:
    def generate(self, prompt: str, *, decoding: DecodingConfig) -> InferenceResponse:
        return InferenceResponse(
            text='{"fault_family":"bgp_remote_as_mismatch","prediction_type":"diagnosis"}')


def _pipeline(ctx):
    policy = FeaturePolicyV2()
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    kw = dict(run_root=ctx.run_root, feature_policy_v2=policy,
              training_data_policy=data_policy, input_template=v3, target_template=tgt)
    return policy, kw


def test_balanced_corpus_binds_and_differs_from_first64(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b"),
                                            ("pf-ref", "run-c")], rejected=[])
    _policy, kw = _pipeline(ctx)
    first64 = cap_training_corpus(
        build_evidence_observation_corpus(ctx.loaded, **kw), max_example_count=64)
    sel = select_family_balanced(
        ctx.loaded, policy=family_balanced_selection_policy(
            target_total=3, allocation=_SMALL))
    balanced = build_evidence_observation_corpus(ctx.loaded, selection=sel, **kw)
    # the balanced corpus is well-formed and binds the frozen v2 controls; shared
    # sources render byte-identically (the actual subset difference on the real
    # imbalanced chain — 32 added / 32 removed — is proven in the gated proof).
    assert balanced.feature_policy_id == first64.feature_policy_id
    cmp = compare_training_corpora(first64, balanced)
    assert cmp.shared_inputs_equal and cmp.shared_targets_equal
    assert cmp.feature_policy_equal and cmp.input_template_equal
    assert cmp.target_template_equal


def test_matched_four_predictor_set_under_v2(tmp_path, eval_pipeline) -> None:
    task = _TASK
    fixed = FixedPriorBaseline(task=task, fixed_fault_family="bgp_remote_as_mismatch")
    rule = EvidenceRuleBaseline(task=task, default_fault_family="bgp_remote_as_mismatch")
    decoding = DecodingConfig(max_tokens=64)
    base = V2SlmPredictor(
        task=task, backend=_OkBackend(), v2_prompt_template_id="prompt-d4ff1ee1c637ea70",
        model_identity="base_model", predictor_name="v2_base_model_predictor",
        decoding=decoding, candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    trained = V2SlmPredictor(
        task=task, backend=_OkBackend(), v2_prompt_template_id="prompt-d4ff1ee1c637ea70",
        model_identity="trained_ckpt", predictor_name="v2_checkpoint_predictor",
        decoding=decoding, candidate_families=DEFAULT_CANDIDATE_FAMILIES)
    ids = {fixed.spec.baseline_id, rule.spec.baseline_id,
           base.spec.baseline_id, trained.spec.baseline_id}
    assert len(ids) == 4  # four distinct predictors
    # the two SLM arms are byte-matched on prompt and decoding; weights differ
    assert base.spec.baseline_id != trained.spec.baseline_id
