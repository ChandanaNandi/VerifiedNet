"""Gate 14B property tests: planner determinism and order independence,
id stability/sensitivity, splitter agreement, outcome totality."""

from __future__ import annotations

import pytest

from verifiednet.datasets.models import SplitPolicy
from verifiednet.datasets.splitting import assign_group_split
from verifiednet.evaluation import (
    build_expansion_policy_v3,
    build_identity_coverage_policy,
    derive_identity_policy_id,
    derive_readiness_outcome,
    plan_identity_first_selection,
)

pytestmark = pytest.mark.property

_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def test_planner_is_deterministic_and_order_independent(
    gate14b_pool,
) -> None:
    pool, _topologies = gate14b_pool()
    policy = build_expansion_policy_v3(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    forward = plan_identity_first_selection(
        pool, expansion_policy=policy, identity_policy=identity_policy,
        split_policy=_SPLIT, planned_rejected_identities=12)
    backward = plan_identity_first_selection(
        tuple(reversed(pool)), expansion_policy=policy,
        identity_policy=identity_policy, split_policy=_SPLIT,
        planned_rejected_identities=12)
    assert forward == backward
    assert forward.selection_id == backward.selection_id
    again = plan_identity_first_selection(
        pool, expansion_policy=policy, identity_policy=identity_policy,
        split_policy=_SPLIT, planned_rejected_identities=12)
    assert again == forward


def test_selection_predictions_agree_with_the_production_splitter(
    gate14b_selection_builder,
) -> None:
    selection, _ip, _pp, _topologies = gate14b_selection_builder()
    for entry in selection.entries:
        assert entry.predicted_partition is assign_group_split(
            group_id=entry.candidate.group_id, policy=_SPLIT)


def test_every_selected_identity_run_count_is_within_policy_bounds(
    gate14b_selection_builder,
) -> None:
    selection, identity_policy, _pp, _topologies = gate14b_selection_builder()
    for entry in selection.entries:
        assert identity_policy.min_runs_per_identity \
            <= entry.candidate.planned_runs \
            <= identity_policy.max_runs_per_identity
    assert selection.planned_rejected_runs == \
        selection.planned_rejected_identities \
        * identity_policy.rejected_runs_per_identity


def test_selection_id_sensitivity(
    gate14b_selection_builder, gate14b_pool,
) -> None:
    base, identity_policy, policy, _topologies = gate14b_selection_builder()
    pool, _t = gate14b_pool()
    fewer_rejected = plan_identity_first_selection(
        pool, expansion_policy=policy, identity_policy=identity_policy,
        split_policy=_SPLIT, planned_rejected_identities=6)
    assert fewer_rejected.selection_id != base.selection_id
    different_rule = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id,
        runs_per_test_identity=4)
    reruled = plan_identity_first_selection(
        pool, expansion_policy=policy, identity_policy=different_rule,
        split_policy=_SPLIT, planned_rejected_identities=12)
    assert reruled.selection_id != base.selection_id


def test_identity_policy_id_sensitivity_to_every_field() -> None:
    base_policy = build_identity_coverage_policy(
        expansion_policy_id="ecexp-" + "0" * 16)
    base = base_policy.identity_policy_id
    overrides = {
        "expansion_policy_id": "ecexp-" + "1" * 16,
        "min_distinct_test_identities": 9,
        "min_distinct_validation_identities": 7,
        "min_topology_variants": 5,
        "min_runs_per_identity": 3,
        "max_runs_per_identity": 5,
        "runs_per_test_identity": 4,
        "runs_per_validation_identity": 4,
        "runs_per_train_identity": 3,
        "rejected_runs_per_identity": 1,
    }
    hashed_fields = set(type(base_policy).model_fields) - {
        "schema_version", "policy_version", "identity_policy_id"}
    assert set(overrides) == hashed_fields  # every derivable input covered
    for field, value in overrides.items():
        kwargs = {"expansion_policy_id": "ecexp-" + "0" * 16, field: value}
        mutated = build_identity_coverage_policy(**kwargs)  # type: ignore[arg-type]
        assert mutated.identity_policy_id != base, field
        assert mutated.identity_policy_id == derive_identity_policy_id(
            mutated)


def test_readiness_outcome_is_total_with_documented_precedence() -> None:
    thresholds = {
        "min_test_accepted": 30, "min_validation_accepted": 24,
        "min_distinct_test_identities": 8,
        "min_distinct_validation_identities": 6, "min_topology_variants": 4}
    outcomes = set()
    for quality in (True, False):
        for test_examples in (0, 30):
            for validation in (0, 24):
                for test_ids in (5, 12):
                    for val_ids in (3, 14):
                        for topologies in (2, 6):
                            outcome = derive_readiness_outcome(
                                quality_verified=quality,
                                eligible_test_examples=test_examples,
                                validation_accepted=validation,
                                distinct_test_identities=test_ids,
                                distinct_validation_identities=val_ids,
                                topology_variants=topologies, **thresholds)
                            outcomes.add(outcome)
                            if not quality:
                                assert outcome == "quality_failed"
                            elif test_examples < 30 or validation < 24:
                                assert outcome == "underpowered"
                            elif test_ids < 8 or val_ids < 6 \
                                    or topologies < 4:
                                assert outcome == \
                                    "coverage_threshold_met_but_low_diversity"
                            else:
                                assert outcome == \
                                    "ready_for_controlled_experiment"
    assert outcomes == {
        "ready_for_controlled_experiment",
        "coverage_threshold_met_but_low_diversity", "underpowered",
        "quality_failed"}  # every outcome reachable, none invented
