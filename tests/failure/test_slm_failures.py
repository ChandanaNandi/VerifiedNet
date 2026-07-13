"""Gate 8 failure tests: malformed output and backend failures become invalid."""

from __future__ import annotations

import json

import pytest

from verifiednet.datasets.features import DatasetFeatures, FeatureEvidenceRef, FeaturePolicy
from verifiednet.evaluation import (
    DecodingConfig,
    FakeInferenceBackend,
    InvalidPrediction,
    SlmPredictor,
    diagnosis_prompt_template,
    diagnosis_task,
)

pytestmark = pytest.mark.failure


def _features() -> DatasetFeatures:
    return DatasetFeatures(
        feature_policy_id=FeaturePolicy().policy_id, topology_hash="a" * 64,
        backend="frr_compose",
        baseline_evidence=FeatureEvidenceRef(relative_path="evidence/baseline.json"),
        onset_evidence=FeatureEvidenceRef(relative_path="evidence/onset.json"))


def _predict(responder=None, *, fail=None) -> object:
    backend = FakeInferenceBackend(responder=responder, fail=fail)
    slm = SlmPredictor(
        task=diagnosis_task(), backend=backend,
        prompt_template=diagnosis_prompt_template(), model_identifier="m",
        backend_name="fake", decoding=DecodingConfig())
    return slm.predict(_features())


@pytest.mark.parametrize(("text", "reason"), [
    ("not json", "malformed_json"),
    ("[1, 2, 3]", "not_an_object"),
    (json.dumps({"prediction_type": "diagnosis"}), "missing_fault_family"),
    (json.dumps({"prediction_type": "diagnosis", "fault_family": ""}), "missing_fault_family"),
    (json.dumps({"prediction_type": "diagnosis", "fault_family": "nope"}), "unknown_fault_family"),
    (json.dumps({"prediction_type": "weird"}), "unsupported_prediction_type"),
    (json.dumps({"no_type": True}), "unsupported_prediction_type"),
])
def test_malformed_outputs_become_invalid(text: str, reason: str) -> None:
    pred = _predict(lambda p, d: text)
    assert isinstance(pred, InvalidPrediction)
    assert pred.reason_code == reason


def test_backend_unavailable_becomes_invalid() -> None:
    pred = _predict(fail="unavailable")
    assert isinstance(pred, InvalidPrediction)
    assert pred.reason_code == "backend_unavailable"


def test_backend_timeout_becomes_invalid() -> None:
    pred = _predict(fail="timeout")
    assert isinstance(pred, InvalidPrediction)
    assert pred.reason_code == "inference_timeout"


def test_no_exception_escapes_predict() -> None:
    # Every failure path returns a prediction; none raises out of predict().
    for responder in (lambda p, d: "", lambda p, d: "{", lambda p, d: "null"):
        assert isinstance(_predict(responder), InvalidPrediction)
