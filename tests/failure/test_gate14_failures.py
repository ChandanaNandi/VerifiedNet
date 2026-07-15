"""Gate 14 failure tests: mismatches, shortfalls, tampering — all fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.datasets.models import SplitPolicy
from verifiednet.evaluation import (
    CorpusExpansionError,
    CorpusProvenance,
    EvaluationCorpusError,
    assess_expansion_targets,
    build_corpus_comparison,
    build_expansion_binding,
    build_expansion_policy,
    build_generation_campaign,
    build_generation_policy,
    build_scenario_coverage_matrix,
    compute_corpus_coverage,
    plan_evaluation_corpus_expansion,
    read_evaluation_corpus,
    register_evaluation_corpus,
    verify_generation_campaign,
    write_generation_campaign,
)

pytestmark = pytest.mark.failure

_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def _v1(tmp_path: Path, eval_pipeline):
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-ref", "run-b")],
                        rejected=["run-rej"])
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=build_generation_policy(
            generator="g", split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=2, requested_rejected_runs=1),
        corpora_root=tmp_path / "corpora")
    return ctx, read_evaluation_corpus(written.root)


def test_changed_split_salt_is_a_different_policy_not_an_override(
    tmp_path: Path, eval_pipeline,
) -> None:
    # Changing the salt/ratios yields DIFFERENT split ids everywhere — the
    # comparison to prior corpora breaks loudly (different split_policy_id in
    # every trace), so a quiet salt tweak cannot masquerade as v1's policy.
    from verifiednet.datasets.splitting import split_policy_id

    changed = SplitPolicy(salt="gate14-cheat", train_buckets=8000,
                          validation_buckets=1000, test_buckets=1000)
    assert split_policy_id(changed) != split_policy_id(_SPLIT)


def test_target_shortfall_blocks_binding_and_registration(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx, v1 = _v1(tmp_path, eval_pipeline)
    policy = build_expansion_policy(
        source_corpus_id=v1.manifest.evaluation_corpus_id,
        source_corpus_digest=v1.manifest.corpus_digest)  # full-size targets
    coverage = compute_corpus_coverage(ctx.loaded)  # tiny corpus: unmet
    matrix = build_scenario_coverage_matrix(ctx.loaded)
    result = assess_expansion_targets(coverage, matrix, policy)
    assert result.satisfied is False
    assert any(c.rule == "min_test_accepted" and not c.passed
               for c in result.checks)
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import StableScenarioIdentity
    from verifiednet.evaluation import CandidateScenario

    plan = plan_evaluation_corpus_expansion(
        v1.coverage, (CandidateScenario(
            case_id="c", fault_family="bgp_remote_as_mismatch",
            identity=StableScenarioIdentity(
                template_id="bgp_remote_as_mismatch", scenario_id="s",
                target_node="router_a", target_session="a-b",
                parameters={}, topology_hash=sha256_canonical({"t": 1}),
                backend="frr-compose"),
            planned_runs=1),),
        policy=policy, split_policy=_SPLIT, planned_rejected_runs=0)
    with pytest.raises(CorpusExpansionError):  # unmet targets: no binding
        build_expansion_binding(
            parent=v1.manifest, policy=policy, plan=plan,
            campaign_id="campaign-" + "0" * 16, target_result=result)


def test_parent_and_policy_mismatches_are_refused(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx, v1 = _v1(tmp_path, eval_pipeline)
    wrong_policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "f" * 16,
        source_corpus_digest="ecdig-" + "f" * 24,
        min_total_examples=1, min_accepted_examples=1,
        min_abstention_examples=1, min_validation_accepted=0,
        min_test_accepted=0, min_examples_per_family=1,
        min_identities_per_family=1)
    coverage = compute_corpus_coverage(ctx.loaded)
    matrix = build_scenario_coverage_matrix(ctx.loaded)
    result = assess_expansion_targets(coverage, matrix, wrong_policy)
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import StableScenarioIdentity
    from verifiednet.evaluation import CandidateScenario

    plan = plan_evaluation_corpus_expansion(
        v1.coverage, (CandidateScenario(
            case_id="c", fault_family="bgp_remote_as_mismatch",
            identity=StableScenarioIdentity(
                template_id="bgp_remote_as_mismatch", scenario_id="s",
                target_node="router_a", target_session="a-b",
                parameters={}, topology_hash=sha256_canonical({"t": 1}),
                backend="frr-compose"),
            planned_runs=1),),
        policy=wrong_policy, split_policy=_SPLIT, planned_rejected_runs=0)
    with pytest.raises(CorpusExpansionError):  # policy binds another parent
        build_expansion_binding(
            parent=v1.manifest, policy=wrong_policy, plan=plan,
            campaign_id="campaign-" + "0" * 16, target_result=result)


def test_campaign_run_accounting_is_fail_closed() -> None:
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import DatasetPartitionCounts, StableScenarioIdentity
    from verifiednet.evaluation import (
        CandidateScenario,
        CorpusCoverageStats,
        DistributionEntry,
    )

    coverage = CorpusCoverageStats(
        total=1, accepted=1, abstention=0,
        partition_counts=DatasetPartitionCounts(
            train=1, validation=0, test=0, abstention=0),
        eligible_test_examples=0,
        fault_family_distribution=(
            DistributionEntry(key="bgp_remote_as_mismatch", count=1),),
        duplicate_feature_content_groups=0)
    policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    plan = plan_evaluation_corpus_expansion(
        coverage, (CandidateScenario(
            case_id="c", fault_family="bgp_remote_as_mismatch",
            identity=StableScenarioIdentity(
                template_id="bgp_remote_as_mismatch", scenario_id="s",
                target_node="router_a", target_session="a-b",
                parameters={}, topology_hash=sha256_canonical({"t": 1}),
                backend="frr-compose"),
            planned_runs=2),),
        policy=policy, split_policy=_SPLIT, planned_rejected_runs=1)
    with pytest.raises(Exception, match="expected count"):  # missing run
        build_generation_campaign(
            plan=plan, backend_policy="sim", execution_policy="seq",
            verified_run_ids=("run-1", "run-2"),
            accepted_count=2, rejected_count=0)
    with pytest.raises(Exception, match="sorted and unique"):  # duplicate run
        build_generation_campaign(
            plan=plan, backend_policy="sim", execution_policy="seq",
            verified_run_ids=("run-1", "run-1", "run-2"),
            accepted_count=3, rejected_count=0)
    campaign = build_generation_campaign(
        plan=plan, backend_policy="sim", execution_policy="seq",
        verified_run_ids=("run-1", "run-2", "run-rej"),
        accepted_count=2, rejected_count=1)
    assert campaign.campaign_id.startswith("campaign-")


def test_campaign_store_tamper_and_overwrite(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import DatasetPartitionCounts, StableScenarioIdentity
    from verifiednet.evaluation import (
        CandidateScenario,
        CorpusCoverageStats,
        DistributionEntry,
    )

    coverage = CorpusCoverageStats(
        total=1, accepted=1, abstention=0,
        partition_counts=DatasetPartitionCounts(
            train=1, validation=0, test=0, abstention=0),
        eligible_test_examples=0,
        fault_family_distribution=(
            DistributionEntry(key="bgp_remote_as_mismatch", count=1),),
        duplicate_feature_content_groups=0)
    policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)
    plan = plan_evaluation_corpus_expansion(
        coverage, (CandidateScenario(
            case_id="c", fault_family="bgp_remote_as_mismatch",
            identity=StableScenarioIdentity(
                template_id="bgp_remote_as_mismatch", scenario_id="s",
                target_node="router_a", target_session="a-b",
                parameters={}, topology_hash=sha256_canonical({"t": 1}),
                backend="frr-compose"),
            planned_runs=1),),
        policy=policy, split_policy=_SPLIT, planned_rejected_runs=0)
    campaign = build_generation_campaign(
        plan=plan, backend_policy="sim", execution_policy="seq",
        verified_run_ids=("run-1",), accepted_count=1, rejected_count=0)
    written = write_generation_campaign(
        campaign, plan, tmp_path / "campaigns")
    with pytest.raises(CorpusExpansionError):  # overwrite refused
        write_generation_campaign(campaign, plan, tmp_path / "campaigns")
    for name in ("manifest.json", "planned-scenarios.json",
                 "verified-runs.json"):
        path = written.root / name
        original = path.read_bytes()
        position = len(original) // 2
        path.write_bytes(original[:position]
                         + bytes([original[position] ^ 0xFF])
                         + original[position + 1:])
        assert verify_generation_campaign(written.root).verified is False, name
        path.write_bytes(original)
    assert verify_generation_campaign(written.root).verified is True


def test_comparison_requires_true_lineage(tmp_path: Path, eval_pipeline) -> None:
    _ctx, v1 = _v1(tmp_path, eval_pipeline)
    with pytest.raises(CorpusExpansionError):  # no expansion binding at all
        build_corpus_comparison(v1.manifest, v1.manifest)


def test_v2_registration_requires_expansion_file_consistency(
    tmp_path: Path, eval_pipeline,
) -> None:
    # a v1-style (no-expansion) registration that claims version 2 is fine,
    # but an expansion binding on version 1 is unrepresentable.
    ctx, _v1reg = _v1(tmp_path, eval_pipeline)
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    gen = build_generation_policy(
        generator="g2", split_policy_id=split_ids[0],
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        requested_accepted_runs=2, requested_rejected_runs=1)
    from verifiednet.datasets.verifier import DatasetCheck
    from verifiednet.evaluation import CorpusExpansionBinding

    binding = CorpusExpansionBinding(
        parent_corpus_id="evalcorpus-" + "9" * 16,
        parent_corpus_digest="ecdig-" + "9" * 24,
        expansion_policy_id="ecexp-" + "0" * 16,
        expansion_plan_id="ecplan-" + "0" * 16,
        campaign_id="campaign-" + "0" * 16,
        target_checks=(DatasetCheck(rule="t", passed=True, detail=""),))
    with pytest.raises(EvaluationCorpusError, match="version"):
        register_evaluation_corpus(
            ctx.loaded, corpus_version=1,
            provenance=CorpusProvenance.PROJECT_PERSISTED,
            generation_policy=gen, corpora_root=tmp_path / "other",
            expansion=binding)
