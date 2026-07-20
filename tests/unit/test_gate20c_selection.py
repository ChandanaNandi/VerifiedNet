"""Gate 20C unit tests: the group-aware budget-preserving 16/16/16/16 selection
policy and result over a v4-like TRAIN partition — exact per-family counts, >=8
independent remote-AS groups, group round-robin coverage, and determinism."""

from __future__ import annotations

import collections

import pytest

from verifiednet.training.selection import (
    GROUP_BALANCED_SELECTION_TOTAL,
    BalancedSelectionResult,
    GroupBalancedSelectionPolicy,
    group_balanced_selection_policy,
    independent_group_counts,
    select_group_balanced,
)

pytestmark = pytest.mark.unit

_RAS = "bgp_remote_as_mismatch"


def test_policy_defaults_and_id() -> None:
    p = group_balanced_selection_policy()
    assert p.policy_id.startswith("gbsel-")
    assert p.target_total == GROUP_BALANCED_SELECTION_TOTAL == 64
    assert p.within_family_order == "group_round_robin_then_example_id"
    assert {q.fault_family: q.count for q in p.per_family_allocation} == {
        "bgp_neighbor_removal": 16, "bgp_prefix_withdrawal": 16,
        "bgp_remote_as_mismatch": 16, "iface_admin_shutdown": 16}
    assert {q.fault_family: q.count for q in p.min_groups_per_family}[_RAS] == 8


def test_selection_is_16_16_16_16(coverage_prepared) -> None:
    sel = select_group_balanced(coverage_prepared(), policy=group_balanced_selection_policy())
    assert sel.total_count == 64
    counts = collections.Counter(s.fault_family for s in sel.selected)
    assert dict(counts) == {
        "bgp_neighbor_removal": 16, "bgp_prefix_withdrawal": 16,
        "bgp_remote_as_mismatch": 16, "iface_admin_shutdown": 16}


def test_remoteas_spans_all_nine_groups(coverage_prepared) -> None:
    sel = select_group_balanced(coverage_prepared(), policy=group_balanced_selection_policy())
    igc = {q.fault_family: q.count for q in independent_group_counts(sel)}
    # the v4-like corpus has 9 remote-AS groups; round-robin covers all of them
    assert igc[_RAS] == 9 >= 8
    ras_examples = [s.example_id for s in sel.selected if s.fault_family == _RAS]
    assert len(ras_examples) == 16 and len(set(ras_examples)) == 16


def test_selection_only_train_and_unique(coverage_prepared) -> None:
    sel = select_group_balanced(coverage_prepared(), policy=group_balanced_selection_policy())
    ids = [s.example_id for s in sel.selected]
    assert len(ids) == len(set(ids)) == 64
    # held-out identities never selected
    assert "ex-ras-val-1" not in ids and "ex-ras-test-1" not in ids


def test_result_revalidates(coverage_prepared) -> None:
    sel = select_group_balanced(coverage_prepared(), policy=group_balanced_selection_policy())
    again = BalancedSelectionResult.model_validate(sel.model_dump())
    assert again.selection_digest == sel.selection_digest
    assert again == sel


def test_policy_id_content_addressed() -> None:
    a = group_balanced_selection_policy()
    b = group_balanced_selection_policy(
        min_groups=(("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
                    ("bgp_remote_as_mismatch", 9), ("iface_admin_shutdown", 1)))
    assert a.policy_id != b.policy_id
    assert isinstance(b, GroupBalancedSelectionPolicy)
