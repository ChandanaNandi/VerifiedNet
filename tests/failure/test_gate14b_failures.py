"""Gate 14B failure tests: repeated executions cannot fake diversity,
mismatched bindings refuse, identity shortfalls block registration,
tampered stores fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets.models import SplitPolicy
from verifiednet.evaluation import (
    CorpusExpansionError,
    CorpusProvenance,
    assess_evaluation_readiness,
    assess_expansion_targets,
    assess_identity_coverage,
    build_corpus_comparison_with_identity_deltas,
    build_expansion_binding,
    build_expansion_policy,
    build_generation_policy,
    build_identity_coverage_policy,
    build_scenario_coverage_matrix,
    combine_target_results,
    compute_corpus_coverage,
    compute_partition_identity_coverage,
    plan_identity_first_selection,
    read_evaluation_corpus,
    register_evaluation_corpus,
    verify_identity_selection,
    verify_readiness_assessment,
    write_identity_selection,
    write_readiness_assessment,
)
from verifiednet.orchestrator.catalog import case_by_id
from verifiednet.orchestrator.expansion import expansion_topology

pytestmark = pytest.mark.failure

_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def _register(ctx, corpora_root, *, version=1):
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    return register_evaluation_corpus(
        ctx.loaded, corpus_version=version,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=build_generation_policy(
            generator="g", split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=1, requested_rejected_runs=1),
        corpora_root=corpora_root)


def _scaled_policy(**overrides):
    kwargs = {
        "source_corpus_id": "evalcorpus-" + "0" * 16,
        "source_corpus_digest": "ecdig-" + "0" * 24,
        "min_total_examples": 1, "min_accepted_examples": 1,
        "min_abstention_examples": 1, "min_validation_accepted": 0,
        "min_test_accepted": 0, "min_examples_per_family": 1,
        "min_identities_per_family": 1,
        "max_class_imbalance_ratio": "99.000000",
    }
    kwargs.update(overrides)
    return build_expansion_policy(**kwargs)


def test_repeated_executions_of_one_identity_cannot_fake_diversity(
    tmp_path: Path, eval_pipeline,
) -> None:
    """The Gate 14 lesson, fail-closed: many runs of ONE held-out identity
    satisfy the ROW targets yet stay a low-diversity corpus."""
    case = case_by_id("ras-ref")
    topo = expansion_topology("2r-v2")  # (2r-v2, ras-ref) is test-partition
    accepted = [(case, topo, f"run-rep-{i}") for i in range(1, 11)]
    ctx = eval_pipeline(tmp_path, accepted=accepted, rejected=["run-rej"])
    coverage = compute_corpus_coverage(ctx.loaded)
    assert coverage.eligible_test_examples == 10  # rows inflated...
    identity_coverage = compute_partition_identity_coverage(ctx.loaded)
    assert identity_coverage.counts.test_identities == 1  # ...identities not

    written = _register(ctx, tmp_path / "corpora")
    corpus = read_evaluation_corpus(written.root)
    policy = _scaled_policy(  # row targets ARE met; binds the real parent
        min_test_accepted=10,
        source_corpus_id=corpus.manifest.evaluation_corpus_id,
        source_corpus_digest=corpus.manifest.corpus_digest)
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    assessment = assess_evaluation_readiness(
        corpus=corpus, identity_coverage=identity_coverage,
        expansion_policy=policy, identity_policy=identity_policy)
    assert assessment.outcome == "coverage_threshold_met_but_low_diversity"
    # and the identity target check blocks any v3-style binding outright
    identity_result = assess_identity_coverage(
        identity_coverage,
        topology_variants=len(coverage.topology_distribution),
        policy=identity_policy)
    assert identity_result.satisfied is False
    example_result = assess_expansion_targets(
        coverage, build_scenario_coverage_matrix(ctx.loaded), policy)
    combined = combine_target_results(example_result, identity_result)
    assert combined.satisfied is False
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import StableScenarioIdentity
    from verifiednet.evaluation import (
        CandidateScenario,
        plan_evaluation_corpus_expansion,
    )

    plan = plan_evaluation_corpus_expansion(
        corpus.coverage, (CandidateScenario(
            case_id="c", fault_family="bgp_remote_as_mismatch",
            identity=StableScenarioIdentity(
                template_id="bgp_remote_as_mismatch", scenario_id="s",
                target_node="router_a", target_session="a-b",
                parameters={}, topology_hash=sha256_canonical({"t": 1}),
                backend="frr-compose"),
            planned_runs=1),),
        policy=policy, split_policy=_SPLIT, planned_rejected_runs=0)
    with pytest.raises(CorpusExpansionError, match="unmet"):
        build_expansion_binding(
            parent=corpus.manifest, policy=policy, plan=plan,
            campaign_id="campaign-" + "0" * 16, target_result=combined)


def test_underpowered_verdict_when_row_counts_fall_short(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    written = _register(ctx, tmp_path / "corpora")
    corpus = read_evaluation_corpus(written.root)
    policy = _scaled_policy(min_test_accepted=30,
                            min_validation_accepted=24)
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    assessment = assess_evaluation_readiness(
        corpus=corpus,
        identity_coverage=compute_partition_identity_coverage(ctx.loaded),
        expansion_policy=policy, identity_policy=identity_policy)
    assert assessment.outcome == "underpowered"


def test_mismatched_prepared_digest_is_refused(
    tmp_path: Path, eval_pipeline,
) -> None:
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    root_a.mkdir(), root_b.mkdir()
    ctx_a = eval_pipeline(root_a, accepted=[("ras-ref", "run-a")],
                          rejected=["run-rej"])
    ctx_b = eval_pipeline(root_b, accepted=[("nr-ref", "run-b")],
                          rejected=["run-rej"])
    written = _register(ctx_a, tmp_path / "corpora")
    corpus = read_evaluation_corpus(written.root)
    policy = _scaled_policy()
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    foreign = compute_partition_identity_coverage(ctx_b.loaded)
    with pytest.raises(CorpusExpansionError, match="different prepared"):
        assess_evaluation_readiness(
            corpus=corpus, identity_coverage=foreign,
            expansion_policy=policy, identity_policy=identity_policy)


def test_identity_delta_comparison_requires_matching_coverage(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.datasets.verifier import DatasetCheck
    from verifiednet.evaluation import CorpusExpansionBinding

    root_a, root_b = tmp_path / "a", tmp_path / "b"
    root_a.mkdir(), root_b.mkdir()
    ctx = eval_pipeline(root_a, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    ctx_other = eval_pipeline(root_b, accepted=[("nr-ref", "run-b")],
                              rejected=["run-rej"])
    written = _register(ctx, tmp_path / "corpora")
    v1 = read_evaluation_corpus(written.root)
    binding = CorpusExpansionBinding(
        parent_corpus_id=v1.manifest.evaluation_corpus_id,
        parent_corpus_digest=v1.manifest.corpus_digest,
        expansion_policy_id="ecexp-" + "0" * 16,
        expansion_plan_id="ecplan-" + "0" * 16,
        campaign_id="campaign-" + "0" * 16,
        target_checks=(DatasetCheck(rule="t", passed=True, detail=""),))
    descendant = v1.manifest.model_copy(update={
        "corpus_version": 2, "expansion": binding})
    good = compute_partition_identity_coverage(ctx.loaded)
    foreign = compute_partition_identity_coverage(ctx_other.loaded)
    with pytest.raises(CorpusExpansionError, match="parent identity"):
        build_corpus_comparison_with_identity_deltas(
            v1.manifest, descendant, parent_identities=foreign,
            descendant_identities=good)
    with pytest.raises(CorpusExpansionError, match="descendant identity"):
        build_corpus_comparison_with_identity_deltas(
            v1.manifest, descendant, parent_identities=good,
            descendant_identities=foreign)


def test_policy_binding_mismatches_are_refused(gate14b_pool) -> None:
    pool, _topologies = gate14b_pool()
    policy = _scaled_policy()
    stranger = build_identity_coverage_policy(
        expansion_policy_id="ecexp-" + "f" * 16)
    with pytest.raises(CorpusExpansionError, match="different expansion"):
        plan_identity_first_selection(
            pool, expansion_policy=policy, identity_policy=stranger,
            split_policy=_SPLIT, planned_rejected_identities=1)


def test_planner_refuses_empty_and_conflicting_pools() -> None:
    policy = _scaled_policy()
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    with pytest.raises(CorpusExpansionError, match="empty"):
        plan_identity_first_selection(
            (), expansion_policy=policy, identity_policy=identity_policy,
            split_policy=_SPLIT, planned_rejected_identities=0)


def test_combine_refuses_colliding_rule_names(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    coverage = compute_corpus_coverage(ctx.loaded)
    matrix = build_scenario_coverage_matrix(ctx.loaded)
    policy = _scaled_policy()
    result = assess_expansion_targets(coverage, matrix, policy)
    with pytest.raises(CorpusExpansionError, match="rule names"):
        combine_target_results(result, result)


def test_selection_store_tamper_and_overwrite(
    gate14b_selection_builder, tmp_path: Path,
) -> None:
    selection, _ip, _pp, _topologies = gate14b_selection_builder()
    written = write_identity_selection(
        selection, tmp_path / "identity-selections")
    with pytest.raises(CorpusExpansionError):  # overwrite refused
        write_identity_selection(selection, tmp_path / "identity-selections")
    for name in ("manifest.json", "summary.json"):
        path = written.root / name
        original = path.read_bytes()
        position = len(original) // 2
        path.write_bytes(original[:position]
                         + bytes([original[position] ^ 0xFF])
                         + original[position + 1:])
        assert verify_identity_selection(written.root).verified is False, name
        path.write_bytes(original)
    assert verify_identity_selection(written.root).verified is True


def test_readiness_store_tamper_and_overwrite(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    written_corpus = _register(ctx, tmp_path / "corpora")
    corpus = read_evaluation_corpus(written_corpus.root)
    policy = _scaled_policy()
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    assessment = assess_evaluation_readiness(
        corpus=corpus,
        identity_coverage=compute_partition_identity_coverage(ctx.loaded),
        expansion_policy=policy, identity_policy=identity_policy)
    written = write_readiness_assessment(
        assessment, tmp_path / "readiness-assessments")
    with pytest.raises(CorpusExpansionError):  # overwrite refused
        write_readiness_assessment(
            assessment, tmp_path / "readiness-assessments")
    for name in ("manifest.json", "summary.json"):
        path = written.root / name
        original = path.read_bytes()
        position = len(original) // 2
        path.write_bytes(original[:position]
                         + bytes([original[position] ^ 0xFF])
                         + original[position + 1:])
        assert verify_readiness_assessment(written.root).verified is False, \
            name
        path.write_bytes(original)
    assert verify_readiness_assessment(written.root).verified is True
