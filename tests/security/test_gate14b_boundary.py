"""Gate 14B security proofs: model-free and network-free planning, no
model/benchmark input channel, artifacts free of host facts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.security


def test_gate14b_planning_is_model_free_and_network_free(
    tmp_path: Path, eval_pipeline, gate14b_selection_builder, monkeypatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 14B must not use the network")

    import socket
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    import verifiednet.training.hfexecutor as hfexec
    from verifiednet.training import realckptstore

    def _trainboom(*args: object, **kwargs: object) -> object:
        raise AssertionError("Gate 14B must never train or write checkpoints")

    monkeypatch.setattr(hfexec.StubTrainingEngine, "run", _trainboom)
    monkeypatch.setattr(hfexec.HFTrainingEngine, "run", _trainboom)
    monkeypatch.setattr(realckptstore, "write_real_checkpoint", _trainboom)

    from verifiednet.evaluation import (
        CorpusProvenance,
        assess_evaluation_readiness,
        build_expansion_policy,
        build_generation_policy,
        build_identity_coverage_policy,
        compute_partition_identity_coverage,
        read_evaluation_corpus,
        register_evaluation_corpus,
        write_identity_selection,
        write_readiness_assessment,
    )

    selection, _ip, _pp, _topologies = gate14b_selection_builder()
    write_identity_selection(selection, tmp_path / "identity-selections")
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.FIXTURE_GENERATED,
        generation_policy=build_generation_policy(
            generator="g", split_policy_id=split_ids[0],
            feature_policy_id=manifest.feature_policy_id,
            label_policy_id=manifest.label_policy_id,
            requested_accepted_runs=1, requested_rejected_runs=1),
        corpora_root=tmp_path / "corpora")
    policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24,
        min_total_examples=1, min_accepted_examples=1,
        min_abstention_examples=1, min_validation_accepted=0,
        min_test_accepted=0, min_examples_per_family=1,
        min_identities_per_family=1)
    assessment = assess_evaluation_readiness(
        corpus=read_evaluation_corpus(written.root),
        identity_coverage=compute_partition_identity_coverage(ctx.loaded),
        expansion_policy=policy,
        identity_policy=build_identity_coverage_policy(
            expansion_policy_id=policy.expansion_policy_id))
    write_readiness_assessment(assessment, tmp_path / "readiness-assessments")
    # the whole planning + readiness chain imported NO ML runtime
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_planner_has_no_channel_for_model_or_benchmark_inputs() -> None:
    import inspect

    from verifiednet.evaluation import (
        assess_evaluation_readiness,
        plan_identity_first_selection,
    )

    planner = set(inspect.signature(
        plan_identity_first_selection).parameters)
    assert planner == {"pool", "expansion_policy", "identity_policy",
                       "split_policy", "planned_rejected_identities"}
    readiness = set(inspect.signature(
        assess_evaluation_readiness).parameters)
    assert readiness == {"corpus", "identity_coverage", "expansion_policy",
                         "identity_policy"}


def test_gate14b_artifacts_carry_no_host_or_environment_facts(
    gate14b_selection_builder, tmp_path: Path,
) -> None:
    import os

    from verifiednet.evaluation import write_identity_selection

    selection, _ip, _pp, _topologies = gate14b_selection_builder()
    written = write_identity_selection(
        selection, tmp_path / "identity-selections")
    for name in ("manifest.json", "summary.json"):
        payload = (written.root / name).read_text()
        assert str(tmp_path) not in payload
        assert os.getcwd() not in payload
        assert str(Path.home()) not in payload
        for variable in ("HOME", "USER", "HOSTNAME", "PATH"):
            value = os.environ.get(variable, "")
            if len(value) > 3:  # short values collide by accident
                assert value not in payload, variable
