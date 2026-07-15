"""Gate 16A property tests (Hypothesis): v2/prompt byte-equality across the
feature space, determinism, identity stability/sensitivity, canonical
ordering, and the target parser round-trip."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from verifiednet.datasets.features import (
    DatasetFeatures,
    FeatureEvidenceRef,
    FeaturePolicy,
)
from verifiednet.evaluation import diagnosis_prompt_template, diagnosis_task
from verifiednet.training import (
    TRAINING_CANDIDATE_FAMILIES,
    contract_aligned_input_template,
    derive_input_template_id,
    diagnosis_input_template,
    diagnosis_target_template,
)

pytestmark = pytest.mark.property

_TASK = diagnosis_task()
_FEATURE_POLICY_ID = FeaturePolicy().policy_id
_PROMPT = diagnosis_prompt_template()
_V2 = contract_aligned_input_template(
    task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)

_token = st.text(
    alphabet=st.characters(codec="ascii", categories=("L", "N"),
                           include_characters="-_."),
    min_size=1, max_size=64)


@st.composite
def features(draw) -> DatasetFeatures:
    onset = draw(st.booleans())
    return DatasetFeatures(
        feature_policy_id=_FEATURE_POLICY_ID,
        topology_hash=draw(_token), backend=draw(_token),
        baseline_evidence=FeatureEvidenceRef(
            relative_path="evidence/baseline.json"),
        onset_evidence=FeatureEvidenceRef(
            relative_path="evidence/onset.json") if onset else None)


@settings(max_examples=200, deadline=None)
@given(payload=features())
def test_v2_equals_the_deployed_prompt_for_every_feature_payload(
    payload: DatasetFeatures,
) -> None:
    assert _V2.render(payload) == _PROMPT.render(payload)


@settings(max_examples=100, deadline=None)
@given(payload=features())
def test_v2_rendering_is_deterministic_and_feature_pure(
    payload: DatasetFeatures,
) -> None:
    first = _V2.render(payload)
    assert _V2.render(payload) == first
    clone = DatasetFeatures(
        feature_policy_id=payload.feature_policy_id,
        topology_hash=payload.topology_hash, backend=payload.backend,
        baseline_evidence=payload.baseline_evidence,
        onset_evidence=payload.onset_evidence)
    assert _V2.render(clone) == first  # equivalent features -> same bytes


def test_template_identity_stability_and_sensitivity() -> None:
    again = contract_aligned_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)
    assert again.input_template_id == _V2.input_template_id  # stable
    v1 = diagnosis_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID)
    assert v1.input_template_id != _V2.input_template_id  # version-sensitive
    for field, value in (("template_version", 1),
                         ("name", "other"),
                         ("instructions", "other instructions"),
                         ("task_id", "task-other"),
                         ("feature_policy_id", "feat-other")):
        kwargs: dict[str, object] = {
            "schema_version": 1, "template_version": 2,
            "name": _V2.name, "instructions": _V2.instructions,
            "candidate_families": _V2.candidate_families,
            "task_id": _V2.task_id,
            "feature_policy_id": _V2.feature_policy_id,
        }
        kwargs[field] = value
        assert derive_input_template_id(**kwargs) \
            != _V2.input_template_id, field  # type: ignore[arg-type]


@settings(max_examples=50, deadline=None)
@given(data=st.data())
def test_candidate_ordering_is_canonical(data) -> None:
    shuffled = tuple(data.draw(
        st.permutations(list(TRAINING_CANDIDATE_FAMILIES))))
    template = diagnosis_input_template(
        task_id=_TASK.task_id, feature_policy_id=_FEATURE_POLICY_ID,
        candidate_families=shuffled)
    assert template.candidate_families == TRAINING_CANDIDATE_FAMILIES
    assert template.input_template_id == diagnosis_input_template(
        task_id=_TASK.task_id,
        feature_policy_id=_FEATURE_POLICY_ID).input_template_id


@settings(max_examples=40, deadline=None)
@given(family=st.sampled_from(TRAINING_CANDIDATE_FAMILIES))
def test_target_parser_round_trip_for_every_family(family: str) -> None:
    from verifiednet.evaluation.prediction import DiagnosisPrediction
    from verifiednet.evaluation.slm import parse_backend_response

    target = diagnosis_target_template(task_id=_TASK.task_id)
    prediction = parse_backend_response(
        target.render(family), baseline_id="baseline-prop",
        task_id=_TASK.task_id,
        features_payload={"feature_policy_id": _FEATURE_POLICY_ID},
        normalization=_TASK.normalization,
        normalized_candidates=frozenset(
            _TASK.normalization.normalize(f)
            for f in TRAINING_CANDIDATE_FAMILIES))
    assert isinstance(prediction, DiagnosisPrediction)
