"""Gate 20B failure tests: the run record, campaign result, append-only diff, and
readiness all fail closed — on unverifiable acceptance, missing failure categories,
forged counts, over-budget execution, mutated/removed/repartitioned/colliding
prior rows, and coverage shortfalls."""

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

pytestmark = pytest.mark.failure


def test_non_verified_run_requires_a_failure_category() -> None:
    with pytest.raises(ValidationError, match="failure category"):
        RemoteAsRunRecord(
            planned_group_id="grp-a", case_id="ras-ref", topology_id="2r-v1",
            attempt=1, run_id="r1", run_digest="rd", observed_group_id="grp-a",
            verified=False, accepted=False)


def test_unknown_failure_category_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown failure category"):
        RemoteAsRunRecord(
            planned_group_id="grp-a", case_id="ras-ref", topology_id="2r-v1",
            attempt=1, run_id="r1", run_digest="rd", observed_group_id="grp-a",
            verified=False, accepted=False, failure_category="made_up")


def test_result_rejects_forged_counts(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    good = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    payload = good.model_dump()
    payload["accepted_example_count"] = 99  # lie about the count
    with pytest.raises(ValidationError, match="accepted_example_count"):
        RemoteAsCampaignResult.model_validate(payload)


def test_result_rejects_forged_coverage_flag(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    good = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    payload = good.model_dump()
    payload["coverage_ok"] = False  # counts say True; flag lies
    with pytest.raises(ValidationError, match="coverage_ok"):
        RemoteAsCampaignResult.model_validate(payload)


def test_result_rejects_execution_over_budget(remoteas_campaign) -> None:
    ctx = remoteas_campaign(retry_allowance=0)  # bound == 16
    exp = ctx.inventory.expected
    records = list(remoteas_campaign.accepted_records(ctx.inventory))
    records.append(remoteas_campaign.record(exp[0], attempt=3, run_suffix="over"))
    with pytest.raises(ValidationError, match="exceeds the campaign bound"):
        build_campaign_result(ctx.plan, ctx.inventory, tuple(records))


def test_result_rejects_record_for_unplanned_group(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    good = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    payload = good.model_dump()
    payload["records"][0]["planned_group_id"] = "grp-not-in-plan"
    payload["records"][0]["observed_group_id"] = "grp-not-in-plan"
    with pytest.raises(ValidationError, match="unplanned group"):
        RemoteAsCampaignResult.model_validate(payload)


def test_diff_catches_modified_prior_row(remoteas_prepared_pair) -> None:
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8,
                                            mutate="ex-v3-0004")
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    assert diff.append_only is False
    assert diff.modified_prior_rows >= 1
    assert any(c.rule == "no_modified_prior_rows" and not c.passed
               for c in diff.checks)


def test_diff_catches_removed_prior_row(remoteas_prepared_pair) -> None:
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8, drop_v3=True)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    assert diff.append_only is False
    assert diff.removed_prior_rows >= 1
    assert any(c.rule == "no_removed_prior_rows" and not c.passed
               for c in diff.checks)
    assert any(c.rule == "all_v3_rows_present" and not c.passed
               for c in diff.checks)


def test_diff_catches_heldout_repartition(remoteas_prepared_pair) -> None:
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8,
                                            repartition=True)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    assert diff.append_only is False
    assert diff.prior_partition_changes >= 1
    assert diff.heldout_changed_rows >= 1
    assert any(c.rule == "no_prior_partition_changes" and not c.passed
               for c in diff.checks)
    assert any(c.rule == "no_heldout_drift" and not c.passed
               for c in diff.checks)


def test_diff_catches_frozen_group_collision(remoteas_prepared_pair) -> None:
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8,
                                            collide_group="grp-ras-test-c")
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    assert diff.append_only is False
    assert diff.frozen_group_collisions >= 1
    assert any(c.rule == "no_frozen_group_collisions" and not c.passed
               for c in diff.checks)


def test_readiness_fails_on_group_shortfall(remoteas_campaign,
                                            remoteas_prepared_pair) -> None:
    ctx = remoteas_campaign()
    # only 7 groups accepted -> below the >=8 target
    records = list(remoteas_campaign.accepted_records(ctx.inventory))
    exp = ctx.inventory.expected
    # turn both runs of the last planned group into rejected (non-accepted) runs
    records = [r for r in records if r.planned_group_id != exp[-1].group_id]
    records.append(remoteas_campaign.record(
        exp[-1], attempt=1, accepted=False, verified=False,
        failure_category="verification"))
    records.append(remoteas_campaign.record(
        exp[-1], attempt=2, accepted=False, verified=False,
        failure_category="verification"))
    result = build_campaign_result(ctx.plan, ctx.inventory, tuple(records))
    assert result.verified_group_count == 7
    assert result.coverage_ok is False
    v3, v4, frozen = remoteas_prepared_pair(added=14, added_groups=7)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    readiness = assess_v4_readiness(
        result, diff, remoteas_train_groups_after=8,
        remoteas_train_examples_after=18, leakage_clean=True, v2_derivation_ok=True)
    assert readiness.ready_for_gate20c is False
    assert any(c.rule == "verified_train_groups_ge_8" and not c.passed
               for c in readiness.checks)
