"""Gate 8 property tests: deterministic prompts, normalization, predictions."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.datasets.features import DatasetFeatures, FeatureEvidenceRef, FeaturePolicy
from verifiednet.evaluation import (
    DecodingConfig,
    FakeInferenceBackend,
    NormalizationPolicy,
    SlmPredictor,
    diagnosis_prompt_template,
    diagnosis_task,
)

pytestmark = pytest.mark.property

_hex = st.integers(min_value=0, max_value=(1 << 64) - 1).map(lambda n: f"{n:064x}")


@st.composite
def _features(draw: st.DrawFn) -> DatasetFeatures:
    onset = draw(st.booleans())
    return DatasetFeatures(
        feature_policy_id=FeaturePolicy().policy_id, topology_hash=draw(_hex),
        backend=draw(st.sampled_from(["frr_compose", "other"])),
        baseline_evidence=FeatureEvidenceRef(relative_path="evidence/baseline.json"),
        onset_evidence=(FeatureEvidenceRef(relative_path="evidence/onset.json")
                        if onset else None),
    )


@given(features=_features())
@settings(max_examples=200)
def test_prompt_render_is_deterministic(features: DatasetFeatures) -> None:
    tmpl = diagnosis_prompt_template()
    assert tmpl.render(features) == tmpl.render(features)


@given(features=_features())
@settings(max_examples=150)
def test_prediction_request_is_deterministic(features: DatasetFeatures) -> None:
    # Identical features under the same predictor yield the identical prediction id.
    captured: list[str] = []

    def responder(prompt: str, decoding: DecodingConfig) -> str:
        captured.append(prompt)
        return '{"prediction_type": "abstention"}'

    slm = SlmPredictor(
        task=diagnosis_task(), backend=FakeInferenceBackend(responder=responder),
        prompt_template=diagnosis_prompt_template(), model_identifier="m",
        backend_name="fake")
    p1 = slm.predict(features)
    p2 = slm.predict(features)
    assert p1.prediction_id == p2.prediction_id
    assert captured[0] == captured[1]  # identical rendered prompt each time


@given(raw=st.text(min_size=1, max_size=30))
@settings(max_examples=100)
def test_normalization_is_stable_and_idempotent(raw: str) -> None:
    norm = NormalizationPolicy()
    once = norm.normalize(raw)
    assert once == norm.normalize(raw)
    assert once == norm.normalize(once)  # idempotent
