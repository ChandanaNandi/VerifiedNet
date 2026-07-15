"""Optional Gate 13 integration: diagnostics over the REAL Gate 12 artifacts,
and registration of the first PROJECT-PERSISTED evaluation corpus.

Never runs in offline CI. Both tests are explicitly env-gated and require no
ML runtime — they read persisted artifacts and build/register derived ones.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    CorpusProvenance,
    InvalidOutputCategory,
    build_generation_policy,
    compute_parser_statistics,
    read_evaluation,
    register_evaluation_corpus,
    verify_evaluation_corpus,
)

pytestmark = pytest.mark.integration

_RUN = os.environ.get("VERIFIEDNET_RUN_REAL_GATE13") == "1"
_GATE12_OUT = os.environ.get("VERIFIEDNET_GATE12_OUTPUT_ROOT", "")
_CORPUS_ROOT = os.environ.get("VERIFIEDNET_EVAL_CORPUS_ROOT", "")


def test_real_gate12_outputs_are_diagnosed(tmp_path: Path) -> None:
    if not _RUN:
        pytest.skip("VERIFIEDNET_RUN_REAL_GATE13!=1")
    if not _GATE12_OUT or not Path(_GATE12_OUT, "evaluations").is_dir():
        pytest.skip("VERIFIEDNET_GATE12_OUTPUT_ROOT not set / no evaluations")
    evaluations = sorted(Path(_GATE12_OUT, "evaluations").iterdir())
    assert len(evaluations) >= 2
    categories: set[InvalidOutputCategory] = set()
    for directory in evaluations:
        run = read_evaluation(directory)  # verify-then-read, fail closed
        statistics = compute_parser_statistics(run)
        assert statistics.total == len(run.records)
        categories.update(f.category for f in statistics.failure_categories)
    # the two REAL Gate 12 failure modes are now named, first-class evidence
    assert InvalidOutputCategory.PROSE_WRAPPED_JSON in categories
    assert InvalidOutputCategory.DEGENERATE_REPETITION in categories


def test_project_evaluation_corpus_v1_is_registered(
    tmp_path: Path, eval_pipeline,
) -> None:
    if not _RUN:
        pytest.skip("VERIFIEDNET_RUN_REAL_GATE13!=1")
    if not _CORPUS_ROOT:
        pytest.skip("VERIFIEDNET_EVAL_CORPUS_ROOT is not set")
    root = Path(_CORPUS_ROOT)
    chain_root = root / "chain"
    if chain_root.exists():
        pytest.skip(f"corpus chain already persisted: {chain_root}")
    chain_root.mkdir(parents=True)

    # The project evaluation corpus v1: every catalog case twice (18 accepted
    # runs across all four families) + 4 rejected runs, deterministic chain.
    cases = ("ras-ref", "ras-rev", "ras-alt", "nr-ref", "nr-rev",
             "if-ref", "if-rev", "pf-ref", "pf-rev")
    accepted = [(case, f"run-{case}-{i}") for case in cases for i in (1, 2)]
    rejected = [f"run-rej-{i}" for i in (1, 2, 3, 4)]
    ctx = eval_pipeline(chain_root, accepted=accepted, rejected=rejected)
    manifest = ctx.loaded.manifest
    assert manifest.example_count == len(accepted) + len(rejected)
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    policy = build_generation_policy(
        generator=("verifiednet deterministic simulated catalog chain "
                   "(gate6 verified run artifacts; all 9 catalog cases x2)"),
        split_policy_id=split_ids[0],
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        requested_accepted_runs=len(accepted),
        requested_rejected_runs=len(rejected))
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=policy,
        corpora_root=root / "evaluation-corpora")
    assert verify_evaluation_corpus(written.root).verified is True
    from verifiednet.evaluation import read_evaluation_corpus

    registration = read_evaluation_corpus(written.root)
    coverage = registration.coverage
    assert coverage.total == len(accepted) + len(rejected)
    assert len(coverage.fault_family_distribution) == 4
    # the whole point of the project corpus: eligible test examples exist
    assert coverage.eligible_test_examples >= 1
