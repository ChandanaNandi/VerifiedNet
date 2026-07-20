"""Gate 20A failure tests: the expansion contracts and firewall fail closed on
frozen collisions, coverage shortfalls, cosmetic-rename aliasing, unsupported
inputs, unbounded/excessive campaigns, and forged inventories."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.datasets.models import DatasetPartition, SplitPolicy
from verifiednet.datasets.splitting import assign_group_split
from verifiednet.experiment.remoteas_expansion import (
    ExpectedIdentity,
    ExpectedIdentityInventory,
    FrozenGroup,
    RemoteAsCampaignPlan,
    RemoteAsExpansionError,
    audit_expansion_firewall,
    build_frozen_inventory,
    plan_remoteas_expansion,
    remoteas_expansion_spec,
    remoteas_identity,
)

pytestmark = pytest.mark.failure

_POL = SplitPolicy(salt="gate6", train_buckets=8000,
                   validation_buckets=1000, test_buckets=1000)


def _frozen(groups=()):
    return build_frozen_inventory(
        "prep-" + "0" * 24,
        groups or (FrozenGroup(group_id="grp-frozenplaceholder", fault_family="x",
                               partition="test", example_count=3),))


def test_spec_rejects_unsupported_topology_and_case() -> None:
    with pytest.raises(ValidationError):
        remoteas_expansion_spec(allowed_topologies=("nope",))
    with pytest.raises(ValidationError):
        remoteas_expansion_spec(allowed_case_ids=("nope",))


def test_coverage_shortfall_fails_closed(remoteas_pool) -> None:
    # freeze EVERY train-bucketing candidate in the pool -> nothing remains
    spec = remoteas_expansion_spec()
    pool = remoteas_pool(40)
    frozen_groups = tuple(
        FrozenGroup(group_id=c.group_id, fault_family="bgp_remote_as_mismatch",
                    partition="train", example_count=3)
        for c in pool
        if assign_group_split(group_id=c.group_id, policy=_POL) is DatasetPartition.TRAIN)
    with pytest.raises(RemoteAsExpansionError, match="available; need"):
        plan_remoteas_expansion(spec, pool, _frozen(frozen_groups), split_policy=_POL)


def test_inventory_rejects_duplicate_planned_group(remoteas_pool) -> None:
    cand = remoteas_pool(1)[0]
    e = ExpectedIdentity(
        case_id=cand.case_id, topology_id=cand.topology_id, group_id=cand.group_id,
        identity=cand.identity, parameter_digest="pdig-x")
    with pytest.raises(ValidationError):
        ExpectedIdentityInventory(
            spec_id="rasexp-x", split_policy_id="split-x", expected=(e, e),
            planned_group_count=2, planned_example_count=4,
            inventory_digest="rasinv-x")


def test_expected_identity_rejects_mismatched_group_id(remoteas_pool) -> None:
    cand = remoteas_pool(1)[0]
    with pytest.raises(ValidationError, match="hash of the identity"):
        ExpectedIdentity(
            case_id=cand.case_id, topology_id=cand.topology_id,
            group_id="grp-forged", identity=cand.identity, parameter_digest="pdig-x")


def test_firewall_catches_cosmetic_rename_alias(remoteas_pool) -> None:
    # forge an inventory whose ExpectedIdentity carries a mismatched group_id via
    # model_construct (bypassing per-model validation) -> firewall must reject.
    spec = remoteas_expansion_spec()
    frozen = _frozen()
    good = plan_remoteas_expansion(spec, remoteas_pool(40), frozen, split_policy=_POL)
    other_identity = remoteas_identity(
        scenario_id="bgp-remote-as-mismatch-alias", target_node="router_a",
        target_session="a-b", parameters={"wrong_asn": 64512},
        topology_hash="9" + "a" * 63, backend="frr-compose")
    forged_entry = good.expected[0].model_copy(update={"identity": other_identity})
    forged = good.model_copy(update={"expected": (forged_entry, *good.expected[1:])})
    fw = audit_expansion_firewall(spec, forged, frozen)
    assert fw.passed is False
    assert any(c.rule == "planned_identities_canonical" and not c.passed
               for c in fw.checks)


def test_firewall_catches_frozen_collision(remoteas_pool) -> None:
    spec = remoteas_expansion_spec()
    inv = plan_remoteas_expansion(spec, remoteas_pool(40), _frozen(), split_policy=_POL)
    collide = FrozenGroup(group_id=inv.expected[0].group_id,
                          fault_family="bgp_remote_as_mismatch",
                          partition="test", example_count=3)
    fw = audit_expansion_firewall(spec, inv, _frozen((collide,)))
    assert fw.passed is False
    assert any(c.rule == "planned_disjoint_from_frozen" and not c.passed
               for c in fw.checks)


def test_campaign_plan_rejects_unbounded_and_excessive_executions() -> None:
    with pytest.raises(ValidationError):
        RemoteAsCampaignPlan(
            spec_id="rasexp-x", expected_inventory_digest="rasinv-x",
            ordered_group_ids=("grp-a", "grp-b"), runs_per_group=2,
            min_accepted_examples=16, max_total_executions=2,
            retry_allowance=0, plan_id="rasplan-x")
    with pytest.raises(ValidationError):
        RemoteAsCampaignPlan(
            spec_id="rasexp-x", expected_inventory_digest="rasinv-x",
            ordered_group_ids=("grp-a", "grp-b"), runs_per_group=2,
            min_accepted_examples=16, max_total_executions=999,
            retry_allowance=1, plan_id="rasplan-x")
