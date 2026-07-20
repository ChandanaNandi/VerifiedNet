"""Gate 20C property tests: deterministic group-aware selection, independence
from source enumeration order, group-coverage accounting, and policy sensitivity."""

from __future__ import annotations

import pytest

from verifiednet.training.selection import (
    group_balanced_selection_policy,
    independent_group_counts,
    select_group_balanced,
)

pytestmark = pytest.mark.property

_RAS = "bgp_remote_as_mismatch"


def test_selection_deterministic(coverage_prepared) -> None:
    p = group_balanced_selection_policy()
    a = select_group_balanced(coverage_prepared(), policy=p)
    b = select_group_balanced(coverage_prepared(), policy=p)
    assert a == b
    assert a.selection_digest == b.selection_digest


def test_independent_of_source_order(coverage_prepared) -> None:
    from verifiednet.datasets.prepared import LoadedPrepared
    p = group_balanced_selection_policy()
    prepared = coverage_prepared()
    reversed_prepared = LoadedPrepared(
        manifest=prepared.manifest,
        examples=tuple(reversed(prepared.examples)), by_partition={})
    a = select_group_balanced(prepared, policy=p)
    b = select_group_balanced(reversed_prepared, policy=p)
    assert a.selection_digest == b.selection_digest


def test_group_coverage_at_least_floor(coverage_prepared) -> None:
    p = group_balanced_selection_policy()
    sel = select_group_balanced(coverage_prepared(), policy=p)
    igc = {q.fault_family: q.count for q in independent_group_counts(sel)}
    floors = {q.fault_family: q.count for q in p.min_groups_per_family}
    for fam, floor in floors.items():
        assert igc[fam] >= floor


def test_no_group_overrepresented_when_diversity_available(coverage_prepared) -> None:
    # with 9 remote-AS groups and a 16 quota, no group should contribute more than
    # ceil(16/9)=2 in the round-robin (legacy 4-example group is not drained first)
    sel = select_group_balanced(coverage_prepared(), policy=group_balanced_selection_policy())
    import collections
    per_group = collections.Counter(
        s.group_id for s in sel.selected if s.fault_family == _RAS)
    assert max(per_group.values()) <= 2


def test_policy_quota_sensitivity(coverage_prepared) -> None:
    a = select_group_balanced(coverage_prepared(), policy=group_balanced_selection_policy())
    # a different (still-valid) allocation changes the selection digest
    b = select_group_balanced(
        coverage_prepared(),
        policy=group_balanced_selection_policy(
            allocation=(("bgp_neighbor_removal", 17), ("bgp_prefix_withdrawal", 15),
                        ("bgp_remote_as_mismatch", 16), ("iface_admin_shutdown", 16))))
    assert a.selection_digest != b.selection_digest
