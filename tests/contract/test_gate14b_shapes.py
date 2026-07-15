"""Gate 14B contract tests: frozen identity models, self-validating
readiness verdicts, split-leakage-unrepresentable coverage, run bounds."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from verifiednet.evaluation import (
    EvaluationReadinessAssessment,
    IdentityCoveragePolicy,
    IdentityFirstSelection,
    PartitionIdentityCoverage,
    build_identity_coverage_policy,
)

pytestmark = pytest.mark.contract


def _policy() -> IdentityCoveragePolicy:
    return build_identity_coverage_policy(
        expansion_policy_id="ecexp-" + "0" * 16)


def test_identity_policy_is_frozen_and_id_locked() -> None:
    policy = _policy()
    dump = policy.model_dump(mode="json")
    with pytest.raises(ValidationError):  # tampered id
        IdentityCoveragePolicy.model_validate_json(json.dumps(
            dump | {"identity_policy_id": "icpol-" + "0" * 16}))
    with pytest.raises(ValidationError):  # any threshold change breaks the id
        IdentityCoveragePolicy.model_validate_json(json.dumps(
            dump | {"min_distinct_test_identities": 1}))
    with pytest.raises(ValidationError):  # extras forbidden
        IdentityCoveragePolicy.model_validate_json(json.dumps(
            dump | {"note": "x"}))
    with pytest.raises(ValidationError):  # frozen
        policy.min_distinct_test_identities = 1  # type: ignore[misc]


def test_run_rule_outside_bounds_is_unrepresentable() -> None:
    with pytest.raises(ValidationError, match="outside"):
        build_identity_coverage_policy(
            expansion_policy_id="ecexp-" + "0" * 16,
            runs_per_train_identity=5)  # max is 4
    with pytest.raises(ValidationError, match="outside"):
        build_identity_coverage_policy(
            expansion_policy_id="ecexp-" + "0" * 16,
            runs_per_test_identity=1)  # min is 2
    with pytest.raises(ValidationError):
        build_identity_coverage_policy(
            expansion_policy_id="ecexp-" + "0" * 16,
            min_runs_per_identity=4, max_runs_per_identity=2)


def test_split_leakage_is_unrepresentable_in_identity_coverage() -> None:
    with pytest.raises(ValidationError, match="leakage"):
        PartitionIdentityCoverage.model_validate_json(json.dumps({
            "schema_version": 1, "prepared_digest": "d",
            "train_group_ids": ["grp-" + "0" * 16],
            "validation_group_ids": [],
            "test_group_ids": ["grp-" + "0" * 16],
            "abstention_group_ids": []}))
    with pytest.raises(ValidationError, match="sorted"):
        PartitionIdentityCoverage.model_validate_json(json.dumps({
            "schema_version": 1, "prepared_digest": "d",
            "train_group_ids": ["grp-b", "grp-a"],
            "validation_group_ids": [], "test_group_ids": [],
            "abstention_group_ids": []}))


def test_readiness_verdict_cannot_contradict_its_own_facts(
    tmp_path, eval_pipeline,
) -> None:
    from verifiednet.evaluation import (
        CorpusProvenance,
        assess_evaluation_readiness,
        build_expansion_policy,
        build_generation_policy,
        compute_partition_identity_coverage,
        read_evaluation_corpus,
        register_evaluation_corpus,
    )

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
    corpus = read_evaluation_corpus(written.root)
    policy = build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24,
        min_total_examples=1, min_accepted_examples=1,
        min_abstention_examples=1, min_validation_accepted=0,
        min_test_accepted=0, min_examples_per_family=1,
        min_identities_per_family=1)
    identity_policy = build_identity_coverage_policy(
        expansion_policy_id=policy.expansion_policy_id,
        min_distinct_test_identities=1,
        min_distinct_validation_identities=1, min_topology_variants=1)
    assessment = assess_evaluation_readiness(
        corpus=corpus,
        identity_coverage=compute_partition_identity_coverage(ctx.loaded),
        expansion_policy=policy, identity_policy=identity_policy)
    dump = assessment.model_dump(mode="json")
    # this tiny corpus has NO held-out identities -> low diversity, not ready
    assert assessment.outcome == "coverage_threshold_met_but_low_diversity"
    with pytest.raises(ValidationError, match="outcome"):  # claimed readiness
        EvaluationReadinessAssessment.model_validate_json(json.dumps(
            dump | {"outcome": "ready_for_controlled_experiment"}))
    with pytest.raises(ValidationError):  # tampered facts break the checks
        EvaluationReadinessAssessment.model_validate_json(json.dumps(
            dump | {"distinct_test_identities": 99}))
    with pytest.raises(ValidationError):  # tampered id
        EvaluationReadinessAssessment.model_validate_json(json.dumps(
            dump | {"assessment_id": "ready-" + "0" * 16}))
    with pytest.raises(ValidationError):  # extras forbidden
        EvaluationReadinessAssessment.model_validate_json(json.dumps(
            dump | {"note": "x"}))


def test_selection_ordering_and_uniqueness_are_enforced(
    gate14b_selection_builder,
) -> None:
    selection, _ip, _pp, _topologies = gate14b_selection_builder()
    dump = selection.model_dump(mode="json")
    reordered = dump | {"entries": list(reversed(dump["entries"]))}
    with pytest.raises(ValidationError, match="ordered"):
        IdentityFirstSelection.model_validate_json(json.dumps(reordered))
    duplicated = dump | {
        "entries": [dump["entries"][0]] + dump["entries"]}
    with pytest.raises(ValidationError):
        IdentityFirstSelection.model_validate_json(json.dumps(duplicated))
    with pytest.raises(ValidationError):  # tampered id
        IdentityFirstSelection.model_validate_json(json.dumps(
            dump | {"selection_id": "icsel-" + "0" * 16}))


def test_identity_models_have_no_model_or_benchmark_fields() -> None:
    for model in (IdentityCoveragePolicy, IdentityFirstSelection,
                  EvaluationReadinessAssessment, PartitionIdentityCoverage):
        fields = set(model.model_fields)
        assert not fields & {"accuracy", "ranking", "rank", "prediction",
                             "predictions", "correct", "benchmark_id",
                             "evaluation_id", "model_identifier",
                             "checkpoint_id", "baseline_id"}, model
