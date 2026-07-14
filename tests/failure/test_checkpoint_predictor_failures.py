"""Gate 11 failure tests: everything fails CLOSED, and backend failure is an
explicit InvalidPrediction — never an abstention, never an escaping exception."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from verifiednet.evaluation import (
    BackendUnavailableError,
    CheckpointPredictionError,
    DecodingConfig,
    FakeInferenceBackend,
    HfCheckpointInferenceBackend,
    InferenceError,
    InferenceResponse,
    VerifiedCheckpointPredictor,
    assess_checkpoint_prediction_eligibility,
    build_checkpoint_inference_compatibility,
    load_verified_checkpoint_bundle,
)

pytestmark = pytest.mark.failure

_ACC = [("ras-ref", "run-a"), ("nr-rev", "run-b"), ("if-ref", "run-c")]


def _features():
    from verifiednet.datasets.features import DatasetFeatures, FeatureEvidenceRef

    return DatasetFeatures(
        feature_policy_id="feat-" + "0" * 12, topology_hash="topo-hash",
        backend="frr", baseline_evidence=FeatureEvidenceRef(
            relative_path="evidence/baseline.json"))


def test_missing_or_empty_directory_is_ineligible(tmp_path: Path) -> None:
    compat = build_checkpoint_inference_compatibility()
    result = assess_checkpoint_prediction_eligibility(
        tmp_path / "nope", compat)
    assert result.eligible is False
    with pytest.raises(CheckpointPredictionError):
        load_verified_checkpoint_bundle(tmp_path / "nope", compatibility=compat)


def test_simulated_or_foreign_manifest_is_rejected(tmp_path: Path) -> None:
    # A hand-built fake/simulated manifest can NEVER validate as a real
    # checkpoint manifest (Literal locks), so eligibility fails closed.
    root = tmp_path / "fake-ckpt"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "checkpoint.json").write_text("{}")
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "checkpoint_format_version": 1,
        "simulated": True,
        "payload_format": "verifiednet.fake-checkpoint-v1",
        "checkpoint_id": "ckpt-" + "0" * 24}))
    result = assess_checkpoint_prediction_eligibility(
        root, build_checkpoint_inference_compatibility())
    assert result.eligible is False
    assert any(c.rule == "manifest_parses" and not c.passed
               for c in result.checks)


def test_corrupt_extra_symlink_and_incomplete_are_rejected(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    compat = ctx.compatibility
    root = ctx.checkpoint_dir

    weights = root / "payload" / "model.safetensors"
    original = weights.read_bytes()
    weights.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
    assert assess_checkpoint_prediction_eligibility(
        root, compat).eligible is False  # corrupted weights
    weights.write_bytes(original)

    extra = root / "payload" / "undeclared.bin"
    extra.write_bytes(b"sneaky")
    result = assess_checkpoint_prediction_eligibility(root, compat)
    assert result.eligible is False  # undeclared file
    assert any(c.rule == "no_unexpected_files" and not c.passed
               for c in result.checks)
    extra.unlink()

    link = root / "payload" / "alias.safetensors"
    link.symlink_to(weights)
    assert assess_checkpoint_prediction_eligibility(
        root, compat).eligible is False  # symlink
    link.unlink()

    marker = root / ".INCOMPLETE"
    marker.write_bytes(b"incomplete\n")
    result = assess_checkpoint_prediction_eligibility(root, compat)
    assert result.eligible is False  # incomplete write
    assert any(c.rule == "incomplete_marker_absent" and not c.passed
               for c in result.checks)
    marker.unlink()

    assert assess_checkpoint_prediction_eligibility(
        root, compat).eligible is True  # fully restored


def test_unsupported_architecture_is_rejected(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    narrow = build_checkpoint_inference_compatibility(
        supported_architectures=("Qwen2ForCausalLM",))  # stub arch not listed
    result = assess_checkpoint_prediction_eligibility(
        ctx.checkpoint_dir, narrow)
    assert result.eligible is False
    assert any(c.rule == "architecture_supported" and not c.passed
               for c in result.checks)
    with pytest.raises(CheckpointPredictionError):
        load_verified_checkpoint_bundle(ctx.checkpoint_dir, compatibility=narrow)


def test_reverify_refuses_a_checkpoint_mutated_after_bundling(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    weights = ctx.checkpoint_dir / "payload" / "model.safetensors"
    original = weights.read_bytes()
    weights.write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
    try:
        with pytest.raises(CheckpointPredictionError):
            ctx.bundle.reverify()
    finally:
        weights.write_bytes(original)
    assert ctx.bundle.reverify().eligible is True


def test_backend_failures_become_invalid_never_abstention(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    features = _features()

    class _ErrorBackend:
        backend_id = "error-v1"

        def generate(self, prompt: str, *, decoding: DecodingConfig
                     ) -> InferenceResponse:
            raise InferenceError("overlength prompt refused")

    cases = [
        (FakeInferenceBackend(fail="unavailable"), "backend_unavailable"),
        (FakeInferenceBackend(fail="timeout"), "inference_timeout"),
        (_ErrorBackend(), "backend_error"),
    ]
    for backend, expected_reason in cases:
        predictor = VerifiedCheckpointPredictor(
            task=ctx.task, bundle=ctx.bundle, backend=backend,
            prompt_template=ctx.template, device_policy=ctx.device_policy,
            backend_family="fake")
        prediction = predictor.predict(features)
        assert prediction.outcome_kind == "invalid", expected_reason
        assert prediction.reason_code == expected_reason


def test_unusable_model_output_is_invalid(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    features = _features()
    cases = [
        ("not json", "malformed_json"),
        ("[1, 2]", "not_an_object"),
        ('{"prediction_type": "diagnosis"}', "missing_fault_family"),
        ('{"prediction_type": "diagnosis", "fault_family": "made_up"}',
         "unknown_fault_family"),
        ('{"prediction_type": "other"}', "unsupported_prediction_type"),
    ]
    for text, expected_reason in cases:
        predictor = VerifiedCheckpointPredictor(
            task=ctx.task, bundle=ctx.bundle,
            backend=FakeInferenceBackend(fixed_text=text),
            prompt_template=ctx.template, device_policy=ctx.device_policy,
            backend_family="fake")
        prediction = predictor.predict(features)
        assert prediction.outcome_kind == "invalid", text
        assert prediction.reason_code == expected_reason


def test_non_greedy_decoding_is_refused_at_construction(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    with pytest.raises(CheckpointPredictionError):
        VerifiedCheckpointPredictor(
            task=ctx.task, bundle=ctx.bundle,
            backend=FakeInferenceBackend(fixed_text="{}"),
            prompt_template=ctx.template, device_policy=ctx.device_policy,
            backend_family="fake", decoding=DecodingConfig(temperature=0.7))


def test_hf_backend_refuses_unsupported_decoding_before_any_ml(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    backend = HfCheckpointInferenceBackend(
        bundle=ctx.bundle, device_policy=ctx.device_policy)
    with pytest.raises(InferenceError):  # sampling temperature
        backend.generate("p", decoding=DecodingConfig(temperature=0.5))
    with pytest.raises(InferenceError):  # stop sequences unsupported
        backend.generate("p", decoding=DecodingConfig(stop=("\n",)))


@pytest.mark.skipif(
    importlib.util.find_spec("transformers") is not None,
    reason="training-hf extras installed; the unavailable path is not reachable",
)
def test_hf_backend_without_extras_is_a_structured_refusal(
    tmp_path: Path, ckpt_predictor_pipeline,
) -> None:
    ctx = ckpt_predictor_pipeline(tmp_path, accepted=_ACC, rejected=["run-rej"])
    backend = HfCheckpointInferenceBackend(
        bundle=ctx.bundle, device_policy=ctx.device_policy)
    with pytest.raises(BackendUnavailableError):
        backend.generate("p", decoding=DecodingConfig())
    # and through the predictor it becomes an explicit InvalidPrediction
    predictor = VerifiedCheckpointPredictor(
        task=ctx.task, bundle=ctx.bundle, backend=backend,
        prompt_template=ctx.template, device_policy=ctx.device_policy)
    prediction = predictor.predict(_features())
    assert prediction.outcome_kind == "invalid"
    assert prediction.reason_code == "backend_unavailable"
