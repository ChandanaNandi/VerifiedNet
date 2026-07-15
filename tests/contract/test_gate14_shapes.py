"""Gate 14 contract tests: frozen models, append-only versions, fixed split,
no model/benchmark facts in planning, unmet targets unrepresentable."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation import (
    CorpusExpansionBinding,
    EvaluationCorpusExpansionPolicy,
    FamilyCoverage,
    PairedPredictorFacts,
    ScenarioCoverageMatrix,
    VerifiedRunGenerationCampaign,
    build_expansion_policy,
)

pytestmark = pytest.mark.contract


def _policy() -> EvaluationCorpusExpansionPolicy:
    return build_expansion_policy(
        source_corpus_id="evalcorpus-" + "0" * 16,
        source_corpus_digest="ecdig-" + "0" * 24)


def test_expansion_policy_is_frozen_and_id_locked() -> None:
    policy = _policy()
    dump = policy.model_dump(mode="json")
    with pytest.raises(ValidationError):  # tampered id
        EvaluationCorpusExpansionPolicy.model_validate_json(json.dumps(
            dump | {"expansion_policy_id": "ecexp-" + "0" * 16}))
    with pytest.raises(ValidationError):  # any threshold change breaks the id
        EvaluationCorpusExpansionPolicy.model_validate_json(json.dumps(
            dump | {"min_test_accepted": 1}))
    with pytest.raises(ValidationError):  # extras forbidden
        EvaluationCorpusExpansionPolicy.model_validate_json(json.dumps(
            dump | {"note": "x"}))
    with pytest.raises(ValidationError):  # frozen
        policy.min_test_accepted = 1  # type: ignore[misc]


def test_unmet_mandatory_targets_are_unrepresentable_in_a_binding() -> None:
    failing = (DatasetCheck(rule="min_test_accepted", passed=False,
                            detail="5"),)
    with pytest.raises(ValidationError):
        CorpusExpansionBinding(
            parent_corpus_id="evalcorpus-" + "0" * 16,
            parent_corpus_digest="ecdig-" + "0" * 24,
            expansion_policy_id="ecexp-" + "0" * 16,
            expansion_plan_id="ecplan-" + "0" * 16,
            campaign_id="campaign-" + "0" * 16,
            target_checks=failing)
    with pytest.raises(ValidationError):  # the flag itself is Literal[True]
        CorpusExpansionBinding.model_validate_json(json.dumps({
            "schema_version": 1,
            "parent_corpus_id": "evalcorpus-" + "0" * 16,
            "parent_corpus_digest": "ecdig-" + "0" * 24,
            "expansion_policy_id": "ecexp-" + "0" * 16,
            "expansion_plan_id": "ecplan-" + "0" * 16,
            "campaign_id": "campaign-" + "0" * 16,
            "targets_satisfied": False,
            "target_checks": [{"schema_version": 1, "rule": "x",
                               "passed": True, "detail": ""}],
            "advisory_findings": []}))


def test_planning_models_have_no_model_or_benchmark_fields() -> None:
    for model in (ScenarioCoverageMatrix, FamilyCoverage,
                  EvaluationCorpusExpansionPolicy,
                  VerifiedRunGenerationCampaign):
        fields = set(model.model_fields)
        assert not fields & {"accuracy", "ranking", "rank", "prediction",
                             "predictions", "correct", "benchmark_id",
                             "evaluation_id", "model_identifier",
                             "checkpoint_id", "baseline_id"}, model


def test_v1_registration_cannot_be_mutated(
    tmp_path, eval_pipeline,
) -> None:
    from verifiednet.evaluation import (
        CorpusProvenance,
        EvaluationCorpusError,
        build_generation_policy,
        register_evaluation_corpus,
    )

    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a")],
                        rejected=["run-rej"])
    manifest = ctx.loaded.manifest
    split_ids = sorted({e.trace.split_policy_id for e in ctx.loaded.examples})
    policy = build_generation_policy(
        generator="g", split_policy_id=split_ids[0],
        feature_policy_id=manifest.feature_policy_id,
        label_policy_id=manifest.label_policy_id,
        requested_accepted_runs=1, requested_rejected_runs=1)
    written = register_evaluation_corpus(
        ctx.loaded, corpus_version=1,
        provenance=CorpusProvenance.PROJECT_PERSISTED,
        generation_policy=policy, corpora_root=tmp_path / "corpora")
    with pytest.raises(EvaluationCorpusError):  # same identity: refused
        register_evaluation_corpus(
            ctx.loaded, corpus_version=1,
            provenance=CorpusProvenance.PROJECT_PERSISTED,
            generation_policy=policy, corpora_root=tmp_path / "corpora")
    # the manifest model refuses a version claiming itself as parent
    raw = json.loads((written.root / "manifest.json").read_bytes())
    from verifiednet.evaluation import EvaluationCorpusManifest

    with pytest.raises(ValidationError):
        EvaluationCorpusManifest.model_validate_json(json.dumps(raw | {
            "expansion": {
                "schema_version": 1,
                "parent_corpus_id": raw["evaluation_corpus_id"],
                "parent_corpus_digest": raw["corpus_digest"],
                "expansion_policy_id": "ecexp-" + "0" * 16,
                "expansion_plan_id": "ecplan-" + "0" * 16,
                "campaign_id": "campaign-" + "0" * 16,
                "targets_satisfied": True,
                "target_checks": [{"schema_version": 1, "rule": "x",
                                   "passed": True, "detail": ""}],
                "advisory_findings": []}}))


def test_split_policy_is_fixed_and_source_kind_locked() -> None:
    # The Gate 6 split policy in use everywhere in this repository:
    from verifiednet.datasets.models import SplitPolicy
    from verifiednet.evaluation import EvaluationCorpusGenerationPolicy

    policy = SplitPolicy(salt="gate6", train_buckets=8000,
                         validation_buckets=1000, test_buckets=1000)
    assert policy.algorithm_version == 1  # Literal-locked upstream
    with pytest.raises(ValidationError):  # ratio changes cannot be partial
        SplitPolicy(salt="gate6", train_buckets=9000,
                    validation_buckets=1000, test_buckets=1000)
    # corpus generation can never claim a non-verified-artifact source
    dump = {
        "schema_version": 1, "policy_version": 1,
        "source_kind": "hand_authored", "generator": "g",
        "split_policy_id": "s", "feature_policy_id": "f",
        "label_policy_id": "l", "requested_accepted_runs": 1,
        "requested_rejected_runs": 1,
        "generation_policy_id": "ecgen-" + "0" * 16}
    with pytest.raises(ValidationError):
        EvaluationCorpusGenerationPolicy.model_validate_json(json.dumps(dump))


def test_facts_model_is_untouched_by_gate14() -> None:
    # Gate 12's matched-pair facts (a model-facing contract) gained no fields.
    assert set(PairedPredictorFacts.model_fields) == {
        "schema_version", "role", "predictor_id", "baseline_id",
        "prompt_template_id", "decoding_config_id", "normalization_policy_id",
        "backend_family", "inference_precision", "device_policy_id",
        "compatibility_id"}
