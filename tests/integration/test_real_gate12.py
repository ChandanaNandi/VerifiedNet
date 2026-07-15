"""Optional Gate 12 integration: evaluate + benchmark the REAL trained
checkpoint against the matched base model. Never runs in offline CI.

Gated on: the ``integration`` marker AND ``VERIFIEDNET_RUN_REAL_GATE12=1``
AND a verified real checkpoint dir AND the approved base-model snapshot dir
AND the ``training-hf`` extras. Strict offline mode is enforced (network
sabotaged + HF offline env forced by the backend). Output artifacts go to
``VERIFIEDNET_GATE12_OUTPUT_ROOT`` if set, else the test tmp dir.

The test asserts STRUCTURAL consistency only (artifacts verify, sources stay
byte-identical, the comparison recomputes). It makes NO model-quality
assertion — with a fixture-generated corpus this is an engineering proof by
policy, and the interpretation artifact says so explicitly.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    CorpusProvenance,
    DecodingConfig,
    EvidenceRuleBaseline,
    FixedPriorBaseline,
    HfCheckpointInferenceBackend,
    VerifiedBaseModelPredictor,
    VerifiedCheckpointPredictor,
    assess_matched_pair_fairness,
    base_model_predictor_facts,
    build_checkpoint_inference_compatibility,
    build_cpu_inference_device_policy,
    build_default_interpretation_policy,
    build_paired_comparison,
    checkpoint_predictor_facts,
    diagnosis_prompt_template,
    diagnosis_task,
    evaluate_prepared_corpus,
    interpret_paired_comparison,
    load_verified_base_model_bundle,
    load_verified_checkpoint_bundle,
    run_benchmark,
    verify_benchmark,
    verify_comparison,
    verify_evaluation,
    write_benchmark,
    write_comparison,
    write_evaluation,
)

pytestmark = pytest.mark.integration

_ENABLED = os.environ.get("VERIFIEDNET_RUN_REAL_GATE12") == "1"
_CKPT_DIR = os.environ.get("VERIFIEDNET_REAL_CHECKPOINT_DIR", "")
_BASE_DIR = os.environ.get("VERIFIEDNET_BASE_MODEL_DIR", "")
_MODEL_ID = os.environ.get("VERIFIEDNET_MODEL_IDENTIFIER",
                           "Qwen/Qwen2.5-0.5B-Instruct")
_REVISION = os.environ.get("VERIFIEDNET_MODEL_REVISION",
                           "7ae557604adf67be50417f59c2c2f167def9a775")
_OUT_ROOT = os.environ.get("VERIFIEDNET_GATE12_OUTPUT_ROOT", "")


def _dirs_or_skip() -> tuple[Path, Path]:
    if not _ENABLED:
        pytest.skip("VERIFIEDNET_RUN_REAL_GATE12!=1")
    if not _CKPT_DIR or not Path(_CKPT_DIR).is_dir():
        pytest.skip("VERIFIEDNET_REAL_CHECKPOINT_DIR not set / not a dir")
    if not _BASE_DIR or not Path(_BASE_DIR).is_dir():
        pytest.skip("VERIFIEDNET_BASE_MODEL_DIR not set / not a dir")
    for module in ("torch", "transformers"):
        if importlib.util.find_spec(module) is None:
            pytest.skip(f"{module} not installed (training-hf extras required)")
    return Path(_CKPT_DIR), Path(_BASE_DIR)


def _tree_fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_real_gate12_end_to_end(tmp_path: Path, eval_pipeline, monkeypatch) -> None:
    checkpoint_dir, base_dir = _dirs_or_skip()
    out_root = Path(_OUT_ROOT) if _OUT_ROOT else tmp_path / "gate12"

    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("real Gate 12 must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    # 1-3: verify checkpoint + base model; build both verified bundles.
    compatibility = build_checkpoint_inference_compatibility()  # Qwen2 only
    device_policy = build_cpu_inference_device_policy()
    decoding = DecodingConfig(max_tokens=64)
    trained_bundle = load_verified_checkpoint_bundle(
        checkpoint_dir, compatibility=compatibility)
    base_bundle = load_verified_base_model_bundle(
        base_dir, model_identifier=_MODEL_ID, model_revision=_REVISION,
        architecture_class="Qwen2ForCausalLM", compatibility=compatibility)
    source_fingerprints = {
        "checkpoint": _tree_fingerprint(checkpoint_dir),
        "base_model": _tree_fingerprint(base_dir),
    }

    # 4-5: matched predictors — ONE inference stack, weights differ only.
    task = diagnosis_task()
    template = diagnosis_prompt_template()
    base = VerifiedBaseModelPredictor(
        task=task, bundle=base_bundle,
        backend=HfCheckpointInferenceBackend(
            bundle=base_bundle, device_policy=device_policy),
        prompt_template=template, device_policy=device_policy,
        decoding=decoding)
    trained = VerifiedCheckpointPredictor(
        task=task, bundle=trained_bundle,
        backend=HfCheckpointInferenceBackend(
            bundle=trained_bundle, device_policy=device_policy),
        prompt_template=template, device_policy=device_policy,
        decoding=decoding)

    # Fixture-generated prepared corpus (declared as such to the policy).
    ctx = eval_pipeline(tmp_path, accepted=[("ras-ref", "run-a"),
                                            ("nr-rev", "run-b"),
                                            ("pf-ref", "run-c")],
                        rejected=["run-rej"])
    corpus_fp = _tree_fingerprint(Path(str(ctx.prepared_dir)))

    # 6-7: evaluate BOTH through the unchanged Gate 7 engine; persist+verify.
    base_run = evaluate_prepared_corpus(ctx.loaded, base, task)
    trained_run = evaluate_prepared_corpus(ctx.loaded, trained, task)
    for run in (base_run, trained_run):
        written = write_evaluation(run, out_root / "evaluations")
        assert verify_evaluation(written.root).verified is True

    # 8-9: unchanged Gate 9 benchmark over all four predictors; persist+verify.
    benchmark = run_benchmark(
        ctx.loaded, task=task,
        predictors=[
            FixedPriorBaseline(task=task,
                               fixed_fault_family="bgp_remote_as_mismatch"),
            EvidenceRuleBaseline(task=task,
                                 default_fault_family="bgp_remote_as_mismatch"),
            base, trained])
    assert len(benchmark.comparison) == 4
    written_benchmark = write_benchmark(benchmark, out_root / "benchmarks")
    assert verify_benchmark(written_benchmark.root).verified is True

    # 10: fairness + paired comparison + disagreement report; persist+verify.
    fairness = assess_matched_pair_fairness(
        base=base_model_predictor_facts(base),
        trained=checkpoint_predictor_facts(trained),
        base_run=base_run, trained_run=trained_run)
    assert fairness.fair is True, fairness.checks
    result = build_paired_comparison(base_run, trained_run, fairness=fairness)
    interpretation = interpret_paired_comparison(
        result.comparison, policy=build_default_interpretation_policy(),
        corpus_provenance=CorpusProvenance.FIXTURE_GENERATED)
    assert interpretation.engineering_proof_only is True  # by policy
    written_comparison = write_comparison(
        result, interpretation, out_root / "comparisons")
    assert verify_comparison(written_comparison.root).verified is True

    # 11: every source artifact remained byte-identical.
    assert _tree_fingerprint(checkpoint_dir) == source_fingerprints["checkpoint"]
    assert _tree_fingerprint(base_dir) == source_fingerprints["base_model"]
    assert _tree_fingerprint(Path(str(ctx.prepared_dir))) == corpus_fp
    assert trained_bundle.reverify().eligible is True
    base_bundle.reverify()

    # 12: structural consistency only — NO quality assertion. The offline env
    # was forced before any Transformers call.
    assert os.environ.get("HF_HUB_OFFLINE") == "1"
    assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
