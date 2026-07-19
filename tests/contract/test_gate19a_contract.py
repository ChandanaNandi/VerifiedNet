"""Gate 19A contract tests: frozen selection policy/result identities, the
budget-preserving 20/20/20/4 composition, train-only partition, and the balanced
corpus binding the same v2 feature/prompt/target controls as Gate 18B."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.evaluation import diagnosis_task
from verifiednet.evaluation.prompt import derive_prompt_v2_template_id
from verifiednet.training import diagnosis_target_template
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    TRAINING_CANDIDATE_FAMILIES,
    evidence_observation_input_template,
    evidence_observation_training_policy,
)
from verifiednet.training.selection import (
    FamilyBalancedSelectionPolicy,
    family_balanced_selection_policy,
    select_family_balanced,
)

pytestmark = pytest.mark.contract

_TASK = diagnosis_task()
V2_FEAT = "feat-228b357dd9f256fa"
V2_PROMPT = "prompt-d4ff1ee1c637ea70"


def test_policy_forbids_extra_fields() -> None:
    p = family_balanced_selection_policy()
    payload = p.model_dump()
    payload["sneaky"] = 1
    with pytest.raises(ValidationError):
        FamilyBalancedSelectionPolicy.model_validate(payload)


def test_default_composition_is_budget_preserving_20_20_20_4() -> None:
    p = family_balanced_selection_policy()
    assert p.allowed_partition == "train"
    assert p.target_total == 64
    assert p.family_order == TRAINING_CANDIDATE_FAMILIES
    assert p.scarcity_rule == "exact_quota_no_redistribution"
    assert p.final_order_rule == "round_robin_by_family_order"
    quotas = {q.fault_family: q.count for q in p.per_family_allocation}
    assert quotas == {"bgp_neighbor_removal": 20, "bgp_prefix_withdrawal": 20,
                      "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 20}


def test_policy_id_is_content_addressed() -> None:
    a = family_balanced_selection_policy()
    b = family_balanced_selection_policy()
    assert a.policy_id == b.policy_id  # deterministic
    c = family_balanced_selection_policy(
        target_total=60, allocation=(
            ("bgp_neighbor_removal", 20), ("bgp_prefix_withdrawal", 20),
            ("bgp_remote_as_mismatch", 4), ("iface_admin_shutdown", 16)))
    assert c.policy_id != a.policy_id  # quota-sensitive


def test_balanced_corpus_binds_same_v2_controls_as_gate18b(
        tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b"),
                                            ("pf-ref", "run-c")], rejected=[])
    policy = FeaturePolicyV2()
    assert policy.policy_id == V2_FEAT
    assert derive_prompt_v2_template_id(feature_policy_v2_id=policy.policy_id) == V2_PROMPT
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    kw = dict(run_root=ctx.run_root, feature_policy_v2=policy,
              training_data_policy=data_policy, input_template=v3, target_template=tgt)
    baseline = build_evidence_observation_corpus(ctx.loaded, **kw)
    sel = select_family_balanced(
        ctx.loaded, policy=family_balanced_selection_policy(
            target_total=3, allocation=(
                ("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
                ("bgp_remote_as_mismatch", 1))))
    candidate = build_evidence_observation_corpus(ctx.loaded, selection=sel, **kw)
    # same v2 controls; only the corpus id (source set) differs
    assert candidate.feature_policy_id == baseline.feature_policy_id == V2_FEAT
    assert candidate.input_template.input_template_id == v3.input_template_id
    assert candidate.target_template.target_template_id == tgt.target_template_id
    assert candidate.policy.training_data_policy_id == \
        baseline.policy.training_data_policy_id


def test_unselected_build_is_unchanged_by_the_selection_parameter(
        tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b")],
                        rejected=[])
    policy = FeaturePolicyV2()
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)
    kw = dict(run_root=ctx.run_root, feature_policy_v2=policy,
              training_data_policy=data_policy, input_template=v3, target_template=tgt)
    a = build_evidence_observation_corpus(ctx.loaded, **kw)
    b = build_evidence_observation_corpus(ctx.loaded, selection=None, **kw)
    assert a.training_corpus_id == b.training_corpus_id
    assert a == b
