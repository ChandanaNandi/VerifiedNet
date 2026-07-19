"""Gate 19A unit tests: the family-balanced selection policy, the selection
result, the v2 corpus build from a selection, and the corpus comparison."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.datasets.evidence_features import FeaturePolicyV2
from verifiednet.evaluation import diagnosis_task
from verifiednet.training import diagnosis_target_template
from verifiednet.training.evidence_corpus import build_evidence_observation_corpus
from verifiednet.training.policy import (
    TRAINING_CANDIDATE_FAMILIES,
    evidence_observation_input_template,
    evidence_observation_training_policy,
)
from verifiednet.training.selection import (
    DEFAULT_SELECTION_TOTAL,
    BalancedSelectionResult,
    FamilyBalancedSelectionPolicy,
    compare_training_corpora,
    family_balanced_selection_policy,
    select_family_balanced,
)

pytestmark = pytest.mark.unit

_TASK = diagnosis_task()
_REAL_AVAILABILITY = {
    "bgp_neighbor_removal": 40, "bgp_prefix_withdrawal": 40,
    "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 44}


def test_default_policy_ids_and_quotas() -> None:
    p = family_balanced_selection_policy()
    assert p.policy_id.startswith("fbsel-")
    assert p.target_total == DEFAULT_SELECTION_TOTAL == 64
    assert p.allowed_partition == "train"
    assert p.family_order == TRAINING_CANDIDATE_FAMILIES
    quotas = {q.fault_family: q.count for q in p.per_family_allocation}
    assert quotas == {"bgp_neighbor_removal": 20, "bgp_prefix_withdrawal": 20,
                      "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 20}
    assert sum(quotas.values()) == 64


def test_selects_exact_balanced_64(balanced_prepared) -> None:
    prepared = balanced_prepared(_REAL_AVAILABILITY)
    result = select_family_balanced(prepared, policy=family_balanced_selection_policy())
    assert result.total_count == 64
    counts = {q.fault_family: q.count for q in result.per_family_counts}
    assert counts == {"bgp_neighbor_removal": 20, "bgp_prefix_withdrawal": 20,
                      "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 20}
    assert len(set(result.ordered_source_example_ids)) == 64


def test_deterministic_per_family_prefix(balanced_prepared) -> None:
    prepared = balanced_prepared(_REAL_AVAILABILITY)
    result = select_family_balanced(prepared, policy=family_balanced_selection_policy())
    # the remote_as family has exactly 4 available -> all 4 are selected
    ras = [s.example_id for s in result.selected
           if s.fault_family == "bgp_remote_as_mismatch"]
    assert len(ras) == 4


def test_round_robin_interleave(balanced_prepared) -> None:
    prepared = balanced_prepared(_REAL_AVAILABILITY)
    result = select_family_balanced(prepared, policy=family_balanced_selection_policy())
    # first 4 selected are one per family in family_order
    first4 = [s.fault_family for s in result.selected[:4]]
    assert first4 == list(TRAINING_CANDIDATE_FAMILIES)
    # remote_as (quota 4) appears only within the first 4 round-robin columns
    ras_positions = [i for i, s in enumerate(result.selected)
                     if s.fault_family == "bgp_remote_as_mismatch"]
    assert max(ras_positions) < 4 * len(TRAINING_CANDIDATE_FAMILIES)


def test_selection_result_self_validates(balanced_prepared) -> None:
    prepared = balanced_prepared(_REAL_AVAILABILITY)
    result = select_family_balanced(prepared, policy=family_balanced_selection_policy())
    # round-trip through validation
    again = BalancedSelectionResult.model_validate(result.model_dump())
    assert again.selection_digest == result.selection_digest


def test_corpus_build_from_selection_matches_unselected(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b"),
                                            ("pf-ref", "run-c")], rejected=[])
    policy = FeaturePolicyV2()
    v3 = evidence_observation_input_template(
        task_id=_TASK.task_id, feature_policy_v2_id=policy.policy_id)
    tgt = diagnosis_target_template(task_id=_TASK.task_id)
    data_policy = evidence_observation_training_policy(
        task_id=_TASK.task_id, input_template=v3, target_template=tgt)

    def build(selection=None):
        return build_evidence_observation_corpus(
            ctx.loaded, run_root=ctx.run_root, feature_policy_v2=policy,
            training_data_policy=data_policy, input_template=v3,
            target_template=tgt, selection=selection)

    unselected = build()
    sel = select_family_balanced(
        ctx.loaded, policy=family_balanced_selection_policy(
            target_total=3,
            allocation=(("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
                        ("bgp_remote_as_mismatch", 1))))
    selected_corpus = build(selection=sel)
    assert len(selected_corpus.examples) == 3
    # shared examples render byte-identical
    u = {e.trace.source_example_id: e for e in unselected.examples}
    for e in selected_corpus.examples:
        assert e.input.text == u[e.trace.source_example_id].input.text
        assert e.target.text == u[e.trace.source_example_id].target.text
    # the corpus is canonically example-id ordered (invariant); the selection's
    # round-robin order is provenance and equals the same set
    assert [e.trace.source_example_id for e in selected_corpus.examples] == \
        sorted(sel.ordered_source_example_ids)
    assert set(sel.ordered_source_example_ids) == \
        {e.trace.source_example_id for e in selected_corpus.examples}


def test_comparison_report(tmp_path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"), ("nr-ref", "run-b"),
                                            ("pf-ref", "run-c")], rejected=[])
    policy = FeaturePolicyV2()
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
            target_total=3,
            allocation=(("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
                        ("bgp_remote_as_mismatch", 1))))
    candidate = build_evidence_observation_corpus(ctx.loaded, selection=sel, **kw)
    cmp = compare_training_corpora(baseline, candidate)
    assert cmp.shared_inputs_equal and cmp.shared_targets_equal
    assert cmp.feature_policy_equal and cmp.input_template_equal
    assert cmp.target_template_equal
    assert cmp.baseline_unique and cmp.candidate_unique
    assert cmp.intersection_count == 3


def test_policy_is_frozen() -> None:
    p = family_balanced_selection_policy()
    with pytest.raises(ValidationError):
        p.target_total = 32  # type: ignore[misc]
    assert isinstance(p, FamilyBalancedSelectionPolicy)
