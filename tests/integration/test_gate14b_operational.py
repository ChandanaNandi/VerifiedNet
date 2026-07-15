"""Optional Gate 14B operational generation: the FULL identity-first v3
campaign, registered as project evaluation corpus v3 with parent binding to
v2, plus the identity-delta comparison and the readiness assessment that
governs Gate 15 authorisation.

Never runs in offline CI (the full campaign is 230 deterministic runs).
Gated on ``VERIFIEDNET_RUN_GATE14B=1`` + the v1/v2 corpus registration dirs +
the v2 prepared chain dir + an output artifact root. Requires NO ML runtime
and no network.
"""

from __future__ import annotations

import os
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
    plan_identity_first_selection,
    read_evaluation_corpus,
    register_evaluation_corpus,
    verify_corpus_comparison,
    verify_evaluation_corpus,
    verify_generation_campaign,
    verify_identity_selection,
    verify_readiness_assessment,
    write_corpus_comparison,
    write_generation_campaign,
    write_identity_selection,
    write_readiness_assessment,
)

pytestmark = pytest.mark.integration

_RUN = os.environ.get("VERIFIEDNET_RUN_GATE14B") == "1"
_V1_DIR = os.environ.get("VERIFIEDNET_EVAL_CORPUS_V1_DIR", "")
_V2_DIR = os.environ.get("VERIFIEDNET_EVAL_CORPUS_V2_DIR", "")
_V2_PREPARED = os.environ.get("VERIFIEDNET_EVAL_CORPUS_V2_PREPARED_DIR", "")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE14B_OUTPUT_ROOT", "")
_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def _fingerprint(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_full_identity_first_campaign_registers_project_corpus_v3(
    tmp_path: Path, eval_pipeline, gate14b_pool, gate14b_entries,
    monkeypatch,
) -> None:
    if not _RUN:
        pytest.skip("VERIFIEDNET_RUN_GATE14B!=1")
    for name, value in (("VERIFIEDNET_EVAL_CORPUS_V1_DIR", _V1_DIR),
                        ("VERIFIEDNET_EVAL_CORPUS_V2_DIR", _V2_DIR),
                        ("VERIFIEDNET_EVAL_CORPUS_V2_PREPARED_DIR",
                         _V2_PREPARED)):
        if not value or not Path(value).is_dir():
            pytest.skip(f"{name} not set / not a dir")
    if not _OUT_ROOT:
        pytest.skip("VERIFIEDNET_GATE14B_OUTPUT_ROOT is not set")
    out_root = Path(_OUT_ROOT)
    chain_root = out_root / "chain"
    if chain_root.exists():
        pytest.skip(f"campaign chain already persisted: {chain_root}")

    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 14B must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    # verify-then-read both ancestors, fail closed; fingerprint for later
    v1_fingerprint = _fingerprint(Path(_V1_DIR))
    v2_fingerprint = _fingerprint(Path(_V2_DIR))
    read_evaluation_corpus(Path(_V1_DIR))
    v2 = read_evaluation_corpus(Path(_V2_DIR))

    # the Gate 14 lesson, measured on the REAL v2 prepared corpus
    from verifiednet.datasets import load_prepared

    v2_prepared = load_prepared(Path(_V2_PREPARED))
    assert v2_prepared.manifest.prepared_digest \
        == v2.manifest.prepared_digest
    v2_identities = compute_partition_identity_coverage(v2_prepared)
    assert v2_identities.counts.test_identities < 8  # why Gate 14B exists

    # frozen v3 policies + the identity-first selection over the full pool
    policy = build_expansion_policy_v3(
        source_corpus_id=v2.manifest.evaluation_corpus_id,
        source_corpus_digest=v2.manifest.corpus_digest)
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id)
    pool, topologies_by_hash = gate14b_pool()
    selection = plan_identity_first_selection(
        pool, expansion_policy=policy, identity_policy=identity_policy,
        split_policy=_SPLIT, planned_rejected_identities=12)
    assert len(selection.entries) == 58
    assert selection.identity_counts.test_identities == 12
    assert selection.identity_counts.validation_identities == 14
    written_selection = write_identity_selection(
        selection, out_root / "identity-selections")
    assert verify_identity_selection(written_selection.root).verified is True

    # the FULL campaign: 206 accepted + 24 rejected deterministic runs
    accepted, rejected = gate14b_entries(
        selection=selection, topologies_by_hash=topologies_by_hash)
    assert (len(accepted), len(rejected)) == (206, 24)
    chain_root.mkdir(parents=True)
    ctx = eval_pipeline(chain_root, accepted=accepted, rejected=rejected)
    coverage = compute_corpus_coverage(ctx.loaded)
    matrix = build_scenario_coverage_matrix(ctx.loaded)
    v3_identities = compute_partition_identity_coverage(ctx.loaded)

    from collections import Counter

    from verifiednet.common.hashing import sha256_canonical
    from verifiednet.datasets.models import StableScenarioIdentity
    from verifiednet.evaluation import CandidateScenario

    counts: Counter[tuple[str, str]] = Counter()
    keyed = {}
    for case, topo, _run_id in accepted:
        counts[(case.case_id, topo.name)] += 1
        keyed[(case.case_id, topo.name)] = (case, topo)
    candidates = tuple(
        CandidateScenario(
            case_id=case_id, fault_family=case.scenario.template_id,
            identity=StableScenarioIdentity(
                template_id=case.scenario.template_id,
                scenario_id=case.scenario.scenario_id,
                target_node=str(case.scenario.parameters.get(
                    "target_node", "")),
                target_session=str(case.scenario.parameters.get(
                    "target_session", "")),
                parameters={k: case.scenario.parameters[k]
                            for k in sorted(case.scenario.parameters)},
                topology_hash=sha256_canonical(topo),
                backend="frr-compose"),
            planned_runs=counts[(case_id, tn)])
        for (case_id, tn), (case, topo) in keyed.items())
    plan = plan_evaluation_corpus_expansion(
        v2.coverage, candidates, policy=policy, split_policy=_SPLIT,
        planned_rejected_runs=len(rejected))
    # split prediction verified EXACT after projection
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
        campaign, plan, out_root / "generation-campaigns")
    assert verify_generation_campaign(written_campaign.root).verified is True

    binding = build_expansion_binding(
        parent=v2.manifest, policy=policy, plan=plan,
        campaign_id=campaign.campaign_id, target_result=target_result)
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id
                        for e in ctx.loaded.examples
                        if e.trace.partition.value != "abstention"})
    v3_written = register_evaluation_corpus(
        ctx.loaded, corpus_version=3,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=build_generation_policy(
            generator=("gate14b identity-first coverage campaign: "
                       "deterministic simulated catalog chain over 58 "
                       "selected stable identities x 6 approved topology "
                       "variants (12 held-out test, 14 validation)"),
            split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=len(accepted),
            requested_rejected_runs=len(rejected)),
        corpora_root=out_root / "evaluation-corpora", expansion=binding)
    assert verify_evaluation_corpus(v3_written.root).verified is True
    v3 = read_evaluation_corpus(v3_written.root)
    versions = list_evaluation_corpus_versions(out_root
                                               / "evaluation-corpora")
    assert [m.corpus_version for m in versions] == [3]

    # mandatory growth + diversity facts
    assert v3.coverage.eligible_test_examples >= 30
    assert v3.coverage.partition_counts.validation >= 24
    assert v3_identities.counts.test_identities >= 8
    assert v3_identities.counts.validation_identities >= 6
    assert v3_identities.counts.test_identities > \
        v2_identities.counts.test_identities
    assert len(v3.coverage.topology_distribution) == 6
    assert float(v3.coverage.class_imbalance_ratio) <= 1.5

    report = build_corpus_comparison_with_identity_deltas(
        v2.manifest, v3.manifest, parent_identities=v2_identities,
        descendant_identities=v3_identities)
    deltas = {d.metric: (d.before, d.after) for d in report.deltas}
    assert deltas["distinct_test_identities"][1] > \
        deltas["distinct_test_identities"][0]
    assert deltas["distinct_validation_identities"][1] > \
        deltas["distinct_validation_identities"][0]
    written_cmp = write_corpus_comparison(
        report, out_root / "corpus-comparisons")
    assert verify_corpus_comparison(written_cmp.root).verified is True

    # the readiness verdict that governs Gate 15 authorisation
    assessment = assess_evaluation_readiness(
        corpus=v3, identity_coverage=v3_identities,
        expansion_policy=policy, identity_policy=identity_policy)
    assert assessment.outcome == "ready_for_controlled_experiment"
    written_assessment = write_readiness_assessment(
        assessment, out_root / "readiness-assessments")
    assert verify_readiness_assessment(written_assessment.root).verified \
        is True

    # v1 and v2 remained byte-identical
    assert _fingerprint(Path(_V1_DIR)) == v1_fingerprint
    assert _fingerprint(Path(_V2_DIR)) == v2_fingerprint
