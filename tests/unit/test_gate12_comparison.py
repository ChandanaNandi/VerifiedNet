"""Gate 12 unit tests: base bundle, matched predictors, paired comparison,
disagreements, interpretation policy, comparison store, benchmark reuse."""

from __future__ import annotations

from pathlib import Path

import pytest

from verifiednet.evaluation import (
    CorpusProvenance,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    InterpretationConclusion,
    TransitionCategory,
    build_default_interpretation_policy,
    build_paired_comparison,
    interpret_paired_comparison,
    read_comparison,
    run_benchmark,
    verify_benchmark,
    verify_comparison,
    write_benchmark,
    write_comparison,
)

pytestmark = pytest.mark.unit

_ABST = '{"prediction_type": "abstention"}'


def _family_from_prompt(prompt: str) -> str:
    # Deterministic "oracle-ish" fake: pick a family from the candidate list
    # rendered in the prompt (the class space is public; this is NOT a label).
    return "bgp_remote_as_mismatch"


def _base_responder(prompt: str, decoding) -> str:
    return _ABST


def _trained_responder(prompt: str, decoding) -> str:
    return ('{"prediction_type": "diagnosis", "fault_family": "'
            + _family_from_prompt(prompt) + '"}')


def test_base_bundle_and_predictor_specs(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=_base_responder,
        trained_responder=_trained_responder)
    bundle = ctx.base_bundle
    assert bundle.base_model_id.startswith("basemodel-")
    assert set(bundle.content_hashes) == {
        "config.json", "model.safetensors", "tokenizer.json"}
    assert bundle.reverify().verified is True
    spec = ctx.base.predictor_spec
    assert spec.predictor_id.startswith("basepred-")
    assert spec.model_revision == "a" * 40
    assert spec.inference_precision == "float32"
    # the Gate-7 BaselineSpec embeds the base predictor spec
    assert ctx.base.spec.rule_configuration["base_model_predictor_id"] == \
        spec.predictor_id


def test_fairness_passes_for_matched_pair(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=_base_responder,
        trained_responder=_trained_responder)
    assert ctx.fairness.fair is True, ctx.fairness.checks
    assert ctx.fairness.confounded_fields == ()
    assert ctx.fairness.base.role == "matched_base_model"
    assert ctx.fairness.trained.role == "trained_checkpoint"


def test_paired_comparison_counts_and_disagreements(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=_base_responder,
        trained_responder=_trained_responder)
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    comparison = result.comparison
    total = len(ctx.base_run.records)
    counts = comparison.counts_all
    assert counts.total == total
    # base abstains everywhere: correct only on the abstention example;
    # trained diagnoses ras everywhere: correct only on the ras example.
    assert counts.base_incorrect_trained_correct == 1  # the ras example
    assert counts.base_correct_trained_incorrect == 1  # the abstention example
    assert counts.predictions_differed == total
    assert counts.predictions_identical == 0
    assert counts.abstention_decision_changes == total
    assert len(result.disagreements) == total
    transitions = {d.example_id: d.transition for d in result.disagreements}
    assert TransitionCategory.IMPROVED in transitions.values()
    assert TransitionCategory.REGRESSED in transitions.values()
    ids = [d.example_id for d in result.disagreements]
    assert ids == sorted(ids)
    assert comparison.comparison_id.startswith("cmp-")
    # partition counts align with the corpus
    parts = comparison.aligned_partitions
    assert (parts.train + parts.validation + parts.test
            + parts.abstention) == total


def test_interpretation_policy_and_wording(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=_base_responder,
        trained_responder=_trained_responder)
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    policy = build_default_interpretation_policy()
    assert policy.interpretation_policy_id.startswith("interp-")
    assert policy.min_eligible_test_examples == 30
    interp = interpret_paired_comparison(
        result.comparison, policy=policy,
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    # tiny fixture corpus: underpowered, engineering-proof only
    assert interp.conclusion is InterpretationConclusion.INCONCLUSIVE_UNDERPOWERED
    assert interp.engineering_proof_only is True
    assert "fixture_generated_corpus_engineering_proof_only" in interp.qualifiers
    assert any("insufficient evidence" in q for q in interp.qualifiers)
    assert "regressions_present" in interp.qualifiers  # regressions surfaced
    assert any(r.startswith("eligible_test_examples=") for r in interp.reasons)
    # deterministic
    again = interpret_paired_comparison(
        result.comparison, policy=policy,
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    assert again == interp


def test_no_changed_predictions_is_no_observed_effect(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=_base_responder,
        trained_responder=_base_responder)  # identical outputs
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    assert result.comparison.counts_all.predictions_differed == 0
    assert result.disagreements == ()
    interp = interpret_paired_comparison(
        result.comparison, policy=build_default_interpretation_policy(),
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    assert interp.conclusion is InterpretationConclusion.NO_OBSERVED_EFFECT


def test_comparison_store_round_trip(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=_base_responder,
        trained_responder=_trained_responder)
    result = build_paired_comparison(
        ctx.base_run, ctx.trained_run, fairness=ctx.fairness)
    interp = interpret_paired_comparison(
        result.comparison, policy=build_default_interpretation_policy(),
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    written = write_comparison(result, interp, tmp_path / "comparisons")
    assert written.comparison_id == result.comparison.comparison_id
    assert written.comparison_digest.startswith("cmpdig-")
    verification = verify_comparison(written.root)
    assert verification.verified is True, verification.failures
    loaded = read_comparison(written.root)
    assert loaded.comparison == result.comparison
    assert loaded.disagreements == result.disagreements
    assert loaded.interpretation == interp
    assert loaded.manifest.comparison_digest == written.comparison_digest


def test_benchmark_includes_all_four_predictor_roles(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=_base_responder,
        trained_responder=_trained_responder)
    task = ctx.ckptctx.task
    predictors = [
        FixedPriorBaseline(task=task,
                           fixed_fault_family="bgp_remote_as_mismatch"),
        EvidenceRuleBaseline(task=task,
                             default_fault_family="bgp_remote_as_mismatch"),
        ctx.base, ctx.trained,
    ]
    result = run_benchmark(ctx.evalctx.loaded, task=task,
                           predictors=predictors)
    assert len(result.comparison) == 4  # nobody silently dropped
    identifiers = {row.predictor_identifier for row in result.comparison}
    assert ctx.base.spec.baseline_id in identifiers
    assert ctx.trained.spec.baseline_id in identifiers
    ranks = [e.rank for e in result.ranking]
    assert ranks == [1, 2, 3, 4]
    written = write_benchmark(result, tmp_path / "benchmarks")
    assert verify_benchmark(written.root).verified is True
