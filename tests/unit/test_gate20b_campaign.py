"""Gate 20B unit tests: the remote-AS run record, the self-validating bounded
campaign result (>=8 verified groups / >=16 accepted examples), the append-only
v3->v4 prepared diff, and the Gate 20C readiness assessment."""

from __future__ import annotations

import pytest

from verifiednet.experiment.remoteas_campaign import (
    AppendOnlyPreparedDiff,
    RemoteAsCampaignResult,
    RemoteAsRunRecord,
    assess_v4_readiness,
    build_campaign_result,
    compute_append_only_diff,
)

pytestmark = pytest.mark.unit


def test_run_record_accepts_a_verified_matching_run() -> None:
    r = RemoteAsRunRecord(
        planned_group_id="grp-abc", case_id="ras-ref", topology_id="2r-v1",
        attempt=1, run_id="run-1", run_digest="rd-1",
        observed_group_id="grp-abc", verified=True, accepted=True)
    assert r.accepted is True and r.verified is True
    assert r.failure_category == ""


def test_build_campaign_result_full_coverage(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    records = remoteas_campaign.accepted_records(ctx.inventory)
    result = build_campaign_result(ctx.plan, ctx.inventory, records)
    assert isinstance(result, RemoteAsCampaignResult)
    assert result.verified_group_count == 8
    assert result.accepted_example_count == 16
    assert result.rejected_count == 0
    assert result.total_executions == 16
    assert result.retry_count == 0
    assert result.coverage_ok is True
    assert result.result_id.startswith("rascamp-")
    assert result.result_digest.startswith("rascdig-")
    # revalidation reproduces the derived ids
    again = RemoteAsCampaignResult.model_validate(result.model_dump())
    assert again.result_id == result.result_id
    assert again.result_digest == result.result_digest


def test_result_respects_the_execution_bound(remoteas_campaign) -> None:
    ctx = remoteas_campaign(retry_allowance=2)
    assert ctx.plan.max_total_executions == 8 * 2 + 2
    records = remoteas_campaign.accepted_records(ctx.inventory)
    result = build_campaign_result(ctx.plan, ctx.inventory, records)
    assert result.total_executions <= result.max_total_executions


def test_retry_reuses_identity_and_counts_as_no_new_group(remoteas_campaign) -> None:
    ctx = remoteas_campaign(retry_allowance=2)
    exp = ctx.inventory.expected
    records = list(remoteas_campaign.accepted_records(ctx.inventory))
    # the first group's first slot (attempt#1) fails outright; a retry of the SAME
    # identity (attempt#2) then succeeds, restoring the group's two accepted runs.
    records[0] = remoteas_campaign.record(
        exp[0], attempt=1, accepted=False, verified=False,
        failure_category="infrastructure", run_suffix="s1")
    records.append(remoteas_campaign.record(exp[0], attempt=2, run_suffix="retry"))
    result = build_campaign_result(ctx.plan, ctx.inventory, tuple(records))
    # still 8 independent verified groups; the retry creates no new coverage
    assert result.verified_group_count == 8
    assert result.accepted_example_count == 16
    assert result.retry_count == 1
    assert result.total_executions == 17


def test_append_only_diff_clean(remoteas_prepared_pair) -> None:
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    assert isinstance(diff, AppendOnlyPreparedDiff)
    assert diff.append_only is True
    assert diff.modified_prior_rows == 0
    assert diff.removed_prior_rows == 0
    assert diff.prior_partition_changes == 0
    assert diff.heldout_changed_rows == 0
    assert diff.frozen_group_collisions == 0
    assert diff.unchanged_v3_rows == 4
    assert diff.appended_accepted == 16
    assert diff.new_group_count == 8


def test_v4_readiness_ready(remoteas_campaign, remoteas_prepared_pair) -> None:
    ctx = remoteas_campaign()
    result = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    readiness = assess_v4_readiness(
        result, diff, remoteas_train_groups_after=9,
        remoteas_train_examples_after=20, leakage_clean=True,
        v2_derivation_ok=True)
    assert readiness.ready_for_gate20c is True
    assert readiness.result_id.startswith("rasready-")
    assert readiness.verified_train_groups == 8
    assert readiness.accepted_train_examples == 16
