"""Gate 11 unit tests: eligibility, bundle, spec, predictor happy paths."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    DecodingConfig,
    FakeInferenceBackend,
    VerifiedCheckpointPredictor,
    assess_checkpoint_prediction_eligibility,
)

pytestmark = pytest.mark.unit

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]

_DIAG = '{"prediction_type": "diagnosis", "fault_family": "bgp_remote_as_mismatch"}'
_ABST = '{"prediction_type": "abstention"}'


def _features(ctx):
    """A DatasetFeatures instance derived from the pipeline's prepared corpus."""
    from verifiednet.datasets.features import DatasetFeatures, FeatureEvidenceRef

    return DatasetFeatures(
        feature_policy_id="feat-" + "0" * 12, topology_hash="topo-hash",
        backend="frr", baseline_evidence=FeatureEvidenceRef(
            relative_path="evidence/baseline.json"))


def test_eligibility_accepts_genuine_checkpoint(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    result = assess_checkpoint_prediction_eligibility(
        ctx.checkpoint_dir, ctx.compatibility)
    assert result.eligible is True, result.failures
    assert result.checkpoint_id is not None
    assert result.checkpoint_id.startswith("realckpt-")
    assert result.checkpoint_digest is not None
    assert result.checkpoint_digest.startswith("realdig-")
    rules = {c.rule for c in result.checks}
    assert {"genuine_real_payload_format", "not_simulated",
            "loadable_as_real_model", "never_evaluated_or_benchmarked",
            "architecture_supported",
            "completed_execution_recorded"} <= rules


def test_bundle_binds_verified_paths_without_loading_a_model(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    bundle = ctx.bundle
    assert bundle.weights_path.name == "model.safetensors"
    assert bundle.config_path.name == "config.json"
    assert bundle.tokenizer_path.name == "tokenizer.json"
    assert bundle.manifest.checkpoint_id == ctx.checkpoint_dir.name
    assert bundle.eligibility.eligible is True
    # constructing the bundle (and the whole offline pipeline) never imports ML
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules
    # the fresh fingerprint matches the manifest-declared hashes
    fp = bundle.fingerprint()
    for entry in bundle.manifest.files:
        assert fp[entry.relative_path] == entry.sha256
    assert "manifest.json" in fp
    assert bundle.reverify().eligible is True


def test_predictor_spec_and_baseline_embedding(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    predictor = VerifiedCheckpointPredictor(
        task=ctx.task, bundle=ctx.bundle,
        backend=FakeInferenceBackend(fixed_text=_ABST),
        prompt_template=ctx.template, device_policy=ctx.device_policy,
        backend_family="fake")
    spec = predictor.predictor_spec
    assert spec.predictor_id.startswith("ckptpred-")
    assert len(spec.predictor_id) == len("ckptpred-") + 24
    assert spec.checkpoint_id == ctx.bundle.manifest.checkpoint_id
    assert spec.checkpoint_digest == ctx.bundle.manifest.checkpoint_digest
    assert spec.inference_precision == "float32"
    assert spec.device_policy_id == ctx.device_policy.device_policy_id
    # the Gate-7 BaselineSpec embeds the full checkpoint-predictor spec
    baseline = predictor.spec
    assert baseline.rule_configuration["checkpoint_predictor_id"] == \
        spec.predictor_id
    assert spec.predictor_id in \
        baseline.rule_configuration["checkpoint_predictor_spec"]
    assert baseline.task_id == ctx.task.task_id


def test_predict_maps_outputs_through_the_gate8_pipeline(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    features = _features(ctx)

    def build(text: str) -> VerifiedCheckpointPredictor:
        return VerifiedCheckpointPredictor(
            task=ctx.task, bundle=ctx.bundle,
            backend=FakeInferenceBackend(fixed_text=text),
            prompt_template=ctx.template, device_policy=ctx.device_policy,
            backend_family="fake")

    diagnosis = build(_DIAG).predict(features)
    assert diagnosis.outcome_kind == "diagnosis"
    assert diagnosis.fault_family == "bgp_remote_as_mismatch"
    abstention = build(_ABST).predict(features)
    assert abstention.outcome_kind == "abstention"
    assert abstention.reason_code == "model_abstained"
    invalid = build("not json at all").predict(features)
    assert invalid.outcome_kind == "invalid"
    assert invalid.reason_code == "malformed_json"
    for prediction in (diagnosis, abstention, invalid):
        assert prediction.prediction_id.startswith("pred-")


def test_build_twice_is_deterministic(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    features = _features(ctx)

    def build() -> VerifiedCheckpointPredictor:
        return VerifiedCheckpointPredictor(
            task=ctx.task, bundle=ctx.bundle,
            backend=FakeInferenceBackend(fixed_text=_DIAG),
            prompt_template=ctx.template, device_policy=ctx.device_policy,
            backend_family="fake", decoding=DecodingConfig(max_tokens=64))

    first, second = build(), build()
    assert first.predictor_spec == second.predictor_spec
    assert first.spec == second.spec
    p1, p2 = first.predict(features), second.predict(features)
    assert p1 == p2
    assert p1.prediction_id == p2.prediction_id
