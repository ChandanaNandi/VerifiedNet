"""Gate 20A property tests: deterministic build-twice equality, enumeration-order
independence, id sensitivity, group uniqueness, collision detection, and
retry-never-creates-a-new-group."""

from __future__ import annotations

import pytest

from verifiednet.datasets.models import SplitPolicy
from verifiednet.experiment.remoteas_expansion import (
    FrozenGroup,
    build_campaign_plan,
    build_frozen_inventory,
    plan_remoteas_expansion,
    remoteas_expansion_spec,
)

pytestmark = pytest.mark.property

_POL = SplitPolicy(salt="gate6", train_buckets=8000,
                   validation_buckets=1000, test_buckets=1000)


def _frozen(groups=()):
    return build_frozen_inventory(
        "prep-" + "0" * 24,
        groups or (FrozenGroup(group_id="grp-frozenplaceholder", fault_family="x",
                               partition="test", example_count=3),))


def test_plan_is_deterministic(remoteas_pool) -> None:
    pool = remoteas_pool(40)
    a = plan_remoteas_expansion(remoteas_expansion_spec(), pool, _frozen(),
                                split_policy=_POL)
    b = plan_remoteas_expansion(remoteas_expansion_spec(), pool, _frozen(),
                                split_policy=_POL)
    assert a == b
    assert a.inventory_digest == b.inventory_digest


def test_independent_of_pool_input_order(remoteas_pool) -> None:
    pool = remoteas_pool(40)
    a = plan_remoteas_expansion(remoteas_expansion_spec(), pool, _frozen(),
                                split_policy=_POL)
    b = plan_remoteas_expansion(remoteas_expansion_spec(), tuple(reversed(pool)),
                                _frozen(), split_policy=_POL)
    assert a.inventory_digest == b.inventory_digest


def test_id_sensitive_to_quota() -> None:
    a = remoteas_expansion_spec()
    b = remoteas_expansion_spec(requested_group_count=10, min_accepted_examples=20)
    assert a.spec_id != b.spec_id


def test_planned_groups_unique_and_independent(remoteas_pool) -> None:
    inv = plan_remoteas_expansion(remoteas_expansion_spec(), remoteas_pool(40),
                                  _frozen(), split_policy=_POL)
    gids = [e.group_id for e in inv.expected]
    assert len(set(gids)) == len(gids) == inv.planned_group_count


def test_collision_detection_excludes_a_frozen_candidate(remoteas_pool) -> None:
    spec = remoteas_expansion_spec()
    pool = remoteas_pool(40)
    first = plan_remoteas_expansion(spec, pool, _frozen(), split_policy=_POL).expected[0]
    collide = FrozenGroup(group_id=first.group_id,
                          fault_family="bgp_remote_as_mismatch",
                          partition="validation", example_count=3)
    inv2 = plan_remoteas_expansion(spec, pool, _frozen((collide,)), split_policy=_POL)
    assert first.group_id not in {e.group_id for e in inv2.expected}
    assert inv2.planned_group_count == 8


def test_retry_allowance_does_not_add_groups(remoteas_pool) -> None:
    spec = remoteas_expansion_spec()
    inv = plan_remoteas_expansion(spec, remoteas_pool(40), _frozen(), split_policy=_POL)
    p0 = build_campaign_plan(spec, inv, retry_allowance=0)
    p2 = build_campaign_plan(spec, inv, retry_allowance=4)
    assert p0.ordered_group_ids == p2.ordered_group_ids
    assert p2.max_total_executions == p0.max_total_executions + 4
