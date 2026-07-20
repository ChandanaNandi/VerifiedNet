"""Optional Gate 20B operational campaign: the REAL bounded remote-AS run
campaign on the live FRR lab + append-only v4 registration.

Executes the Gate 20A preregistered plan (8 unused, TRAIN-assigned, disjoint
remote-AS identities) on the real two-router FRR lab via the PRODUCTION entry
point ``run_accepted_incident``: 2 verified executions per group = 16 intended
accepted examples, bounded by the campaign plan's ``max_total_executions``. Every
accepted run's projected ``group_id`` is verified to equal its planned identity;
the 16 verified runs are projected through the unchanged dataset pipeline and
APPENDED to the frozen v3 prepared corpus, proving byte-for-byte append-only
integrity (v3 rows unchanged, held-out partitions untouched, no frozen-group
collision) and Gate 20C readiness. All artifacts are written OUTSIDE the
repository; the v3 chain is fingerprinted immutable before and after.

DOUBLE-GATED: the ``integration`` marker AND ``VERIFIEDNET_RUN_GATE20B=1`` plus a
v3 artifact root and an output root, and it needs a working Docker/FRR lab. Skips
by default so offline CI never touches the lab or the chain.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_GATE20B") == "1"
_V3_ROOT = os.environ.get("VERIFIEDNET_GATE20B_V3_ROOT", "")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE20B_OUTPUT_ROOT", "")
_RETRY_ALLOWANCE = int(os.environ.get("VERIFIEDNET_GATE20B_RETRY_ALLOWANCE", "2"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_ENABLED and _V3_ROOT and Path(_V3_ROOT).is_dir() and _OUT_ROOT),
        reason="Gate 20B campaign is opt-in and needs VERIFIEDNET_RUN_GATE20B=1, a "
               "v3 artifact root, an output root, and a live FRR lab"),
]

_RAS = "bgp_remote_as_mismatch"
_SPLIT_KW = dict(salt="gate6", train_buckets=8000, validation_buckets=1000,
                 test_buckets=1000)


def _fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _real_pool(split_policy):
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
    scen_by_case = {}
    topo_by_id = {}
    for case_id, topology_id in product(APPROVED_REMOTEAS_CASE_IDS,
                                        APPROVED_TOPOLOGY_IDS):
        scenario = case_by_id(case_id).scenario
        params = scenario.parameters
        topology = expansion_topology(topology_id)
        scen_by_case[case_id] = scenario
        topo_by_id[topology_id] = topology
        identity = remoteas_identity(
            scenario_id=scenario.scenario_id,
            target_node=str(params.get("target_node", "")),
            target_session=str(params.get("target_session", "")),
            parameters={k: params[k] for k in params},
            topology_hash=sha256_canonical(topology), backend=topology.backend)
        pool.append(RemoteAsCandidate(
            case_id=case_id, topology_id=topology_id, identity=identity))
    return tuple(pool), scen_by_case, topo_by_id


def _classify(observed: str | None, planned: str, *, verified: bool,
              collided: bool) -> tuple[bool, str]:
    """(accepted, failure_category). Never manually asserts acceptance: a run is
    accepted only if it verified, its projected group matches the planned
    identity, and it did not collide with an existing accepted output."""
    if not verified:
        return False, "verification"
    if observed is None:
        return False, "evidence_collection"
    if observed != planned:
        return False, "unexpected_group_id"
    if collided:
        return False, "output_collision"
    return True, ""


def test_gate20b_remoteas_campaign_and_append_only_v4(
    unique_run_id: Callable[[str], str],
    project_containers: Callable[[str], list[str]],
    project_networks: Callable[[str], list[str]],
) -> None:
    from verifiednet.artifacts.index import load_run_index
    from verifiednet.common.canonical import canonical_json_bytes
    from verifiednet.common.hashing import sha256_canonical, sha256_file
    from verifiednet.common.runctx import RunContext
    from verifiednet.datasets import load_prepared
    from verifiednet.datasets.discovery import discover_verified_runs
    from verifiednet.datasets.features import FeaturePolicy, LabelPolicy
    from verifiednet.datasets.models import (
        AssignedDatasetExample,
        DatasetExampleKind,
        DatasetPartition,
        SplitPolicy,
    )
    from verifiednet.datasets.prepared import build_prepared, write_prepared
    from verifiednet.datasets.projection import project_verified_run
    from verifiednet.datasets.separation import separate_example
    from verifiednet.datasets.splitting import assign_group_split, split_policy_id
    from verifiednet.experiment.remoteas_campaign import (
        RemoteAsRunRecord,
        assess_v4_readiness,
        build_campaign_result,
        compute_append_only_diff,
    )
    from verifiednet.experiment.remoteas_expansion import (
        MIN_TRAIN_EXAMPLES,
        MIN_TRAIN_GROUPS,
        FrozenGroup,
        audit_expansion_firewall,
        build_campaign_plan,
        build_frozen_inventory,
        plan_remoteas_expansion,
        remoteas_expansion_spec,
    )
    from verifiednet.labs.frr.compose_project import project_name_for_run
    from verifiednet.orchestrator import run_accepted_incident
    from verifiednet.runtime.process import default_runner

    v3 = Path(_V3_ROOT)
    out_root = Path(_OUT_ROOT)
    if out_root.exists() and any(out_root.iterdir()):
        pytest.skip(f"output root already populated: {out_root}")
    prepared_dir = v3 / "chain" / "prepared"
    before = _fingerprint(v3)

    split_policy = SplitPolicy(**_SPLIT_KW)
    v3_prepared = load_prepared(prepared_dir)

    # ---- frozen inventory + frozen remote-AS groups from the real v3 corpus ----
    import collections

    fam: dict[str, str] = {}
    part: dict[str, str] = {}
    count: collections.Counter = collections.Counter()
    for ex in v3_prepared.examples:
        if ex.trace.example_kind is not DatasetExampleKind.ACCEPTED_FAULT:
            continue
        gid = ex.trace.group_id
        fam[gid] = ex.labels.fault_family
        part[gid] = ex.trace.partition.value
        count[gid] += 1
    frozen_groups = tuple(
        FrozenGroup(group_id=g, fault_family=fam[g], partition=part[g],
                    example_count=count[g]) for g in sorted(fam))
    frozen = build_frozen_inventory(v3_prepared.manifest.prepared_digest,
                                    frozen_groups)
    frozen_ras = frozenset(g for g in fam if fam[g] == _RAS)

    # ---- plan the 8 unused, TRAIN, disjoint identities + firewall + bound ------
    pool, scen_by_case, topo_by_id = _real_pool(split_policy)
    spec = remoteas_expansion_spec()
    inventory = plan_remoteas_expansion(spec, pool, frozen,
                                        split_policy=split_policy)
    firewall = audit_expansion_firewall(spec, inventory, frozen)
    assert firewall.passed, [c for c in firewall.checks if not c.passed]
    assert inventory.planned_group_count >= MIN_TRAIN_GROUPS
    plan = build_campaign_plan(spec, inventory, retry_allowance=_RETRY_ALLOWANCE)

    # ---- execute the bounded live campaign -------------------------------------
    new_runs_root = out_root / "chain" / "new-runs"
    new_runs_root.mkdir(parents=True)
    commit = default_runner(["git", "rev-parse", "HEAD"], 10.0, 4096).stdout.strip() \
        or "unknown"
    lock = Path("uv.lock")
    lock_hash = sha256_file(lock) if lock.is_file() else "0" * 64

    records: list[RemoteAsRunRecord] = []
    accepted_run_ids: dict[str, str] = {}  # run_id -> group_id, for later projection
    seen_outputs: set[str] = set()
    total = 0
    for expected in inventory.expected:
        scenario = scen_by_case[expected.case_id]
        topology = topo_by_id[expected.topology_id]
        exec_index = 0
        accepted_for_group = 0
        # run until this identity has runs_per_group ACCEPTED runs or the bounded
        # execution budget is exhausted; a retry re-runs the SAME identity and is
        # the only thing beyond the first runs_per_group executions of the group.
        while (accepted_for_group < spec.runs_per_group
               and total < plan.max_total_executions):
            exec_index += 1
            total += 1
            attempt = max(1, exec_index - spec.runs_per_group + 1)
            run_id = unique_run_id(f"g20b-{expected.case_id}-{expected.topology_id}")
            project = project_name_for_run(run_id)
            verified = False
            observed: str | None = None
            run_digest = ""
            try:
                result = run_accepted_incident(
                    out_root=new_runs_root,
                    work_dir=out_root / "lab" / run_id,
                    run_ctx=RunContext(run_id),
                    topology=topology, scenario=scenario,
                    git_rev=commit, lock_hash=lock_hash,
                    convergence_timeout_s=60.0)
                assembled = result.assembled
                run_digest = assembled.run_digest
                # project this single verified run to read its true group_id
                ex = project_verified_run(next(
                    d for d in discover_verified_runs(new_runs_root)
                    if d.loaded.run_id == run_id))
                observed = ex.group_id
                verified = (ex.example_kind is DatasetExampleKind.ACCEPTED_FAULT
                            and assign_group_split(group_id=observed,
                                                   policy=split_policy)
                            is DatasetPartition.TRAIN)
            finally:
                assert project_containers(project) == []
                assert project_networks(project) == []
            collided = observed is not None and observed in seen_outputs \
                and observed != expected.group_id
            accepted, failure = _classify(observed, expected.group_id,
                                          verified=verified, collided=collided)
            if accepted:
                accepted_for_group += 1
                accepted_run_ids[run_id] = observed  # type: ignore[assignment]
                seen_outputs.add(observed)  # type: ignore[arg-type]
            records.append(RemoteAsRunRecord(
                planned_group_id=expected.group_id, case_id=expected.case_id,
                topology_id=expected.topology_id, attempt=attempt, run_id=run_id,
                run_digest=run_digest or "unverified", observed_group_id=observed
                or "none", verified=verified, accepted=accepted,
                failure_category=failure))

    campaign = build_campaign_result(plan, inventory, tuple(records))
    assert campaign.verified_group_count >= MIN_TRAIN_GROUPS, campaign.model_dump()
    assert campaign.accepted_example_count >= MIN_TRAIN_EXAMPLES
    assert campaign.coverage_ok is True
    assert campaign.total_executions <= plan.max_total_executions

    # ---- project every accepted run + append-only v4 build ---------------------
    new_separated = []
    for d in discover_verified_runs(new_runs_root):
        if d.loaded.run_id not in accepted_run_ids:
            continue
        ex = project_verified_run(d)
        assert ex.group_id == accepted_run_ids[d.loaded.run_id]
        partition = assign_group_split(group_id=ex.group_id, policy=split_policy)
        assert partition is DatasetPartition.TRAIN
        assert ex.group_id not in frozen_ras
        assigned = AssignedDatasetExample(
            example=ex, partition=partition,
            split_policy_id=split_policy_id(split_policy))
        new_separated.append(separate_example(
            assigned, feature_policy=FeaturePolicy(), label_policy=LabelPolicy(),
            dataset_version="v4-remoteas-expansion",
            source_index_digest=load_run_index(new_runs_root).index_digest))
    assert len(new_separated) == campaign.accepted_example_count

    v4_export = build_prepared(
        (*v3_prepared.examples, *new_separated),
        feature_policy=FeaturePolicy(), label_policy=LabelPolicy(),
        dataset_version="v4-remoteas-expansion",
        source_index_digest=load_run_index(new_runs_root).index_digest,
        source_dataset_digest="v4-" + v3_prepared.manifest.source_dataset_digest)
    v4_prepared_dir = out_root / "chain" / "prepared"
    write_prepared(v4_export, v4_prepared_dir)
    v4_prepared = load_prepared(v4_prepared_dir)

    diff = compute_append_only_diff(v3_prepared, v4_prepared,
                                    frozen_remoteas_group_ids=frozen_ras)
    assert diff.append_only is True, [c for c in diff.checks if not c.passed]
    assert diff.unchanged_v3_rows == len(v3_prepared.examples)
    assert diff.modified_prior_rows == 0 and diff.removed_prior_rows == 0
    assert diff.prior_partition_changes == 0 and diff.heldout_changed_rows == 0
    assert diff.frozen_group_collisions == 0
    assert diff.appended_accepted == campaign.accepted_example_count
    assert diff.new_group_count >= MIN_TRAIN_GROUPS

    remoteas_train_after = frozenset(
        e.trace.group_id for e in v4_prepared.examples
        if e.trace.example_kind is DatasetExampleKind.ACCEPTED_FAULT
        and e.labels.fault_family == _RAS
        and e.trace.partition is DatasetPartition.TRAIN)
    remoteas_train_examples_after = sum(
        1 for e in v4_prepared.examples
        if e.trace.example_kind is DatasetExampleKind.ACCEPTED_FAULT
        and e.labels.fault_family == _RAS
        and e.trace.partition is DatasetPartition.TRAIN)
    readiness = assess_v4_readiness(
        campaign, diff,
        remoteas_train_groups_after=len(remoteas_train_after),
        remoteas_train_examples_after=remoteas_train_examples_after,
        leakage_clean=firewall.passed, v2_derivation_ok=True)
    assert readiness.ready_for_gate20c is True, \
        [c for c in readiness.checks if not c.passed]

    # ---- persist the append-only v4 lineage artifacts (outside the repo) --------
    reg = out_root / "gate20b"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "campaign-result.json").write_bytes(canonical_json_bytes(campaign))
    (reg / "append-only-diff.json").write_bytes(canonical_json_bytes(diff))
    (reg / "readiness.json").write_bytes(canonical_json_bytes(readiness))
    lineage = {
        "v3_prepared_digest": v3_prepared.manifest.prepared_digest,
        "v4_prepared_digest": v4_prepared.manifest.prepared_digest,
        "parent_dataset_version": v3_prepared.manifest.dataset_version,
        "v4_dataset_version": v4_prepared.manifest.dataset_version,
        "appended_accepted": diff.appended_accepted,
        "new_train_groups": sorted(
            {s.trace.group_id for s in new_separated}),
        "campaign_result_id": campaign.result_id,
        "readiness_id": readiness.result_id}
    (reg / "v4-lineage.json").write_bytes(
        canonical_json_bytes(lineage))  # content: append-only v4 -> v3
    lineage_digest = "v4lin-" + sha256_canonical(lineage)[:24]

    # ---- the v3 chain is byte-identical before and after -----------------------
    assert _fingerprint(v3) == before

    print(f"GATE20B: spec={spec.spec_id} plan={plan.plan_id} "
          f"executions={campaign.total_executions}/{plan.max_total_executions} "
          f"retries={campaign.retry_count} verified_groups="
          f"{campaign.verified_group_count} accepted={campaign.accepted_example_count} "
          f"v3_prepared={v3_prepared.manifest.prepared_digest[:16]} "
          f"v4_prepared={v4_prepared.manifest.prepared_digest[:16]} "
          f"append_only={diff.append_only} unchanged={diff.unchanged_v3_rows} "
          f"appended={diff.appended_accepted} new_groups={diff.new_group_count} "
          f"ras_train_groups_after={len(remoteas_train_after)} "
          f"ready={readiness.ready_for_gate20c} lineage={lineage_digest} "
          f"result_id={campaign.result_id} readiness_id={readiness.result_id}")
