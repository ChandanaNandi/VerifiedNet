"""Gate 14 unit tests: expansion policy, coverage matrix, planner, campaign,
v2 registration with parent binding, v1-versus-v2 comparison."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets.models import SplitPolicy
from verifiednet.evaluation import (
    CorpusProvenance,
    assess_expansion_targets,
    build_corpus_comparison,
    build_expansion_binding,
    build_expansion_policy,
    build_generation_campaign,
    build_generation_policy,
    build_scenario_coverage_matrix,
    compute_corpus_coverage,
    list_evaluation_corpus_versions,
    plan_evaluation_corpus_expansion,
    predict_candidate_partition,
    read_evaluation_corpus,
    register_evaluation_corpus,
    verify_corpus_comparison,
    verify_evaluation_corpus,
    verify_generation_campaign,
    write_corpus_comparison,
    write_generation_campaign,
)

pytestmark = pytest.mark.unit

_V1_ACCEPTED = [("ras-ref", "run-a"), ("ras-rev", "run-b"), ("nr-ref", "run-c"),
                ("if-ref", "run-d"), ("pf-ref", "run-e"), ("pf-rev", "run-f")]
_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def _candidates(accepted_entries):
    """CandidateScenarios from the matrix entries (identity fully defined)."""
    from collections import Counter

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


def _register_v1(tmp_path: Path, eval_pipeline):
    root = tmp_path / "v1side"
    root.mkdir()
    ctx = eval_pipeline(root, accepted=_V1_ACCEPTED, rejected=["run-rej"])
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=build_generation_policy(
            generator="v1", split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=6, requested_rejected_runs=1),
        corpora_root=tmp_path / "corpora")
    return ctx, written


def test_expansion_policy_id_and_defaults() -> None:
    policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    assert policy.expansion_policy_id.startswith("ecexp-")
    assert policy.min_test_accepted == 20
    assert policy.min_validation_accepted == 12
    assert policy.required_rejection_codes == ("precondition_failed",)
    again = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    assert again == policy  # deterministic


def test_coverage_matrix_and_deficits(
    tmp_path: Path, eval_pipeline, expansion_entries,
) -> None:
    ctx, written = _register_v1(tmp_path, eval_pipeline)
    matrix = build_scenario_coverage_matrix(ctx.loaded)
    assert [f.fault_family for f in matrix.families] == sorted(
        f.fault_family for f in matrix.families)
    assert sum(f.accepted_examples for f in matrix.families) == 6
    assert matrix.rejection_codes == ("precondition_failed",)
    assert len(matrix.topology_hashes) == 1  # v1: one topology context
    registration = read_evaluation_corpus(written.root)
    policy = build_expansion_policy(
        source_corpus_id=registration.manifest.evaluation_corpus_id,
        source_corpus_digest=registration.manifest.corpus_digest)
    plan = plan_evaluation_corpus_expansion(
        registration.coverage, _candidates_from_matrix(expansion_entries),
        policy=policy, split_policy=_SPLIT, planned_rejected_runs=12)
    assert plan.expansion_plan_id.startswith("ecplan-")
    assert any("test_accepted" in d for d in plan.coverage_deficits)
    assert any("validation_accepted" in d for d in plan.coverage_deficits)
    assert len(plan.candidates) == 30  # the complete matrix, nothing dropped


def _candidates_from_matrix(expansion_entries):
    accepted, _rejected = expansion_entries()
    return _candidates(accepted)


def test_planner_predicts_with_the_production_splitter(
    expansion_entries,
) -> None:
    candidates = _candidates_from_matrix(expansion_entries)
    predicted = {c.group_id: predict_candidate_partition(
        c, split_policy=_SPLIT).value for c in candidates}
    # exact production-splitter agreement, and a nontrivial spread
    assert set(predicted.values()) == {"train", "validation", "test"}
    test_groups = [g for g, p in predicted.items() if p == "test"]
    validation_groups = [g for g, p in predicted.items() if p == "validation"]
    assert len(test_groups) == 5
    assert len(validation_groups) == 3


def test_capped_campaign_chain_registers_v2(
    tmp_path: Path, eval_pipeline, expansion_corpus_pipeline,
) -> None:
    """The COMPLETE chain on a runs-capped campaign (1 run per identity).

    Group-level structure (identities, split growth, parent binding, target
    gating, comparison) is identical to the full campaign; only the uniform
    runs-per-identity differs. The FULL campaign runs as the gated
    operational integration test.
    """
    _v1ctx, v1_written = _register_v1(tmp_path, eval_pipeline)
    v1 = read_evaluation_corpus(v1_written.root)

    v2root = tmp_path / "v2side"
    v2root.mkdir()
    ctx, accepted, rejected = expansion_corpus_pipeline(v2root, runs_cap=1)
    coverage = compute_corpus_coverage(ctx.loaded)
    matrix = build_scenario_coverage_matrix(ctx.loaded)
    policy = build_expansion_policy(
        source_corpus_id=v1.manifest.evaluation_corpus_id,
        source_corpus_digest=v1.manifest.corpus_digest,
        # scaled minimums for the capped campaign (one run per identity)
        min_total_examples=36, min_accepted_examples=30,
        min_abstention_examples=6, min_validation_accepted=3,
        min_test_accepted=5, min_examples_per_family=6,
        min_identities_per_family=6,
        # capped runs remove the per-family run balancing (12 RAS vs 6)
        max_class_imbalance_ratio="2.000000")
    plan = plan_evaluation_corpus_expansion(
        v1.coverage, _candidates(accepted), policy=policy,
        split_policy=_SPLIT, planned_rejected_runs=len(rejected))
    # the prediction is verified EXACT after projection
    assert plan.predicted_split.test_examples == \
        coverage.eligible_test_examples
    assert plan.predicted_split.validation_examples == \
        coverage.partition_counts.validation
    target_result = assess_expansion_targets(coverage, matrix, policy)
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
        parent=v1.manifest, policy=policy, plan=plan,
        campaign_id=campaign.campaign_id, target_result=target_result)
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id
                        for e in ctx.loaded.examples
                        if e.trace.partition.value != "abstention"})
    v2_written = register_evaluation_corpus(
        ctx.loaded, corpus_version=2,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=build_generation_policy(
            generator="gate14 expansion campaign",
            split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=len(accepted),
            requested_rejected_runs=len(rejected)),
        corpora_root=tmp_path / "corpora", expansion=binding)
    assert verify_evaluation_corpus(v2_written.root).verified is True
    v2 = read_evaluation_corpus(v2_written.root)
    assert v2.manifest.expansion is not None
    assert v2.manifest.expansion.parent_corpus_id == \
        v1.manifest.evaluation_corpus_id

    # v1 remains verified and byte-identical alongside v2
    assert verify_evaluation_corpus(v1_written.root).verified is True
    versions = list_evaluation_corpus_versions(tmp_path / "corpora")
    assert [m.corpus_version for m in versions] == [1, 2]

    # split growth: strictly more eligible test + validation appears — with
    # ZERO manual assignment (the production splitter is the only authority)
    assert v2.coverage.eligible_test_examples > \
        v1.coverage.eligible_test_examples
    assert v1.coverage.partition_counts.validation == 0
    assert v2.coverage.partition_counts.validation >= 3
    assert v2.coverage.eligible_test_examples >= 5
    assert len(v2.coverage.topology_distribution) == 3

    # v1-versus-v2 comparison artifact
    report = build_corpus_comparison(v1.manifest, v2.manifest)
    deltas = {d.metric: (d.before, d.after) for d in report.deltas}
    assert deltas["eligible_test_examples"] == (
        v1.coverage.eligible_test_examples,
        v2.coverage.eligible_test_examples)
    assert deltas["total_examples"][1] > deltas["total_examples"][0]
    assert report.targets_unmet == ()
    written_cmp = write_corpus_comparison(
        report, tmp_path / "corpus-comparisons")
    assert verify_corpus_comparison(written_cmp.root).verified is True
