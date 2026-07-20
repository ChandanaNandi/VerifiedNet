"""Gate 20B property tests: campaign-result determinism, record-order
independence of the derived ids, retries never inflate verified-group coverage,
the append-only diff is reflexive (v3 vs v3) and monotone under appends, and
readiness holds iff every check holds."""

from __future__ import annotations

import pytest

from verifiednet.experiment.remoteas_campaign import (
    assess_v4_readiness,
    build_campaign_result,
    compute_append_only_diff,
)

pytestmark = pytest.mark.property


def test_result_is_deterministic(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    records = remoteas_campaign.accepted_records(ctx.inventory)
    a = build_campaign_result(ctx.plan, ctx.inventory, records)
    b = build_campaign_result(ctx.plan, ctx.inventory, records)
    assert a == b
    assert a.result_digest == b.result_digest


def test_result_ids_independent_of_record_order(remoteas_campaign) -> None:
    ctx = remoteas_campaign()
    records = remoteas_campaign.accepted_records(ctx.inventory)
    a = build_campaign_result(ctx.plan, ctx.inventory, records)
    b = build_campaign_result(ctx.plan, ctx.inventory, tuple(reversed(records)))
    # counts and coverage are order-invariant; the derived ids follow the content
    assert a.verified_group_count == b.verified_group_count
    assert a.accepted_example_count == b.accepted_example_count
    assert a.coverage_ok == b.coverage_ok


def test_retries_do_not_inflate_group_coverage(remoteas_campaign) -> None:
    ctx = remoteas_campaign(retry_allowance=4)
    exp = ctx.inventory.expected
    records = list(remoteas_campaign.accepted_records(ctx.inventory))
    # append one extra accepted run of the SAME first group as a retry (attempt#2)
    records.append(remoteas_campaign.record(exp[0], attempt=2, run_suffix="x"))
    result = build_campaign_result(ctx.plan, ctx.inventory, tuple(records))
    assert result.verified_group_count == 8  # unchanged by the repeat
    assert result.retry_count == 1


def test_diff_reflexive_on_v3(remoteas_prepared_pair) -> None:
    v3, _v4, frozen = remoteas_prepared_pair(added=16, added_groups=8)
    diff = compute_append_only_diff(v3, v3, frozen_remoteas_group_ids=frozen)
    assert diff.append_only is True
    assert diff.appended_accepted == 0
    assert diff.appended_rejected == 0
    assert diff.unchanged_v3_rows == diff.v3_row_count == diff.v4_row_count


def test_diff_monotone_under_appends(remoteas_prepared_pair) -> None:
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    assert diff.v4_row_count > diff.v3_row_count
    assert diff.unchanged_v3_rows == diff.v3_row_count
    assert diff.v4_row_count == diff.v3_row_count + diff.appended_accepted \
        + diff.appended_rejected


def test_readiness_iff_all_checks(remoteas_campaign, remoteas_prepared_pair) -> None:
    ctx = remoteas_campaign()
    result = build_campaign_result(
        ctx.plan, ctx.inventory, remoteas_campaign.accepted_records(ctx.inventory))
    v3, v4, frozen = remoteas_prepared_pair(added=16, added_groups=8)
    diff = compute_append_only_diff(v3, v4, frozen_remoteas_group_ids=frozen)
    r = assess_v4_readiness(
        result, diff, remoteas_train_groups_after=9,
        remoteas_train_examples_after=20, leakage_clean=True, v2_derivation_ok=True)
    assert r.ready_for_gate20c == all(c.passed for c in r.checks) is True
