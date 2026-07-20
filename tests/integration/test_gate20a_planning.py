"""Optional Gate 20A real-chain PLANNING proof (read-only): build the real
remote-AS candidate pool from the live catalog, prove it reproduces every frozen
v3 remote-AS group_id, reproduce the authoritative counts, and plan >= 8 UNUSED
remote-AS TRAIN identities disjoint from all frozen groups and TRAIN-assigned by
the production splitter. Creates NO scenario, run, dataset, corpus, experiment, or
model artifact.

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE20A=1`` plus a
v3 artifact root. Skips by default.
"""

from __future__ import annotations

import collections
import hashlib
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE20A") == "1"
_V3_ROOT = os.environ.get("VERIFIEDNET_GATE20A_V3_ROOT", "")
_PRIOR_DIRS = os.environ.get("VERIFIEDNET_GATE20A_PRIOR_ARTIFACT_DIRS", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_ENABLED and _V3_ROOT and Path(_V3_ROOT).is_dir()),
        reason="Gate 20A planning proof is opt-in and needs VERIFIEDNET_RUN_GATE20A=1 "
               "and a v3 artifact root"),
]

_RAS = "bgp_remote_as_mismatch"


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _real_pool():
    """Build the real remote-AS candidate pool from the live catalog + topologies
    (test/harness scope may import the composition root)."""
    from itertools import product

    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.experiment.remoteas_expansion import (
        APPROVED_REMOTEAS_CASE_IDS,
        APPROVED_TOPOLOGY_IDS,
        RemoteAsCandidate,
        remoteas_identity,
    )
    from verifiednet.orchestrator.catalog import case_by_id
    from verifiednet.orchestrator.expansion import expansion_topology

    pool = []
    for case_id, topology_id in product(APPROVED_REMOTEAS_CASE_IDS,
                                        APPROVED_TOPOLOGY_IDS):
        scenario = case_by_id(case_id).scenario
        params = scenario.parameters
        topology = expansion_topology(topology_id)
        identity = remoteas_identity(
            scenario_id=scenario.scenario_id,
            target_node=str(params.get("target_node", "")),
            target_session=str(params.get("target_session", "")),
            parameters={k: params[k] for k in params},
            topology_hash=sha256_canonical(topology), backend=topology.backend)
        pool.append(RemoteAsCandidate(
            case_id=case_id, topology_id=topology_id, identity=identity))
    return tuple(pool)


def test_gate20a_remoteas_expansion_planning_on_v3_chain() -> None:
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.models import (
        DatasetExampleKind,
        DatasetPartition,
        SplitPolicy,
    )
    from verifiednet.datasets.splitting import assign_group_split
    from verifiednet.experiment.remoteas_expansion import (
        MIN_TRAIN_EXAMPLES,
        MIN_TRAIN_GROUPS,
        FrozenGroup,
        audit_expansion_firewall,
        build_append_only_plan,
        build_campaign_plan,
        build_frozen_inventory,
        build_readiness_preview,
        plan_remoteas_expansion,
        remoteas_expansion_spec,
    )

    v3 = Path(_V3_ROOT)
    prepared_dir = v3 / "chain" / "prepared"
    before = _fingerprint(v3)
    prior_roots = [Path(p) for p in _PRIOR_DIRS.split(os.pathsep) if p]
    prior_before = {str(p): _fingerprint(p) for p in prior_roots if p.is_dir()}

    prepared = load_prepared(prepared_dir)
    split_policy = SplitPolicy(salt="gate6", train_buckets=8000,
                               validation_buckets=1000, test_buckets=1000)

    group_family: dict[str, str] = {}
    group_partition: dict[str, str] = {}
    group_count: collections.Counter = collections.Counter()
    ras_by_part: collections.Counter = collections.Counter()
    ras_groups_by_part: collections.defaultdict = collections.defaultdict(set)
    for ex in prepared.examples:
        if ex.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        gid = ex.trace.group_id
        group_family[gid] = ex.labels.fault_family
        group_partition[gid] = ex.trace.partition.value
        group_count[gid] += 1
        if ex.labels.fault_family == _RAS:
            ras_by_part[ex.trace.partition.value] += 1
            ras_groups_by_part[ex.trace.partition.value].add(gid)

    assert sum(ras_by_part.values()) == 67, dict(ras_by_part)
    assert ras_by_part["train"] == 4 and len(ras_groups_by_part["train"]) == 1
    assert ras_by_part["validation"] == 33 and len(ras_groups_by_part["validation"]) == 11
    assert ras_by_part["test"] == 30 and len(ras_groups_by_part["test"]) == 10
    frozen_ras_groups = (ras_groups_by_part["train"] | ras_groups_by_part["validation"]
                         | ras_groups_by_part["test"])
    assert len(frozen_ras_groups) == 22

    frozen_groups = tuple(
        FrozenGroup(group_id=g, fault_family=group_family[g],
                    partition=group_partition[g], example_count=group_count[g])
        for g in sorted(group_family))
    frozen = build_frozen_inventory(prepared.manifest.prepared_digest, frozen_groups)

    # ---- real pool reproduces every frozen remote-AS group id ---------------
    pool = _real_pool()
    assert len(pool) == 60
    derived_buckets: collections.Counter = collections.Counter()
    reproduced = 0
    for cand in pool:
        derived_buckets[
            assign_group_split(group_id=cand.group_id, policy=split_policy).value] += 1
        if cand.group_id in frozen_ras_groups:
            reproduced += 1
    assert reproduced == 22, reproduced

    # ---- plan >= 8 unused TRAIN identities; firewall + bounds ---------------
    spec = remoteas_expansion_spec()
    inventory = plan_remoteas_expansion(spec, pool, frozen, split_policy=split_policy)
    firewall = audit_expansion_firewall(spec, inventory, frozen)
    assert firewall.passed is True, [c for c in firewall.checks if not c.passed]
    assert inventory.planned_group_count >= MIN_TRAIN_GROUPS
    assert inventory.planned_example_count >= MIN_TRAIN_EXAMPLES
    planned = {e.group_id for e in inventory.expected}
    assert planned.isdisjoint(frozen.group_ids)
    for e in inventory.expected:
        assert assign_group_split(group_id=e.group_id, policy=split_policy) \
            is DatasetPartition.TRAIN

    plan = build_campaign_plan(spec, inventory, retry_allowance=2)
    assert plan.max_total_executions == len(planned) * spec.runs_per_group + 2
    preview = build_readiness_preview(spec, inventory, firewall)
    assert preview.ready_for_campaign is True
    append_only = build_append_only_plan(frozen, inventory)
    assert append_only.satisfied is True

    assert _fingerprint(v3) == before
    for p in prior_roots:
        if p.is_dir():
            assert _fingerprint(p) == prior_before[str(p)], f"mutated prior: {p}"

    print(f"GATE20A: ras_by_partition={dict(ras_by_part)} frozen_ras_groups=22 "
          f"derived_bucket_dist={dict(derived_buckets)} reproduced_frozen={reproduced}/22 "
          f"planned_train_groups={inventory.planned_group_count} "
          f"planned_train_examples={inventory.planned_example_count} "
          f"firewall_passed={firewall.passed} spec_id={spec.spec_id} "
          f"plan_id={plan.plan_id}")
