"""Gate 19A property tests: selection is deterministic, independent of input
enumeration order, identity-sensitive to its parameters, and conserves counts
and uniqueness."""

from __future__ import annotations

import pytest

from verifiednet.training.selection import (
    family_balanced_selection_policy,
    select_family_balanced,
)

pytestmark = pytest.mark.property

_AVAIL = {"bgp_neighbor_removal": 40, "bgp_prefix_withdrawal": 40,
          "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 44}


def test_selection_is_deterministic(balanced_prepared) -> None:
    prepared = balanced_prepared(_AVAIL)
    policy = family_balanced_selection_policy()
    a = select_family_balanced(prepared, policy=policy)
    b = select_family_balanced(prepared, policy=policy)
    assert a == b
    assert a.selection_digest == b.selection_digest


def test_independent_of_input_enumeration_order(balanced_prepared) -> None:
    from dataclasses import replace
    prepared = balanced_prepared(_AVAIL)
    prepared_rev = replace(prepared, examples=tuple(reversed(prepared.examples)))
    policy = family_balanced_selection_policy()
    a = select_family_balanced(prepared, policy=policy)
    b = select_family_balanced(prepared_rev, policy=policy)
    assert a.ordered_source_example_ids == b.ordered_source_example_ids
    assert a.selection_digest == b.selection_digest


def test_policy_id_sensitive_to_family_order() -> None:
    a = family_balanced_selection_policy()
    b = family_balanced_selection_policy(allocation=(
        ("bgp_prefix_withdrawal", 20), ("bgp_neighbor_removal", 20),
        ("bgp_remote_as_mismatch", 4), ("iface_admin_shutdown", 20)))
    assert a.policy_id != b.policy_id


def test_digest_sensitive_to_prepared_digest(balanced_prepared) -> None:
    policy = family_balanced_selection_policy()
    a = select_family_balanced(balanced_prepared(_AVAIL, prepared_digest="prep-aaa"),
                               policy=policy)
    b = select_family_balanced(balanced_prepared(_AVAIL, prepared_digest="prep-bbb"),
                               policy=policy)
    assert a.selection_digest != b.selection_digest


def test_count_and_uniqueness_conservation(balanced_prepared) -> None:
    result = select_family_balanced(
        balanced_prepared(_AVAIL), policy=family_balanced_selection_policy())
    assert result.total_count == len(result.selected) == 64
    assert len(set(result.ordered_source_example_ids)) == 64
    assert sum(q.count for q in result.per_family_counts) == 64


def test_round_robin_order_is_stable(balanced_prepared) -> None:
    prepared = balanced_prepared(_AVAIL)
    policy = family_balanced_selection_policy()
    orders = {
        tuple(select_family_balanced(prepared, policy=policy).ordered_source_example_ids)
        for _ in range(3)}
    assert len(orders) == 1


def test_scarcity_exact_availability_succeeds(balanced_prepared) -> None:
    # remote_as availability exactly equals its quota (4)
    result = select_family_balanced(
        balanced_prepared({"bgp_neighbor_removal": 20, "bgp_prefix_withdrawal": 20,
                           "bgp_remote_as_mismatch": 4, "iface_admin_shutdown": 20}),
        policy=family_balanced_selection_policy())
    counts = {q.fault_family: q.count for q in result.per_family_counts}
    assert counts["bgp_remote_as_mismatch"] == 4
    assert result.total_count == 64
