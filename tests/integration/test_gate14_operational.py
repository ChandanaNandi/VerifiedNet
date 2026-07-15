"""Optional Gate 14 operational generation: the FULL expansion campaign,
registered as project evaluation corpus v2 with parent binding to v1.

Never runs in offline CI (the full campaign is ~170 deterministic runs).
Gated on ``VERIFIEDNET_RUN_GATE14=1`` + the v1 corpus registration dir +
an output artifact root. Requires NO ML runtime and no network.
"""

from __future__ import annotations

import os
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
    plan_evaluation_corpus_expansion,
    read_evaluation_corpus,
    register_evaluation_corpus,
    verify_corpus_comparison,
    verify_evaluation_corpus,
    verify_generation_campaign,
    write_corpus_comparison,
    write_generation_campaign,
)

pytestmark = pytest.mark.integration

_RUN = os.environ.get("VERIFIEDNET_RUN_GATE14") == "1"
_V1_DIR = os.environ.get("VERIFIEDNET_EVAL_CORPUS_V1_DIR", "")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE14_OUTPUT_ROOT", "")
_SPLIT = SplitPolicy(salt="gate6", train_buckets=8000,
                     validation_buckets=1000, test_buckets=1000)


def test_full_expansion_campaign_registers_project_corpus_v2(
    tmp_path: Path, eval_pipeline, expansion_entries, monkeypatch,
) -> None:
    if not _RUN:
        pytest.skip("VERIFIEDNET_RUN_GATE14!=1")
    if not _V1_DIR or not Path(_V1_DIR).is_dir():
        pytest.skip("VERIFIEDNET_EVAL_CORPUS_V1_DIR not set / not a dir")
    if not _OUT_ROOT:
        pytest.skip("VERIFIEDNET_GATE14_OUTPUT_ROOT is not set")
    out_root = Path(_OUT_ROOT)
    chain_root = out_root / "chain"
    if chain_root.exists():
        pytest.skip(f"campaign chain already persisted: {chain_root}")

    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 14 must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    v1 = read_evaluation_corpus(Path(_V1_DIR))  # verify-then-read, fail closed
    v1_fingerprint = {p.name: p.read_bytes()
                      for p in sorted(Path(_V1_DIR).iterdir()) if p.is_file()}

    accepted, rejected = expansion_entries()  # the FULL campaign
    chain_root.mkdir(parents=True)
    ctx = eval_pipeline(chain_root, accepted=accepted, rejected=rejected)
    coverage = compute_corpus_coverage(ctx.loaded)
    matrix = build_scenario_coverage_matrix(ctx.loaded)

    policy = build_expansion_policy(
        source_corpus_id=v1.manifest.evaluation_corpus_id,
        source_corpus_digest=v1.manifest.corpus_digest)
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
            planned_runs=counts[(case_id, topo.name)])
        for (case_id, _tn), (case, topo) in keyed.items())
    plan = plan_evaluation_corpus_expansion(
        v1.coverage, candidates, policy=policy, split_policy=_SPLIT,
        planned_rejected_runs=len(rejected))
    # split prediction verified EXACT after projection
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
        campaign, plan, out_root / "generation-campaigns")
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
            generator=("gate14 expansion campaign: deterministic simulated "
                       "catalog chain over 30 stable identities x 3 "
                       "approved topology variants"),
            split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=len(accepted),
            requested_rejected_runs=len(rejected)),
        corpora_root=out_root / "evaluation-corpora", expansion=binding)
    assert verify_evaluation_corpus(v2_written.root).verified is True
    v2 = read_evaluation_corpus(v2_written.root)

    # mandatory growth facts
    assert v2.coverage.eligible_test_examples >= 20
    assert v2.coverage.partition_counts.validation >= 12
    assert v2.coverage.eligible_test_examples > \
        v1.coverage.eligible_test_examples
    assert len(v2.coverage.topology_distribution) == 3

    report = build_corpus_comparison(v1.manifest, v2.manifest)
    written_cmp = write_corpus_comparison(
        report, out_root / "corpus-comparisons")
    assert verify_corpus_comparison(written_cmp.root).verified is True

    # v1 remained byte-identical
    after = {p.name: p.read_bytes()
             for p in sorted(Path(_V1_DIR).iterdir()) if p.is_file()}
    assert after == v1_fingerprint
