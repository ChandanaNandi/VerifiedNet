"""Gate 14 property tests: order independence, id stability/sensitivity,
split-prediction agreement, delta consistency."""

from __future__ import annotations

import pytest

from verifiednet.common.hashing import sha256_canonical
from verifiednet.datasets.models import SplitPolicy, StableScenarioIdentity
from verifiednet.datasets.projection import group_id_for_identity
from verifiednet.datasets.splitting import assign_group_split
from verifiednet.evaluation import (
    CandidateScenario,
    build_expansion_policy,
    derive_campaign_id,
    plan_evaluation_corpus_expansion,
    predict_candidate_partition,
)

pytestmark = pytest.mark.property

_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def _candidate(tag: str, runs: int = 2) -> CandidateScenario:
    return CandidateScenario(
        case_id=f"case-{tag}", fault_family="bgp_remote_as_mismatch",
        identity=StableScenarioIdentity(
            template_id="bgp_remote_as_mismatch",
            scenario_id=f"bgp-remote-as-mismatch-case-{tag}",
            target_node="router_a", target_session="a-b",
            parameters={"target_node": "router_a", "target_session": "a-b",
                        "wrong_asn": 65999},
            topology_hash=sha256_canonical({"topo": tag}),
            backend="frr-compose"),
        planned_runs=runs)


def _coverage():
    from verifiednet.datasets.models import DatasetPartitionCounts
    from verifiednet.evaluation import CorpusCoverageStats, DistributionEntry

    return CorpusCoverageStats(
        total=4, accepted=3, abstention=1,
        partition_counts=DatasetPartitionCounts(
            train=3, validation=0, test=0, abstention=1),
        eligible_test_examples=0,
        fault_family_distribution=(
            DistributionEntry(key="bgp_remote_as_mismatch", count=3),),
        rejection_distribution=(
            DistributionEntry(key="precondition_failed", count=1),),
        topology_distribution=(DistributionEntry(key="t", count=4),),
        duplicate_feature_content_groups=1,
        class_imbalance_ratio=None, topology_imbalance_ratio=None)


def test_planning_is_input_order_independent() -> None:
    policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    candidates = tuple(_candidate(tag) for tag in "abcdefgh")
    forward = plan_evaluation_corpus_expansion(
        _coverage(), candidates, policy=policy, split_policy=_SPLIT,
        planned_rejected_runs=4)
    backward = plan_evaluation_corpus_expansion(
        _coverage(), tuple(reversed(candidates)), policy=policy,
        split_policy=_SPLIT, planned_rejected_runs=4)
    assert forward == backward
    assert forward.expansion_plan_id == backward.expansion_plan_id


def test_split_prediction_agrees_with_the_production_splitter() -> None:
    for tag in "abcdefghijklmnop":
        candidate = _candidate(tag)
        predicted = predict_candidate_partition(
            candidate, split_policy=_SPLIT)
        direct = assign_group_split(
            group_id=group_id_for_identity(candidate.identity),
            policy=_SPLIT)
        assert predicted is direct


def test_plan_predicted_counts_are_run_weighted() -> None:
    policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    candidates = tuple(_candidate(tag, runs=3) for tag in "abcdefgh")
    plan = plan_evaluation_corpus_expansion(
        _coverage(), candidates, policy=policy, split_policy=_SPLIT,
        planned_rejected_runs=0)
    total = (plan.predicted_split.train_examples
             + plan.predicted_split.validation_examples
             + plan.predicted_split.test_examples)
    assert total == sum(c.planned_runs for c in candidates)


