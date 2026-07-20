"""Gate 20C contract tests: content-addressed gbsel ids, extra="forbid", the
group-coverage floor is a bound, the selection binds its prepared corpus, and the
result honours the shared round-robin-by-family invariant."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.training.selection import (
    BalancedSelectionResult,
    GroupBalancedSelectionPolicy,
    group_balanced_selection_policy,
    select_group_balanced,
)

pytestmark = pytest.mark.contract

_RAS = "bgp_remote_as_mismatch"


def test_policy_forbids_extra_fields() -> None:
    payload = group_balanced_selection_policy().model_dump()
    payload["sneaky"] = 1
    with pytest.raises(ValidationError):
        GroupBalancedSelectionPolicy.model_validate(payload)


def test_policy_id_is_deterministic() -> None:
    assert group_balanced_selection_policy().policy_id \
        == group_balanced_selection_policy().policy_id


def test_gbsel_namespace_disjoint_from_fbsel() -> None:
    from verifiednet.training.selection import family_balanced_selection_policy
    g = group_balanced_selection_policy()
    f = family_balanced_selection_policy()
    assert g.policy_id.startswith("gbsel-")
    assert f.policy_id.startswith("fbsel-")
    assert g.policy_id != f.policy_id


def test_quotas_must_sum_to_total() -> None:
    with pytest.raises(ValidationError):
        group_balanced_selection_policy(
            allocation=(("bgp_neighbor_removal", 10), ("bgp_prefix_withdrawal", 16),
                        ("bgp_remote_as_mismatch", 16), ("iface_admin_shutdown", 16)))


def test_group_floor_cannot_exceed_quota() -> None:
    with pytest.raises(ValidationError, match="floor exceeds quota"):
        group_balanced_selection_policy(
            min_groups=(("bgp_neighbor_removal", 1), ("bgp_prefix_withdrawal", 1),
                        ("bgp_remote_as_mismatch", 17), ("iface_admin_shutdown", 1)))


def test_selection_binds_prepared_and_is_round_robin(coverage_prepared) -> None:
    prepared = coverage_prepared()
    sel = select_group_balanced(prepared, policy=group_balanced_selection_policy())
    assert sel.source_prepared_digest == prepared.manifest.prepared_digest
    assert sel.dataset_version == prepared.manifest.dataset_version
    # revalidation re-checks the round-robin-by-family invariant (shared with 19A)
    assert BalancedSelectionResult.model_validate(sel.model_dump()) == sel
    # first four entries cycle the family_order exactly once
    first_families = [s.fault_family for s in sel.selected[:4]]
    assert first_families == list(sel.family_order)
