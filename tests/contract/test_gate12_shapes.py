"""Gate 12 contract tests: frozen shapes, unchanged Gate 7/9 semantics,
honest-interpretation locks, and self-validating identities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verifiednet.evaluation import (
    BaseModelPredictorSpec,
    BenchmarkInterpretationPolicy,
    CorpusProvenance,
    InterpretationConclusion,
    PairedComparison,
    PairedComparisonCounts,
    build_default_interpretation_policy,
    build_paired_comparison,
    interpret_paired_comparison,
)

pytestmark = pytest.mark.contract

_ABST = '{"prediction_type": "abstention"}'
_RAS = '{"prediction_type": "diagnosis", "fault_family": "bgp_remote_as_mismatch"}'


def test_gate7_and_gate9_definitions_unchanged() -> None:
    # The Gate 7 task and Gate 9 ranking definitions are untouched by Gate 12:
    # identical canonical ids to the pre-Gate-12 framework.
    from verifiednet.evaluation import compute_ranking, diagnosis_task
    from verifiednet.evaluation.benchmark import ComparisonRow

    task = diagnosis_task()
    assert task.task_id.startswith("task-")
    assert task.scoring_policy_version == 1
    assert task.normalization.policy_id.startswith("norm-")
    rows = (
        ComparisonRow(predictor_identifier="baseline-b", evaluation_id="eval-2",
                      accepted_evaluated=2, accepted_correct=1,
                      exact_match_accuracy="0.500000", abstention_count=1,
                      abstention_correct=1, abstention_accuracy="1.000000",
                      invalid_prediction_count=0, evaluation_count=3),
        ComparisonRow(predictor_identifier="baseline-a", evaluation_id="eval-1",
                      accepted_evaluated=2, accepted_correct=2,
                      exact_match_accuracy="1.000000", abstention_count=1,
                      abstention_correct=0, abstention_accuracy="0.000000",
                      invalid_prediction_count=1, evaluation_count=3),
    )
    ranking = compute_ranking(rows)
    assert [e.predictor_identifier for e in ranking] == \
        ["baseline-a", "baseline-b"]  # accuracy-first, unchanged tie-break


def test_counts_model_enforces_sum_invariants() -> None:
    with pytest.raises(ValidationError):  # quadrants must sum to total
        PairedComparisonCounts(
            total=3, both_correct=1, both_incorrect=1,
            base_correct_trained_incorrect=0,
            base_incorrect_trained_correct=0, predictions_identical=2,
            predictions_differed=1, base_invalid=0, trained_invalid=0,
            abstention_decision_changes=0)
    with pytest.raises(ValidationError):  # identical + differed must sum
        PairedComparisonCounts(
            total=2, both_correct=1, both_incorrect=1,
            base_correct_trained_incorrect=0,
            base_incorrect_trained_correct=0, predictions_identical=0,
            predictions_differed=1, base_invalid=0, trained_invalid=0,
            abstention_decision_changes=0)


def test_interpretation_policy_is_frozen_and_conservative() -> None:
    policy = build_default_interpretation_policy()
    dump = policy.model_dump(mode="json")
    with pytest.raises(ValidationError):  # train can never become evidence
        BenchmarkInterpretationPolicy.model_validate(
            dump | {"exclude_train_partition_from_conclusions": False})
    with pytest.raises(ValidationError):  # fixture corpora stay engineering
        BenchmarkInterpretationPolicy.model_validate(
            dump | {"fixture_corpus_engineering_only": False})
    with pytest.raises(ValidationError):  # tampered id
        BenchmarkInterpretationPolicy.model_validate(
            dump | {"interpretation_policy_id": "interp-" + "0" * 16})
    with pytest.raises(ValidationError):  # threshold change changes id
        BenchmarkInterpretationPolicy.model_validate(
            dump | {"min_eligible_test_examples": 1})


def test_fixture_generated_results_cannot_claim_generalization(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    interp = interpret_paired_comparison(
        result.comparison, policy=build_default_interpretation_policy(),
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    assert interp.engineering_proof_only is True
    assert "fixture_generated_corpus_engineering_proof_only" in interp.qualifiers
    # and no interpretation field can even express a generalization claim
    fields = set(type(interp).model_fields)
    assert not fields & {"generalizes", "production_ready", "learned",
                         "model_quality", "improved_overall"}


def test_train_partition_cannot_drive_the_conclusion(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    # Conclusions derive ONLY from the non-train counts: a comparison whose
    # only changes sit in the train partition must read as no observed effect.
    from verifiednet.evaluation import DiagnosisPrediction

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    comparison = result.comparison
    if comparison.counts_non_train.predictions_differed > 0:
        # rebuild a synthetic view with train-only changes for the contract
        changed_all = comparison.counts_all
        train_only = comparison.model_copy(update={
            "counts_non_train": PairedComparisonCounts(
                total=0, both_correct=0, both_incorrect=0,
                base_correct_trained_incorrect=0,
                base_incorrect_trained_correct=0, predictions_identical=0,
                predictions_differed=0, base_invalid=0, trained_invalid=0,
                abstention_decision_changes=0)})
        interp = interpret_paired_comparison(
            train_only, policy=build_default_interpretation_policy(),
            corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
        assert interp.conclusion is InterpretationConclusion.NO_OBSERVED_EFFECT
        assert changed_all.predictions_differed > 0  # changes existed overall
    assert isinstance(result.disagreements[0].trained_prediction,
                      DiagnosisPrediction)


def test_base_model_spec_is_self_validating_and_pathless(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    from verifiednet.common.canonical import canonical_json_str

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    spec = ctx.base.predictor_spec
    dump = spec.model_dump(mode="json")

    def validate(payload: dict[str, object]) -> BaseModelPredictorSpec:
        return BaseModelPredictorSpec.model_validate_json(json.dumps(payload))

    assert validate(dump) == spec
    with pytest.raises(ValidationError):  # tampered id
        validate(dump | {"predictor_id": "basepred-" + "0" * 24})
    with pytest.raises(ValidationError):  # mutable revision unrepresentable
        validate(dump | {"model_revision": "main"})
    with pytest.raises(ValidationError):  # weight-hash change breaks the id
        validate(dump | {"weights_sha256": "0" * 64})
    rendered = canonical_json_str(spec)
    assert str(tmp_path) not in rendered
    assert str(ctx.base_dir) not in rendered
    fields = set(BaseModelPredictorSpec.model_fields)
    assert not fields & {"path", "model_path", "hostname", "username",
                         "timestamp", "cache_path", "labels"}


def test_paired_comparison_is_frozen_and_id_locked(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    comparison = result.comparison
    with pytest.raises(ValidationError):  # frozen
        comparison.comparison_id = "cmp-" + "0" * 16  # type: ignore[misc]
    dump = json.loads(comparison.model_dump_json())
    with pytest.raises(ValidationError):  # tampered id refused
        PairedComparison.model_validate_json(
            json.dumps(dump | {"comparison_id": "cmp-" + "0" * 16}))
    with pytest.raises(ValidationError):  # extras forbidden
        PairedComparison.model_validate_json(
            json.dumps(dump | {"note": "extra"}))
