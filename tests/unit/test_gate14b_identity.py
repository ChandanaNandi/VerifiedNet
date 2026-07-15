"""Gate 14B unit tests: identity policy, per-partition identity coverage,
identity-first planner over the real pool, capped v3 chain with readiness."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from verifiednet.datasets.models import SplitPolicy
from verifiednet.evaluation import (
    CorpusProvenance,
    assess_evaluation_readiness,
    assess_expansion_targets,
    assess_identity_coverage,
    build_corpus_comparison_with_identity_deltas,
    build_expansion_binding,
    build_expansion_policy_v3,
    build_generation_campaign,
    build_generation_policy,
    build_identity_coverage_policy,
    build_scenario_coverage_matrix,
    combine_target_results,
    compute_corpus_coverage,
    compute_partition_identity_coverage,
    list_evaluation_corpus_versions,
    plan_evaluation_corpus_expansion,
    read_evaluation_corpus,
    register_evaluation_corpus,
    verify_evaluation_corpus,
    verify_generation_campaign,
    verify_identity_selection,
    verify_readiness_assessment,
    write_generation_campaign,
    write_identity_selection,
    write_readiness_assessment,
)

pytestmark = pytest.mark.unit

_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def test_identity_policy_id_and_defaults() -> None:
    policy = build_identity_coverage_policy(
        expansion_policy_id="ecexp-" + "0" * 16)
    assert policy.identity_policy_id.startswith("icpol-")
    assert policy.min_distinct_test_identities == 8
    assert policy.min_distinct_validation_identities == 6
    assert policy.min_topology_variants == 4
    assert (policy.min_runs_per_identity, policy.max_runs_per_identity) \
        == (2, 4)
    assert policy.runs_per_test_identity == 3
    assert policy.runs_per_train_identity == 4
    assert policy.rejected_runs_per_identity == 2
    again = build_identity_coverage_policy(
        expansion_policy_id="ecexp-" + "0" * 16)
    assert again == policy  # deterministic


def test_v3_expansion_policy_targets() -> None:
    policy = build_expansion_policy_v3(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    assert policy.min_total_examples == 220
    assert policy.min_accepted_examples == 196
    assert policy.min_abstention_examples == 16
    assert policy.min_validation_accepted == 24
    assert policy.min_test_accepted == 30
    assert policy.min_examples_per_family == 15
    assert policy.min_identities_per_family == 4
    assert policy.max_class_imbalance_ratio == "1.500000"


def test_partition_identity_coverage_counts(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("ras-ref", "run-a2"),
                                            ("nr-ref", "run-b")],
                        rejected=["run-rej"])
    coverage = compute_partition_identity_coverage(ctx.loaded)
    counts = coverage.counts
    # two runs of the SAME identity are one group — identities, not rows
    assert (counts.train_identities + counts.validation_identities
            + counts.test_identities) == 2
    assert counts.abstention_identities == 1
    assert coverage.prepared_digest == ctx.loaded.manifest.prepared_digest


def test_identity_first_planner_over_the_real_pool(
    gate14b_selection_builder,
) -> None:
    selection, identity_policy, _policy, _topologies = \
        gate14b_selection_builder()
    assert selection.selection_id.startswith("icsel-")
    assert selection.pool_size == 96
    assert len(selection.entries) == 58
    counts = selection.identity_counts
    # the held-out identity coverage Gate 14 lacked
    assert counts.test_identities == 12
    assert counts.validation_identities == 14
    assert counts.abstention_identities == 12
    # priority order: every test identity precedes every validation identity
    rules = [e.priority_rule for e in selection.entries]
    assert rules[:12] == ["missing_test_identity"] * 12
    assert rules[12:26] == ["missing_validation_identity"] * 14
    # exactly one approved parameter combination needed backfilling
    backfilled = [e.candidate.case_id for e in selection.entries
                  if e.priority_rule == "missing_parameter_dimension"]
    assert backfilled == ["ras-alt4"]
    # run allocation follows the frozen per-partition rule
    for entry in selection.entries:
        assert entry.candidate.planned_runs == \
            identity_policy.runs_for_partition(entry.predicted_partition)
    assert selection.planned_accepted_runs == 206
    assert selection.planned_rejected_runs == 24


def test_planner_selection_meets_every_v3_target_by_construction(
    gate14b_selection_builder,
) -> None:
    selection, _identity_policy, policy, _topologies = \
        gate14b_selection_builder()
    families: Counter[str] = Counter()
    partitions: Counter[str] = Counter()
    for entry in selection.entries:
        families[entry.candidate.fault_family] += entry.candidate.planned_runs
        partitions[entry.predicted_partition.value] += \
            entry.candidate.planned_runs
    assert partitions["test"] >= policy.min_test_accepted  # 36 >= 30
    assert partitions["validation"] >= policy.min_validation_accepted
    assert min(families.values()) >= policy.min_examples_per_family
    assert max(families.values()) / min(families.values()) \
        <= float(policy.max_class_imbalance_ratio)
    identities_per_family = Counter(
        e.candidate.fault_family for e in selection.entries)
    assert min(identities_per_family.values()) \
        >= policy.min_identities_per_family
    topologies = {e.candidate.identity.topology_hash
                  for e in selection.entries}
    assert len(topologies) == 6
    total = (selection.planned_accepted_runs
             + selection.planned_rejected_runs)
    assert total >= policy.min_total_examples
    assert selection.planned_accepted_runs >= policy.min_accepted_examples
    assert selection.planned_rejected_runs >= policy.min_abstention_examples


def _candidates(accepted_entries):
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import StableScenarioIdentity
    from verifiednet.evaluation import CandidateScenario

    counts: Counter[tuple[str, str]] = Counter()
    keyed = {}
    for case, topo, _run_id in accepted_entries:
        counts[(case.case_id, topo.name)] += 1
        keyed[(case.case_id, topo.name)] = (case, topo)
    out = []
    for (case_id, _topo_name), (case, topo) in keyed.items():
        params = dict(case.scenario.parameters)
        out.append(CandidateScenario(
            case_id=case_id, fault_family=case.scenario.template_id,
            identity=StableScenarioIdentity(
                template_id=case.scenario.template_id,
                scenario_id=case.scenario.scenario_id,
                target_node=str(params.get("target_node", "")),
                target_session=str(params.get("target_session", "")),
                parameters={k: params[k] for k in sorted(params)},
                topology_hash=sha256_canonical(topo),
                backend="frr-compose"),
            planned_runs=counts[(case_id, topo.name)]))
    return tuple(out)


def _register(ctx, corpora_root, *, version, generator, expansion=None):
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    return register_evaluation_corpus(
        ctx.loaded, corpus_version=version,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=build_generation_policy(
            generator=generator, split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=1, requested_rejected_runs=1),
        corpora_root=corpora_root, expansion=expansion)


def test_capped_v3_chain_registers_v3_with_ready_verdict(
    tmp_path: Path, eval_pipeline, gate14b_corpus_pipeline,
    gate14b_selection_builder,
) -> None:
    """The COMPLETE Gate 14B chain on a runs-capped campaign (1 run per
    identity): identity-first selection -> campaign -> v3 registration bound
    to v2 -> identity-delta comparison -> readiness assessment. Identity
    structure (58 identities, 12 test / 14 validation) is EXACTLY the full
    campaign's; only reproducibility repeats are capped."""
    corpora = tmp_path / "corpora"
    v1root = tmp_path / "v1side"
    v1root.mkdir()
    v1ctx = eval_pipeline(v1root, accepted=[("ras-ref", "run-a")],
                          rejected=["run-rej"])
    _register(v1ctx, corpora, version=1, generator="v1")
    v2root = tmp_path / "v2side"
    v2root.mkdir()
    v2ctx = eval_pipeline(v2root, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b")],
                          rejected=["run-rej"])
    v2_written = _register(v2ctx, corpora, version=2, generator="v2")
    v2 = read_evaluation_corpus(v2_written.root)
    v2_identities = compute_partition_identity_coverage(v2ctx.loaded)

    # scaled v3 policy for the capped campaign (identity minimums UNSCALED)
    from verifiednet.evaluation import build_expansion_policy

    policy = build_expansion_policy(
        source_corpus_id=v2.manifest.evaluation_corpus_id,
        source_corpus_digest=v2.manifest.corpus_digest,
        min_total_examples=70, min_accepted_examples=58,
        min_abstention_examples=12, min_validation_accepted=14,
        min_test_accepted=12, min_examples_per_family=12,
        min_identities_per_family=4,
        # capped runs remove the per-partition run balancing (22 RAS vs 12)
        max_class_imbalance_ratio="2.000000")
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    selection, _ip, _pp, _topologies = gate14b_selection_builder(
        expansion_policy=policy)
    written_selection = write_identity_selection(
        selection, tmp_path / "identity-selections")
    assert verify_identity_selection(written_selection.root).verified is True

    v3root = tmp_path / "v3side"
    v3root.mkdir()
    ctx, accepted, rejected = gate14b_corpus_pipeline(v3root, runs_cap=1)
    assert len(accepted) == 58 and len(rejected) == 12
    coverage = compute_corpus_coverage(ctx.loaded)
    matrix = build_scenario_coverage_matrix(ctx.loaded)
    v3_identities = compute_partition_identity_coverage(ctx.loaded)
    assert v3_identities.counts.test_identities == 12
    assert v3_identities.counts.validation_identities == 14

    plan = plan_evaluation_corpus_expansion(
        v2.coverage, _candidates(accepted), policy=policy,
        split_policy=_SPLIT, planned_rejected_runs=len(rejected))
    assert plan.predicted_split.test_examples == \
        coverage.eligible_test_examples
    assert plan.predicted_split.validation_examples == \
        coverage.partition_counts.validation
    example_result = assess_expansion_targets(coverage, matrix, policy)
    identity_result = assess_identity_coverage(
        v3_identities,
        topology_variants=len(coverage.topology_distribution),
        policy=identity_policy)
    target_result = combine_target_results(example_result, identity_result)
    assert target_result.satisfied, target_result.failures

    campaign = build_generation_campaign(
        plan=plan, backend_policy="offline-deterministic-catalog-sim",
        execution_policy="sequential-single-process",
        verified_run_ids=tuple(sorted(
            [entry[2] for entry in accepted] + [r[0] for r in rejected])),
        accepted_count=len(accepted), rejected_count=len(rejected))
    written_campaign = write_generation_campaign(
        campaign, plan, tmp_path / "generation-campaigns")
    assert verify_generation_campaign(written_campaign.root).verified is True

    binding = build_expansion_binding(
        parent=v2.manifest, policy=policy, plan=plan,
        campaign_id=campaign.campaign_id, target_result=target_result)
    # identity checks travel INSIDE the registration's binding
    assert {c.rule for c in binding.target_checks} >= {
        "min_distinct_test_identities",
        "min_distinct_validation_identities", "min_topology_variants"}
    v3_written = _register(ctx, corpora, version=3, generator="v3",
                           expansion=binding)
    assert verify_evaluation_corpus(v3_written.root).verified is True
    v3 = read_evaluation_corpus(v3_written.root)
    versions = list_evaluation_corpus_versions(corpora)
    assert [m.corpus_version for m in versions] == [1, 2, 3]

    report = build_corpus_comparison_with_identity_deltas(
        v2.manifest, v3.manifest, parent_identities=v2_identities,
        descendant_identities=v3_identities)
    deltas = {d.metric: (d.before, d.after) for d in report.deltas}
    assert deltas["distinct_test_identities"][1] == 12
    assert deltas["distinct_validation_identities"][1] == 14
    assert deltas["distinct_test_identities"][1] > \
        deltas["distinct_test_identities"][0]
    assert deltas["distinct_identities_total"][1] > \
        deltas["distinct_identities_total"][0]

    assessment = assess_evaluation_readiness(
        corpus=v3, identity_coverage=v3_identities,
        expansion_policy=policy, identity_policy=identity_policy)
    assert assessment.outcome == "ready_for_controlled_experiment"
    written_assessment = write_readiness_assessment(
        assessment, tmp_path / "readiness-assessments")
    assert verify_readiness_assessment(written_assessment.root).verified \
        is True

    # v1 and v2 registrations remain verified alongside v3
    assert verify_evaluation_corpus(v2_written.root).verified is True
