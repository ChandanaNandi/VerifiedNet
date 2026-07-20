"""Gate 20B — remote-AS verified run campaign result and append-only v4 checks.

Gate 20A preregistered 8 unused, TRAIN-assigned, disjoint remote-AS identities
(spec ``rasexp-b6512b5825f8f109``, plan ``rasplan-8453e82e...``). Gate 20B executes
the bounded campaign on the real FRR lab (via the composition root, in the gated
harness), then this offline layer records the campaign result, proves append-only
v4 integrity against the frozen v3 prepared corpus, and assesses Gate 20C
readiness.

Boundary-safe: this module lives in the offline ``experiment`` package, reuses the
``datasets`` prepared corpus + Gate 20A expansion contracts, and imports no live
composition root / lab / ML. It records and verifies; live execution, projection,
and registration happen in the caller (the gated harness) with the production
run/dataset machinery. A retry reuses the same identity and never creates new
group coverage; only verified runs become accepted examples.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from verifiednet.common.errors import VerifiedNetError
from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.prepared import LoadedPrepared
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.experiment.remoteas_expansion import (
    MIN_TRAIN_EXAMPLES,
    MIN_TRAIN_GROUPS,
    ExpectedIdentityInventory,
    RemoteAsCampaignPlan,
)
from verifiednet.schemas.base import StrictModel

FAILURE_CATEGORIES = frozenset({
    "infrastructure", "baseline_precondition", "fault_injection",
    "evidence_collection", "verification", "recovery", "identity_mismatch",
    "unexpected_group_id", "output_collision"})


class RemoteAsCampaignError(VerifiedNetError):
    """Raised when a Gate 20B campaign result or v4 diff is inconsistent."""


class RemoteAsRunRecord(StrictModel):
    """One executed run against one planned identity."""

    schema_version: Literal[1] = 1
    planned_group_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    topology_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    run_digest: str = Field(min_length=1)
    observed_group_id: str = Field(min_length=1)
    verified: bool
    accepted: bool
    failure_category: str = ""

    @model_validator(mode="after")
    def _valid(self) -> RemoteAsRunRecord:
        if self.accepted and not self.verified:
            raise ValueError("an accepted run must be verified")
        if self.accepted and self.observed_group_id != self.planned_group_id:
            raise ValueError("an accepted run must match its planned group_id")
        if self.failure_category and self.failure_category not in FAILURE_CATEGORIES:
            raise ValueError(f"unknown failure category: {self.failure_category}")
        if not self.verified and not self.failure_category:
            raise ValueError("a non-verified run must record a failure category")
        return self


class RemoteAsCampaignResult(StrictModel):
    """The self-validating result of the bounded Gate 20B campaign."""

    schema_version: Literal[1] = 1
    spec_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    inventory_digest: str = Field(min_length=1)
    ordered_planned_group_ids: tuple[str, ...] = Field(min_length=1)
    max_total_executions: int = Field(ge=1)
    records: tuple[RemoteAsRunRecord, ...] = Field(min_length=1)
    verified_group_count: int = Field(ge=0)
    accepted_example_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    total_executions: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    coverage_ok: bool
    result_id: str = Field(min_length=1)
    result_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> RemoteAsCampaignResult:
        planned = set(self.ordered_planned_group_ids)
        for r in self.records:
            if r.planned_group_id not in planned:
                raise ValueError(f"record for an unplanned group: {r.planned_group_id}")
        run_ids = [r.run_id for r in self.records]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run_ids must be unique")
        if self.total_executions != len(self.records):
            raise ValueError("total_executions must equal the record count")
        if self.total_executions > self.max_total_executions:
            raise ValueError("total_executions exceeds the campaign bound")
        accepted = [r for r in self.records if r.accepted]
        if self.accepted_example_count != len(accepted):
            raise ValueError("accepted_example_count mismatch")
        if self.rejected_count != sum(1 for r in self.records if not r.accepted):
            raise ValueError("rejected_count mismatch")
        verified_groups = {r.planned_group_id for r in accepted}
        if self.verified_group_count != len(verified_groups):
            raise ValueError("verified_group_count must equal accepted-group count")
        first_attempts = sum(1 for r in self.records if r.attempt == 1)
        if self.retry_count != len(self.records) - first_attempts:
            raise ValueError("retry_count must equal executions beyond first attempts")
        expect_cov = (self.verified_group_count >= MIN_TRAIN_GROUPS
                      and self.accepted_example_count >= MIN_TRAIN_EXAMPLES)
        if self.coverage_ok != expect_cov:
            raise ValueError("coverage_ok inconsistent with counts")
        if self.result_id != _derive_result_id(self):
            raise ValueError("result_id does not match content")
        if self.result_digest != _derive_result_digest(self):
            raise ValueError("result_digest does not match content")
        return self


def _campaign_payload(result: RemoteAsCampaignResult) -> dict[str, object]:
    payload = result.model_dump(mode="json")
    payload.pop("result_id", None)
    payload.pop("result_digest", None)
    return payload


def _derive_result_id(result: RemoteAsCampaignResult) -> str:
    return "rascamp-" + sha256_canonical(_campaign_payload(result))[:16]


def _derive_result_digest(result: RemoteAsCampaignResult) -> str:
    return "rascdig-" + sha256_canonical(_campaign_payload(result))[:24]


def build_campaign_result(
    plan: RemoteAsCampaignPlan, inventory: ExpectedIdentityInventory,
    records: tuple[RemoteAsRunRecord, ...],
) -> RemoteAsCampaignResult:
    """Assemble the self-validating campaign result from the executed records."""
    accepted = [r for r in records if r.accepted]
    verified_groups = {r.planned_group_id for r in accepted}
    retries = len(records) - sum(1 for r in records if r.attempt == 1)
    coverage_ok = (len(verified_groups) >= MIN_TRAIN_GROUPS
                   and len(accepted) >= MIN_TRAIN_EXAMPLES)
    fields: dict[str, object] = {
        "spec_id": plan.spec_id, "plan_id": plan.plan_id,
        "inventory_digest": inventory.inventory_digest,
        "ordered_planned_group_ids": plan.ordered_group_ids,
        "max_total_executions": plan.max_total_executions,
        "records": records, "verified_group_count": len(verified_groups),
        "accepted_example_count": len(accepted),
        "rejected_count": sum(1 for r in records if not r.accepted),
        "total_executions": len(records), "retry_count": retries,
        "coverage_ok": coverage_ok}
    probe = RemoteAsCampaignResult.model_construct(**fields)  # type: ignore[arg-type]
    return RemoteAsCampaignResult(
        **fields,  # type: ignore[arg-type]
        result_id=_derive_result_id(probe), result_digest=_derive_result_digest(probe))


class AppendOnlyPreparedDiff(StrictModel):
    """A byte-level append-only diff between the frozen v3 prepared corpus and the
    expanded v4 prepared corpus."""

    schema_version: Literal[1] = 1
    v3_prepared_digest: str = Field(min_length=1)
    v4_prepared_digest: str = Field(min_length=1)
    v3_row_count: int = Field(ge=0)
    v4_row_count: int = Field(ge=0)
    unchanged_v3_rows: int = Field(ge=0)
    appended_accepted: int = Field(ge=0)
    appended_rejected: int = Field(ge=0)
    modified_prior_rows: int = Field(ge=0)
    removed_prior_rows: int = Field(ge=0)
    prior_partition_changes: int = Field(ge=0)
    new_group_count: int = Field(ge=0)
    frozen_group_collisions: int = Field(ge=0)
    heldout_changed_rows: int = Field(ge=0)
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def append_only(self) -> bool:
        return all(c.passed for c in self.checks)


def _example_bytes(example: object) -> str:
    return sha256_canonical(example.model_dump(mode="json"))  # type: ignore[attr-defined]


def compute_append_only_diff(
    v3_prepared: LoadedPrepared, v4_prepared: LoadedPrepared, *,
    frozen_remoteas_group_ids: frozenset[str],
) -> AppendOnlyPreparedDiff:
    """Prove v4 is an append-only descendant of v3: every v3 example is present
    byte-identically, nothing prior is modified/removed/repartitioned, and new
    accepted groups are disjoint from the frozen remote-AS groups.
    """
    from verifiednet.datasets.models import DatasetExampleKind, DatasetPartition

    v3_by_id = {e.trace.example_id: e for e in v3_prepared.examples}
    v4_by_id = {e.trace.example_id: e for e in v4_prepared.examples}
    v3_ids, v4_ids = set(v3_by_id), set(v4_by_id)

    unchanged = 0
    modified = 0
    partition_changes = 0
    heldout_changed = 0
    heldout_partitions = (DatasetPartition.VALIDATION, DatasetPartition.TEST)
    for eid in v3_ids:
        if eid not in v4_ids:
            continue
        a, b = v3_by_id[eid], v4_by_id[eid]
        same = _example_bytes(a) == _example_bytes(b)
        if same:
            unchanged += 1
        else:
            modified += 1
            if a.trace.partition in heldout_partitions or \
                    b.trace.partition in heldout_partitions:
                heldout_changed += 1
        if a.trace.partition is not b.trace.partition:
            partition_changes += 1

    removed = len(v3_ids - v4_ids)
    new_ids = v4_ids - v3_ids
    appended_accepted = sum(
        1 for eid in new_ids
        if v4_by_id[eid].trace.example_kind is DatasetExampleKind.ACCEPTED_FAULT)
    appended_rejected = len(new_ids) - appended_accepted
    new_groups = {v4_by_id[eid].trace.group_id for eid in new_ids}
    collisions = len(new_groups & frozen_remoteas_group_ids)

    checks = (
        DatasetCheck(rule="all_v3_rows_present",
                     passed=v3_ids.issubset(v4_ids),
                     detail=f"missing={len(v3_ids - v4_ids)}"),
        DatasetCheck(rule="no_modified_prior_rows", passed=modified == 0,
                     detail=f"modified={modified}"),
        DatasetCheck(rule="no_removed_prior_rows", passed=removed == 0,
                     detail=f"removed={removed}"),
        DatasetCheck(rule="no_prior_partition_changes", passed=partition_changes == 0,
                     detail=f"changes={partition_changes}"),
        DatasetCheck(rule="no_heldout_drift", passed=heldout_changed == 0,
                     detail=f"heldout_changed={heldout_changed}"),
        DatasetCheck(rule="no_frozen_group_collisions", passed=collisions == 0,
                     detail=f"collisions={collisions}"),
        DatasetCheck(rule="v4_is_superset", passed=len(v4_ids) >= len(v3_ids)))
    return AppendOnlyPreparedDiff(
        v3_prepared_digest=v3_prepared.manifest.prepared_digest,
        v4_prepared_digest=v4_prepared.manifest.prepared_digest,
        v3_row_count=len(v3_ids), v4_row_count=len(v4_ids),
        unchanged_v3_rows=unchanged, appended_accepted=appended_accepted,
        appended_rejected=appended_rejected, modified_prior_rows=modified,
        removed_prior_rows=removed, prior_partition_changes=partition_changes,
        new_group_count=len(new_groups), frozen_group_collisions=collisions,
        heldout_changed_rows=heldout_changed, checks=checks)


class V4ReadinessResult(StrictModel):
    """Gate 20C readiness over the executed, registered v4 chain."""

    schema_version: Literal[1] = 1
    result_id: str = Field(min_length=1)
    verified_train_groups: int = Field(ge=0)
    accepted_train_examples: int = Field(ge=0)
    remoteas_train_groups_after: int = Field(ge=0)
    remoteas_train_examples_after: int = Field(ge=0)
    checks: tuple[DatasetCheck, ...] = Field(min_length=1)

    @property
    def ready_for_gate20c(self) -> bool:
        return all(c.passed for c in self.checks)


def assess_v4_readiness(
    campaign: RemoteAsCampaignResult, diff: AppendOnlyPreparedDiff, *,
    remoteas_train_groups_after: int, remoteas_train_examples_after: int,
    leakage_clean: bool, v2_derivation_ok: bool,
) -> V4ReadinessResult:
    """Fail-closed Gate 20C readiness: independent-group coverage, append-only
    integrity, held-out immutability, leakage cleanliness, and 16/16/16/16
    feasibility."""
    checks = (
        DatasetCheck(rule="verified_train_groups_ge_8",
                     passed=campaign.verified_group_count >= MIN_TRAIN_GROUPS),
        DatasetCheck(rule="accepted_train_examples_ge_16",
                     passed=campaign.accepted_example_count >= MIN_TRAIN_EXAMPLES),
        DatasetCheck(rule="campaign_coverage_ok", passed=campaign.coverage_ok),
        DatasetCheck(rule="append_only_integrity", passed=diff.append_only),
        DatasetCheck(rule="heldout_byte_identical", passed=diff.heldout_changed_rows == 0),
        DatasetCheck(rule="no_frozen_collision",
                     passed=diff.frozen_group_collisions == 0),
        DatasetCheck(rule="leakage_clean", passed=leakage_clean),
        DatasetCheck(rule="v2_derivation_ok", passed=v2_derivation_ok),
        DatasetCheck(rule="balanced_16_feasible",
                     passed=remoteas_train_examples_after >= 16
                     and remoteas_train_groups_after >= MIN_TRAIN_GROUPS + 1))
    payload = {
        "verified_train_groups": campaign.verified_group_count,
        "accepted_train_examples": campaign.accepted_example_count,
        "remoteas_train_groups_after": remoteas_train_groups_after,
        "remoteas_train_examples_after": remoteas_train_examples_after,
        "diff_digest": diff.v4_prepared_digest}
    return V4ReadinessResult(
        result_id="rasready-" + sha256_canonical(payload)[:16],
        verified_train_groups=campaign.verified_group_count,
        accepted_train_examples=campaign.accepted_example_count,
        remoteas_train_groups_after=remoteas_train_groups_after,
        remoteas_train_examples_after=remoteas_train_examples_after, checks=checks)
