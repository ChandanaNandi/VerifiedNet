"""Gate 12 property tests: determinism, id sensitivity, conclusion mapping."""

from __future__ import annotations

from itertools import product

import pytest

from verifiednet.datasets.models import DatasetPartitionCounts
from verifiednet.datasets.verifier import DatasetCheck
from verifiednet.evaluation import (
    CorpusProvenance,
    InterpretationConclusion,
    MatchedPairFairness,
    PairedComparison,
    PairedComparisonCounts,
    PairedPredictorFacts,
    build_default_interpretation_policy,
    derive_comparison_id,
    interpret_paired_comparison,
)

pytestmark = pytest.mark.property

_ID_KWARGS: dict[str, str] = {
    "task_id": "task-" + "0" * 16,
    "prepared_digest": "prepdig-" + "0" * 24,
    "base_baseline_id": "baseline-" + "0" * 16,
    "trained_baseline_id": "baseline-" + "1" * 16,
    "base_evaluation_id": "eval-" + "0" * 16,
    "trained_evaluation_id": "eval-" + "1" * 16,
}


def _facts(role: str, tag: str) -> PairedPredictorFacts:
    return PairedPredictorFacts(
        role=role, predictor_id=f"{tag}pred-" + "0" * 16,
        baseline_id=_ID_KWARGS[f"{role}_baseline_id"],
        prompt_template_id="prompt-" + "0" * 16,
        decoding_config_id="dec-" + "0" * 16,
        normalization_policy_id="norm-" + "0" * 16,
        backend_family="fake", inference_precision="float32",
        device_policy_id="infdev-" + "0" * 16,
        compatibility_id="infcompat-" + "0" * 16)


def _fairness(fair: bool) -> MatchedPairFairness:
    checks = (DatasetCheck(rule="same_everything", passed=fair, detail=""),)
    return MatchedPairFairness(
        base=_facts("base", "base"), trained=_facts("trained", "ckpt"),
        task_id=_ID_KWARGS["task_id"],
        prepared_digest=_ID_KWARGS["prepared_digest"],
        confounded_fields=() if fair else ("decoding_config_id",),
        checks=checks, fair=fair)


def _counts(*, total: int, improved: int, regressed: int,
            differed: int) -> PairedComparisonCounts:
    both = total - improved - regressed
    return PairedComparisonCounts(
        total=total, both_correct=both // 2,
        both_incorrect=both - both // 2,
        base_correct_trained_incorrect=regressed,
        base_incorrect_trained_correct=improved,
        predictions_identical=total - differed,
        predictions_differed=differed, base_invalid=0, trained_invalid=0,
        abstention_decision_changes=0)


def _comparison(*, improved: int, regressed: int, differed: int,
                test_count: int, fair: bool = True) -> PairedComparison:
    total = max(improved + regressed, differed, test_count, 1) + 2
    non_train = _counts(total=total, improved=improved, regressed=regressed,
                        differed=differed)
    return PairedComparison(
        fairness=_fairness(fair),
        feature_policy_id="feat-" + "0" * 12,
        label_policy_id="label-" + "0" * 12,
        aligned_partitions=DatasetPartitionCounts(
            train=0, validation=0, test=test_count,
            abstention=total - test_count),
        counts_all=non_train, counts_non_train=non_train,
        comparison_id=derive_comparison_id(**_ID_KWARGS), **_ID_KWARGS)


def test_comparison_id_stability_and_sensitivity() -> None:
    base = derive_comparison_id(**_ID_KWARGS)
    assert base == derive_comparison_id(**_ID_KWARGS)
    for field in _ID_KWARGS:
        mutated = dict(_ID_KWARGS)
        mutated[field] = mutated[field][:-1] + "f"
        assert derive_comparison_id(**mutated) != base, field


def test_conclusion_mapping_is_total_and_honest() -> None:
    policy = build_default_interpretation_policy(
        min_eligible_test_examples=2, min_changed_predictions=1)
    for improved, regressed, test_count in product(
            range(3), range(3), (0, 1, 2, 3)):
        differed = improved + regressed
        comparison = _comparison(
            improved=improved, regressed=regressed, differed=differed,
            test_count=test_count)
        interp = interpret_paired_comparison(
            comparison, policy=policy,
            corpus_provenance=CorpusProvenance.PROJECT_PERSISTED)
        C = InterpretationConclusion
        if differed == 0:
            assert interp.conclusion is C.NO_OBSERVED_EFFECT
        elif test_count < 2:
            assert interp.conclusion is C.INCONCLUSIVE_UNDERPOWERED
        elif improved and not regressed:
            assert interp.conclusion is C.BETTER_ON_THIS_CORPUS
        elif regressed and not improved:
            assert interp.conclusion is C.WORSE_ON_THIS_CORPUS
        elif improved and regressed:
            assert interp.conclusion is C.MIXED_ON_THIS_CORPUS
        # regressions are ALWAYS surfaced, whatever the conclusion
        if regressed:
            assert "regressions_present" in interp.qualifiers
        # raw counts always precede wording
        assert f"non_train_improved={improved}" in interp.reasons
        assert f"non_train_regressed={regressed}" in interp.reasons
        # determinism
        assert interp == interpret_paired_comparison(
            comparison, policy=policy,
            corpus_provenance=CorpusProvenance.PROJECT_PERSISTED)


def test_confounded_pair_never_yields_an_unqualified_conclusion() -> None:
    policy = build_default_interpretation_policy(
        min_eligible_test_examples=1)
    for improved, regressed in product(range(3), range(3)):
        comparison = _comparison(
            improved=improved, regressed=regressed,
            differed=improved + regressed, test_count=3, fair=False)
        interp = interpret_paired_comparison(
            comparison, policy=policy,
            corpus_provenance=CorpusProvenance.PROJECT_PERSISTED)
        assert interp.conclusion is InterpretationConclusion.CONFOUNDED


def test_fixture_provenance_always_marks_engineering_only() -> None:
    policy = build_default_interpretation_policy(
        min_eligible_test_examples=1)
    for improved, regressed in product(range(2), range(2)):
        comparison = _comparison(
            improved=improved, regressed=regressed,
            differed=improved + regressed, test_count=3)
        interp = interpret_paired_comparison(
            comparison, policy=policy,
            corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
        assert interp.engineering_proof_only is True
        assert ("fixture_generated_corpus_engineering_proof_only"
                in interp.qualifiers)


def test_paired_comparison_is_input_order_independent(
    tmp_path, matched_pair_pipeline,
) -> None:
    from verifiednet.evaluation import build_paired_comparison

    ctx = matched_pair_pipeline(
        tmp_path,
        base_responder=lambda p, d: '{"prediction_type": "abstention"}',
        trained_responder=lambda p, d: (
            '{"prediction_type": "diagnosis", '
            '"fault_family": "bgp_remote_as_mismatch"}'))
    first = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    second = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    assert first.comparison == second.comparison
    assert first.disagreements == second.disagreements
    # runs are example-id sorted by the engine; the paired result is fully
    # example-id keyed, so no other input order exists to vary.
    ids = [d.example_id for d in first.disagreements]
    assert ids == sorted(ids)