def test_campaign_id_stability_and_sensitivity() -> None:
    kwargs = {
        "expansion_plan_id": "ecplan-" + "0" * 16,
        "intended_group_ids": ("grp-" + "0" * 16, "grp-" + "1" * 16),
        "expected_run_count": 10,
        "backend_policy": "offline-deterministic-catalog-sim",
        "execution_policy": "sequential-single-process",
    }
    base = derive_campaign_id(**kwargs)  # type: ignore[arg-type]
    assert base == derive_campaign_id(**kwargs)  # type: ignore[arg-type]
    reordered = dict(kwargs)
    reordered["intended_group_ids"] = tuple(
        reversed(kwargs["intended_group_ids"]))
    assert derive_campaign_id(**reordered) == base  # type: ignore[arg-type]
    for field, mutated in (
            ("expansion_plan_id", "ecplan-" + "f" * 16),
            ("intended_group_ids", ("grp-" + "0" * 16,)),
            ("expected_run_count", 11),
            ("backend_policy", "other"),
            ("execution_policy", "other")):
        changed = dict(kwargs)
        changed[field] = mutated
        assert derive_campaign_id(**changed) != base, field  # type: ignore[arg-type]


def test_policy_id_sensitivity_to_every_field() -> None:
    from verifiednet.evaluation import derive_expansion_policy_id

    base_policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    base = base_policy.expansion_policy_id
    overrides = {
        "source_corpus_id": "evalcorpus-" + "1" * 16,
        "source_corpus_digest": "ecdig-" + "1" * 24,
        "min_total_examples": 81,
        "min_accepted_examples": 65,
        "min_abstention_examples": 13,
        "min_validation_accepted": 13,
        "min_test_accepted": 21,
        "min_examples_per_family": 13,
        "min_identities_per_family": 4,
        "max_class_imbalance_ratio": "1.600000",
        "required_rejection_codes": ("other_code", "precondition_failed"),
        "advisory_min_topology_variants": 4,
        "advisory_max_duplicate_content_ratio": "0.300000",
    }
    # every derivable input is covered by an override
    hashed_fields = set(type(base_policy).model_fields) - {
        "schema_version", "policy_version", "expansion_policy_id"}
    assert set(overrides) == hashed_fields
    for field, value in overrides.items():
        mutated = build_expansion_policy(**{
            "source_corpus_id": "evalcorpus-" + "0" * 16,
            "source_corpus_digest": "ecdig-" + "0" * 24,
            field: value})  # type: ignore[arg-type]
        assert mutated.expansion_policy_id != base, field
        assert mutated.expansion_policy_id == derive_expansion_policy_id(
            mutated)


def test_comparison_delta_consistency(tmp_path, eval_pipeline) -> None:
    from verifiednet.evaluation import (
        CorpusProvenance,
        build_generation_policy,
        register_evaluation_corpus,
    )

    # deltas of a corpus against itself are all zero (built as v1 and v2)
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    gen = build_generation_policy(
        generator="g", split_policy_id=split_ids[0],
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        requested_accepted_runs=1, requested_rejected_runs=1)
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=gen, corpora_root=tmp_path / "corpora")
    from verifiednet.evaluation import read_evaluation_corpus

    v1 = read_evaluation_corpus(written.root)
    # build a synthetic descendant view of the SAME coverage
    from verifiednet.datasets.verifier import DatasetCheck
    from verifiednet.evaluation import (
        CorpusExpansionBinding,
        build_corpus_comparison,
    )

    binding = CorpusExpansionBinding(
        parent_corpus_id=v1.manifest.evaluation_corpus_id,
        parent_corpus_digest=v1.manifest.corpus_digest,
        expansion_policy_id="ecexp-" + "0" * 16,
        expansion_plan_id="ecplan-" + "0" * 16,
        campaign_id="campaign-" + "0" * 16,
        target_checks=(DatasetCheck(rule="t", passed=True, detail=""),))
    descendant = v1.manifest.model_copy(update={
        "corpus_version": 2, "expansion": binding})
    report = build_corpus_comparison(v1.manifest, descendant)
    assert all(d.before == d.after for d in report.deltas)
    assert report.class_imbalance_before == report.class_imbalance_after
