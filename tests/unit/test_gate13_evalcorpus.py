"""Gate 13 unit tests: coverage stats, quality verification, registration."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.evaluation import (
    CorpusProvenance,
    audit_evaluation_corpus,
    build_generation_policy,
    compute_corpus_coverage,
    list_evaluation_corpus_versions,
    read_evaluation_corpus,
    register_evaluation_corpus,
    verify_corpus_quality,
    verify_evaluation_corpus,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("ras-rev", "run-b"), ("nr-ref", "run-c"),
        ("if-ref", "run-d"), ("pf-ref", "run-e"), ("pf-rev", "run-f")]


def _policy(ctx):
    manifest = ctx.loaded.manifest
    split_ids = {e.trace.split_policy_id for e in ctx.loaded.examples}
    return build_generation_policy(
        generator="verifiednet deterministic simulated catalog chain",
        split_policy_id=sorted(split_ids)[0],
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        requested_accepted_runs=len(_ACC), requested_rejected_runs=1)


def test_coverage_statistics_are_exact(tmp_path: Path, eval_pipeline) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    coverage = compute_corpus_coverage(ctx.loaded)
    assert coverage.total == len(ctx.loaded.examples) == 7
    assert coverage.accepted == 6
    assert coverage.abstention == 1
    families = {e.key: e.count for e in coverage.fault_family_distribution}
    assert sum(families.values()) == 6
    assert len(families) == 4  # all four fault families covered
    assert families["bgp_remote_as_mismatch"] == 2
    assert families["bgp_prefix_withdrawal"] == 2
    scenarios = {e.key for e in coverage.scenario_distribution}
    assert len(scenarios) == 6  # one scenario per accepted case
    assert {e.key for e in coverage.rejection_distribution} \
        == {"precondition_failed"}
    parts = coverage.partition_counts
    assert parts.train + parts.validation + parts.test == 6
    assert parts.abstention == 1
    assert coverage.eligible_test_examples == parts.test
    balance_total = sum(b.count for b in coverage.split_balance)
    assert balance_total == 6
    # deterministic
    assert compute_corpus_coverage(ctx.loaded) == coverage


def test_quality_verification_passes_and_reports(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    quality = verify_corpus_quality(ctx.loaded)
    assert quality.verified is True, quality.failures
    rules = {c.rule for c in quality.checks}
    assert {"unique_example_ids", "no_split_leakage",
            "no_malformed_examples", "no_missing_evidence",
            "uniform_feature_policy", "uniform_label_policy"} <= rules
    # imbalance is REPORTED, never a failure
    assert any(r.startswith("class_imbalance_ratio=")
               for r in quality.imbalance_reports)
    assert any(r.startswith("eligible_test_examples=")
               for r in quality.imbalance_reports)


def test_registration_round_trip_and_audit(
    tmp_path: Path, eval_pipeline,
) -> None:
    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy = _policy(ctx)
    assert policy.generation_policy_id.startswith("ecgen-")
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=policy,
        corpora_root=tmp_path / "evaluation-corpora")
    assert written.evaluation_corpus_id.startswith("evalcorpus-")
    assert written.corpus_digest.startswith("ecdig-")
    verification = verify_evaluation_corpus(written.root)
    assert verification.verified is True, verification.failures
    loaded = read_evaluation_corpus(written.root)
    assert loaded.manifest.corpus_version == 1
    assert loaded.manifest.provenance is CorpusProvenance.PROJECT_PERSISTED
    assert loaded.manifest.prepared_digest == \
        ctx.loaded.manifest.prepared_digest
    assert loaded.coverage == compute_corpus_coverage(ctx.loaded)
    assert loaded.quality.verified is True
    ok, checks = audit_evaluation_corpus(written.root, ctx.loaded)
    assert ok, [c for c in checks if not c.passed]
    versions = list_evaluation_corpus_versions(tmp_path / "evaluation-corpora")
    assert [m.evaluation_corpus_id for m in versions] == \
        [written.evaluation_corpus_id]


def test_version_identity_is_content_addressed(
    tmp_path: Path, eval_pipeline,
) -> None:
    from verifiednet.evaluation import derive_evaluation_corpus_id

    ctx = eval_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    policy = _policy(ctx)
    kwargs = {
        "corpus_version": 1,
        "prepared_digest": ctx.loaded.manifest.prepared_digest,
        "generation_policy_id": policy.generation_policy_id,
        "provenance": CorpusProvenance.PROJECT_PERSISTED,
    }
    base = derive_evaluation_corpus_id(**kwargs)  # type: ignore[arg-type]
    assert base == derive_evaluation_corpus_id(**kwargs)  # type: ignore[arg-type]
    for field, mutated in (
            ("corpus_version", 2),
            ("prepared_digest", "prepdig-" + "f" * 24),
            ("generation_policy_id", "ecgen-" + "f" * 16),
            ("provenance", CorpusProvenance.FIXTURE_GENERATED)):
        changed = dict(kwargs)
        changed[field] = mutated
        assert derive_evaluation_corpus_id(**changed) != base, field  # type: ignore[arg-type]
