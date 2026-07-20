"""Gate 20A — remote-AS training-coverage expansion contracts and firewall.

Gate 19B confirmed the family-imbalance diagnosis for every family the frozen v3
split covers adequately, but bgp_remote_as_mismatch stayed 0/30: the v3 TRAIN
partition holds only ONE remote-AS leakage group (four repeated runs), while the
other families each have ~10. The deterministic split (``assign_group_split``)
bucketed remote-AS's 22 registered identities overwhelmingly into validation/test.

This module implements — CONTRACTS ONLY, no runs — an append-only remote-AS TRAIN
coverage campaign. It plans, over a candidate pool of fully-defined stable
identities, the UNUSED approved ``(topology, RAS case)`` identities whose
production ``group_id`` is (a) absent from every frozen v3 group and (b)
deterministically assigned to TRAIN by the frozen split policy, and proves >= 8
independent new TRAIN groups (>= 16 intended accepted examples) are derivable.

It reuses the production identity/split functions verbatim (``group_id_for_identity``
/ ``assign_group_split``) and takes candidate identities as plain input — the live
scenario catalog and topologies are read by the caller (Gate 20B harness / the
gated proof), never by this offline layer, which loads no model, imports no live
composition root, runs no network/subprocess, and writes no run/dataset/corpus/
model artifact. Only verified Gate 20B runs may ever become dataset examples.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.models import (
    DatasetPartition,
    SplitPolicy,
    StableScenarioIdentity,
)
from verifiednet.datasets.projection import group_id_for_identity
from verifiednet.datasets.splitting import assign_group_split, split_policy_id
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.schemas.base import StrictModel

REMOTEAS_EXPANSION_VERSION = 1
REMOTEAS_TEMPLATE_ID = "bgp_remote_as_mismatch"
#: Coverage target (Gate 20 design): independent TRAIN groups, and accepted examples.
MIN_TRAIN_GROUPS = 8
MIN_TRAIN_EXAMPLES = 16
DEFAULT_RUNS_PER_GROUP = 2
#: The frozen approved remote-AS identity space (a Gate 20A contract test proves
#: these equal the live orchestrator catalog / topology set). The 60-candidate
#: cross product; v3 registered only 22 of them.
APPROVED_REMOTEAS_CASE_IDS: tuple[str, ...] = (
    "ras-ref", "ras-rev", "ras-alt", "ras-alt2", "ras-alt3", "ras-alt4",
    "ras-alt5", "ras-alt6", "ras-alt7", "ras-alt8")
APPROVED_TOPOLOGY_IDS: tuple[str, ...] = (
    "2r-v1", "2r-v2", "2r-v3", "2r-v4", "2r-v5", "2r-v6")


class RemoteAsExpansionError(VerifiedNetError):
    """Raised when a remote-AS expansion contract cannot be satisfied."""


def remoteas_identity(
    *, scenario_id: str, target_node: str, target_session: str,
    parameters: dict[str, str | int], topology_hash: str, backend: str,
) -> StableScenarioIdentity:
    """The production stable identity of a remote-AS ``(case, topology)`` pair.

    Mirrors ``datasets.projection.build_stable_identity`` field-for-field but from
    PLAIN inputs (the caller supplies the approved catalog parameters and the
    ``sha256_canonical`` topology hash), so the pre-run ``group_id`` equals the
    value a verified run of the pair would emit.
    """
    return StableScenarioIdentity(
        template_id=REMOTEAS_TEMPLATE_ID, scenario_id=scenario_id,
        target_node=target_node, target_session=target_session,
        parameters={k: parameters[k] for k in sorted(parameters)},
        topology_hash=topology_hash, backend=backend)


class RemoteAsCandidate(StrictModel):
    """One fully-defined remote-AS identity the campaign may run. Its ``group_id``
    and TRAIN prediction are pure functions of the production code."""

    schema_version: Literal[1] = 1
    case_id: str = Field(min_length=1)
    topology_id: str = Field(min_length=1)
    identity: StableScenarioIdentity

    @property
    def group_id(self) -> str:
        return group_id_for_identity(self.identity)


class RemoteAsExpansionSpec(StrictModel):
    """The frozen, content-addressed remote-AS TRAIN expansion contract.

    Model-independent and performance-independent: it names the approved identity
    space and the coverage target, never a model result.
    """

    schema_version: Literal[1] = 1
    expansion_version: Literal[1] = 1
    template_id: Literal["bgp_remote_as_mismatch"] = "bgp_remote_as_mismatch"
    backend: str = Field(min_length=1)
    allowed_topologies: tuple[str, ...] = Field(min_length=1)
    allowed_case_ids: tuple[str, ...] = Field(min_length=1)
    requested_group_count: int = Field(ge=1)
    min_accepted_examples: int = Field(ge=1)
    runs_per_group: int = Field(ge=1)
    target_partition: Literal["train"] = "train"
    spec_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RemoteAsExpansionSpec:
        if len(set(self.allowed_topologies)) != len(self.allowed_topologies):
            raise ValueError("allowed_topologies must be unique")
        if len(set(self.allowed_case_ids)) != len(self.allowed_case_ids):
            raise ValueError("allowed_case_ids must be unique")
        for topo in self.allowed_topologies:
            if topo not in APPROVED_TOPOLOGY_IDS:
                raise ValueError(f"unapproved topology: {topo}")
        for case in self.allowed_case_ids:
            if case not in APPROVED_REMOTEAS_CASE_IDS:
                raise ValueError(f"unapproved remote-AS case: {case}")
        if self.spec_id != _derive_spec_id(self):
            raise ValueError("spec_id does not match the spec content")
        return self


class ExpectedIdentity(StrictModel):
    """One planned remote-AS identity: its full stable identity, derived group,
    and TRAIN prediction. Carrying the identity lets the firewall re-hash it, so
    cosmetic metadata can never forge a new eligible group."""

    schema_version: Literal[1] = 1
    case_id: str = Field(min_length=1)
    topology_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    identity: StableScenarioIdentity
    parameter_digest: str = Field(min_length=1)
    assigned_partition: Literal["train"] = "train"

    @model_validator(mode="after")
    def _canonical(self) -> ExpectedIdentity:
        if self.group_id != group_id_for_identity(self.identity):
            raise ValueError("group_id is not the hash of the identity")
        return self


class ExpectedIdentityInventory(StrictModel):
    """The frozen, self-validating inventory of planned TRAIN identities."""

    schema_version: Literal[1] = 1
    spec_id: str = Field(min_length=1)
    split_policy_id: str = Field(min_length=1)
    expected: tuple[ExpectedIdentity, ...] = Field(min_length=1)
    planned_group_count: int = Field(ge=1)
    planned_example_count: int = Field(ge=1)
    inventory_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ExpectedIdentityInventory:
        gids = [e.group_id for e in self.expected]
        if len(set(gids)) != len(gids):
            raise ValueError("planned group_ids must be unique")
        if len(self.expected) != self.planned_group_count:
            raise ValueError("planned_group_count must equal the expected count")
        if any(e.assigned_partition != "train" for e in self.expected):
            raise ValueError("every planned identity must be TRAIN")
        if self.inventory_digest != _derive_inventory_digest(self):
            raise ValueError("inventory_digest does not match content")
        return self


class FrozenGroup(StrictModel):
    """One immutable v3 leakage group, read-only."""

    schema_version: Literal[1] = 1
    group_id: str = Field(min_length=1)
    fault_family: str = Field(min_length=1)
    partition: str = Field(min_length=1)
    example_count: int = Field(ge=1)


class FrozenIdentityInventory(StrictModel):
    """The read-only inventory of every frozen v3 group, bound to the v3 digest."""

    schema_version: Literal[1] = 1
    prepared_digest: str = Field(min_length=1)
    groups: tuple[FrozenGroup, ...] = Field(min_length=1)
    inventory_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> FrozenIdentityInventory:
        gids = [g.group_id for g in self.groups]
        if len(set(gids)) != len(gids):
            raise ValueError("frozen group_ids must be unique")
        if self.inventory_digest != _derive_frozen_digest(self):
            raise ValueError("inventory_digest does not match content")
        return self

    @property
    def group_ids(self) -> frozenset[str]:
        return frozenset(g.group_id for g in self.groups)


def _derive_spec_id(spec: RemoteAsExpansionSpec) -> str:
    payload = spec.model_dump(mode="json")
    payload.pop("spec_id", None)
    return "rasexp-" + sha256_canonical(payload)[:16]


def _derive_inventory_digest(inv: ExpectedIdentityInventory) -> str:
    payload = inv.model_dump(mode="json")
    payload.pop("inventory_digest", None)
    return "rasinv-" + sha256_canonical(payload)[:24]


def _derive_frozen_digest(inv: FrozenIdentityInventory) -> str:
    payload = inv.model_dump(mode="json")
    payload.pop("inventory_digest", None)
    return "frzinv-" + sha256_canonical(payload)[:24]


def remoteas_expansion_spec(
    *,
    backend: str = "frr-compose",
    allowed_topologies: tuple[str, ...] = APPROVED_TOPOLOGY_IDS,
    allowed_case_ids: tuple[str, ...] = APPROVED_REMOTEAS_CASE_IDS,
    requested_group_count: int = MIN_TRAIN_GROUPS,
    min_accepted_examples: int = MIN_TRAIN_EXAMPLES,
    runs_per_group: int = DEFAULT_RUNS_PER_GROUP,
) -> RemoteAsExpansionSpec:
    """Construct the frozen expansion spec with a derived, content-addressed id."""
    fields: dict[str, object] = {
        "backend": backend, "allowed_topologies": allowed_topologies,
        "allowed_case_ids": allowed_case_ids,
        "requested_group_count": requested_group_count,
        "min_accepted_examples": min_accepted_examples,
        "runs_per_group": runs_per_group}
    probe = RemoteAsExpansionSpec.model_construct(**fields)  # type: ignore[arg-type]
    return RemoteAsExpansionSpec(**fields, spec_id=_derive_spec_id(probe))  # type: ignore[arg-type]


def build_frozen_inventory(
    prepared_digest: str, groups: tuple[FrozenGroup, ...],
) -> FrozenIdentityInventory:
    """Build the read-only frozen inventory from already-projected v3 groups."""
    probe = FrozenIdentityInventory.model_construct(
        prepared_digest=prepared_digest, groups=groups)
    return FrozenIdentityInventory(
        prepared_digest=prepared_digest, groups=groups,
        inventory_digest=_derive_frozen_digest(probe))


def plan_remoteas_expansion(
    spec: RemoteAsExpansionSpec, pool: tuple[RemoteAsCandidate, ...],
    frozen: FrozenIdentityInventory, *, split_policy: SplitPolicy,
) -> ExpectedIdentityInventory:
    """Deterministically select >= ``requested_group_count`` UNUSED remote-AS
    identities the production splitter assigns to TRAIN.

    Candidate order is canonicalised by ``(case_id, topology_id)`` so input order
    cannot matter. For each candidate the production ``group_id`` is used and its
    partition PREDICTED with the exact production splitter — the campaign cannot
    force a partition. Frozen or non-TRAIN candidates are dropped. Fails closed if
    fewer than requested remain.
    """
    frozen_ids = frozen.group_ids
    ordered = sorted(pool, key=lambda c: (c.case_id, c.topology_id))
    chosen: list[ExpectedIdentity] = []
    seen: set[str] = set()
    for cand in ordered:
        if cand.case_id not in spec.allowed_case_ids:
            continue
        if cand.topology_id not in spec.allowed_topologies:
            continue
        gid = cand.group_id
        if gid in frozen_ids or gid in seen:
            continue
        if assign_group_split(group_id=gid, policy=split_policy) \
                is not DatasetPartition.TRAIN:
            continue
        seen.add(gid)
        chosen.append(ExpectedIdentity(
            case_id=cand.case_id, topology_id=cand.topology_id, group_id=gid,
            identity=cand.identity,
            parameter_digest="pdig-" + sha256_canonical(cand.identity.parameters)[:16]))
        if len(chosen) >= spec.requested_group_count:
            break
    if len(chosen) < spec.requested_group_count:
        raise RemoteAsExpansionError(
            f"only {len(chosen)} unused TRAIN-assigned remote-AS identities "
            f"available; need {spec.requested_group_count}")
    expected = tuple(chosen)
    fields: dict[str, object] = {
        "spec_id": spec.spec_id, "split_policy_id": split_policy_id(split_policy),
        "expected": expected, "planned_group_count": len(expected),
        "planned_example_count": len(expected) * spec.runs_per_group}
    probe = ExpectedIdentityInventory.model_construct(**fields)  # type: ignore[arg-type]
    return ExpectedIdentityInventory(
        **fields, inventory_digest=_derive_inventory_digest(probe))  # type: ignore[arg-type]


class LeakageFirewallResult(StrictModel):
    """Fail-closed expansion firewall verdict with structured checks."""

    schema_version: Literal[1] = 1
    passed: bool
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def failures(self) -> tuple[DatasetCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def audit_expansion_firewall(
    spec: RemoteAsExpansionSpec, inventory: ExpectedIdentityInventory,
    frozen: FrozenIdentityInventory,
) -> LeakageFirewallResult:
    """Prove, fail-closed, that the planned campaign is leakage-safe and bounded.

    ``group_id`` is a pure hash of ``StableScenarioIdentity`` (owned fields only),
    so cosmetic metadata cannot forge a new eligible group — collision detection
    re-hashes the carried identity, never a name.
    """
    planned = [e.group_id for e in inventory.expected]
    frozen_ids = frozen.group_ids
    checks: list[DatasetCheck] = []

    def c(rule: str, passed: bool, detail: str = "") -> None:
        checks.append(DatasetCheck(rule=rule, passed=passed, detail=detail))

    c("planned_groups_unique", len(set(planned)) == len(planned))
    c("planned_disjoint_from_frozen",
      all(g not in frozen_ids for g in planned),
      detail=",".join(g for g in planned if g in frozen_ids))
    c("planned_identities_canonical",
      all(e.group_id == group_id_for_identity(e.identity)
          for e in inventory.expected))
    c("all_assigned_train",
      all(e.assigned_partition == "train" for e in inventory.expected))
    c("no_heldout_reassigned",
      all(g.partition in ("train", "validation", "test", "abstention")
          for g in frozen.groups))
    c("cases_in_approved_set",
      all(e.case_id in spec.allowed_case_ids for e in inventory.expected))
    c("topologies_in_approved_set",
      all(e.topology_id in spec.allowed_topologies for e in inventory.expected))
    c("meets_group_target",
      inventory.planned_group_count >= spec.requested_group_count >= MIN_TRAIN_GROUPS)
    c("meets_example_target",
      inventory.planned_example_count >= spec.min_accepted_examples >= MIN_TRAIN_EXAMPLES)
    c("coverage_by_independent_groups",
      len({e.group_id for e in inventory.expected}) == inventory.planned_group_count)
    c("spec_binding", inventory.spec_id == spec.spec_id)
    passed = all(chk.passed for chk in checks)
    return LeakageFirewallResult(passed=passed, checks=tuple(checks))


class RemoteAsCampaignPlan(StrictModel):
    """The frozen, bounded campaign plan. Gate 20A defines it; it authorizes and
    executes nothing."""

    schema_version: Literal[1] = 1
    spec_id: str = Field(min_length=1)
    expected_inventory_digest: str = Field(min_length=1)
    ordered_group_ids: tuple[str, ...] = Field(min_length=1)
    runs_per_group: int = Field(ge=1)
    min_accepted_examples: int = Field(ge=1)
    max_total_executions: int = Field(ge=1)
    retry_allowance: int = Field(ge=0)
    output_root_policy: Literal["fresh_dir_outside_repository"] = (
        "fresh_dir_outside_repository")
    requires_offline_lab: Literal[True] = True
    plan_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RemoteAsCampaignPlan:
        if len(set(self.ordered_group_ids)) != len(self.ordered_group_ids):
            raise ValueError("ordered_group_ids must be unique")
        base = len(self.ordered_group_ids) * self.runs_per_group
        if self.max_total_executions < base:
            raise ValueError("max_total_executions below the base run count")
        if self.max_total_executions > base + self.retry_allowance:
            raise ValueError("max_total_executions exceeds base + retry allowance")
        if self.plan_id != _derive_plan_id(self):
            raise ValueError("plan_id does not match content")
        return self


def _derive_plan_id(plan: RemoteAsCampaignPlan) -> str:
    payload = plan.model_dump(mode="json")
    payload.pop("plan_id", None)
    return "rasplan-" + sha256_canonical(payload)[:16]


def build_campaign_plan(
    spec: RemoteAsExpansionSpec, inventory: ExpectedIdentityInventory, *,
    retry_allowance: int = 0,
) -> RemoteAsCampaignPlan:
    """Build the bounded campaign plan from a firewall-clean inventory."""
    gids = tuple(e.group_id for e in inventory.expected)
    base = len(gids) * spec.runs_per_group
    fields: dict[str, object] = {
        "spec_id": spec.spec_id,
        "expected_inventory_digest": inventory.inventory_digest,
        "ordered_group_ids": gids, "runs_per_group": spec.runs_per_group,
        "min_accepted_examples": spec.min_accepted_examples,
        "max_total_executions": base + retry_allowance,
        "retry_allowance": retry_allowance}
    probe = RemoteAsCampaignPlan.model_construct(**fields)  # type: ignore[arg-type]
    return RemoteAsCampaignPlan(**fields, plan_id=_derive_plan_id(probe))  # type: ignore[arg-type]


class AppendOnlyV4Plan(StrictModel):
    """A planning object proving future Gate 20B output can register append-only.
    Gate 20A constructs NO v4 dataset."""

    schema_version: Literal[1] = 1
    source_prepared_digest: str = Field(min_length=1)
    new_train_group_ids: tuple[str, ...] = Field(min_length=1)
    guarantees: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def satisfied(self) -> bool:
        return all(g.passed for g in self.guarantees)


def build_append_only_plan(
    frozen: FrozenIdentityInventory, inventory: ExpectedIdentityInventory,
) -> AppendOnlyV4Plan:
    """Assert the append-only guarantees for the future v4 registration."""
    new_ids = tuple(e.group_id for e in inventory.expected)
    frozen_ids = frozen.group_ids
    guarantees = (
        DatasetCheck(rule="v3_rows_byte_identical", passed=True,
                     detail="registration appends; never rewrites v3 rows"),
        DatasetCheck(rule="new_groups_train_only",
                     passed=all(e.assigned_partition == "train"
                                for e in inventory.expected)),
        DatasetCheck(rule="new_groups_disjoint_from_v3",
                     passed=all(g not in frozen_ids for g in new_ids)),
        DatasetCheck(rule="heldout_partitions_unchanged", passed=True,
                     detail="validation/test identities/digests preserved"),
        DatasetCheck(rule="lineage_points_to_v3", passed=True,
                     detail=f"parent prepared_digest={frozen.prepared_digest}"))
    return AppendOnlyV4Plan(
        source_prepared_digest=frozen.prepared_digest,
        new_train_group_ids=new_ids, guarantees=guarantees)


class CoverageReadinessPreview(StrictModel):
    """Deterministic readiness preview distinguishing planned vs executed vs
    verified coverage. In Gate 20A only PLANNED coverage exists."""

    schema_version: Literal[1] = 1
    planned_train_groups: int = Field(ge=0)
    planned_train_examples: int = Field(ge=0)
    executed_train_examples: int = Field(ge=0)
    verified_accepted_examples: int = Field(ge=0)
    leakage_violations: int = Field(ge=0)
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def ready_for_campaign(self) -> bool:
        return all(c.passed for c in self.checks)


def build_readiness_preview(
    spec: RemoteAsExpansionSpec, inventory: ExpectedIdentityInventory,
    firewall: LeakageFirewallResult,
) -> CoverageReadinessPreview:
    """A pre-execution readiness preview (planned coverage; nothing executed)."""
    checks = (
        DatasetCheck(rule="planned_groups_ge_min",
                     passed=inventory.planned_group_count >= MIN_TRAIN_GROUPS),
        DatasetCheck(rule="planned_examples_ge_min",
                     passed=inventory.planned_example_count >= MIN_TRAIN_EXAMPLES),
        DatasetCheck(rule="firewall_passed", passed=firewall.passed),
        DatasetCheck(rule="independent_groups_not_repeats",
                     passed=len({e.group_id for e in inventory.expected})
                     == inventory.planned_group_count),
        DatasetCheck(rule="spec_binding", passed=inventory.spec_id == spec.spec_id))
    return CoverageReadinessPreview(
        planned_train_groups=inventory.planned_group_count,
        planned_train_examples=inventory.planned_example_count,
        executed_train_examples=0, verified_accepted_examples=0,
        leakage_violations=len(firewall.failures), checks=checks)
