"""Gate 20A contract tests: frozen content-addressed identities, extra="forbid",
reuse of the production identity/split functions, TRAIN assignment fixed before
execution, the approved sets matching the live catalog, and no frozen mutation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.datasets.models import DatasetPartition, SplitPolicy
from verifiednet.datasets.projection import group_id_for_identity
from verifiednet.datasets.splitting import assign_group_split
from verifiednet.experiment.remoteas_expansion import (
    APPROVED_REMOTEAS_CASE_IDS,
    APPROVED_TOPOLOGY_IDS,
    FrozenGroup,
    RemoteAsExpansionSpec,
    audit_expansion_firewall,
    build_frozen_inventory,
    plan_remoteas_expansion,
    remoteas_expansion_spec,
)

pytestmark = pytest.mark.contract

_POL = SplitPolicy(salt="gate6", train_buckets=8000,
                   validation_buckets=1000, test_buckets=1000)


def _frozen():
    return build_frozen_inventory(
        "prep-" + "0" * 24,
        (FrozenGroup(group_id="grp-frozenplaceholder", fault_family="x",
                     partition="test", example_count=3),))


def test_approved_sets_match_the_live_catalog() -> None:
    # the frozen approved sets equal the live orchestrator catalog / topologies
    from verifiednet.orchestrator.expansion import (
        GATE14B_RAS_CASE_IDS,
        GATE14B_TOPOLOGY_FACTORIES,
    )
    assert APPROVED_REMOTEAS_CASE_IDS == GATE14B_RAS_CASE_IDS
    assert set(APPROVED_TOPOLOGY_IDS) == set(GATE14B_TOPOLOGY_FACTORIES)


def test_spec_forbids_extra_fields() -> None:
    payload = remoteas_expansion_spec().model_dump()
    payload["sneaky"] = 1
    with pytest.raises(ValidationError):
        RemoteAsExpansionSpec.model_validate(payload)


def test_spec_id_is_content_addressed() -> None:
    a = remoteas_expansion_spec()
    assert remoteas_expansion_spec().spec_id == a.spec_id
    c = remoteas_expansion_spec(requested_group_count=9, min_accepted_examples=18)
    assert c.spec_id != a.spec_id


def test_uses_production_split_and_hash(remoteas_pool) -> None:
    inv = plan_remoteas_expansion(remoteas_expansion_spec(), remoteas_pool(40),
                                  _frozen(), split_policy=_POL)
    for e in inv.expected:
        assert e.group_id == group_id_for_identity(e.identity)
        assert assign_group_split(group_id=e.group_id, policy=_POL) \
            is DatasetPartition.TRAIN


def test_topology_and_case_sets_are_locked() -> None:
    with pytest.raises(ValidationError):
        remoteas_expansion_spec(allowed_topologies=("2r-v1", "not-a-topology"))
    with pytest.raises(ValidationError):
        remoteas_expansion_spec(allowed_case_ids=("ras-ref", "not-a-case"))


def test_planned_groups_disjoint_from_frozen(remoteas_pool) -> None:
    spec = remoteas_expansion_spec()
    frozen = _frozen()
    inv = plan_remoteas_expansion(spec, remoteas_pool(40), frozen, split_policy=_POL)
    fw = audit_expansion_firewall(spec, inv, frozen)
    assert all(e.group_id not in frozen.group_ids for e in inv.expected)
    assert next(c for c in fw.checks if c.rule == "planned_disjoint_from_frozen").passed
