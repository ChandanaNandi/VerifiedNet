"""Gate 20A unit tests: the remote-AS expansion spec, candidate identities,
expected-identity inventory (TRAIN-only, >=8 groups / >=16 examples), frozen
inventory, campaign plan, readiness preview, and append-only plan."""

from __future__ import annotations

import pytest

from verifiednet.datasets.models import SplitPolicy
from verifiednet.datasets.projection import group_id_for_identity
from verifiednet.experiment.remoteas_expansion import (
    MIN_TRAIN_EXAMPLES,
    MIN_TRAIN_GROUPS,
    ExpectedIdentityInventory,
    FrozenGroup,
    audit_expansion_firewall,
    build_append_only_plan,
    build_campaign_plan,
    build_frozen_inventory,
    build_readiness_preview,
    plan_remoteas_expansion,
    remoteas_expansion_spec,
)

pytestmark = pytest.mark.unit

_POL = SplitPolicy(salt="gate6", train_buckets=8000,
                   validation_buckets=1000, test_buckets=1000)


def _frozen():
    return build_frozen_inventory(
        "prep-" + "0" * 24,
        (FrozenGroup(group_id="grp-frozenplaceholder", fault_family="x",
                     partition="test", example_count=3),))


def test_candidate_group_id_is_production_hash(remoteas_pool) -> None:
    pool = remoteas_pool(6)
    assert len(pool) == 6
    for cand in pool:
        assert cand.group_id == group_id_for_identity(cand.identity)
    assert len({c.group_id for c in pool}) == 6  # distinct identities


def test_spec_defaults_and_id() -> None:
    s = remoteas_expansion_spec()
    assert s.spec_id.startswith("rasexp-")
    assert s.template_id == "bgp_remote_as_mismatch"
    assert s.target_partition == "train"
    assert s.requested_group_count == MIN_TRAIN_GROUPS == 8
    assert s.min_accepted_examples == MIN_TRAIN_EXAMPLES == 16


def test_plan_selects_eight_train_groups(remoteas_pool) -> None:
    inv = plan_remoteas_expansion(remoteas_expansion_spec(), remoteas_pool(40),
                                  _frozen(), split_policy=_POL)
    assert inv.planned_group_count == 8
    assert inv.planned_example_count == 16
    assert len({e.group_id for e in inv.expected}) == 8
    assert all(e.assigned_partition == "train" for e in inv.expected)
    again = ExpectedIdentityInventory.model_validate(inv.model_dump())
    assert again.inventory_digest == inv.inventory_digest


def test_firewall_passes_for_a_clean_plan(remoteas_pool) -> None:
    spec = remoteas_expansion_spec()
    frozen = _frozen()
    inv = plan_remoteas_expansion(spec, remoteas_pool(40), frozen, split_policy=_POL)
    fw = audit_expansion_firewall(spec, inv, frozen)
    assert fw.passed is True
    assert not fw.failures


def test_campaign_plan_is_bounded(remoteas_pool) -> None:
    spec = remoteas_expansion_spec()
    inv = plan_remoteas_expansion(spec, remoteas_pool(40), _frozen(), split_policy=_POL)
    plan = build_campaign_plan(spec, inv, retry_allowance=2)
    assert plan.plan_id.startswith("rasplan-")
    assert plan.max_total_executions == 8 * 2 + 2
    assert plan.retry_allowance == 2
    assert len(plan.ordered_group_ids) == 8


def test_readiness_and_append_only(remoteas_pool) -> None:
    spec = remoteas_expansion_spec()
    frozen = _frozen()
    inv = plan_remoteas_expansion(spec, remoteas_pool(40), frozen, split_policy=_POL)
    fw = audit_expansion_firewall(spec, inv, frozen)
    prev = build_readiness_preview(spec, inv, fw)
    assert prev.ready_for_campaign is True
    assert prev.planned_train_groups == 8
    assert prev.planned_train_examples == 16
    assert prev.executed_train_examples == 0
    ap = build_append_only_plan(frozen, inv)
    assert ap.satisfied is True
    assert len(ap.new_train_group_ids) == 8
