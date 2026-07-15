"""Gate 12 failure tests: every mismatch, corruption, and confound fails closed."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    CheckpointPredictionError,
    ComparisonError,
    CorpusProvenance,
    EvaluationRun,
    build_default_interpretation_policy,
    build_paired_comparison,
    interpret_paired_comparison,
    load_verified_base_model_bundle,
    run_benchmark,
    verify_base_model_dir,
    verify_comparison,
    write_comparison,
)
from verifiednet.training import build_minimal_safetensors

pytestmark = pytest.mark.failure

_ABST = '{"prediction_type": "abstention"}'
_RAS = '{"prediction_type": "diagnosis", "fault_family": "bgp_remote_as_mismatch"}'


def _make_base_dir(root: Path, *, architecture: str = "AutoModelForCausalLM") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.safetensors").write_bytes(build_minimal_safetensors(
        {"w.weight": ((2, 2), bytes(16))}))
    (root / "config.json").write_text(json.dumps(
        {"architectures": [architecture], "vocab_size": 2}, sort_keys=True))
    (root / "tokenizer.json").write_text(json.dumps(
        {"version": "1.0"}, sort_keys=True))
    return root


def _compat(*arches: str):
    from verifiednet.evaluation import build_checkpoint_inference_compatibility

    return build_checkpoint_inference_compatibility(
        supported_architectures=arches or ("AutoModelForCausalLM",))


def test_base_model_verification_failures(tmp_path: Path) -> None:
    compat = _compat()
    missing = verify_base_model_dir(tmp_path / "nope",
                                    architecture_class="AutoModelForCausalLM")
    assert missing.verified is False

    root = _make_base_dir(tmp_path / "base")
    ok = verify_base_model_dir(root, architecture_class="AutoModelForCausalLM")
    assert ok.verified is True

    # corrupted weights: not a safetensors payload
    (root / "model.safetensors").write_bytes(b"\xff" * 32)
    assert verify_base_model_dir(
        root, architecture_class="AutoModelForCausalLM").verified is False
    _make_base_dir(root)

    # wrong architecture vs config
    assert verify_base_model_dir(
        root, architecture_class="Qwen2ForCausalLM").verified is False

    # symlinked payload refused
    (root / "model.safetensors").unlink()
    (root / "model.safetensors").symlink_to(root / "config.json")
    assert verify_base_model_dir(
        root, architecture_class="AutoModelForCausalLM").verified is False
    (root / "model.safetensors").unlink()
    _make_base_dir(root)

    # bundle loader fail-closed paths
    with pytest.raises(CheckpointPredictionError):  # mutable revision
        load_verified_base_model_bundle(
            root, model_identifier="m", model_revision="main",
            architecture_class="AutoModelForCausalLM", compatibility=compat)
    with pytest.raises(CheckpointPredictionError):  # out-of-scope architecture
        load_verified_base_model_bundle(
            root, model_identifier="m", model_revision="a" * 40,
            architecture_class="OtherLM", compatibility=compat)
    bundle = load_verified_base_model_bundle(
        root, model_identifier="m", model_revision="a" * 40,
        architecture_class="AutoModelForCausalLM", compatibility=compat)
    # post-bundle mutation refused at the moment of use
    (root / "config.json").write_text(json.dumps(
        {"architectures": ["AutoModelForCausalLM"], "vocab_size": 3},
        sort_keys=True))
    with pytest.raises(CheckpointPredictionError):
        bundle.reverify()


def test_mismatched_runs_are_refused(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    # same predictor on both sides is not a pair
    with pytest.raises(ComparisonError):
        build_paired_comparison(ctx.base_run, ctx.base_run,
                                fairness=ctx.fairness)
    # fairness facts must bind exactly these runs
    swapped = ctx.fairness.model_copy(update={
        "base": ctx.fairness.trained, "trained": ctx.fairness.base})
    with pytest.raises(ComparisonError):
        build_paired_comparison(ctx.base_run, ctx.trained_run,
                                fairness=swapped)
    # a doctored run with a missing aligned example is refused
    doctored = EvaluationRun.model_construct(
        **{**dict(ctx.trained_run), "records": ctx.trained_run.records[1:]})
    with pytest.raises(ComparisonError):
        build_paired_comparison(ctx.base_run, doctored, fairness=ctx.fairness)
    # a doctored run over a different prepared corpus is refused
    other = EvaluationRun.model_construct(
        **{**dict(ctx.trained_run), "prepared_digest": "prepdig-" + "f" * 24})
    with pytest.raises(ComparisonError):
        build_paired_comparison(ctx.base_run, other, fairness=ctx.fairness)


def test_confounded_pair_is_visible_and_qualified(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    from verifiednet.evaluation import (
        DecodingConfig,
        FakeInferenceBackend,
        InterpretationConclusion,
        VerifiedBaseModelPredictor,
        assess_matched_pair_fairness,
        base_model_predictor_facts,
        checkpoint_predictor_facts,
        evaluate_prepared_corpus,
    )

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    # deliberately confounded: the base side uses DIFFERENT decoding
    confounded_base = VerifiedBaseModelPredictor(
        task=ctx.ckptctx.task, bundle=ctx.base_bundle,
        backend=FakeInferenceBackend(fixed_text=_ABST),
        prompt_template=ctx.ckptctx.template,
        device_policy=ctx.ckptctx.device_policy, backend_family="fake",
        decoding=DecodingConfig(max_tokens=64))
    base_run = evaluate_prepared_corpus(
        ctx.evalctx.loaded, confounded_base, ctx.ckptctx.task)
    fairness = assess_matched_pair_fairness(
        base=base_model_predictor_facts(confounded_base),
        trained=checkpoint_predictor_facts(ctx.trained),
        base_run=base_run, trained_run=ctx.trained_run)
    assert fairness.fair is False
    assert "decoding_config_id" in fairness.confounded_fields
    result = build_paired_comparison(base_run, ctx.trained_run,
                                     fairness=fairness)
    interp = interpret_paired_comparison(
        result.comparison, policy=build_default_interpretation_policy(),
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    assert interp.conclusion is InterpretationConclusion.CONFOUNDED


def test_comparison_store_failures(
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
    root = tmp_path / "comparisons"
    written = write_comparison(result, interp, root)

    with pytest.raises(ComparisonError):  # unsafe overwrite refused
        write_comparison(result, interp, root)

    # incorrect paired counts: a dropped disagreement is refused pre-write
    truncated = replace(result, disagreements=result.disagreements[:-1])
    with pytest.raises(ComparisonError):
        write_comparison(truncated, interp, tmp_path / "other")

    # any byte flip in any stored file breaks verification
    for name in ("manifest.json", "summary.json", "disagreements.jsonl"):
        path = written.root / name
        original = path.read_bytes()
        position = len(original) // 2
        path.write_bytes(original[:position]
                         + bytes([original[position] ^ 0xFF])
                         + original[position + 1:])
        assert verify_comparison(written.root).verified is False, name
        path.write_bytes(original)
    assert verify_comparison(written.root).verified is True


def test_benchmark_refuses_duplicates_and_task_mismatch(
    tmp_path: Path, matched_pair_pipeline,
) -> None:
    from verifiednet.evaluation import BenchmarkError, diagnosis_task

    ctx = matched_pair_pipeline(
        tmp_path, base_responder=lambda p, d: _ABST,
        trained_responder=lambda p, d: _RAS)
    with pytest.raises(BenchmarkError):  # duplicate predictor identifier
        run_benchmark(ctx.evalctx.loaded, task=ctx.ckptctx.task,
                      predictors=[ctx.base, ctx.base])
    other_task = diagnosis_task(task_name="another_task")
    with pytest.raises(BenchmarkError):  # predictor built for another task
        run_benchmark(ctx.evalctx.loaded, task=other_task,
                      predictors=[ctx.base, ctx.trained])
