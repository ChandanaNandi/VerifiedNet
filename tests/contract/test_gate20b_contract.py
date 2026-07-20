"""Gate 20B contract tests: content-addressed campaign ids, extra="forbid",
acceptance implies verification and a matching group_id, the campaign binds its
plan/inventory, the append-only diff reuses the production example-byte identity,
and readiness is fail-closed over structured checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verifiednet.experiment.remoteas_campaign import (
    RemoteAsCampaignResult,
    RemoteAsRunRecord,
    assess_v4_readiness,
    build_campaign_result,
    compute_append_only_diff,
)

pytestmark = pytest.mark.contract


def test_run_record_forbids_extra_fields() -> None:
    payload = RemoteAsRunRecord(
        planned_group_id="grp-a", case_id="ras-ref", topology_id="2r-v1",
        attempt=1, run_id="r1", run_digest="rd", observed_group_id="grp-a",
        verified=True, accepted=True).model_dump()
    payload["sneaky"] = 1
    with pytest.raises(ValidationError):
        RemoteAsRunRecord.model_validate(payload)


def test_result_forbids_extra_fields(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    result = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    payload = result.model_dump()
    payload["sneaky"] = 1
    with pytest.raises(ValidationError):
        RemoteAsCampaignResult.model_validate(payload)


def test_result_ids_are_content_addressed(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    records = remoteas_campaign.accepted_records(ctx.inventory)
    a = build_campaign_result(ctx.plan, ctx.inventory, records)
    b = build_campaign_result(ctx.plan, ctx.inventory, records)
    assert a.result_id == b.result_id
    assert a.result_digest == b.result_digest


def test_campaign_binds_its_plan(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    result = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    assert result.spec_id == ctx.plan.spec_id == ctx.spec.spec_id
    assert result.plan_id == ctx.plan.plan_id
    assert result.inventory_digest == ctx.inventory.inventory_digest
    assert result.ordered_planned_group_ids == ctx.plan.ordered_group_ids


def test_acceptance_requires_verification_and_group_match() -> None:
    with pytest.raises(ValidationError, match="must be verified"):
        RemoteAsRunRecord(
            planned_group_id="grp-a", case_id="ras-ref", topology_id="2r-v1",
            attempt=1, run_id="r1", run_digest="rd", observed_group_id="grp-a",
            verified=False, accepted=True, failure_category="verification")
    with pytest.raises(ValidationError, match="match its planned group_id"):
        RemoteAsRunRecord(
            planned_group_id="grp-a", case_id="ras-ref", topology_id="2r-v1",
            attempt=1, run_id="r1", run_digest="rd", observed_group_id="grp-b",
            verified=True, accepted=True)


def test_diff_uses_production_example_bytes(remoteas_prepared_pair) -> None:
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    # v3/v4 digests are carried through verbatim, and the check set is complete
    assert diff.v3_prepared_digest == v3.manifest.prepared_digest
    assert diff.v4_prepared_digest == v4.manifest.prepared_digest
    rules = {c.rule for c in diff.checks}
    assert {"all_v3_rows_present", "no_modified_prior_rows",
            "no_removed_prior_rows", "no_prior_partition_changes",
            "no_heldout_drift", "no_frozen_group_collisions"} <= rules


def test_readiness_is_failclosed_conjunction(remoteas_campaign,
                                             remoteas_prepared_pair) -> None:
    ctx = remoteas_campaign()
    result = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    # a single false input flips the whole verdict to not-ready
    not_ready = assess_v4_readiness(
        result, diff, remoteas_train_groups_after=9,
        remoteas_train_examples_after=20, leakage_clean=False,
        v2_derivation_ok=True)
    assert not_ready.ready_for_gate20c is False
    assert any(c.rule == "leakage_clean" and not c.passed
               for c in not_ready.checks)
